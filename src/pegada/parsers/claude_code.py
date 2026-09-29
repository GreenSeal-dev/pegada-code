"""Parser for Claude Code transcripts (``~/.claude/projects/**.jsonl``).

The transcript format is undocumented. The rules below were derived from real
transcripts (Claude Code 2.1.x) and are documented in METHODOLOGY.md:

* Layout: ``<projects>/<encoded cwd>/<session>.jsonl``; subagents write to
  ``<session>/subagents/agent-<id>.jsonl`` with an ``agent-<id>.meta.json``
  sidecar holding ``agentType``.
* Usage lives on ``type == "assistant"`` lines in ``message.usage``.
* One API response is written as several lines (one per content block) with the
  same ``message.id``; input/cache counts repeat and ``output_tokens`` grows.
  Callers must merge by message id (:func:`pegada.records.merge_records`).
* A continued session re-contains the records of its predecessor under a new
  ``sessionId``, so deduplication must be global across files.
* ``model == "<synthetic>"`` marks locally generated error stubs; skipped.
* ``type == "cost-state"`` lines carry Claude Code's own per-model session
  totals, which include calls never logged as transcript lines.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from pegada.parsers.base import ParseResult
from pegada.records import SessionTotals, UsageRecord

SKIP_MODELS = {"<synthetic>"}


def default_projects_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def encode_project_path(path: str) -> str:
    """Claude Code's directory name for a working directory (lossy: every
    non-alphanumeric character becomes ``-``)."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.normpath(path))


def is_within(path: str, root: str) -> bool:
    if not path:
        return False
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _int(v: Any) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


class ClaudeCodeParser:
    agent = "claude-code"

    def __init__(self, projects_dir: Optional[str] = None):
        self.projects_dir = projects_dir or default_projects_dir()

    # -- discovery ---------------------------------------------------------

    def discover(self, project_root: str) -> List[str]:
        """Main transcripts in directories whose name starts with the encoded
        project path (this includes sessions started in subdirectories, and
        possibly siblings such as ``/a/b-c`` for ``/a/b``; callers must filter
        records by ``cwd`` with :func:`is_within`)."""
        prefix = encode_project_path(project_root)
        try:
            names = sorted(os.listdir(self.projects_dir))
        except OSError:
            return []
        out = []
        for name in names:
            d = os.path.join(self.projects_dir, name)
            if not name.startswith(prefix) or not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if f.endswith(".jsonl") and os.path.isfile(os.path.join(d, f)):
                    out.append(os.path.join(d, f))
        return out

    def session_files(self, transcript_path: str) -> List[str]:
        files = [transcript_path] if os.path.isfile(transcript_path) else []
        sub = os.path.join(transcript_path[: -len(".jsonl")], "subagents")
        if transcript_path.endswith(".jsonl") and os.path.isdir(sub):
            files += [
                os.path.join(sub, f) for f in sorted(os.listdir(sub)) if f.endswith(".jsonl")
            ]
        return files

    # -- parsing -----------------------------------------------------------

    def parse(self, path: str, offset: int = 0) -> ParseResult:
        is_sub_file = f"{os.sep}subagents{os.sep}" in path
        agent_type = self._agent_type(path) if is_sub_file else None
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return ParseResult(offset=offset)
        end = data.rfind(b"\n")
        if end == -1:
            return ParseResult(offset=offset)
        result = ParseResult(offset=offset + end + 1)
        for raw in data[: end + 1].splitlines():
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                result.bad_lines += 1
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            if kind == "assistant":
                rec = self._record(obj, is_sub_file, agent_type)
                if rec is not None:
                    result.records.append(rec)
            elif kind == "cost-state":
                tot = self._totals(obj)
                if tot is not None:
                    result.totals.append(tot)
            elif kind == "continued-in" and obj.get("sessionId") and obj.get("continuedInSessionId"):
                result.continuations.append((str(obj["sessionId"]), str(obj["continuedInSessionId"])))
        return result

    @staticmethod
    def _agent_type(path: str) -> Optional[str]:
        meta = path[: -len(".jsonl")] + ".meta.json"
        try:
            with open(meta, encoding="utf-8") as fh:
                return json.load(fh).get("agentType")
        except (OSError, ValueError, AttributeError):
            return None

    def _record(self, obj: Dict[str, Any], is_sub_file: bool, agent_type: Optional[str]) -> Optional[UsageRecord]:
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return None
        usage, model = msg.get("usage"), msg.get("model")
        if not isinstance(usage, dict) or not model or model in SKIP_MODELS:
            return None
        counts = {
            "input": _int(usage.get("input_tokens")),
            "cache_write": _int(usage.get("cache_creation_input_tokens")),
            "cache_read": _int(usage.get("cache_read_input_tokens")),
            "output": _int(usage.get("output_tokens")),
        }
        if not any(counts.values()):
            return None
        msg_id = msg.get("id") or obj.get("requestId") or obj.get("uuid")
        if not msg_id:
            return None
        details = usage.get("output_tokens_details") or {}
        is_sub = is_sub_file or bool(obj.get("isSidechain"))
        return UsageRecord(
            msg_id=str(msg_id),
            agent=self.agent,
            session_id=str(obj.get("sessionId") or ""),
            timestamp=str(obj.get("timestamp") or ""),
            model=str(model),
            thinking=_int(details.get("thinking_tokens") if isinstance(details, dict) else 0),
            is_subagent=is_sub,
            agent_type=(agent_type or obj.get("attributionAgent")) if is_sub else None,
            extras=self._extras(obj, usage),
            cwd=str(obj.get("cwd") or ""),
            **counts,
        )

    @staticmethod
    def _extras(obj: Dict[str, Any], usage: Dict[str, Any]) -> Dict[str, Any]:
        """Fields that are recorded but not (yet) part of the energy model."""
        ex: Dict[str, Any] = {}
        speed = usage.get("speed")
        if speed and speed != "standard":
            ex["speed"] = speed
        tier = usage.get("service_tier")
        if tier and tier != "standard":
            ex["service_tier"] = tier
        geo = usage.get("inference_geo")
        if geo and geo != "not_available":
            ex["inference_geo"] = geo
        stu = usage.get("server_tool_use")
        if isinstance(stu, dict):
            for k, name in (("web_search_requests", "web_search"), ("web_fetch_requests", "web_fetch")):
                if _int(stu.get(k)):
                    ex[name] = _int(stu.get(k))
        cc = usage.get("cache_creation")
        if isinstance(cc, dict) and _int(cc.get("ephemeral_1h_input_tokens")):
            ex["cache_write_1h"] = _int(cc.get("ephemeral_1h_input_tokens"))
        if obj.get("advisorModel"):
            ex["advisor_model"] = obj["advisorModel"]
        if obj.get("version"):
            ex["cc_version"] = obj["version"]
        return ex

    def _totals(self, obj: Dict[str, Any]) -> Optional[SessionTotals]:
        usage = obj.get("modelUsage")
        sid = obj.get("sessionId")
        if not isinstance(usage, dict) or not sid:
            return None
        models = {}
        for model, v in usage.items():
            if model in SKIP_MODELS or not isinstance(v, dict):
                continue
            # outputTokens includes thinking tokens (it matches the transcript's
            # output_tokens, which do), so thinkingTokens is not added.
            counts = {
                "input": _int(v.get("inputTokens")),
                "cache_write": _int(v.get("cacheCreationInputTokens")),
                "cache_read": _int(v.get("cacheReadInputTokens")),
                "output": _int(v.get("outputTokens")),
            }
            if any(counts.values()):
                models[str(model)] = counts
        if not models:
            return None
        return SessionTotals(agent=self.agent, session_id=str(sid), source="claude-code:cost-state", models=models)
