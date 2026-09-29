---
name: pegada
description: Report the estimated energy (Wh) and carbon (gCO2e) footprint of Claude Code usage in this project, as low/mid/high intervals, or produce a README badge. Use when the user asks about the environmental impact, energy use, carbon emissions or footprint of their AI coding sessions.
argument-hint: "[report | badge | backfill | coverage | coefficients]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/pegada:*)
---

## pegada output

!`${CLAUDE_PLUGIN_ROOT}/bin/pegada code $ARGUMENTS`

## How to present it

The block above is the output of `pegada code $ARGUMENTS` (the project report when no argument was given). It is already formatted Markdown.

- Start your reply by reproducing the whole block verbatim, including every table. The user asked for the report, not a summary of it. Do not recompute, round or drop any numbers, and never collapse an interval into a single value: pegada always reports low–high ranges on purpose.
- Then add at most three sentences of interpretation, grounded only in the numbers shown (for example which model, token class or session dominates). Do not add figures of your own, such as comparisons to cars, flights or households.
- If the output contains the placeholder-coefficients warning, say plainly that the absolute values are not calibrated yet and should not be cited.
- For `badge`, show the Markdown snippet in a code block so it can be copied into a README.
- If the report says no usage is recorded yet, suggest `/pegada backfill`.
- Methodology, assumptions and limitations are in METHODOLOGY.md in the pegada-code repository.
