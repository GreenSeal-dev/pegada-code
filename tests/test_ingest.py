import json
import os
import shutil
import unittest

from helpers import DEMO_ROOT, FIXTURES, TempEnvTestCase

from pegada.ledger import Ledger
from pegada.parsers.claude_code import ClaudeCodeParser
from pegada.records import TOKEN_CLASSES
from pegada_code import ingest


class BackfillTest(TempEnvTestCase):
    """Backfill of /work/demo with the ledger redirected to a temp project dir."""

    def setUp(self):
        super().setUp()
        self._lp, self._eg = ingest.ledger_path, ingest.ensure_gitignore
        self.ledger_file = os.path.join(self.project, ".claude", "pegada.jsonl")
        ingest.ledger_path = lambda root: self.ledger_file
        ingest.ensure_gitignore = lambda root: None
        self.parser = ClaudeCodeParser(FIXTURES)

    def tearDown(self):
        ingest.ledger_path, ingest.ensure_gitignore = self._lp, self._eg
        super().tearDown()

    def read(self):
        return Ledger(self.ledger_file).read()

    def test_backfill_filters_by_cwd_and_dedups(self):
        stats = ingest.backfill(DEMO_ROOT, self.parser)
        self.assertEqual(stats["transcripts"], 4)  # includes the sibling, later filtered by cwd
        recs = self.read().records
        self.assertEqual(sorted(recs), ["msg_a1", "msg_a2", "msg_b1", "msg_c1", "msg_s1"])
        totals = {c: sum(getattr(r, c) for r in recs.values()) for c in TOKEN_CLASSES}
        self.assertEqual(totals, {"input": 1065, "cache_write": 2600, "cache_read": 212000, "output": 650})

    def test_continued_session_keeps_original_attribution(self):
        ingest.backfill(DEMO_ROOT, self.parser)
        recs = self.read().records
        self.assertEqual(recs["msg_a1"].session_id, "sess-a")
        self.assertEqual(recs["msg_a2"].session_id, "sess-a")
        self.assertEqual(recs["msg_b1"].session_id, "sess-b")

    def test_backfill_is_idempotent(self):
        first = ingest.backfill(DEMO_ROOT, self.parser)["written"]
        self.assertGreater(first, 0)
        self.assertEqual(ingest.backfill(DEMO_ROOT, self.parser)["written"], 0)
        with open(self.ledger_file) as fh:
            self.assertEqual(len(fh.readlines()), first)

    def test_hook_ingest_after_backfill_adds_nothing(self):
        ingest.backfill(DEMO_ROOT, self.parser)
        n = ingest.ingest_session(os.path.join(FIXTURES, "-work-demo", "sess-a.jsonl"), DEMO_ROOT, self.parser)
        self.assertEqual(n, 0)

    def test_session_totals_stored_separately(self):
        ingest.backfill(DEMO_ROOT, self.parser)
        totals = self.read().totals
        self.assertEqual(list(totals), ["claude-code:sess-a"])


class IncrementalIngestTest(TempEnvTestCase):
    """Hook path: byte offsets, growing streaming chunks, subagent files."""

    def setUp(self):
        super().setUp()
        self.projects = os.path.join(self.tmp, "projects")
        shutil.copytree(FIXTURES, self.projects)
        self.transcript = os.path.join(self.projects, "-work-demo", "sess-a.jsonl")
        self.parser = ClaudeCodeParser(self.projects)

    def ledger(self):
        return Ledger(os.path.join(self.project, ".claude", "pegada.jsonl")).read()

    def test_offsets_and_growth(self):
        ingest.ingest_session(self.transcript, self.project, self.parser)
        recs = self.ledger().records
        self.assertEqual(sorted(recs), ["msg_a1", "msg_a2", "msg_s1"])
        self.assertTrue(recs["msg_s1"].is_subagent)
        # No new bytes → nothing written.
        self.assertEqual(ingest.ingest_session(self.transcript, self.project, self.parser), 0)
        # A later chunk of an already-ingested message with larger output, plus a new message.
        with open(self.transcript) as fh:
            line = json.loads(fh.readlines()[2])  # first chunk of msg_a1
        grown = dict(line, message=dict(line["message"], usage=dict(line["message"]["usage"], output_tokens=999)))
        new = dict(line, message=dict(line["message"], id="msg_a3"))
        with open(self.transcript, "a") as fh:
            fh.write(json.dumps(grown) + "\n" + json.dumps(new) + "\n")
        self.assertEqual(ingest.ingest_session(self.transcript, self.project, self.parser), 2)
        recs = self.ledger().records
        self.assertEqual(recs["msg_a1"].output, 999)
        self.assertIn("msg_a3", recs)

    def test_gitignore_created_with_ledger(self):
        ingest.ingest_session(self.transcript, self.project, self.parser)
        with open(os.path.join(self.project, ".claude", ".gitignore")) as fh:
            self.assertIn("pegada.jsonl", fh.read().splitlines())

    def test_deleted_ledger_resets_offsets(self):
        ingest.ingest_session(self.transcript, self.project, self.parser)
        os.remove(os.path.join(self.project, ".claude", "pegada.jsonl"))
        self.assertGreater(ingest.ingest_session(self.transcript, self.project, self.parser), 0)


if __name__ == "__main__":
    unittest.main()
