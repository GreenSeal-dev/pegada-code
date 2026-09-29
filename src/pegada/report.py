"""Project reports (Markdown or JSON) and the shields.io badge."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from pegada import __version__
from pegada import fmt
from pegada.coefficients import Parameters
from pegada.estimator import Estimator, Footprint, Impact
from pegada.interval import Interval
from pegada.ledger import LedgerContents
from pegada.records import TOKEN_CLASSES

CLASS_LABELS = {
    "input": "Input (uncached)",
    "cache_write": "Cache write",
    "cache_read": "Cache read",
    "output": "Output (incl. thinking)",
}

METHODOLOGY_URL = "https://github.com/GreenSeal-dev/pegada-code/blob/main/METHODOLOGY.md"


def _fp_dict(est: Estimator, fp: Footprint) -> Dict[str, Any]:
    return {"messages": fp.messages, "tokens": dict(fp.tokens), **est.impact(fp).to_dict()}


def unlogged(contents: LedgerContents, est: Estimator) -> Optional[Dict[str, Any]]:
    """Usage the agent reports in its own session totals but that no transcript
    line accounts for: ``max(0, totals − logged)`` per session, model and token
    class. Reported separately, never added to the headline."""
    if not contents.totals:
        return None
    logged: Dict[tuple, Dict[str, int]] = defaultdict(lambda: {c: 0 for c in TOKEN_CLASSES})
    for r in contents.records.values():
        for c in TOKEN_CLASSES:
            logged[(r.session_id, r.model)][c] += getattr(r, c)
    missing, observed = Footprint(), Footprint()
    by_model: Dict[str, Footprint] = {}
    sessions = set()
    for t in contents.totals.values():
        sessions.add(t.session_id)
        for model, counts in t.models.items():
            diff = {c: max(0, counts.get(c, 0) - logged[(t.session_id, model)][c]) for c in TOKEN_CLASSES}
            # messages=0: the number of unlogged calls is unknown, so no per-response term.
            est.add(missing, model, diff, messages=0)
            est.add(by_model.setdefault(model, Footprint()), model, diff, messages=0)
        for (sid, model), counts in logged.items():
            if sid == t.session_id:
                est.add(observed, model, counts, messages=0)
    obs_mid = est.impact(observed).energy_wh.mid
    miss_mid = est.impact(missing).energy_wh.mid
    return {
        "sessions_with_totals": len(sessions),
        "sessions_total": len({r.session_id for r in contents.records.values()}),
        "coverage_energy_mid": (obs_mid / (obs_mid + miss_mid)) if (obs_mid + miss_mid) else 1.0,
        "unlogged": _fp_dict(est, missing),
        "by_model": {m: _fp_dict(est, fp) for m, fp in by_model.items() if fp.total_tokens},
    }


def build_report(contents: LedgerContents, est: Estimator, project: str = "", top: int = 10) -> Dict[str, Any]:
    recs = list(contents.records.values())
    total = est.footprint(recs)
    impact = est.impact(total)
    by_model = est.group(recs, lambda r: r.model)
    by_role = est.group(recs, lambda r: f"subagent: {r.agent_type or 'unknown'}" if r.is_subagent else "main thread")
    by_session = est.group(recs, lambda r: r.session_id)
    session_meta: Dict[str, Dict[str, Any]] = {}
    for r in recs:
        m = session_meta.setdefault(r.session_id, {"first": "", "models": set()})
        if r.timestamp:
            m["first"] = min(m["first"] or r.timestamp, r.timestamp)
        m["models"].add(r.model)
    sessions = sorted(by_session.items(), key=lambda kv: est.impact(kv[1]).energy_wh.mid, reverse=True)

    extras: Counter = Counter()
    for r in recs:
        ex = r.extras
        extras["web_search_requests"] += ex.get("web_search", 0)
        extras["web_fetch_requests"] += ex.get("web_fetch", 0)
        if ex.get("speed") == "fast":
            extras["fast_mode_messages"] += 1
        if ex.get("advisor_model"):
            extras["messages_with_advisor"] += 1
        extras["thinking_tokens"] += r.thinking

    unl = unlogged(contents, est)  # may add unknown models; compute before warnings
    it_mid_total = total.it_energy_wh.mid or 1.0
    warnings: List[str] = []
    if est.unknown_models:
        warnings.append(
            f"{len(est.unknown_models)} model(s) not recognised, estimated with the wide fallback coefficients: "
            + ", ".join(sorted(est.unknown_models))
        )
    for m, fam in sorted(est.approximated.items()):
        warnings.append(f"`{m}` (coefficients `{fam.id}`): {fam.approximation}")
    if contents.bad_lines:
        warnings.append(f"{contents.bad_lines} unreadable ledger line(s) were skipped.")

    p = est.parameters
    timestamps = sorted(r.timestamp for r in recs if r.timestamp)
    return {
        "pegada_version": __version__,
        "project": project,
        "coefficients_version": est.coefficients.version,
        "uses_placeholders": est.uses_placeholders,
        "parameters": {n: {**getattr(p, n).value.to_dict(), "unit": getattr(p, n).unit, "status": getattr(p, n).status} for n in Parameters.NAMES},
        "embodied_per_family": p.embodied.status != "USER",
        "period": {"first": timestamps[0] if timestamps else None, "last": timestamps[-1] if timestamps else None},
        "sessions": len(by_session),
        "total": {"messages": total.messages, "tokens": dict(total.tokens), **impact.to_dict()},
        "token_classes": {
            c: {
                "tokens": total.tokens[c],
                "token_share": total.tokens[c] / (total.total_tokens or 1),
                "energy_share_mid": total.it_energy[c].mid / it_mid_total,
                "energy_wh": (total.it_energy[c] * p.pue.value).to_dict(),
            }
            for c in TOKEN_CLASSES
        },
        "per_response": {
            "responses": total.messages,
            "energy_share_mid": total.it_energy["request"].mid / it_mid_total,
            "energy_wh": (total.it_energy["request"] * p.pue.value).to_dict(),
        },
        "by_model": {
            m: {"family": est.coefficients.lookup(m)[0].id, **_fp_dict(est, fp)}
            for m, fp in sorted(by_model.items(), key=lambda kv: -est.impact(kv[1]).energy_wh.mid)
        },
        "by_role": {k: _fp_dict(est, fp) for k, fp in sorted(by_role.items())},
        "top_sessions": [
            {
                "session_id": sid,
                "first": session_meta[sid]["first"],
                "models": sorted(session_meta[sid]["models"]),
                **_fp_dict(est, fp),
            }
            for sid, fp in sessions[:top]
        ],
        "unlogged": unl,
        "not_estimated": {k: v for k, v in extras.items() if v},
        "warnings": warnings,
    }


def _iv(d: Dict[str, float]) -> Interval:
    return Interval(d["low"], d["mid"], d["high"])


def render_markdown(rep: Dict[str, Any]) -> str:
    L: List[str] = []
    add = L.append
    add("## pegada-code — AI coding footprint")
    add("")
    if rep["uses_placeholders"]:
        add("> ⚠️ **Placeholder coefficients.** Some coefficients or parameters used here are PLACEHOLDER values "
            "that exercise the pipeline; they are not calibrated. Do not cite these figures.")
        add("")
    tot = rep["total"]
    if not tot["messages"]:
        add("No usage recorded yet for this project. Run `/pegada backfill` to import existing transcripts.")
        return "\n".join(L)
    period = rep["period"]
    add(f"**Project:** `{rep['project']}` · **Sessions:** {rep['sessions']} · **Model responses:** {tot['messages']:,} · "
        f"**Period:** {(period['first'] or '')[:10]} → {(period['last'] or '')[:10]}")
    add("")
    add("| | Estimate (low–high, mid) |")
    add("|---|---|")
    add(f"| **Energy** (facility, incl. PUE) | {fmt.interval(_iv(tot['energy_wh']), 'Wh')} |")
    add(f"| **Emissions** | {fmt.interval(_iv(tot['co2e_g']), 'g', 'CO2e')} |")
    add(f"| ↳ operational | {fmt.interval(_iv(tot['co2e_operational_g']), 'g', 'CO2e')} |")
    add(f"| ↳ embodied (hardware) | {fmt.interval(_iv(tot['co2e_embodied_g']), 'g', 'CO2e')} |")
    add("")
    add("Low/high combine all low/high inputs (a bounding envelope, not a confidence interval).")
    add("")

    add("### Token classes")
    add("")
    add("| Class | Tokens | Share of tokens | Share of energy (mid) | Energy |")
    add("|---|---:|---:|---:|---|")
    for c in TOKEN_CLASSES:
        tc = rep["token_classes"][c]
        name = f"**{CLASS_LABELS[c]}**" if c == "cache_read" else CLASS_LABELS[c]
        add(f"| {name} | {fmt.tokens(tc['tokens'])} | {fmt.pct(tc['token_share'])} | "
            f"{fmt.pct(tc['energy_share_mid'])} | {fmt.interval(_iv(tc['energy_wh']), 'Wh', mid=False)} |")
    pr = rep["per_response"]
    add(f"| Per-response overhead | {pr['responses']:,} responses | — | {fmt.pct(pr['energy_share_mid'])} | "
        f"{fmt.interval(_iv(pr['energy_wh']), 'Wh', mid=False)} |")
    cr = rep["token_classes"]["cache_read"]
    add("")
    add(f"Cache reads are **{fmt.pct(cr['token_share'])} of all tokens** but **{fmt.pct(cr['energy_share_mid'])} of the "
        f"estimated energy** (mid): agents re-read their growing context on every step, so long sessions multiply "
        f"cache reads. Their per-token energy is the most uncertain coefficient.")
    add("")

    add("### By model")
    add("")
    add("| Model | Coefficients | Responses | Tokens | Energy | Emissions |")
    add("|---|---|---:|---:|---|---|")
    for m, d in rep["by_model"].items():
        add(f"| `{m}` | {d['family']} | {d['messages']:,} | {fmt.tokens(sum(d['tokens'].values()))} | "
            f"{fmt.interval(_iv(d['energy_wh']), 'Wh', mid=False)} | {fmt.interval(_iv(d['co2e_g']), 'g', 'CO2e', mid=False)} |")
    add("")

    add("### Main thread vs subagents")
    add("")
    add("| | Responses | Tokens | Energy | Emissions |")
    add("|---|---:|---:|---|---|")
    for k, d in rep["by_role"].items():
        add(f"| {k} | {d['messages']:,} | {fmt.tokens(sum(d['tokens'].values()))} | "
            f"{fmt.interval(_iv(d['energy_wh']), 'Wh', mid=False)} | {fmt.interval(_iv(d['co2e_g']), 'g', 'CO2e', mid=False)} |")
    add("")

    add(f"### Top sessions (of {rep['sessions']})")
    add("")
    add("| Session | Started | Models | Responses | Energy | Emissions |")
    add("|---|---|---|---:|---|---|")
    for s in rep["top_sessions"]:
        add(f"| `{s['session_id'][:8]}` | {(s['first'] or '')[:16].replace('T', ' ')} | {', '.join(s['models'])} | "
            f"{s['messages']:,} | {fmt.interval(_iv(s['energy_wh']), 'Wh', mid=False)} | "
            f"{fmt.interval(_iv(s['co2e_g']), 'g', 'CO2e', mid=False)} |")
    add("")

    unl = rep.get("unlogged")
    add("### Unlogged usage (not included above)")
    add("")
    if unl:
        u = unl["unlogged"]
        add(f"Claude Code's own session totals are available for {unl['sessions_with_totals']} of "
            f"{unl['sessions_total']} sessions. In those, transcripts account for "
            f"**{fmt.pct(unl['coverage_energy_mid'])}** of the estimated energy (mid). The remainder is estimated at "
            f"**{fmt.interval(_iv(u['energy_wh']), 'Wh', mid=False)}** / "
            f"**{fmt.interval(_iv(u['co2e_g']), 'g', 'CO2e', mid=False)}** "
            f"(background calls such as title generation, compaction, side queries).")
        for m, d in unl["by_model"].items():
            add(f"- `{m}`: {fmt.tokens(sum(d['tokens'].values()))} tokens → {fmt.interval(_iv(d['energy_wh']), 'Wh', mid=False)}")
    else:
        add("No session totals recorded, so unlogged usage cannot be quantified. The figures above cover transcript-logged "
            "model calls only and are a lower bound on actual usage.")
    add("")

    if rep["not_estimated"]:
        add("### Recorded but not estimated")
        add("")
        for k, v in rep["not_estimated"].items():
            add(f"- {k.replace('_', ' ')}: {v:,}")
        if rep["not_estimated"].get("messages_with_advisor"):
            add("")
            add("Advisor calls (a second model consulted during a response) have no token usage in transcripts, "
                "so they are neither in the estimate nor separable in the unlogged-usage line. This can be a "
                "large gap; see METHODOLOGY §3.")
        add("")

    params = rep["parameters"]
    add("### Assumptions")
    add("")
    add(f"- Coefficients `{rep['coefficients_version']}` · pegada {rep['pegada_version']}")
    for n, label in (("pue", "PUE"), ("grid_intensity", "Grid intensity"), ("embodied", "Embodied")):
        pv = params[n]
        if n == "embodied" and rep.get("embodied_per_family"):
            add("- Embodied: per model family, derived from EcoLogits (see `/pegada coefficients`)")
            continue
        if pv["low"] == pv["high"]:
            add(f"- {label}: {fmt.sig(pv['mid'])} {pv['unit']} [{pv['status']}]")
        else:
            add(f"- {label}: {fmt.sig(pv['low'])}–{fmt.sig(pv['high'])} (mid {fmt.sig(pv['mid'])}) {pv['unit']} [{pv['status']}]")
    add(f"- Method, sources and limitations: {METHODOLOGY_URL}")
    for w in rep["warnings"]:
        add(f"- ⚠️ {w}")
    return "\n".join(L)


def _shields_escape(s: str) -> str:
    return quote(s.replace("-", "--").replace("_", "__"), safe="")


def badge(rep: Dict[str, Any], label: str = "AI coding CO2e", link: str = METHODOLOGY_URL, color: str = "4c8c4a") -> str:
    co2 = _iv(rep["total"]["co2e_g"])
    message = fmt.interval(co2, "g", mid=False)
    if rep["uses_placeholders"]:
        message += " (placeholder)"
    url = f"https://img.shields.io/badge/{_shields_escape(label)}-{_shields_escape(message)}-{color}"
    return f"[![{label}: {message}]({url})]({link})"


def render_coefficients(est: Estimator) -> str:
    cs = est.coefficients
    trip = lambda i: f"{fmt.sig(i.low)} / {fmt.sig(i.mid)} / {fmt.sig(i.high)}" if i.low != i.high else fmt.sig(i.mid)
    L = [f"## Coefficients `{cs.version}`", "", f"Source file: `{cs.path}`", "",
         "Token coefficients: Wh (IT) per 1M tokens · per response: Wh (IT) · embodied: gCO2e per kWh IT. "
         "Values are low / mid / high.", "",
         "| Family | Prefill | Cache read | Decode | Per response | Embodied | Status |",
         "|---|---|---|---|---|---|---|"]
    for f in cs.families + [cs.fallback]:
        emb = trip(f.embodied) if f.embodied is not None else "(global)"
        name = f.id + (" *" if f.approximation else "")
        L.append(f"| {name} | {trip(f.e_prefill)} | {trip(f.e_cache)} | {trip(f.e_decode)} | "
                 f"{trip(f.e_request)} | {emb} | {f.status} |")
    approx = [f for f in cs.families + [cs.fallback] if f.approximation]
    if approx:
        L += ["", "\\* " + " ".join(sorted({f.approximation for f in approx}))]
    sources = sorted({f.source for f in cs.families + [cs.fallback] if f.source and "Envelope" not in f.source})
    L += ["", "Sources:", ""] + [f"- {s}" for s in sources]
    L += ["", "## Parameters", ""]
    for n in Parameters.NAMES:
        p = getattr(est.parameters, n)
        L.append(f"- **{n}** = {trip(p.value)} {p.unit} [{p.status}]: {p.source}")
    return "\n".join(L)


def impact_line(impact: Impact) -> str:
    """Compact form for the status line."""
    e, c = impact.energy_wh, impact.co2e_g
    return f"🌱 {fmt.interval(c, 'g', 'CO2e', mid=False)} · {fmt.interval(e, 'Wh', mid=False)}"
