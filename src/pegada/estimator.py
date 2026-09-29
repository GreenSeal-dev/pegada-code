"""Token counts → energy (Wh) and emissions (gCO2e), as intervals.

    E_IT   = e_prefill·(input + cache_write) + e_cache·cache_read + e_decode·output
    E      = PUE · E_IT
    CO2e   = E · grid_intensity + E_IT · embodied

Coefficients are in Wh per 1M tokens; grid intensity in gCO2e/kWh; embodied in
gCO2e per kWh of IT energy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Hashable, Iterable, Set

from pegada.coefficients import CoefficientSet, Family, Parameters
from pegada.interval import ZERO, Interval
from pegada.records import TOKEN_CLASSES, UsageRecord


def class_coefficient(family: Family, token_class: str) -> Interval:
    if token_class in ("input", "cache_write"):
        return family.e_prefill
    if token_class == "cache_read":
        return family.e_cache
    return family.e_decode


@dataclass
class Footprint:
    """Accumulated tokens and IT energy (per token class) of a set of records."""

    messages: int = 0
    tokens: Dict[str, int] = field(default_factory=lambda: {c: 0 for c in TOKEN_CLASSES})
    it_energy: Dict[str, Interval] = field(default_factory=lambda: {c: ZERO for c in TOKEN_CLASSES})

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens.values())

    @property
    def it_energy_wh(self) -> Interval:
        total = ZERO
        for c in TOKEN_CLASSES:
            total = total + self.it_energy[c]
        return total

    def add_counts(self, family: Family, counts: Dict[str, int], messages: int = 1) -> None:
        self.messages += messages
        for c in TOKEN_CLASSES:
            n = counts.get(c, 0)
            if n:
                self.tokens[c] += n
                self.it_energy[c] = self.it_energy[c] + class_coefficient(family, c) * (n / 1e6)


@dataclass(frozen=True)
class Impact:
    energy_wh: Interval  # facility energy (PUE applied)
    co2e_operational_g: Interval
    co2e_embodied_g: Interval

    @property
    def co2e_g(self) -> Interval:
        return self.co2e_operational_g + self.co2e_embodied_g

    def to_dict(self) -> dict:
        return {
            "energy_wh": self.energy_wh.to_dict(),
            "co2e_g": self.co2e_g.to_dict(),
            "co2e_operational_g": self.co2e_operational_g.to_dict(),
            "co2e_embodied_g": self.co2e_embodied_g.to_dict(),
        }


class Estimator:
    def __init__(self, coefficients: CoefficientSet, parameters: Parameters):
        self.coefficients = coefficients
        self.parameters = parameters
        self.unknown_models: Set[str] = set()
        self.families_used: Set[str] = set()

    def family(self, model: str) -> Family:
        fam, is_fallback = self.coefficients.lookup(model)
        if is_fallback:
            self.unknown_models.add(model)
        self.families_used.add(fam.id)
        return fam

    @property
    def uses_placeholders(self) -> bool:
        fams = {f.id: f for f in self.coefficients.families + [self.coefficients.fallback]}
        return any(fams[i].is_placeholder for i in self.families_used if i in fams) or any(
            getattr(self.parameters, n).is_placeholder for n in Parameters.NAMES
        )

    def footprint(self, records: Iterable[UsageRecord]) -> Footprint:
        fp = Footprint()
        for r in records:
            fp.add_counts(self.family(r.model), {c: getattr(r, c) for c in TOKEN_CLASSES})
        return fp

    def group(self, records: Iterable[UsageRecord], key: Callable[[UsageRecord], Hashable]) -> Dict[Hashable, Footprint]:
        out: Dict[Hashable, Footprint] = {}
        for r in records:
            fp = out.setdefault(key(r), Footprint())
            fp.add_counts(self.family(r.model), {c: getattr(r, c) for c in TOKEN_CLASSES})
        return out

    def impact(self, fp: Footprint) -> Impact:
        p = self.parameters
        it = fp.it_energy_wh
        energy = it * p.pue.value
        return Impact(
            energy_wh=energy,
            co2e_operational_g=energy * p.grid_intensity.value / 1000.0,
            co2e_embodied_g=it * p.embodied.value / 1000.0,
        )
