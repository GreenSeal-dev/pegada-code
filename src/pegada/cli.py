"""`pegada` command line. Agent integrations register their own subcommand."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from pegada import __version__


def cmd_coefficients(args) -> int:
    from pegada.coefficients import load_coefficients, load_parameters
    from pegada.estimator import Estimator
    from pegada.report import render_coefficients

    print(render_coefficients(Estimator(load_coefficients(args.file), load_parameters())))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pegada",
        description="Estimate the energy and carbon footprint of AI coding agents from token usage.",
    )
    parser.add_argument("--version", action="version", version=f"pegada {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    from pegada_code.cli import configure

    configure(sub.add_parser("code", help="Claude Code: report, badge, backfill, setup, …"))
    p = sub.add_parser("coefficients", help="show the default coefficients and their sources")
    p.add_argument("--file", help="coefficients JSON (default: bundled file or $PEGADA_COEFFICIENTS)")
    p.set_defaults(func=cmd_coefficients)

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except BrokenPipeError:  # e.g. piped into head
        return 0


if __name__ == "__main__":
    sys.exit(main())
