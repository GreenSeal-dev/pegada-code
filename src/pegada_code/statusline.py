"""Status line: live footprint of the current session, appended to any previous status line."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Dict

from pegada.estimator import Footprint
from pegada.parsers.claude_code import ClaudeCodeParser
from pegada.records import TOKEN_CLASSES
from pegada.report import impact_line
from pegada_code.project import estimator_for, project_root, read_json, state_dir, write_json


def previous_config_path() -> str:
    return os.path.join(state_dir(), "statusline.json")


def session_line(payload: Dict[str, Any]) -> str:
    tp = payload.get("transcript_path")
    if not tp:
        return ""
    key = payload.get("session_id") or hashlib.sha1(tp.encode()).hexdigest()[:16]
    cache_path = os.path.join(state_dir(), "statusline", f"{key}.json")
    cache = read_json(cache_path, {})
    offsets = cache.get("offsets", {})
    msgs = cache.get("msgs", {})  # msg_id -> [model, input, cache_write, cache_read, output]
    parser = ClaudeCodeParser()
    changed = False
    for path in parser.session_files(tp):
        start = offsets.get(path, 0)
        if os.path.getsize(path) < start:
            start = 0
        res = parser.parse(path, start)
        for r in res.records:
            new = [r.model] + [getattr(r, c) for c in TOKEN_CLASSES]
            old = msgs.get(r.msg_id)
            msgs[r.msg_id] = new if old is None else [old[0]] + [max(a, b) for a, b in zip(old[1:], new[1:])]
        changed = changed or res.offset != start
        offsets[path] = res.offset
    if changed:
        write_json(cache_path, {"offsets": offsets, "msgs": msgs})
    ws = payload.get("workspace") or {}
    est = estimator_for(project_root(ws.get("project_dir") or payload.get("cwd")))
    fp = Footprint()
    for model, *counts in msgs.values():
        est.add(fp, model, dict(zip(TOKEN_CLASSES, counts)))
    return impact_line(est.impact(fp))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    prev_out = ""
    prev = read_json(previous_config_path(), {}).get("previous") or {}
    if prev.get("command"):
        try:
            prev_out = subprocess.run(
                prev["command"], shell=True, input=raw, capture_output=True, text=True, timeout=5
            ).stdout.rstrip("\n")
        except (OSError, subprocess.SubprocessError):
            prev_out = ""
    try:
        ours = session_line(payload)
    except Exception:
        ours = "🌱 pegada: n/a"
    print("  ".join(p for p in (prev_out, ours) if p))
    return 0
