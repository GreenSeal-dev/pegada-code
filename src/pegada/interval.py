"""Low/mid/high intervals.

pegada propagates uncertainty with interval arithmetic on non-negative
quantities: the low result combines every low input, the high result every
high input, and mid every mid input. This gives a bounding envelope, not a
statistical confidence interval (see METHODOLOGY.md, "Uncertainty").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

Number = Union[int, float]


@dataclass(frozen=True)
class Interval:
    low: float
    mid: float
    high: float

    def __post_init__(self) -> None:
        if self.low < 0:
            raise ValueError(f"interval must be non-negative: {self}")
        if not (self.low <= self.mid <= self.high):
            raise ValueError(f"interval must satisfy low <= mid <= high: {self}")

    @classmethod
    def point(cls, v: Number) -> "Interval":
        return cls(float(v), float(v), float(v))

    @classmethod
    def parse(cls, v: Any) -> "Interval":
        """Accept a number, a ``[low, mid, high]`` list or a ``{low, mid, high}`` dict."""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return cls.point(v)
        if isinstance(v, (list, tuple)) and len(v) == 3:
            return cls(float(v[0]), float(v[1]), float(v[2]))
        if isinstance(v, dict) and {"low", "mid", "high"} <= set(v):
            return cls(float(v["low"]), float(v["mid"]), float(v["high"]))
        raise ValueError(f"cannot read an interval from {v!r}")

    def __add__(self, other: "Interval") -> "Interval":
        return Interval(self.low + other.low, self.mid + other.mid, self.high + other.high)

    def __mul__(self, other: Union["Interval", Number]) -> "Interval":
        if isinstance(other, Interval):
            return Interval(self.low * other.low, self.mid * other.mid, self.high * other.high)
        return Interval(self.low * other, self.mid * other, self.high * other)

    __rmul__ = __mul__

    def __truediv__(self, k: Number) -> "Interval":
        return Interval(self.low / k, self.mid / k, self.high / k)

    def to_dict(self) -> dict:
        return {"low": self.low, "mid": self.mid, "high": self.high}


ZERO = Interval(0.0, 0.0, 0.0)
