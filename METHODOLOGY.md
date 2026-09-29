# pegada-code methodology

This document describes how pegada-code turns Claude Code usage into energy and
greenhouse-gas estimates, what it assumes, how uncertain the result is, and what
it leaves out. It is versioned with the code; every report names the
coefficient version it used.

> **Status (v0.1.0): the bundled coefficients are PLACEHOLDERS.** They are
> order-of-magnitude values that exercise the pipeline and have not been
> calibrated or measured. Reports print a warning while any placeholder is in
> use. Do not cite absolute figures produced with them.

## 1. Scope

- **Functional unit:** the model inference behind one Claude Code project,
  i.e. all model responses logged in the project's sessions, including
  subagents.
- **Included:** inference energy on the provider's servers (IT energy),
  data-centre overhead through PUE, operational emissions from electricity,
  and embodied emissions of the serving hardware amortised over its lifetime
  energy.
- **Excluded:** model training and fine-tuning, the client machine, network
  transfer, idle or over-provisioned capacity not attributable to requests,
  water use, land use and other LCA impact categories, end-of-life, and
  server-side tools (web search/fetch; counted but not estimated).

## 2. Data collection

### 2.1 Source

Claude Code writes a JSONL transcript per session to
`~/.claude/projects/<encoded working directory>/<session id>.jsonl`. Subagents
write their own file to `<session id>/subagents/agent-<id>.jsonl`, with an
`agent-<id>.meta.json` sidecar holding the agent type. The format is
**undocumented**. The rules below were derived from real transcripts (Claude
Code 2.1.x; 23 files, 1,667 usage lines across 7 projects) and are covered by
unit tests on synthetic fixtures (`tests/fixtures/make_fixtures.py`). Each
ledger record stores the Claude Code version that wrote it (`extras.cc_version`)
so format changes can be traced.

Each `type: "assistant"` line carries `message.model`, `message.id` and
`message.usage` with four token classes:

| Ledger field | Transcript field | Meaning |
|---|---|---|
| `input` | `input_tokens` | uncached prompt tokens |
| `cache_write` | `cache_creation_input_tokens` | prompt tokens written to the prompt cache |
| `cache_read` | `cache_read_input_tokens` | prompt tokens served from the prompt cache |
| `output` | `output_tokens` | generated tokens, **including** reasoning ("thinking") tokens |

### 2.2 Deduplication

1. **Streaming chunks.** One API response is written as several lines, one per
   content block (thinking, text, tool use). They share `message.id`, repeat
   the same input/cache counts, and `output_tokens` grows to its final value on
   the last chunk. pegada keeps one record per `message.id`, taking the
   element-wise maximum of the counts. In the sample, 1,667 usage lines
   collapsed to 926 responses.
2. **Continued sessions.** When a session is continued, the new transcript
   re-contains the previous session's records (same `uuid` and `message.id`)
   under the new session id, with identical timestamps, and the old transcript
   gets a `continued-in` marker. Deduplication is therefore global across files.
   Sessions are ingested in order of (first timestamp, depth in the
   `continued-in` chain), so the original session keeps the attribution.
3. **Skipped lines.** `model: "<synthetic>"` (locally generated error stubs)
   and lines whose four counts are all zero.

The ledger is append-only and may hold the same message more than once, for
example after a later chunk or from concurrent writers. Readers apply rule 1,
so repeated ingestion is idempotent.

### 2.3 Attribution to a project

- **Hooks** (Stop, SessionEnd) attribute a whole session to the project in
  which Claude Code runs (`$CLAUDE_PROJECT_DIR`).
- **Backfill** scans all transcripts and keeps a record only if its `cwd` is
  the project root or below it. The directory name alone is not enough: the
  encoding is lossy (every non-alphanumeric character becomes `-`), so
  `/a/b_c` and `/a/b-c` collide.

### 2.4 Validation against Claude Code's own totals

Some transcripts contain a `cost-state` line with Claude Code's own per-model
token totals for the session. In one sample session, the deduplicated transcript
totals matched it **exactly** for the main model (20 / 1,698 / 624,567 / 34,282
input / output / cache-read / cache-write tokens). This validates rules 1–2.

## 3. Coverage: usage that transcripts do not log

`cost-state` also shows usage that **no transcript line records**:

- background calls on a small model (session title generation, web-search
  result summarisation, and similar). In one sample session these were 20k
  input tokens on Haiku 4.5.
- in another sample session, about 20% of the main model's cache reads and
  about 4k input tokens were missing, probably from compaction or side
  queries.
- advisor calls (`advisorModel` on a record) have no separate usage.

pegada therefore reports two things:

- **Headline** figures cover transcript-logged responses only. They are
  reproducible and every token traces to a logged API call, **but they are a
  lower bound on coverage**.
- **Unlogged usage** is shown on a separate line, never added to the headline.
  For sessions with a `cost-state`, it is `max(0, totals − logged)` per session,
  model and token class, estimated with the same coefficients. The report also
  gives the energy-weighted coverage ratio (mid), and `pegada code coverage`
  prints the per-session comparison. `cost-state` lines are rare (3 of 23
  sample files), so this is a diagnostic and not a correction.

## 4. Energy and emissions model

For each model response *r* of a model in family *f*:

```
E_IT(r)  = e_prefill(f) · (input + cache_write)
         + e_cache(f)   · cache_read
         + e_decode(f)  · output                      [Wh; coefficients in Wh per 1M tokens]

E(r)     = PUE · E_IT(r)                              [Wh, facility energy]
CO2e(r)  = E(r) · I_grid / 1000  +  E_IT(r) · k_emb / 1000   [gCO2e]
```

- `I_grid` is the location-based grid carbon intensity (gCO2e/kWh).
- `k_emb` is embodied emissions per kWh of **IT** energy (gCO2e/kWh): the
  serving hardware's manufacturing emissions divided by the IT energy it uses
  over its lifetime. Embodied emissions are therefore allocated in proportion to
  compute energy and are independent of grid intensity and PUE.

### 4.1 Why three token coefficients

- **Prefill** (uncached input and cache writes) processes prompt tokens in
  parallel. Its per-token cost is much lower than decoding but not negligible.
  Cache writes are prefilled like uncached input; the extra cost of storing the
  KV cache is assumed to be negligible.
- **Cache reads** skip prefill computation but still load stored key/value
  tensors and attend over them. Their per-token cost is assumed to be well
  below prefill and is **the most uncertain coefficient**. It also matters
  most: agents re-send their growing context on every step, so cache reads
  typically make up over 95% of a coding agent's tokens.
- **Decode** (output) generates tokens one by one and is memory-bandwidth
  bound. It has the highest per-token cost.

This per-token-class form is a deliberate simplification. It ignores batch
size, context length effects on attention cost, hardware generation, and
speculative decoding.

### 4.2 Coefficients

`src/pegada/data/coefficients.json` is versioned (`coefficients_version`). Each
model family has:

- ordered glob patterns matched against the lower-cased model id (so Bedrock
  and Vertex ids also match);
- a `{low, mid, high}` triple for each of `e_prefill`, `e_cache` and `e_decode`;
- a `status` (`PLACEHOLDER`, or e.g. `MEASURED`/`DERIVED` once calibrated) and
  a free-text `source` citation.

An unrecognised model is estimated with a deliberately wide `fallback` entry,
and the report names it in a warning. `pegada code coefficients` prints the
active table with its sources.

**Calibration (to do).** The intention is to align with
[EcoLogits](https://ecologits.ai), which models energy per output token as a
function of (estimated) active parameters, with server overheads and PUE, and
allocates embodied impacts by request time. Deriving `e_decode` from EcoLogits'
per-output-token model for each family's assumed parameter range is
straightforward. `e_prefill` and `e_cache` need an additional assumption,
because EcoLogits is driven by output tokens and latency rather than by input
token classes. Alternative coefficients can be used without code changes, via
`coefficients_file` in `.claude/pegada.config.json` or the
`PEGADA_COEFFICIENTS` environment variable.

### 4.3 Infrastructure parameters (defaults)

Defined in `src/pegada/data/parameters.json`. Each can be overridden per
project in `.claude/pegada.config.json` with a number or a triple (plus an
optional `source`).

| Parameter | low | mid | high | Status | Basis |
|---|---:|---:|---:|---|---|
| PUE | 1.10 | 1.20 | 1.56 | DEFAULT | Hyperscale fleet PUE (~1.1) up to the Uptime Institute 2024 industry average (1.56) |
| Grid intensity (gCO2e/kWh) | 50 | 370 | 700 | DEFAULT | mid ≈ US average (EIA 2023, ~367 g); low/high = low-carbon vs coal-heavy grids. Location-based; certificates/PPAs not credited |
| Embodied (gCO2e per kWh IT) | 10 | 40 | 150 | PLACEHOLDER | ~3–10 tCO2e per 8-GPU server over ~125 MWh lifetime IT energy |

The serving provider, data centre, region and hardware are not disclosed.
Transcripts carry an `inference_geo` field, which pegada records, but it is
currently always `"not_available"`.

Example `.claude/pegada.config.json`:

```json
{
  "grid_intensity": {"low": 250, "mid": 300, "high": 400, "source": "my assumption about the serving region"},
  "pue": 1.15,
  "coefficients_file": "calibration/coefficients-v1.json"
}
```

## 5. Uncertainty

Every input (three coefficients per family, PUE, grid intensity, embodied
factor) is a `(low, mid, high)` triple. Because the model is a sum of products
of non-negative terms, pegada uses interval arithmetic:

- **low** combines all low values, **high** combines all high values, and
  **mid** combines all mid values;
- the resulting range is a **bounding envelope**, not a statistical confidence
  interval. Because it takes all extremes at once it is wide by design, and
  the probability of the true value lying outside it is not quantified;
- **mid** is a central scenario, not a mean or median of a distribution.

Reports always show the range, and the mid value is labelled as such. pegada
never outputs a single number. Monte Carlo propagation with explicit
distributions is a possible extension once coefficients are calibrated.

Uncertainty that this envelope does **not** capture:

- **model error**, i.e. the per-token-class form itself (§4.1);
- **coverage**, i.e. unlogged calls (§3), which are reported separately;
- **fast mode** (`speed: "fast"`): recorded and counted, but it may run on
  different hardware or batching, and it uses the same coefficients;
- **prompt-cache retention.** The 1-hour vs 5-minute split of cache writes is
  recorded (`extras.cache_write_1h`), but storage energy is not modelled.

## 6. Reproducibility

- The ledger (`.claude/pegada.jsonl`) stores **token counts only**, never
  energy. Estimates are recomputed at report time, so recalibrated
  coefficients apply retroactively and the same ledger with the same
  coefficient version always gives the same result.
- The ledger contains no prompts, code or file paths: only model ids, token
  counts, timestamps, session ids, the subagent type and a few flags. By
  default it is git-ignored. `pegada code setup --share` lets a team commit it,
  using `merge=union` so each contributor's appended lines merge without
  conflicts; readers deduplicate.
- Each report shows the pegada version, the coefficient version and the
  parameter values with their status.

## 7. Limitations (summary)

1. The coefficients are placeholders (v0.1.0).
2. The serving hardware, region, data centre, batch sizes and utilisation are
   unknown, and all of them are folded into the coefficient ranges.
3. Transcript coverage is incomplete (§3), so the headline is a lower bound on
   usage.
4. The transcript format is undocumented and may change. The parser is
   defensive and tested, but it can break silently on a new format. The
   `coverage` check is the recommended sanity check.
5. Only greenhouse-gas emissions and energy are covered: no water, land use,
   abiotic resource depletion or other LCA categories yet.
6. Training and the amortisation of training emissions are not included.
7. A session started in a subdirectory of a project is included by backfill
   in the parent project. Hooks attribute it to the directory Claude Code was
   started in.
