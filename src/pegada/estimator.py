"""Token counts → energy (Wh) and emissions (gCO2e), as intervals.

    E_IT   = e_prefill·(input + cache_write) + e_cache·cache_read + e_decode·output
           + e_request·responses
    E      = PUE · E_IT
    CO2e   = E · grid_intensity + E_IT · embodied

Token coefficients are in Wh per 1M tokens and e_request in Wh per model
response; grid intensity in gCO2e/kWh; embodied in gCO2e per kWh of IT energy
(per model family, unless the project config overrides it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Hashable, Iterable, Set

from pegada.coefficients import CoefficientSet, Family, Parameters
from pegada.interval import ZERO, Interval
from pegada.records import TOKEN_CLASSES, UsageRecord

# Energy terms: the four token classes plus the fixed per-response overhead.
ENERGY_TERMS = TOKEN_CLASSES + ("request",)


def class_coefficient(family: Family, token_class: str) -> Interval:
    if token_class in ("input", "cache_write"):
        return family.e_prefill
    if token_class == "cache_read":
        return family.e_cache
    return family.e_decode


@dataclass
class Footprint:
    """Accumulated tokens, IT energy (per energy term) and embodied emissions."""

    messages: int = 0
    tokens: Dict[str, int] = field(default_factory=lambda: {c: 0 for c in TOKEN_CLASSES})
    it_energy: Dict[str, Interval] = field(default_factory=lambda: {t: ZERO for t in ENERGY_TERMS})
    embodied_g: Interval = ZERO

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens.values())

    @property
    def it_energy_wh(self) -> Interval:
        total = ZERO
        for t in ENERGY_TERMS:
            total = total + self.it_energy[t]
        return total

    def add_counts(self, family: Family, counts: Dict[str, int], messages: int = 1,
                   embodied: Interval = ZERO) -> None:
        """Add ``messages`` responses with the given token ``counts``. ``embodied``
        is the factor (gCO2e per kWh IT) applied to the energy added here."""
        self.messages += messages
        added = ZERO
        for c in TOKEN_CLASSES:
            n = counts.get(c, 0)
            if n:
                self.tokens[c] += n
                e = class_coefficient(family, c) * (n / 1e6)
                self.it_energy[c] = self.it_energy[c] + e
                added = added + e
        if messages:
            e = family.e_request * messages
            self.it_energy["request"] = self.it_energy["request"] + e
            added = added + e
        self.embodied_g = self.embodied_g + added * embodied / 1000.0


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
        self.approximated: Dict[str, Family] = {}  # model -> family whose data is used
        self.families_used: Set[str] = set()

    def family(self, model: str) -> Family:
        fam, is_fallback = self.coefficients.lookup(model)
        if is_fallback:
            self.unknown_models.add(model)
        elif fam.approximation:
            self.approximated[model] = fam
        self.families_used.add(fam.id)
        return fam

    def embodied_factor(self, family: Family) -> Interval:
        """Per-family factor, unless the project config sets one for all models."""
        p = self.parameters.embodied
        if family.embodied is None or p.status == "USER":
            return p.value
        return family.embodied

    @property
    def uses_placeholders(self) -> bool:
        fams = {f.id: f for f in self.coefficients.families + [self.coefficients.fallback]}
        used = [fams[i] for i in self.families_used if i in fams]
        return any(f.is_placeholder for f in used) or any(
            getattr(self.parameters, n).is_placeholder for n in ("pue", "grid_intensity")
        ) or (self.parameters.embodied.is_placeholder and any(
            f.embodied is None or self.parameters.embodied.status == "USER" for f in used))

    def add(self, fp: Footprint, model: str, counts: Dict[str, int], messages: int = 1) -> None:
        fam = self.family(model)
        fp.add_counts(fam, counts, messages, self.embodied_factor(fam))

    def footprint(self, records: Iterable[UsageRecord]) -> Footprint:
        fp = Footprint()
        for r in records:
            self.add(fp, r.model, {c: getattr(r, c) for c in TOKEN_CLASSES})
        return fp

    def group(self, records: Iterable[UsageRecord], key: Callable[[UsageRecord], Hashable]) -> Dict[Hashable, Footprint]:
        out: Dict[Hashable, Footprint] = {}
        for r in records:
            self.add(out.setdefault(key(r), Footprint()), r.model, {c: getattr(r, c) for c in TOKEN_CLASSES})
        return out

    def impact(self, fp: Footprint) -> Impact:
        p = self.parameters
        energy = fp.it_energy_wh * p.pue.value
        return Impact(
            energy_wh=energy,
            co2e_operational_g=energy * p.grid_intensity.value / 1000.0,
            co2e_embodied_g=fp.embodied_g,
        )
