"""Versioned per-model-family energy coefficients and infrastructure parameters."""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pegada.interval import Interval

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_COEFFICIENTS = os.path.join(DATA_DIR, "coefficients.json")
DEFAULT_PARAMETERS = os.path.join(DATA_DIR, "parameters.json")

PLACEHOLDER = "PLACEHOLDER"


@dataclass(frozen=True)
class Family:
    id: str
    match: Tuple[str, ...]
    e_prefill: Interval
    e_cache: Interval
    e_decode: Interval
    status: str = ""
    source: str = ""
    notes: str = ""

    @property
    def is_placeholder(self) -> bool:
        return self.status.upper() == PLACEHOLDER

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Family":
        return cls(
            id=str(d["id"]),
            match=tuple(str(p).lower() for p in d.get("match", [])),
            e_prefill=Interval.parse(d["e_prefill"]),
            e_cache=Interval.parse(d["e_cache"]),
            e_decode=Interval.parse(d["e_decode"]),
            status=str(d.get("status", "")),
            source=str(d.get("source", "")),
            notes=str(d.get("notes", "")),
        )


@dataclass
class CoefficientSet:
    version: str
    families: List[Family]
    fallback: Family
    path: str = ""
    _cache: Dict[str, Tuple[Family, bool]] = field(default_factory=dict, repr=False)

    def lookup(self, model: str) -> Tuple[Family, bool]:
        """Return ``(family, is_fallback)`` for a model id."""
        hit = self._cache.get(model)
        if hit is None:
            m = model.lower()
            hit = next(
                ((f, False) for f in self.families if any(fnmatch.fnmatchcase(m, p) for p in f.match)),
                (self.fallback, True),
            )
            self._cache[model] = hit
        return hit


def load_coefficients(path: Optional[str] = None) -> CoefficientSet:
    path = path or os.environ.get("PEGADA_COEFFICIENTS") or DEFAULT_COEFFICIENTS
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return CoefficientSet(
        version=str(d.get("coefficients_version", "unversioned")),
        families=[Family.from_dict(f) for f in d.get("families", [])],
        fallback=Family.from_dict(d["fallback"]),
        path=path,
    )


@dataclass(frozen=True)
class Parameter:
    value: Interval
    unit: str = ""
    status: str = ""
    source: str = ""

    @property
    def is_placeholder(self) -> bool:
        return self.status.upper() == PLACEHOLDER


@dataclass(frozen=True)
class Parameters:
    pue: Parameter
    grid_intensity: Parameter  # gCO2e per kWh
    embodied: Parameter  # gCO2e per kWh of IT energy

    NAMES = ("pue", "grid_intensity", "embodied")


def load_parameters(overrides: Optional[Dict[str, Any]] = None, path: Optional[str] = None) -> Parameters:
    """Defaults from ``parameters.json``; ``overrides`` (e.g. a project config)
    may replace any parameter with a number or a triple, optionally with a
    ``source`` string: ``{"grid_intensity": {"low": 20, "mid": 30, "high": 60,
    "source": "..."}}``."""
    with open(path or DEFAULT_PARAMETERS, encoding="utf-8") as fh:
        d = json.load(fh)
    overrides = overrides or {}
    out = {}
    for name in Parameters.NAMES:
        base = d[name]
        param = Parameter(Interval.parse(base), base.get("unit", ""), base.get("status", ""), base.get("source", ""))
        if name in overrides:
            ov = overrides[name]
            src = ov.get("source", "") if isinstance(ov, dict) else ""
            param = Parameter(Interval.parse(ov), param.unit, "USER", src or "set in project config")
        out[name] = param
    return Parameters(**out)
