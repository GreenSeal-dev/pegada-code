"""Append-only JSONL ledger of token usage (never energy — estimates are recomputed).

Appends are idempotent: a record is written only if its message id is new or
its token counts grew. Readers merge duplicates, so the ledger stays correct
under concurrent writers, re-ingestion and git ``merge=union``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from pegada.records import (
    SessionTotals,
    UsageRecord,
    merge_record,
    merge_totals,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore


@dataclass
class LedgerContents:
    records: Dict[str, UsageRecord] = field(default_factory=dict)
    totals: Dict[str, SessionTotals] = field(default_factory=dict)
    bad_lines: int = 0


class Ledger:
    def __init__(self, path: str):
        self.path = path

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def read(self) -> LedgerContents:
        out = LedgerContents()
        totals: List[SessionTotals] = []
        try:
            fh = open(self.path, encoding="utf-8")
        except FileNotFoundError:
            return out
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    kind = d.get("kind")
                    if kind == "message":
                        r = UsageRecord.from_dict(d)
                        out.records[r.msg_id] = merge_record(out.records.get(r.msg_id), r)
                    elif kind == "session_totals":
                        totals.append(SessionTotals.from_dict(d))
                    else:
                        out.bad_lines += 1
                except (ValueError, KeyError, TypeError, AttributeError):
                    out.bad_lines += 1
        out.totals = merge_totals(totals)
        return out

    def append(self, records: Iterable[UsageRecord], totals: Iterable[SessionTotals] = ()) -> int:
        """Append what is new; return the number of lines written."""
        records, totals = list(records), list(totals)
        if not records and not totals:
            return 0
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            if fcntl is not None:  # serialise concurrent hooks (read-check-append)
                fcntl.flock(fh, fcntl.LOCK_EX)
            current = self.read()
            lines = []
            for r in records:
                prev = current.records.get(r.msg_id)
                merged = merge_record(prev, r)
                if prev is None or merged is not prev:
                    current.records[r.msg_id] = merged
                    lines.append(json.dumps(merged.to_dict(), separators=(",", ":")))
            for t in totals:
                key = f"{t.agent}:{t.session_id}"
                prev_t = current.totals.get(key)
                if prev_t is None or t.total_tokens > prev_t.total_tokens:
                    current.totals[key] = t
                    lines.append(json.dumps(t.to_dict(), separators=(",", ":")))
            if lines:
                fh.write("\n".join(lines) + "\n")
                fh.flush()
            return len(lines)
