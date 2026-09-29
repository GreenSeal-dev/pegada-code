"""Project-level paths, configuration and local (non-repository) state."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional

from pegada.coefficients import load_coefficients, load_parameters
from pegada.estimator import Estimator

LEDGER_NAME = "pegada.jsonl"
CONFIG_NAME = "pegada.config.json"


def project_root(arg: Optional[str] = None) -> str:
    return os.path.abspath(arg or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def ledger_path(root: str) -> str:
    return os.path.join(root, ".claude", LEDGER_NAME)


def config_path(root: str) -> str:
    return os.path.join(root, ".claude", CONFIG_NAME)


def load_config(root: str) -> Dict[str, Any]:
    try:
        with open(config_path(root), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(root: str, cfg: Dict[str, Any]) -> None:
    write_json(config_path(root), cfg)


def estimator_for(root: str) -> Estimator:
    cfg = load_config(root)
    coeff = cfg.get("coefficients_file")
    if coeff and not os.path.isabs(coeff):
        coeff = os.path.join(root, coeff)
    return Estimator(load_coefficients(coeff), load_parameters(cfg))


def state_dir() -> str:
    """Machine-local state (byte offsets, status line cache, launcher). Kept out
    of the repository and independent of the plugin version directory."""
    d = os.environ.get("PEGADA_STATE_DIR")
    if not d:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        d = os.path.join(base, "pegada")
    os.makedirs(d, exist_ok=True)
    return d


def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: str, data: Any) -> None:
    """Atomic write (temp file + rename)."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".pegada-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def ensure_gitignore(root: str) -> None:
    """Keep the ledger out of git unless the project opted in to sharing it."""
    if load_config(root).get("share"):
        return
    path = os.path.join(root, ".claude", ".gitignore")
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    if LEDGER_NAME in (l.strip() for l in lines):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        if lines and lines[-1].strip():
            fh.write("\n")
        fh.write(f"# pegada-code ledger (token counts). Remove with `pegada code setup --share` to track it in git.\n{LEDGER_NAME}\n")
