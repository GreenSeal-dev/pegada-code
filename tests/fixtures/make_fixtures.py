"""Generate the synthetic Claude Code transcripts used by the tests.

The shapes mirror real transcripts (Claude Code 2.1.x) but contain no real
content. Run from the repository root: ``python3 tests/fixtures/make_fixtures.py``.

Layout (project root ``/work/demo``):

  -work-demo/sess-a.jsonl        streaming chunks, <synthetic> stub, cost-state, continued-in → sess-b
  -work-demo/sess-a/subagents/   haiku subagent (Explore), cache-heavy, 2 chunks
  -work-demo/sess-b.jsonl        continuation: copies of sess-a messages + a cache-heavy opus message,
                                 ends with a torn (incomplete) line
  -work-demo-sub/sess-c.jsonl    session in a subdirectory, unknown model
  -work-demo-extra/sess-z.jsonl  sibling project /work/demo-extra (must be excluded)
  -work-other/sess-o.jsonl       unrelated project (must be excluded)
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "claude_projects")


def usage(i, cw, cr, o, thinking=None):
    u = {
        "input_tokens": i,
        "cache_creation_input_tokens": cw,
        "cache_read_input_tokens": cr,
        "output_tokens": o,
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": "standard",
        "cache_creation": {"ephemeral_1h_input_tokens": cw, "ephemeral_5m_input_tokens": 0},
        "inference_geo": "not_available",
        "iterations": [{"input_tokens": i, "output_tokens": o, "type": "message"}],
        "speed": "standard",
    }
    if thinking is not None:
        u["output_tokens_details"] = {"thinking_tokens": thinking}
    return u


def assistant(session, cwd, ts, msg_id, model, u, block="text", stop=None, sidechain=False, agent_id=None, uuid=None):
    d = {
        "parentUuid": None,
        "isSidechain": sidechain,
        "message": {
            "model": model,
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [{"type": block}],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": u,
        },
        "requestId": "req_" + msg_id,
        "type": "assistant",
        "uuid": uuid or f"{msg_id}-{block}",
        "timestamp": ts,
        "userType": "external",
        "cwd": cwd,
        "sessionId": session,
        "version": "2.1.284",
        "gitBranch": "main",
    }
    if agent_id:
        d["agentId"] = agent_id
    return d


def user(session, cwd, ts):
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": "(redacted)"},
            "uuid": f"u-{session}-{ts}", "timestamp": ts, "cwd": cwd, "sessionId": session}


def write(rel, lines, torn_tail=None):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")
        if torn_tail:
            fh.write(torn_tail)


def main():
    demo, sub = "/work/demo", "/work/demo/sub"
    son, hai, opus = "claude-sonnet-5", "claude-haiku-4-5-20251001", "claude-opus-5-5"
    t0 = "2026-01-01T10:00:00.000Z"

    a1_chunks = [
        assistant("sess-a", demo, t0, "msg_a1", son, usage(10, 2000, 0, 5, thinking=40), "thinking"),
        assistant("sess-a", demo, "2026-01-01T10:00:01.000Z", "msg_a1", son, usage(10, 2000, 0, 5, thinking=40), "text"),
        assistant("sess-a", demo, "2026-01-01T10:00:02.000Z", "msg_a1", son, usage(10, 2000, 0, 120, thinking=40), "tool_use", "tool_use"),
    ]
    a2 = assistant("sess-a", demo, "2026-01-01T10:01:00.000Z", "msg_a2", son, usage(3, 500, 2000, 300), "text", "end_turn")
    synthetic = assistant("sess-a", demo, "2026-01-01T10:02:00.000Z", "msg_syn", "<synthetic>", usage(0, 0, 0, 0), "text", "stop_sequence")
    cost_state = {
        "type": "cost-state", "sessionId": "sess-a", "totalCostUSD": 0.1,
        "modelUsage": {
            son: {"inputTokens": 113, "outputTokens": 420, "thinkingTokens": 40, "cacheReadInputTokens": 3000,
                  "cacheCreationInputTokens": 2500, "webSearchRequests": 0, "costUSD": 0.09},
            hai: {"inputTokens": 5050, "outputTokens": 280, "thinkingTokens": 0, "cacheReadInputTokens": 10000,
                  "cacheCreationInputTokens": 0, "webSearchRequests": 1, "costUSD": 0.01},
        },
        "hasUnknownModelCost": False,
    }
    write("-work-demo/sess-a.jsonl", [
        {"type": "ai-title", "aiTitle": "demo", "sessionId": "sess-a"},
        user("sess-a", demo, t0), *a1_chunks, a2, synthetic, cost_state,
        {"type": "continued-in", "timestamp": "2026-01-01T10:03:00.000Z", "sessionId": "sess-a", "continuedInSessionId": "sess-b"},
    ])

    write("-work-demo/sess-a/subagents/agent-x1.jsonl", [
        assistant("sess-a", demo, "2026-01-01T10:00:30.000Z", "msg_s1", hai, usage(50, 0, 10000, 3), "text", sidechain=True, agent_id="x1"),
        assistant("sess-a", demo, "2026-01-01T10:00:31.000Z", "msg_s1", hai, usage(50, 0, 10000, 80), "tool_use", "tool_use", sidechain=True, agent_id="x1"),
    ])
    with open(os.path.join(OUT, "-work-demo/sess-a/subagents/agent-x1.meta.json"), "w") as fh:
        json.dump({"agentType": "Explore", "description": "find things", "spawnDepth": 1}, fh)

    # The continuation copies sess-a's history (same uuid, message id and
    # timestamps) under its own session id, then continues.
    copies = [dict(a1_chunks[-1], sessionId="sess-b"), dict(a2, sessionId="sess-b")]
    write("-work-demo/sess-b.jsonl", [
        user("sess-b", demo, t0), *copies,
        assistant("sess-b", demo, "2026-01-01T11:00:00.000Z", "msg_b1", opus, usage(2, 100, 200000, 50), "text", "end_turn"),
    ], torn_tail='{"type":"assistant","message":{"id":"msg_b2","model":"claude-opus-5-5","usage":{"input_tok')

    write("-work-demo-sub/sess-c.jsonl", [
        assistant("sess-c", sub, "2026-01-02T09:00:00.000Z", "msg_c1", "mystery-model-1", usage(1000, 0, 0, 100), "text", "end_turn"),
    ])
    write("-work-demo-extra/sess-z.jsonl", [
        assistant("sess-z", "/work/demo-extra", "2026-01-03T09:00:00.000Z", "msg_z1", son, usage(1, 1, 1, 1), "text", "end_turn"),
    ])
    write("-work-other/sess-o.jsonl", [
        assistant("sess-o", "/work/other", "2026-01-03T09:00:00.000Z", "msg_o1", son, usage(1, 1, 1, 1), "text", "end_turn"),
    ])


if __name__ == "__main__":
    main()
