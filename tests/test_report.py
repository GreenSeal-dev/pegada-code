import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import DEMO_ROOT, FIXTURES, TempEnvTestCase

from pegada.coefficients import load_coefficients, load_parameters
from pegada.estimator import Estimator
from pegada.ledger import Ledger
from pegada.parsers.claude_code import ClaudeCodeParser
from pegada.report import badge, build_report, render_markdown
from pegada_code import hooks, ingest, setup, statusline


class ReportTest(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.ledger_file = os.path.join(self.project, ".claude", "pegada.jsonl")
        with mock.patch.object(ingest, "ledger_path", lambda root: self.ledger_file), \
                mock.patch.object(ingest, "ensure_gitignore", lambda root: None):
            ingest.backfill(DEMO_ROOT, ClaudeCodeParser(FIXTURES))
        self.contents = Ledger(self.ledger_file).read()

    def report(self, contents=None, coefficients=None):
        est = Estimator(load_coefficients(coefficients), load_parameters())
        return build_report(contents or self.contents, est, project=DEMO_ROOT)

    def test_unlogged_usage_is_separate(self):
        rep = self.report()
        u = rep["unlogged"]
        self.assertEqual(u["sessions_with_totals"], 1)
        self.assertEqual(u["by_model"]["claude-sonnet-5"]["tokens"], {"input": 100, "cache_write": 0, "cache_read": 1000, "output": 0})
        self.assertEqual(u["by_model"]["claude-haiku-4-5-20251001"]["tokens"], {"input": 5000, "cache_write": 0, "cache_read": 0, "output": 200})
        self.assertLess(u["coverage_energy_mid"], 1)
        # The headline is transcript-only: identical with or without session totals.
        self.contents.totals.clear()
        without = self.report()
        self.assertEqual(without["total"], rep["total"])
        self.assertIsNone(without["unlogged"])

    def test_breakdowns(self):
        rep = self.report()
        self.assertEqual(rep["total"]["messages"], 5)
        self.assertEqual(rep["sessions"], 3)
        self.assertEqual(set(rep["by_role"]), {"main thread", "subagent: Explore"})
        self.assertGreater(rep["token_classes"]["cache_read"]["token_share"], 0.95)
        self.assertEqual(rep["by_model"]["mystery-model-1"]["family"], "unknown")
        self.assertTrue(any("mystery-model-1" in w for w in rep["warnings"]))

    def placeholder_file(self):
        path = os.path.join(self.tmp, "placeholder.json")
        with open(path, "w") as fh:
            json.dump({"coefficients_version": "p", "families": [],
                       "fallback": {"id": "f", "e_prefill": 1, "e_cache": 1, "e_decode": 1, "status": "PLACEHOLDER"}}, fh)
        return path

    def test_markdown_shows_intervals(self):
        md = render_markdown(self.report())
        self.assertNotIn("Placeholder coefficients", md)
        self.assertIn("–", md)
        self.assertIn("Unlogged usage", md)
        self.assertIn("Cache read", md)
        self.assertIn("Per-response overhead", md)
        self.assertIn("PUE: 1.36", md)  # point value shown without a range

    def test_placeholder_banner(self):
        md = render_markdown(self.report(coefficients=self.placeholder_file()))
        self.assertIn("Placeholder coefficients", md)

    def test_badge(self):
        md = badge(self.report())
        self.assertTrue(md.startswith("[![AI coding CO2e: "))
        self.assertIn("https://img.shields.io/badge/AI%20coding%20CO2e-", md)
        self.assertNotIn("placeholder", md)
        self.assertIn("placeholder", badge(self.report(coefficients=self.placeholder_file())))

    def test_approximation_warning(self):
        rep = self.report()
        self.assertTrue(any("claude-opus-5-5 → claude-opus-5-5" in w for w in rep["warnings"]))

    def test_empty_report(self):
        from pegada.ledger import LedgerContents
        md = render_markdown(self.report(LedgerContents()))
        self.assertIn("No usage recorded yet", md)


class StatuslineTest(TempEnvTestCase):
    def test_session_line(self):
        payload = {"session_id": "sess-a", "transcript_path": os.path.join(FIXTURES, "-work-demo", "sess-a.jsonl"),
                   "workspace": {"project_dir": self.project}}
        line = statusline.session_line(payload)
        self.assertTrue(line.startswith("🌱 "))
        self.assertIn("CO2e", line)
        self.assertEqual(statusline.session_line(payload), line)  # cached path gives same answer

    def test_chains_previous_statusline(self):
        os.makedirs(os.environ["CLAUDE_CONFIG_DIR"])
        settings = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "settings.json")
        with open(settings, "w") as fh:
            json.dump({"theme": "dark", "statusLine": {"type": "command", "command": "echo PREV", "padding": 1}}, fh)
        setup.install_statusline()
        with open(settings) as fh:
            s = json.load(fh)
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["statusLine"]["command"], setup.launcher_command())
        self.assertEqual(s["statusLine"]["padding"], 1)
        self.assertTrue(os.path.exists(setup.launcher_path()))
        payload = json.dumps({"session_id": "x", "transcript_path": os.path.join(FIXTURES, "-work-demo", "sess-b.jsonl")})
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(payload)), redirect_stdout(out):
            statusline.main()
        self.assertTrue(out.getvalue().startswith("PREV  🌱 "))
        # Installing twice is a no-op; removing restores the previous status line.
        self.assertIn("already installed", setup.install_statusline()[0])
        setup.remove_statusline()
        with open(settings) as fh:
            self.assertEqual(json.load(fh)["statusLine"]["command"], "echo PREV")


class SetupShareTest(TempEnvTestCase):
    def test_share_toggle(self):
        from pegada_code.project import ensure_gitignore
        ensure_gitignore(self.project)
        gi = os.path.join(self.project, ".claude", ".gitignore")
        ga = os.path.join(self.project, ".gitattributes")
        setup.set_share(self.project, True)
        with open(gi) as fh:
            self.assertNotIn("pegada.jsonl", fh.read().splitlines())
        with open(ga) as fh:
            self.assertIn(".claude/pegada.jsonl merge=union", fh.read())
        ensure_gitignore(self.project)  # must respect the share setting
        with open(gi) as fh:
            self.assertNotIn("pegada.jsonl", fh.read().splitlines())
        setup.set_share(self.project, False)
        with open(gi) as fh:
            self.assertIn("pegada.jsonl", fh.read().splitlines())
        with open(ga) as fh:
            self.assertNotIn("merge=union", fh.read())


class HookTest(TempEnvTestCase):
    def test_hook_never_raises(self):
        for stdin in ("not json", "", json.dumps({"transcript_path": "/does/not/exist.jsonl", "cwd": self.project})):
            with mock.patch("sys.stdin", io.StringIO(stdin)):
                self.assertEqual(hooks.main("stop"), 0)

    def test_stop_hook_writes_ledger(self):
        payload = json.dumps({"transcript_path": os.path.join(FIXTURES, "-work-demo", "sess-a.jsonl"), "cwd": self.project})
        with mock.patch("sys.stdin", io.StringIO(payload)):
            hooks.main("stop")
        self.assertEqual(len(Ledger(os.path.join(self.project, ".claude", "pegada.jsonl")).read().records), 3)


if __name__ == "__main__":
    unittest.main()
