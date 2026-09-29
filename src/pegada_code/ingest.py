"""Transcript → ledger ingestion: incremental (hooks) and retroactive (backfill)."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from pegada.ledger import Ledger
from pegada.parsers.claude_code import ClaudeCodeParser, is_within
from pegada.records import SessionTotals, UsageRecord
from pegada_code.project import ensure_gitignore, ledger_path, read_json, state_dir, write_json


def _offsets_path() -> str:
    return os.path.join(state_dir(), "offsets.json")


def ingest_session(transcript_path: str, root: str, parser: Optional[ClaudeCodeParser] = None) -> int:
    """Append new usage of one session (main transcript + subagents) to the
    project ledger, reading only bytes not seen before. Returns lines written."""
    parser = parser or ClaudeCodeParser()
    ledger = Ledger(ledger_path(root))
    fresh = not ledger.exists()
    if fresh:
        ensure_gitignore(root)
    all_offsets: Dict[str, Dict[str, int]] = read_json(_offsets_path(), {})
    # Offsets are per ledger: if the ledger was deleted, start again from zero.
    offsets = {} if fresh else dict(all_offsets.get(ledger.path, {}))
    records: List[UsageRecord] = []
    totals: List[SessionTotals] = []
    for path in parser.session_files(transcript_path):
        start = offsets.get(path, 0)
        try:
            if os.path.getsize(path) < start:  # rewritten/truncated
                start = 0
        except OSError:
            continue
        res = parser.parse(path, start)
        records += res.records
        totals += res.totals
        offsets[path] = res.offset
    written = ledger.append(records, totals)
    all_offsets[ledger.path] = offsets
    write_json(_offsets_path(), all_offsets)
    return written


def _chain_depth(session_id: str, continues: Dict[str, str]) -> int:
    depth, seen = 0, set()
    while session_id in continues and session_id not in seen:
        seen.add(session_id)
        session_id = continues[session_id]
        depth += 1
    return depth


def backfill(root: str, parser: Optional[ClaudeCodeParser] = None) -> Dict[str, int]:
    """Import every existing transcript whose records ran inside ``root``.

    A continued session re-contains its predecessor's records with the same
    timestamps, so sessions are ingested in order of (first timestamp, depth in
    the ``continued-in`` chain): the original keeps the attribution.
    """
    parser = parser or ClaudeCodeParser()
    ledger = Ledger(ledger_path(root))
    if not ledger.exists():
        ensure_gitignore(root)
    mains = parser.discover(root)
    continues: Dict[str, str] = {}  # continued session -> the session it continues
    parsed = []
    for main in mains:
        recs: List[UsageRecord] = []
        tots: List[SessionTotals] = []
        for path in parser.session_files(main):
            res = parser.parse(path, 0)
            recs += [r for r in res.records if is_within(r.cwd, root)]
            tots += res.totals
            for original, continued in res.continuations:
                continues[continued] = original
        first = min((r.timestamp for r in recs if r.timestamp), default="")
        parsed.append((first, os.path.basename(main)[: -len(".jsonl")], recs, tots))
    parsed.sort(key=lambda p: (p[0], _chain_depth(p[1], continues)))
    records = [r for p in parsed for r in p[2]]
    sessions = {r.session_id for r in records}
    totals = [t for p in parsed for t in p[3] if t.session_id in sessions]
    written = ledger.append(records, totals)
    return {"transcripts": len(mains), "sessions": len(sessions), "records": len(records), "written": written}
