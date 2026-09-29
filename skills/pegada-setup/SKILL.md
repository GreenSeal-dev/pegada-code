---
name: pegada-setup
description: Set up pegada-code — install the live footprint status line and choose whether the project's footprint ledger is shared through git.
argument-hint: "[statusline | share | unshare | remove]"
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/pegada:*)
---

## Planned status line change (dry run)

!`${CLAUDE_PLUGIN_ROOT}/bin/pegada code setup --statusline --dry-run`

## Steps

The plugin's `bin/` directory is on the Bash tool's PATH, so the `pegada` command is available. Arguments given: "$ARGUMENTS".

1. **Status line** (skip if the arguments are only `share`/`unshare`/`remove`). Show the user the dry-run above. It edits the user-level `~/.claude/settings.json` (a timestamped backup is made). Any existing status line is kept, and pegada appends `🌱 <gCO2e range> · <Wh range>` for the current session to it. Ask for confirmation, then run `pegada code setup --statusline`.
2. **Ledger sharing.** Explain in two sentences that the ledger `.claude/pegada.jsonl` holds only model ids, token counts, timestamps and session ids (no prompts, code or paths), and that it is git-ignored by default. Sharing it lets the whole team's usage add up to one project footprint, and `merge=union` avoids merge conflicts. Ask whether they want to share it. If yes, run `pegada code setup --share` and list the files to commit. If the argument is `unshare`, run `pegada code setup --unshare`.
3. If the argument is `remove`, run `pegada code setup --remove-statusline` instead of step 1. This restores the previous status line.
4. Finish by mentioning `/pegada` for the project report and `/pegada badge` for a README badge.
