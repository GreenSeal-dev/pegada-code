"""`pegada code …` — the Claude Code subcommands."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

from pegada import fmt
from pegada.ledger import Ledger
from pegada.records import TOKEN_CLASSES
from pegada.report import badge, build_report, render_coefficients, render_markdown
from pegada_code.project import estimator_for, ledger_path, project_root

COMMANDS = ("report", "badge", "backfill", "coverage", "coefficients", "ingest", "hook", "statusline", "setup")


def _report(args):
    root = project_root(args.project)
    est = estimator_for(root)
    return build_report(Ledger(ledger_path(root)).read(), est, project=root, top=getattr(args, "top", 10)), est


def cmd_report(args) -> int:
    rep, _ = _report(args)
    print(json.dumps(rep, indent=2) if args.json else render_markdown(rep))
    return 0


def cmd_badge(args) -> int:
    rep, _ = _report(args)
    if not rep["total"]["messages"]:
        print("No usage recorded yet; nothing to put on a badge.")
        return 1
    snippet = badge(rep, label=args.label, **({"link": args.link} if args.link else {}))
    print("Paste this into your README (static badge — re-run `/pegada badge` to update it):\n")
    print(snippet)
    return 0


def cmd_backfill(args) -> int:
    from pegada_code.ingest import backfill

    root = project_root(args.project)
    stats = backfill(root)
    if not os.path.exists(ledger_path(root)):
        open(ledger_path(root), "a").close()  # mark as backfilled, even when empty
    if not args.quiet:
        print(f"Scanned {stats['transcripts']} transcript(s); {stats['sessions']} session(s) and "
              f"{stats['records']} usage line(s) belong to {root}; {stats['written']} new ledger line(s) written "
              f"to {ledger_path(root)}.")
    return 0


def cmd_coverage(args) -> int:
    root = project_root(args.project)
    contents = Ledger(ledger_path(root)).read()
    if not contents.totals:
        print("No session totals (cost-state) recorded for this project; coverage cannot be checked.")
        return 0
    logged = defaultdict(lambda: {c: 0 for c in TOKEN_CLASSES})
    for r in contents.records.values():
        for c in TOKEN_CLASSES:
            logged[(r.session_id, r.model)][c] += getattr(r, c)
    print("| Session | Model | Class | Logged (transcripts) | Reported (session totals) | Coverage |")
    print("|---|---|---|---:|---:|---:|")
    for t in contents.totals.values():
        for model, counts in sorted(t.models.items()):
            for c in TOKEN_CLASSES:
                got, rep = logged[(t.session_id, model)][c], counts.get(c, 0)
                if got or rep:
                    cov = fmt.pct(got / rep) if rep else "—"
                    print(f"| `{t.session_id[:8]}` | `{model}` | {c} | {got:,} | {rep:,} | {cov} |")
    return 0


def cmd_coefficients(args) -> int:
    print(render_coefficients(estimator_for(project_root(args.project))))
    return 0


def cmd_ingest(args) -> int:
    from pegada_code.ingest import ingest_session

    n = ingest_session(os.path.abspath(args.transcript), project_root(args.project))
    print(f"{n} new ledger line(s).")
    return 0


def cmd_hook(args) -> int:
    from pegada_code.hooks import main

    return main(args.event)


def cmd_statusline(args) -> int:
    from pegada_code.statusline import main

    return main()


def cmd_setup(args) -> int:
    from pegada_code import setup

    msgs = []
    if args.statusline:
        msgs += setup.install_statusline(dry_run=args.dry_run)
    if args.remove_statusline:
        msgs += setup.remove_statusline()
    if args.share or args.unshare:
        if args.dry_run:
            msgs.append(f"Would turn ledger sharing {'on' if args.share else 'off'} for {project_root(args.project)}.")
        else:
            msgs += setup.set_share(project_root(args.project), share=bool(args.share))
    if not msgs:
        msgs = ["Nothing to do. Options: --statusline, --remove-statusline, --share, --unshare, --dry-run."]
    print("\n".join(msgs))
    return 0


def configure(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="code_cmd", metavar="{" + ",".join(COMMANDS) + "}")

    def add(name, func, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(func=func)
        if name not in ("hook", "statusline"):
            p.add_argument("--project", help="project root (default: $CLAUDE_PROJECT_DIR or the current directory)")
        return p

    p = add("report", cmd_report, "project footprint report (default)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--top", type=int, default=10, help="number of sessions to list")
    p = add("badge", cmd_badge, "shields.io badge snippet for the README")
    p.add_argument("--label", default="AI coding CO2e")
    p.add_argument("--link", help="URL the badge links to (default: METHODOLOGY.md)")
    p = add("backfill", cmd_backfill, "import existing transcripts of this project")
    p.add_argument("--quiet", action="store_true")
    add("coverage", cmd_coverage, "compare logged tokens with Claude Code's own session totals")
    add("coefficients", cmd_coefficients, "show coefficients, parameters and their sources")
    p = add("ingest", cmd_ingest, "ingest one transcript (and its subagents) into the ledger")
    p.add_argument("transcript")
    p = add("hook", cmd_hook, "hook entry point (reads the hook payload on stdin)")
    p.add_argument("event", choices=["session-start", "stop", "subagent-stop", "session-end"])
    add("statusline", cmd_statusline, "status line entry point (reads the payload on stdin)")
    p = add("setup", cmd_setup, "install the status line / choose the ledger sharing policy")
    p.add_argument("--statusline", action="store_true", help="install the status line in ~/.claude/settings.json")
    p.add_argument("--remove-statusline", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--share", action="store_true", help="track the ledger in git (merge=union)")
    g.add_argument("--unshare", action="store_true", help="git-ignore the ledger again")
    p.add_argument("--dry-run", action="store_true")
    parser.set_defaults(func=cmd_report, project=None, json=False, top=10)
