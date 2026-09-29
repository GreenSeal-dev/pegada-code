import os
import unittest

from helpers import DEMO_ROOT, FIXTURES

from pegada.parsers.claude_code import ClaudeCodeParser, encode_project_path, is_within
from pegada.records import merge_records


def parse_all(parser, main):
    recs, tots, conts = [], [], []
    for path in parser.session_files(main):
        res = parser.parse(path)
        recs += res.records
        tots += res.totals
        conts += res.continuations
    return recs, tots, conts


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.p = ClaudeCodeParser(FIXTURES)
        self.sess_a = os.path.join(FIXTURES, "-work-demo", "sess-a.jsonl")

    def test_encode_and_within(self):
        self.assertEqual(encode_project_path("/Users/me/my_proj.v2"), "-Users-me-my-proj-v2")
        self.assertTrue(is_within("/work/demo/sub", "/work/demo"))
        self.assertTrue(is_within("/work/demo", "/work/demo/"))
        self.assertFalse(is_within("/work/demo-extra", "/work/demo"))
        self.assertFalse(is_within("", "/work/demo"))

    def test_discover_uses_prefix(self):
        names = sorted(os.path.relpath(p, FIXTURES) for p in self.p.discover(DEMO_ROOT))
        self.assertEqual(names, ["-work-demo-extra/sess-z.jsonl", "-work-demo-sub/sess-c.jsonl",
                                 "-work-demo/sess-a.jsonl", "-work-demo/sess-b.jsonl"])

    def test_session_files_include_subagents(self):
        files = self.p.session_files(self.sess_a)
        self.assertEqual(len(files), 2)
        self.assertTrue(files[1].endswith("subagents/agent-x1.jsonl"))

    def test_streaming_chunks_merge_to_final_usage(self):
        recs, _, _ = parse_all(self.p, self.sess_a)
        self.assertEqual(sum(r.msg_id == "msg_a1" for r in recs), 3)  # raw: one line per content block
        merged = merge_records(recs)
        a1 = merged["msg_a1"]
        self.assertEqual((a1.input, a1.cache_write, a1.cache_read, a1.output, a1.thinking), (10, 2000, 0, 120, 40))

    def test_synthetic_and_zero_usage_skipped(self):
        recs, _, _ = parse_all(self.p, self.sess_a)
        self.assertNotIn("<synthetic>", {r.model for r in recs})
        self.assertNotIn("msg_syn", {r.msg_id for r in recs})

    def test_subagent_flag_and_type(self):
        recs, _, _ = parse_all(self.p, self.sess_a)
        s1 = merge_records(recs)["msg_s1"]
        self.assertTrue(s1.is_subagent)
        self.assertEqual(s1.agent_type, "Explore")
        self.assertEqual(s1.output, 80)
        self.assertFalse(merge_records(recs)["msg_a1"].is_subagent)

    def test_cost_state_and_continuation(self):
        _, tots, conts = parse_all(self.p, self.sess_a)
        self.assertEqual(conts, [("sess-a", "sess-b")])
        self.assertEqual(len(tots), 1)
        son = tots[0].models["claude-sonnet-5"]
        self.assertEqual(son, {"input": 113, "cache_write": 2500, "cache_read": 3000, "output": 420})
        self.assertEqual(tots[0].as_of, "2026-01-01T10:02:30.000Z")

    def test_torn_last_line_not_consumed(self):
        path = os.path.join(FIXTURES, "-work-demo", "sess-b.jsonl")
        res = self.p.parse(path)
        self.assertEqual(res.bad_lines, 0)
        self.assertLess(res.offset, os.path.getsize(path))
        self.assertNotIn("msg_b2", {r.msg_id for r in res.records})
        again = self.p.parse(path, res.offset)
        self.assertEqual((again.records, again.offset), ([], res.offset))

    def test_record_does_not_serialise_cwd(self):
        recs, _, _ = parse_all(self.p, self.sess_a)
        self.assertEqual(recs[0].cwd, DEMO_ROOT)
        self.assertNotIn("cwd", recs[0].to_dict())


if __name__ == "__main__":
    unittest.main()
