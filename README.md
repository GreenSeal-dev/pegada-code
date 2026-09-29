# pegada-code

[![AI coding CO2e: 0.0522–2.05 kg](https://img.shields.io/badge/AI%20coding%20CO2e-0.0522%E2%80%932.05%20kg-4c8c4a)](https://github.com/GreenSeal-dev/pegada-code/blob/main/METHODOLOGY.md)

**Estimate the energy and carbon footprint of AI coding agents in your project, from the tokens and models they use.**

*Pegada* is Portuguese for *footprint*. pegada-code is the first tool of the pegada family: a Claude Code plugin
backed by `pegada`, a small, dependency-free Python engine that other agent integrations can reuse.

> **Coefficients v0.2.0 are derived, not measured.** Output-token, per-response and embodied values follow
> [EcoLogits](https://ecologits.ai). Prompt-token (prefill) and cache-read values are first-principles estimates
> on EcoLogits' hardware assumptions. Every value is a range with a cited source; see
> [METHODOLOGY.md](METHODOLOGY.md).

## What it does

- **Tracks usage automatically.** A `Stop`/`SessionEnd` hook appends each session's token usage (main thread
  and subagents) to `.claude/pegada.jsonl`. On first use it backfills the project's existing transcripts.
- **Reports footprint as ranges, never single numbers.** `/pegada` shows energy (Wh) and emissions
  (gCO2e, operational + embodied) as low–high intervals with a mid scenario. It breaks them down by model,
  session, main thread vs subagents, and token class, and highlights cache reads, which are typically over 95%
  of a coding agent's tokens.
- **Shows its sources.** Coefficients are versioned, every value carries a `source` citation, and the report
  lists every assumption. Usage that transcripts do not log is quantified on a separate line and never mixed
  into the headline.
- **Adds a live status line.** `/pegada-setup` appends `🌱 0.2–9.1 gCO2e · 0.6–14 Wh` for the current session
  to your status line.
- **Makes a badge.** `/pegada badge` prints a shields.io snippet for your README.

## Install

Requires Claude Code and `python3` ≥ 3.9 (standard library only).

```
/plugin marketplace add GreenSeal-dev/pegada-code
/plugin install pegada-code@pegada-code
```

Then, in any project:

```
/pegada                # project report (the first session in a project backfills existing transcripts)
/pegada badge          # README badge snippet
/pegada backfill       # re-import existing transcripts
/pegada coverage       # compare logged tokens with Claude Code's own session totals
/pegada coefficients   # coefficients, parameters and their sources
/pegada-setup          # status line, ledger sharing
```

The skills are namespaced as `/pegada-code:pegada` and `/pegada-code:pegada-setup`. The short forms work
when no other plugin uses the same names.

The same engine is available as a CLI (it is on the Bash tool's PATH while the plugin is enabled, or
`pip install .` from a clone):

```sh
pegada code report --json      # machine-readable report
pegada code coverage
pegada coefficients --file my-calibration.json
```

## Configuration

Optional, per project, in `.claude/pegada.config.json`:

```json
{
  "grid_intensity": {"low": 250, "mid": 300, "high": 400, "source": "assumed serving region"},
  "pue": 1.1,
  "embodied": {"low": 20, "mid": 40, "high": 80},
  "coefficients_file": "calibration/coefficients.json",
  "share": false
}
```

Any parameter accepts a number (point value) or a `{low, mid, high}` triple. Defaults and their sources are
listed in [METHODOLOGY.md §4.3](METHODOLOGY.md#43-infrastructure-parameters-defaults).

**Team footprint.** The ledger contains only model ids, token counts, timestamps and session ids (no prompts,
code or paths) and is git-ignored by default. Run `pegada code setup --share` (or answer yes in
`/pegada-setup`) to commit it: `.gitattributes` gets `merge=union`, so each contributor's appends merge
cleanly into one project-wide footprint.

## How it works

```
transcripts (~/.claude/projects/**.jsonl)
   │  parser: dedup streaming chunks by message id, global dedup across continued sessions,
   │          subagent files + agent type, skip <synthetic>
   ▼
.claude/pegada.jsonl  (token counts only; append-only, idempotent)
   │  estimator: E_IT = e_prefill·(input+cache_write) + e_cache·cache_read + e_decode·output + e_request
   │             E = PUE·E_IT ;  CO2e = E·grid + E_IT·embodied      (every term low/mid/high)
   ▼
/pegada report · status line · badge
```

Repository layout:

| Path | Purpose |
|---|---|
| `src/pegada/` | core engine (agent-agnostic): records, parsers, coefficients, estimator, ledger, report |
| `src/pegada/parsers/claude_code.py` | Claude Code transcript parser (add `codex.py`, `gemini.py` … next to it) |
| `src/pegada/data/` | `coefficients.json` (generated), `parameters.json` |
| `calibration/` | `derive_coefficients.py` (EcoLogits + first principles), pinned EcoLogits model data |
| `src/pegada_code/` | Claude Code integration: hooks, status line, setup, CLI |
| `skills/`, `hooks/`, `bin/`, `.claude-plugin/` | plugin and marketplace |
| `tests/` | `python3 -m unittest discover -s tests -t tests` |

## Related work

- **[EcoLogits](https://ecologits.ai)** (GenAI Impact) estimates the energy and multi-criteria environmental
  impacts (GWP, ADP, PE) of API-based LLM inference per request, following an LCA approach. pegada-code's
  output-token, per-response and embodied values are computed with EcoLogits itself
  ([calibration/derive_coefficients.py](calibration/derive_coefficients.py)). EcoLogits attributes GPU energy to
  output tokens only. A coding agent's tokens are mostly cached context, so pegada-code adds first-principles
  prefill and cache-read terms, and uses a consequential industry-average PUE instead of the provider's
  (see [METHODOLOGY.md §4](METHODOLOGY.md#4-energy-and-emissions-model)).
- **CNaught**'s [coding-agent-emissions](https://github.com/CNaught-Inc/coding-agent-emissions) estimates
  the aggregate emissions of AI coding agents from their public GitHub activity
  ([blog](https://www.cnaught.com/blog/ai-coding-agents-are-emitting-250-000-tonnes-of-carbon-emissions-each-year-heres-how-we-got-there)).
  CNaught also publishes **Carbonlog**, a Claude Code plugin
  ([CNaught-Inc/claude-code-plugins](https://github.com/CNaught-Inc/claude-code-plugins)), which estimates
  per-session emissions from inference time (time-to-first-token plus output tokens ÷ throughput) × power ×
  PUE, reported as point estimates without embodied emissions
  ([methodology](https://www.cnaught.com/blog/how-to-actually-measure-the-carbon-footprint-of-ai-code)).
  pegada-code takes a complementary approach: token-class coefficients (including cache reads), explicit
  low/mid/high intervals, embodied emissions, a token-only ledger that can be recomputed and shared through
  git, and a separate account of unlogged usage.

## How to cite

If you use pegada-code in research, please cite it (see [CITATION.cff](CITATION.cff)); GitHub shows a
"Cite this repository" button. Please also state the coefficient version reported by the tool.

```bibtex
@software{cruz_pegada_code,
  author  = {Cruz, Luís},
  title   = {pegada-code: energy and carbon footprint estimates for AI coding agents},
  year    = {2026},
  version = {0.2.1},
  url     = {https://github.com/GreenSeal-dev/pegada-code},
  license = {Apache-2.0}
}
```

## License

[Apache-2.0](LICENSE)
