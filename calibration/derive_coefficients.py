"""Derive pegada's coefficients.json from EcoLogits plus first-principles terms.

Usage (from the repository root):

    python3 -m venv .venv-calib && .venv-calib/bin/pip install -r calibration/requirements.txt
    .venv-calib/bin/python calibration/derive_coefficients.py            # writes src/pegada/data/coefficients.json
    .venv-calib/bin/python calibration/derive_coefficients.py --check    # fails if the committed file differs

What comes from where (see METHODOLOGY.md §4 for the reasoning):

* e_decode   EcoLogits, exactly: GPU energy per output token × GPU count plus the
             non-GPU server share over 1/TPS, at EcoLogits' batch size.
* e_request  EcoLogits, exactly: non-GPU server energy during time-to-first-token,
             i.e. EcoLogits' request energy for zero output tokens.
* embodied   EcoLogits' time-allocated embodied GWP per output token divided by
             its IT energy per output token (gCO2e per kWh of IT energy).
* e_prefill  First principles: 2·P_active FLOPs per token on EcoLogits' hardware.
             EcoLogits attributes no GPU energy to input tokens.
* e_cache    First principles: attention over the cached key/value entries by the
             response's new tokens (decode reads + prefill attention FLOPs).

low/mid/high use EcoLogits' min/midpoint/max active parameters together with the
low/mid/high value of each first-principles constant.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from typing import Dict, List, Tuple

import hashlib

from ecologits.impacts import llm as eco
from ecologits.impacts.llm import compute_llm_impacts_dag

# Equations and constants: the EcoLogits 0.11.1 release. Its impacts/llm.py is
# byte-identical to the model-data commit below, but the release predates the
# Claude 5 models, so the model repository is pinned to a later commit and vendored.
ECOLOGITS_VERSION = "0.11.1"
ECOLOGITS_MODELS_COMMIT = "2b3630389e31414155328d1e6d0a91faa36e9409"
ECOLOGITS_MODELS_SHA256 = "6b526763774098b938cf8ec9e756a11f7b1f8ccaa88844f2e403b3ded0d6e13f"
ECOLOGITS_MODELS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ecologits-models-2b36303.json")
COEFFICIENTS_VERSION = "0.2.0"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "pegada", "data", "coefficients.json")

LMH = Tuple[float, float, float]  # low, mid, high

# --- Hardware: EcoLogits assumes NVIDIA H100 SXM 80GB, 8 per server, BF16 --------
GPU_TDP_W = 700.0  # NVIDIA H100 SXM datasheet
GPU_PEAK_BF16_FLOPS = 989.4e12  # dense BF16 tensor-core peak, H100 SXM datasheet
GPU_HBM_BYTES_PER_S = 3.35e12  # HBM3 bandwidth, H100 SXM datasheet
NON_GPU_W_PER_GPU = eco.SERVER_POWER * 1000 / eco.SERVER_GPUS  # EcoLogits: 1.2 kW per 8-GPU server

# --- Prefill ----------------------------------------------------------------
# Model FLOPs utilisation during prefill (compute-bound). The high MFU gives the low energy.
PREFILL_MFU: LMH = (0.60, 0.45, 0.30)
# Energy per FLOP while compute-bound, with the GPU at TDP plus its non-GPU server share.
J_PER_FLOP: LMH = tuple((GPU_TDP_W + NON_GPU_W_PER_GPU) / (m * GPU_PEAK_BF16_FLOPS) for m in PREFILL_MFU)  # type: ignore

# --- Cache reads -------------------------------------------------------------
# Frontier architectures are unpublished; span open models of comparable scale.
# KV cache bytes per token (BF16): DeepSeek-V3 (MLA: 61 layers × (512+64) × 2 B),
# Qwen3-235B-A22B (GQA: 2 × 94 layers × 4 KV heads × 128 × 2 B),
# Llama-3.1-405B (GQA: 2 × 126 layers × 8 KV heads × 128 × 2 B).
KV_BYTES_PER_TOKEN: LMH = (70_272, 192_512, 516_096)
# Attention FLOPs per (query token, cached token) pair over all layers (QK^T and AV):
# Qwen3-235B-A22B 4·94·64·128; DeepSeek-V3 61·(2·128·192 + 2·128·128); Llama-3.1-405B 4·126·128·128.
ATTN_FLOPS_PER_PAIR: LMH = (3.08e6, 5.00e6, 8.26e6)
# Energy per byte of KV read during decode. low: HBM access energy only (≈3.9 pJ/bit,
# O'Connor et al., MICRO 2017); high: GPU + non-GPU power attributed for the time the
# read takes at full bandwidth; mid: geometric mean.
_J_B_LOW = 3.9e-12 * 8
_J_B_HIGH = (GPU_TDP_W + NON_GPU_W_PER_GPU) / GPU_HBM_BYTES_PER_S
J_PER_KV_BYTE: LMH = (_J_B_LOW, (_J_B_LOW * _J_B_HIGH) ** 0.5, _J_B_HIGH)
# New tokens per response that attend to the cached context, weighted by cache reads
# (quartiles p25/p50/p75). Observed in 1,006 Claude Code responses (5 projects,
# Claude Code 2.1.x, July–September 2026); only these aggregates are published.
N_OUTPUT_PER_RESPONSE: LMH = (205, 347, 580)
N_NEW_INPUT_PER_RESPONSE: LMH = (420, 648, 1318)

# --- Families (mirroring EcoLogits' model repository) --------------------------
# (family id, EcoLogits model, glob patterns, approximation note or "")
FAMILIES = [
    ("claude-fable-5-1", "claude-fable-5-1", ["*claude-fable-5-1*"], ""),
    ("claude-fable-5", "claude-fable-5", ["*claude-fable-5*"], ""),
    ("claude-opus-5-5", "claude-opus-5", ["*claude-opus-5-5*"],
     "Not in EcoLogits 0.11.1: uses the parameters and deployment data of claude-opus-5."),
    ("claude-opus-5", "claude-opus-5", ["*claude-opus-5*"], ""),
    ("claude-opus-4-8", "claude-opus-4-8", ["*claude-opus-4-8*"], ""),
    ("claude-opus-4-7", "claude-opus-4-7", ["*claude-opus-4-7*"], ""),
    ("claude-opus-4-6", "claude-opus-4-6", ["*claude-opus-4-6*"], ""),
    ("claude-opus-4-5", "claude-opus-4-5-20251101", ["*claude-opus-4-5*"], ""),
    ("claude-sonnet-5", "claude-sonnet-5", ["*claude-sonnet-5*"], ""),
    ("claude-sonnet-4-6", "claude-sonnet-4-6", ["*claude-sonnet-4-6*"], ""),
    ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929", ["*claude-sonnet-4-5*"], ""),
    ("claude-haiku-4-5", "claude-haiku-4-5-20251001", ["*claude-haiku-4-5*"], ""),
]
# Tier catch-alls for models EcoLogits does not cover: envelope of the tier's families.
TIERS = [
    ("claude-fable (tier)", "*claude*fable*", "claude-fable"),
    ("claude-opus (tier)", "*claude*opus*", "claude-opus"),
    ("claude-sonnet (tier)", "*claude*sonnet*", "claude-sonnet"),
    ("claude-haiku (tier)", "*claude*haiku*", "claude-haiku"),
]


def load_models() -> Dict[str, Dict]:
    with open(ECOLOGITS_MODELS_FILE, "rb") as fh:
        raw = fh.read()
    if hashlib.sha256(raw).hexdigest() != ECOLOGITS_MODELS_SHA256:
        raise SystemExit(f"{ECOLOGITS_MODELS_FILE} does not match the pinned SHA-256")
    return {m["name"]: m for m in json.loads(raw)["models"] if m.get("provider") == "anthropic"}


MODELS = load_models()


def eco_model(name: str) -> Dict:
    if name not in MODELS:
        raise SystemExit(f"EcoLogits models @{ECOLOGITS_MODELS_COMMIT[:7]} has no model {name!r}")
    return MODELS[name]


def params(m: Dict) -> Tuple[LMH, LMH]:
    """Active and total parameter counts (low, mid, high), in billions. For a dense
    model the total equals the active count in each scenario, as in EcoLogits."""
    p = m["architecture"]["parameters"]
    if "active" in p:  # mixture of experts
        lo, hi, tot = float(p["active"]["min"]), float(p["active"]["max"]), float(p["total"])
        return (lo, (lo + hi) / 2, hi), (tot, tot, tot)
    lo, hi = float(p["min"]), float(p["max"])
    return (lo, (lo + hi) / 2, hi), (lo, (lo + hi) / 2, hi)


def dag(active: float, total: float, out_tokens: float, tps: float, ttft_s: float) -> Dict:
    return compute_llm_impacts_dag(
        model_active_parameter_count=active, model_total_parameter_count=total,
        output_token_count=out_tokens, request_latency=1e12,
        if_electricity_mix_adpe=0, if_electricity_mix_pe=0, if_electricity_mix_gwp=0, if_electricity_mix_wue=0,
        datacenter_pue=1.0, datacenter_wue=0, tps=tps, ttft=ttft_s,
    )


def triple(lo: float, mid: float, hi: float, digits: int = 4) -> Dict[str, float]:
    r = lambda x: float(f"{x:.{digits}g}")
    return {"low": r(lo), "mid": r(mid), "high": r(hi)}


def derive(fid: str, eco_name: str, patterns: List[str], approx: str) -> Dict:
    m = eco_model(eco_name)
    active, total = params(m)
    tps, ttft = m["deployment"]["tps"], m["deployment"]["ttft"] / 1000.0
    n = 1e6
    runs = [dag(a, t, n, tps, 0.0) for a, t in zip(active, total)]
    gpus = [int(d["gpu_required_count"]) for d in runs]
    # Wh per 1M output tokens: GPU energy × GPU count + non-GPU server share.
    decode = [(d["gpu_required_count"] * d["gpu_energy"] + d["server_energy"]) * 1000 for d in runs]
    # gCO2e per 1M output tokens (time-allocated embodied emissions of GPUs + server).
    emb = [d["request_embodied_gwp"] * 1000 for d in runs]
    # Wh per response: non-GPU server energy during time-to-first-token.
    req = [dag(a, t, 0, tps, ttft)["request_it_energy"] * 1000 for a, t in zip(active, total)]
    # gCO2e per kWh IT. Per-token embodied depends on GPU count and TPS, not on active
    # params, so the ratio falls as energy rises; order the triple ascending.
    k_emb = sorted(e / w * 1000 for e, w in zip(emb, decode))
    # J per token × 1M tokens / 3600 J per Wh → Wh per 1M tokens
    prefill = [2 * a * 1e9 * j * 1e6 / 3600 for a, j in zip(active, J_PER_FLOP)]
    cache = [
        (no * kv * jb + nn * fl * jf) * 1e6 / 3600
        for no, kv, jb, nn, fl, jf in zip(N_OUTPUT_PER_RESPONSE, KV_BYTES_PER_TOKEN, J_PER_KV_BYTE,
                                          N_NEW_INPUT_PER_RESPONSE, ATTN_FLOPS_PER_PAIR, J_PER_FLOP)
    ]
    fam = {
        "id": fid,
        "match": patterns,
        "e_prefill": triple(*prefill),
        "e_cache": triple(*cache),
        "e_decode": triple(*decode),
        "e_request": triple(*sorted(req)),
        "embodied": triple(*k_emb),
        "status": "DERIVED",
        "source": f"e_decode, e_request, embodied: EcoLogits {ECOLOGITS_VERSION}, models@{ECOLOGITS_MODELS_COMMIT[:7]} ({eco_name}); "
                  f"e_prefill, e_cache: first principles on EcoLogits' hardware assumptions. "
                  f"calibration/derive_coefficients.py, METHODOLOGY.md §4.",
        "derivation": {
            "ecologits_model": eco_name,
            "active_params_b": list(active),
            "total_params_b": list(total),
            "gpus": gpus,
            "tps": tps,
            "ttft_s": ttft,
            "embodied_g_per_mtok_output": [round(e, 3) for e in emb],
        },
    }
    if approx:
        fam["approximation"] = approx
    return fam


def envelope(fid: str, patterns: List[str], members: List[Dict], approx: str) -> Dict:
    def env(key):
        return triple(min(f[key]["low"] for f in members),
                      statistics.median(f[key]["mid"] for f in members),
                      max(f[key]["high"] for f in members))
    return {
        "id": fid,
        "match": patterns,
        **{k: env(k) for k in ("e_prefill", "e_cache", "e_decode", "e_request", "embodied")},
        "status": "DERIVED",
        "source": "Envelope (min low, median mid, max high) of: " + ", ".join(f["id"] for f in members),
        "approximation": approx,
    }


def build() -> Dict:
    families = [derive(*f) for f in FAMILIES]
    tiers = [
        envelope(tid, [pat], [f for f in families if f["id"].startswith(prefix)],
                 "Model not in EcoLogits: estimated with the envelope of its tier.")
        for tid, pat, prefix in TIERS
    ]
    fallback = envelope("unknown", [], families, "Unrecognised model: envelope of all derived families.")
    return {
        "schema_version": 2,
        "coefficients_version": COEFFICIENTS_VERSION,
        "units": {
            "e_prefill, e_cache, e_decode": "Wh of IT energy per 1,000,000 tokens (before PUE)",
            "e_request": "Wh of IT energy per model response (before PUE)",
            "embodied": "gCO2e per kWh of IT energy",
        },
        "description": "Generated by calibration/derive_coefficients.py; do not edit by hand. Families are matched in order; the first family with a glob pattern matching the lower-cased model id is used.",
        "inputs": {
            "ecologits_version": ECOLOGITS_VERSION,
            "ecologits_models_commit": ECOLOGITS_MODELS_COMMIT,
            "gpu": "NVIDIA H100 SXM 80GB (EcoLogits assumption)",
            "gpu_tdp_w": GPU_TDP_W, "gpu_peak_bf16_flops": GPU_PEAK_BF16_FLOPS, "gpu_hbm_bytes_per_s": GPU_HBM_BYTES_PER_S,
            "non_gpu_w_per_gpu": NON_GPU_W_PER_GPU,
            "prefill_mfu": list(PREFILL_MFU),
            "kv_bytes_per_token": list(KV_BYTES_PER_TOKEN),
            "attn_flops_per_pair": list(ATTN_FLOPS_PER_PAIR),
            "j_per_kv_byte": [float(f"{x:.4g}") for x in J_PER_KV_BYTE],
            "n_output_per_response": list(N_OUTPUT_PER_RESPONSE),
            "n_new_input_per_response": list(N_NEW_INPUT_PER_RESPONSE),
        },
        "families": families + tiers,
        "fallback": fallback,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed file is out of date")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    text = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        with open(args.out, encoding="utf-8") as fh:
            same = fh.read() == text
        print("coefficients.json is up to date" if same else "coefficients.json is OUT OF DATE")
        return 0 if same else 1
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
