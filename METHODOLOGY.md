# pegada-code methodology

This document describes how pegada-code turns Claude Code usage into energy and
greenhouse-gas estimates, what it assumes, how uncertain the result is, and what
it leaves out. It is versioned with the code; every report names the
coefficient version it used.

> **Status (v0.2.0): coefficients DERIVED.** Decode, per-response and embodied
> values come from EcoLogits. Prefill and cache-read values are first-principles
> estimates on the same hardware assumptions (§4.2). No coefficient has been
> measured on Anthropic's infrastructure, which is not possible from outside.
> The ranges express that.

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
         + e_decode(f)  · output
         + e_request(f)                               [Wh; token coefficients in Wh per 1M tokens]

E(r)     = PUE · E_IT(r)                              [Wh, facility energy]
CO2e(r)  = E(r) · I_grid / 1000  +  E_IT(r) · k_emb(f) / 1000   [gCO2e]
```

- `e_request` is a fixed IT energy per model response (Wh).
- `I_grid` is the location-based grid carbon intensity (gCO2e/kWh).
- `k_emb(f)` is embodied emissions per kWh of **IT** energy (gCO2e/kWh) for the
  hardware serving family *f*. Embodied emissions are therefore allocated in
  proportion to compute energy and do not depend on grid intensity or PUE.

### 4.1 Why separate prefill, cache-read and decode coefficients

- **Prefill** (uncached input and cache writes) processes prompt tokens in
  parallel and is compute-bound. Its per-token cost is much lower than
  decoding. Cache writes are prefilled like uncached input; the energy of
  storing the key/value (KV) cache is assumed to be negligible.
- **Cache reads** skip the prefill matrix multiplications. What remains is the
  attention of the response's new tokens over the cached KV entries. Coding
  agents re-send their growing context on every step, so cache reads are
  typically over 95% of their tokens, and this is the most uncertain
  coefficient.
- **Decode** (output) generates tokens one by one and is memory-bandwidth
  bound. It has by far the highest per-token cost.

This per-token-class form is a simplification. It ignores the dependence of
attention cost on context length within a response, as well as batching
dynamics, hardware generation and speculative decoding.

### 4.2 Coefficients (v0.2.0)

`src/pegada/data/coefficients.json` is **generated** by
`calibration/derive_coefficients.py`; `--check` verifies that the committed
file is reproducible. Each family has ordered glob patterns matched against
the lower-cased model id (Bedrock and Vertex ids match too), `{low, mid,
high}` triples, a `status`, a `source`, and a `derivation` block with every
input. `pegada code coefficients` prints the active table.

**EcoLogits alignment.** Equations and constants come from EcoLogits 0.11.1
(`ecologits/impacts/llm.py`). That release predates the Claude 5 models, so
model data (parameter estimates, throughput, time to first token) comes from
the EcoLogits model repository at commit `2b36303` (21 Sep 2026). Its
`llm.py` is byte-identical to 0.11.1's. The file is vendored in
`calibration/data/` and checked against its SHA-256.

| Term | Source | How |
|---|---|---|
| `e_decode` | EcoLogits | GPU energy per output token `(α·e^(β·64)·P_active + γ)` × GPU count, plus the non-GPU server share (1.2 kW per 8 GPUs) over `1/TPS`, both divided by EcoLogits' batch size of 64. Exactly EcoLogits' IT energy per output token. |
| `e_request` | EcoLogits | EcoLogits' request IT energy for zero output tokens: the non-GPU server share during time to first token (TTFT). |
| `k_emb` | EcoLogits | EcoLogits' time-allocated embodied GWP per output token (H100: 273 kgCO2e; server: 5,700 kgCO2e per 8 GPUs; 3-year lifetime) ÷ its IT energy per output token. |
| `e_prefill` | first principles | `2·P_active` FLOPs per token ÷ (MFU × 989.4 TFLOP/s BF16 dense) × (700 W GPU + 150 W non-GPU share). |
| `e_cache` | first principles | Per cached token: `N_out · KV_bytes · ε_byte + N_new · F_pair · ε_FLOP` (see below). |

*Low/mid/high.* The active parameters are EcoLogits' min / midpoint / max.
For a dense model the total follows the same scenario, as in EcoLogits, which
changes the GPU count. First-principles constants take their low/mid/high
value in the same scenario:

| Constant | low | mid | high | Basis |
|---|---:|---:|---:|---|
| Prefill MFU | 0.60 | 0.45 | 0.30 | Typical compute-bound serving utilisation (high MFU → low energy) |
| KV bytes per cached token | 70,272 | 192,512 | 516,096 | DeepSeek-V3 (MLA), Qwen3-235B-A22B (GQA-4), Llama-3.1-405B (GQA-8), BF16 |
| Attention FLOPs per (query, cached token) pair | 3.08 M | 5.00 M | 8.26 M | Same three architectures, QKᵀ + AV over all layers |
| ε_byte, energy per KV byte read (J) | 3.1e-11 | 8.9e-11 | 2.5e-10 | low: HBM access ≈3.9 pJ/bit (O'Connor et al., MICRO 2017); high: 850 W for the read time at 3.35 TB/s; mid: geometric mean |
| N_out, output tokens per response | 205 | 347 | 580 | Cache-read-weighted quartiles, 1,006 Claude Code responses (§4.2.1) |
| N_new, new input tokens per response | 420 | 648 | 1,318 | Same |

Hardware figures are from the NVIDIA H100 SXM datasheet (700 W TDP, 989.4
TFLOP/s dense BF16, 3.35 TB/s HBM3). The 150 W non-GPU share per GPU is
EcoLogits' 1.2 kW per 8-GPU server.

**4.2.1 Calibration sample.** `N_out` and `N_new` are the only quantities
taken from observed usage: 1,006 responses with cache reads, from the
author's Claude Code transcripts (5 projects, Claude Code 2.1.x, July–September
2026). They are weighted by cache-read tokens, because that is what a
per-cached-token cost averages over. Only these six numbers are published.

**Resulting values (Wh IT per 1M tokens; low / mid / high):**

| Family | Prefill | Cache read | Decode | Per response (Wh) | Embodied (g/kWh IT) |
|---|---|---|---|---|---|
| claude-haiku-4-5 | 8.0 / 24 / 56 | 0.64 / 3.4 / 30 | 57 / 64 / 143 | 0.0004–0.0007 | 39 / 43 / 48 |
| claude-sonnet-5 | 23 / 62 / 140 | 0.64 / 3.4 / 30 | 1,081 / 1,349 / 1,618 | 0.024 | 26 / 31 / 39 |
| claude-sonnet-4-5 | 35 / 93 / 210 | 0.64 / 3.4 / 30 | 1,303 / 1,704 / 2,105 | 0.012 | 30 / 37 / 49 |
| claude-opus-5 (and 5-5) | 53 / 142 / 318 | 0.64 / 3.4 / 30 | 2,803 / 4,015 / 5,227 | 0.070 | 14 / 18 / 25 |
| claude-fable-5 | 107 / 283 / 636 | 0.64 / 3.4 / 30 | 8,160 / 13,010 / 17,850 | 0.20 | 9.6 / 13 / 21 |

Other Opus 4.x, Sonnet 4.6 and Fable 5.1 entries are in the file. At the mid
values, prefill costs about 2–9% of decode per token, and a cache read about
5% of a Sonnet 5 prefill token. For comparison, Anthropic prices a cache read
at 10% of an uncached input token. Prices are not energy, but the orders of
magnitude agree.

**Models outside EcoLogits.**
- `claude-opus-5-5` uses `claude-opus-5`'s data.
- Other Claude models without an EcoLogits entry (e.g. `claude-sonnet-4`,
  `claude-3-5-haiku`) use the envelope of their tier: minimum low, median mid,
  maximum high over the tier's families.
- Any other model uses the envelope of all families (`unknown`).

Reports name every approximated or unknown model in a warning.

**Alternative coefficients.** Set `coefficients_file` in
`.claude/pegada.config.json` or the `PEGADA_COEFFICIENTS` environment
variable. Any file in the same schema works, including hand-made ones marked
`PLACEHOLDER`; reports then show a warning banner.

**Caveats.**
1. `e_cache` is not model-specific. Frontier architectures (layers, KV heads)
   are unpublished, so one range spanning three open architectures is used
   for all families.
2. Taken together, EcoLogits' energy and latency models imply more than the
   700 W TDP per GPU (for example, Sonnet 5 at 64 × 62 tokens/s). pegada
   adopts EcoLogits' values as-is for alignment.
3. EcoLogits' decode energy was fitted on open models at moderate context
   lengths, so part of the cost of decoding against long contexts may already
   be inside `e_decode`. The decode term of `e_cache` may partly double-count
   it, which pushes the estimate upward.
4. EcoLogits assumes 16-bit weights. FP8 serving would lower prefill and
   decode energy, which the low bound does not capture.

### 4.3 Infrastructure parameters (defaults)

Defined in `src/pegada/data/parameters.json`. Each can be overridden per
project in `.claude/pegada.config.json` with a number or a triple (plus an
optional `source`).

| Parameter | low | mid | high | Status | Basis |
|---|---:|---:|---:|---|---|
| PUE | 1.36 | 1.36 | 1.36 | DEFAULT | Uptime Institute Global Data Center Survey 2026, capacity-weighted average (Uptime Intelligence, 6 Aug 2026). Point value, see below |
| Grid intensity (gCO2e/kWh) | 50 | 384 | 700 | DEFAULT | mid: EcoLogits 0.11.1 USA electricity mix; low/high: indicative spread between low-carbon and coal-heavy grids (not from a dataset). Location-based: certificates and PPAs are not credited |
| Embodied (gCO2e per kWh IT) | 9.6 | 30 | 97 | DERIVED | Only for families without their own factor (all bundled families have one): envelope of the per-family EcoLogits values |

**PUE: consequential framing.** pegada does not use the provider's own PUE
(EcoLogits uses 1.09–1.14 for Anthropic on AWS/Google). An efficient
hyperscale facility serving this load displaces other load onto the rest of
the fleet, so the relevant value is the industry average, weighted by
capacity: this reflects where IT load actually runs, and gives 1.36. The
per-facility average is 1.52; the 2025 survey reported 1.54. PUE is a point
value by design: under this framing, which facility hosts the model does not
matter. It is also a minor source of uncertainty compared with the per-token
coefficients.

The serving provider, data centre, region and hardware are not disclosed.
Transcripts carry an `inference_geo` field, which pegada records, but it is
currently always `"not_available"`.

Example `.claude/pegada.config.json`:

```json
{
  "grid_intensity": {"low": 250, "mid": 300, "high": 400, "source": "my assumption about the serving region"},
  "pue": 1.1,
  "coefficients_file": "calibration/my-coefficients.json"
}
```

## 5. Uncertainty

Every coefficient and parameter is a `(low, mid, high)` triple; a point value
has three equal entries. The model is a sum of products of non-negative
terms, so pegada uses interval arithmetic:

- **low** combines all low values, **high** combines all high values, and
  **mid** combines all mid values;
- the resulting range is a **bounding envelope**, not a statistical confidence
  interval. Because it takes all extremes at once it is wide by design, and
  the probability of the true value lying outside it is not quantified;
- **mid** is a central scenario, not a mean or median of a distribution.

Reports always show the range, and the mid value is labelled as such. pegada
never outputs a single number. Monte Carlo propagation with explicit
distributions is a possible extension.

Where the width comes from, in decreasing order of typical impact on a coding
agent's footprint: cache-read energy (a factor of about 50 between low and
high), grid intensity (about 14), prefill energy (about 6), then decode
energy and embodied factor (about 1.5–2.5 within a family). PUE contributes
none.

Uncertainty that this envelope does **not** capture:

- **model error**, i.e. the per-token-class form itself (§4.1), and the
  EcoLogits model it builds on;
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

1. No coefficient is measured on Anthropic's infrastructure. Decode,
   per-response and embodied values inherit EcoLogits' assumptions (parameter
   estimates, H100 hardware, batch size 64, throughput data). Prefill and
   cache-read values are first-principles estimates (§4.2), and the cache-read
   value is not model-specific.
2. The serving hardware, region, data centre, batch sizes and utilisation are
   unknown and are folded into the ranges. PUE is set by a consequential
   choice (§4.3), not a measurement.
3. Transcript coverage is incomplete (§3), so the headline is a lower bound on
   usage.
4. The transcript format is undocumented and may change. The parser is
   defensive and tested, but it can break silently on a new format. The
   `coverage` check is the recommended sanity check.
5. Only greenhouse-gas emissions and energy are covered: no water, land use,
   abiotic resource depletion or other LCA categories yet. EcoLogits provides
   some of these, so they are a natural next step.
6. Training and the amortisation of training emissions are not included.
7. A session started in a subdirectory of a project is included by backfill
   in the parent project. Hooks attribute it to the directory Claude Code was
   started in.
