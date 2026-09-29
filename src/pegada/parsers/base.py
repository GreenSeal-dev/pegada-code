"""Parser interface. One parser per agent (Claude Code today; Codex, Gemini CLI later)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

try:  # Python 3.8+ has Protocol in typing
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

from pegada.records import SessionTotals, UsageRecord


@dataclass
class ParseResult:
    records: List[UsageRecord] = field(default_factory=list)
    totals: List[SessionTotals] = field(default_factory=list)
    # Byte offset just past the last complete line read. Passing it back to
    # ``parse`` resumes incrementally; a trailing partial line is never consumed.
    offset: int = 0
    bad_lines: int = 0
    # (session_id, continued_in_session_id) links: the second session re-contains
    # the first one's records, so the first must be ingested first.
    continuations: List[Tuple[str, str]] = field(default_factory=list)


class Parser(Protocol):
    agent: str

    def discover(self, project_root: str) -> List[str]:
        """Return the primary transcript files that may belong to ``project_root``."""
        ...

    def session_files(self, transcript_path: str) -> List[str]:
        """Return every file holding usage for the session of ``transcript_path``."""
        ...

    def parse(self, path: str, offset: int = 0) -> ParseResult:
        """Parse one file from ``offset`` (bytes). Records are not yet deduplicated."""
        ...
