"""Human-readable numbers. Intervals are always shown as ranges, never as a single value."""

from __future__ import annotations

import math

from pegada.interval import Interval

_SCALES = {
    "Wh": [(1e6, "MWh"), (1e3, "kWh"), (1, "Wh")],
    "g": [(1e6, "t"), (1e3, "kg"), (1, "g")],
}


def sig(x: float, digits: int = 3) -> str:
    if x == 0:
        return "0"
    if abs(x) >= 10 ** (digits - 1):
        return f"{x:,.0f}"
    decimals = max(0, digits - 1 - int(math.floor(math.log10(abs(x)))))
    return f"{x:.{decimals}f}"


def _scale(iv: Interval, base: str):
    for factor, unit in _SCALES[base]:
        if iv.high >= factor:
            return factor, unit
    return 1, base


def interval(iv: Interval, base: str, suffix: str = "", mid: bool = True) -> str:
    """``interval(Interval(1200, 2400, 4800), "Wh")`` → ``1.20–4.80 kWh (mid 2.40)``."""
    factor, unit = _scale(iv, base)
    s = f"{sig(iv.low / factor)}–{sig(iv.high / factor)} {unit}{suffix}"
    if mid:
        s += f" (mid {sig(iv.mid / factor)})"
    return s


def tokens(n: int) -> str:
    for factor, unit in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= factor:
            return f"{sig(n / factor)}{unit}"
    return str(n)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%" if x < 0.995 or x == 1 else f"{x * 100:.2f}%"
