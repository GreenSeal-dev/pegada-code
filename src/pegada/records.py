"""Agent-agnostic usage records and the deduplication rules shared by all parsers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, Optional

# The four token classes that drive the energy model, in display order.
TOKEN_CLASSES = ("input", "cache_write", "cache_read", "output")

LEDGER_SCHEMA = 1


@dataclass(frozen=True)
class UsageRecord:
    """Token usage of one model response (one API message).

    ``output`` already includes reasoning/thinking tokens; ``thinking`` is the
    subset reported separately by the provider, kept for information only.
    """

    msg_id: str
    agent: str
    session_id: str
    timestamp: str
    model: str
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    thinking: int = 0
    is_subagent: bool = False
    agent_type: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)
    # Working directory of the agent. Used to attribute records to a project;
    # deliberately not written to the ledger (it can reveal local paths).
    cwd: str = field(default="", compare=False)

    @property
    def total_tokens(self) -> int:
        return self.input + self.cache_write + self.cache_read + self.output

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "v": LEDGER_SCHEMA,
            "kind": "message",
            "agent": self.agent,
            "msg_id": self.msg_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "model": self.model,
            "input": self.input,
            "cache_write": self.cache_write,
            "cache_read": self.cache_read,
            "output": self.output,
        }
        if self.thinking:
            d["thinking"] = self.thinking
        if self.is_subagent:
            d["is_subagent"] = True
        if self.agent_type:
            d["agent_type"] = self.agent_type
        if self.extras:
            d["extras"] = self.extras
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UsageRecord":
        return cls(
            msg_id=str(d["msg_id"]),
            agent=str(d.get("agent", "unknown")),
            session_id=str(d.get("session_id", "")),
            timestamp=str(d.get("timestamp", "")),
            model=str(d["model"]),
            input=int(d.get("input", 0)),
            cache_write=int(d.get("cache_write", 0)),
            cache_read=int(d.get("cache_read", 0)),
            output=int(d.get("output", 0)),
            thinking=int(d.get("thinking", 0)),
            is_subagent=bool(d.get("is_subagent", False)),
            agent_type=d.get("agent_type"),
            extras=dict(d.get("extras") or {}),
        )


@dataclass(frozen=True)
class SessionTotals:
    """Per-model token totals that the agent itself reports for a session.

    Used only to quantify usage that transcripts do not log (see METHODOLOGY,
    "Coverage"); never added to headline figures. The totals are a snapshot:
    ``as_of`` (ISO 8601, UTC) is when they were taken, so they must only be
    compared with records logged up to that time. Empty when unknown.
    """

    agent: str
    session_id: str
    source: str
    models: Dict[str, Dict[str, int]]
    as_of: str = ""

    @property
    def total_tokens(self) -> int:
        return sum(sum(v.get(c, 0) for c in TOKEN_CLASSES) for v in self.models.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": LEDGER_SCHEMA,
            "kind": "session_totals",
            "agent": self.agent,
            "session_id": self.session_id,
            "source": self.source,
            "models": self.models,
            **({"as_of": self.as_of} if self.as_of else {}),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionTotals":
        models = {
            str(m): {c: int((v or {}).get(c, 0)) for c in TOKEN_CLASSES}
            for m, v in (d.get("models") or {}).items()
        }
        return cls(
            agent=str(d.get("agent", "unknown")),
            session_id=str(d.get("session_id", "")),
            source=str(d.get("source", "")),
            models=models,
            as_of=str(d.get("as_of", "")),
        )


def _grew(new: UsageRecord, old: UsageRecord) -> bool:
    return any(getattr(new, c) > getattr(old, c) for c in TOKEN_CLASSES + ("thinking",))


def merge_record(old: Optional[UsageRecord], new: UsageRecord) -> UsageRecord:
    """Combine two observations of the same message id.

    Streaming writes one line per content block with the same message id; the
    input/cache counts repeat and the output count grows to its final value.
    Taking the element-wise maximum recovers the final usage. Attribution
    (session, subagent flag) is kept from the first observation, so a message
    copied into a continued session stays with its original session.
    """
    if old is None:
        return new
    if not _grew(new, old):
        return old
    return replace(
        old,
        **{c: max(getattr(old, c), getattr(new, c)) for c in TOKEN_CLASSES + ("thinking",)},
    )


def merge_records(records: Iterable[UsageRecord]) -> Dict[str, UsageRecord]:
    """Deduplicate records by message id (first-seen order is preserved)."""
    out: Dict[str, UsageRecord] = {}
    for r in records:
        out[r.msg_id] = merge_record(out.get(r.msg_id), r)
    return out


def totals_supersede(new: SessionTotals, old: Optional[SessionTotals]) -> bool:
    """Snapshots are cumulative: a larger one is later. A timestamped copy of
    the same snapshot replaces an untimestamped one (older ledgers)."""
    if old is None or new.total_tokens > old.total_tokens:
        return True
    return new.total_tokens == old.total_tokens and bool(new.as_of) and not old.as_of


def merge_totals(totals: Iterable[SessionTotals]) -> Dict[str, SessionTotals]:
    """Keep, per (agent, session), the latest (largest) snapshot."""
    out: Dict[str, SessionTotals] = {}
    for t in totals:
        key = f"{t.agent}:{t.session_id}"
        if totals_supersede(t, out.get(key)):
            out[key] = t
    return out
