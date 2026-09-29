"""Claude Code hook entry points. Hooks must be fast, silent and never fail the session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from typing import Any, Dict

import pegada
from pegada_code.ingest import ingest_session
from pegada_code.project import ledger_path, project_root, state_dir
from pegada_code.setup import refresh_launcher


def src_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(pegada.__file__)))


def spawn_backfill(root: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = src_dir() + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    subprocess.Popen(
        [sys.executable, "-m", "pegada", "code", "backfill", "--project", root, "--quiet"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, env=env, cwd=root,
    )


def log(msg: str) -> None:
    path = os.path.join(state_dir(), "pegada.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def handle(event: str, payload: Dict[str, Any]) -> None:
    root = project_root(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd"))
    if event == "session-start":
        refresh_launcher()
        if not os.path.exists(ledger_path(root)):
            spawn_backfill(root)
    elif event in ("stop", "session-end", "subagent-stop"):
        tp = payload.get("transcript_path")
        if tp:
            ingest_session(tp, root)


def main(event: str) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        handle(event, payload if isinstance(payload, dict) else {})
    except Exception:  # never break the user's session
        log(f"hook {event} failed:\n{traceback.format_exc()}")
    return 0
