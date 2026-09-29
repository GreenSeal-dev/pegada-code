import json
import os
import unittest

from helpers import TempEnvTestCase

from pegada.coefficients import CoefficientSet, Family, load_coefficients, load_parameters
from pegada.estimator import Estimator
from pegada.interval import Interval
from pegada.records import UsageRecord


def fam(fid, patterns, prefill, cache, decode):
    return Family(fid, tuple(patterns), Interval.parse(prefill), Interval.parse(cache), Interval.parse(decode), "TEST")


def rec(model, i=0, cw=0, cr=0, o=0, mid="m"):
    return UsageRecord(msg_id=mid, agent="t", session_id="s", timestamp="", model=model,
                       input=i, cache_write=cw, cache_read=cr, output=o)


COEFFS = CoefficientSet(
    version="test",
    families=[fam("small", ["small-*"], [1, 2, 4], [0.1, 0.2, 0.4], [10, 20, 40])],
    fallback=fam("unknown", [], [1, 5, 10], [0.1, 0.5, 1], [10, 50, 100]),
)


class IntervalTest(unittest.TestCase):
    def test_parse_forms(self):
        self.assertEqual(Interval.parse(3), Interval(3, 3, 3))
        self.assertEqual(Interval.parse([1, 2, 3]), Interval(1, 2, 3))
        self.assertEqual(Interval.parse({"low": 1, "mid": 2, "high": 3, "source": "x"}), Interval(1, 2, 3))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            Interval(2, 1, 3)
        with self.assertRaises(ValueError):
            Interval(-1, 0, 1)

    def test_arithmetic(self):
        a = Interval(1, 2, 3) * Interval(2, 3, 4) + Interval(1, 1, 1)
        self.assertEqual(a, Interval(3, 7, 13))


class EstimatorTest(TempEnvTestCase):
    def params(self, **overrides):
        base = {"pue": 1.5, "grid_intensity": 400, "embodied": 100}
        base.update(overrides)
        return load_parameters(base)

    def test_formula(self):
        est = Estimator(COEFFS, self.params())
        fp = est.footprint([rec("small-1", i=1_000_000, cw=1_000_000, cr=10_000_000, o=1_000_000)])
        # IT energy (Wh): prefill 2·(1M+1M)/1M + cache 0.2·10 + decode 20·1 = 4 + 2 + 20 = 26 (mid)
        self.assertAlmostEqual(fp.it_energy_wh.mid, 26)
        self.assertAlmostEqual(fp.it_energy_wh.low, 2 + 1 + 10)
        self.assertAlmostEqual(fp.it_energy_wh.high, 8 + 4 + 40)
        imp = est.impact(fp)
        self.assertAlmostEqual(imp.energy_wh.mid, 39)  # × PUE 1.5
        self.assertAlmostEqual(imp.co2e_operational_g.mid, 39 * 400 / 1000)
        self.assertAlmostEqual(imp.co2e_embodied_g.mid, 26 * 100 / 1000)  # on IT energy, not facility energy
        self.assertAlmostEqual(imp.co2e_g.mid, 15.6 + 2.6)

    def test_intervals_are_ordered(self):
        est = Estimator(load_coefficients(), load_parameters())
        imp = est.impact(est.footprint([rec(m, 5, 500, 50_000, 300, mid=m) for m in
                                        ("claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5", "x")]))
        for iv in (imp.energy_wh, imp.co2e_g):
            self.assertLess(iv.low, iv.mid)
            self.assertLess(iv.mid, iv.high)

    def test_unknown_model_uses_fallback_and_is_reported(self):
        est = Estimator(COEFFS, self.params())
        fp = est.footprint([rec("mystery", o=1_000_000)])
        self.assertEqual(est.unknown_models, {"mystery"})
        self.assertAlmostEqual(fp.it_energy_wh.mid, 50)

    def test_bundled_families_match_real_model_ids(self):
        cs = load_coefficients()
        for model, fid in [("claude-opus-5-5", "claude-opus"), ("claude-sonnet-5", "claude-sonnet"),
                           ("claude-haiku-4-5-20251001", "claude-haiku"),
                           ("us.anthropic.claude-sonnet-4-20250514-v1:0", "claude-sonnet"),
                           ("gpt-5", "unknown")]:
            self.assertEqual(cs.lookup(model)[0].id, fid, model)

    def test_bundled_coefficients_are_marked_placeholder(self):
        est = Estimator(load_coefficients(), load_parameters())
        est.footprint([rec("claude-sonnet-5", o=1)])
        self.assertTrue(est.uses_placeholders)

    def test_parameter_override_from_config(self):
        p = load_parameters({"grid_intensity": {"low": 20, "mid": 30, "high": 60, "source": "my region"}})
        self.assertEqual(p.grid_intensity.value, Interval(20, 30, 60))
        self.assertEqual(p.grid_intensity.status, "USER")
        self.assertEqual(p.grid_intensity.source, "my region")

    def test_coefficients_file_env_override(self):
        path = os.path.join(self.tmp, "c.json")
        with open(path, "w") as fh:
            json.dump({"coefficients_version": "calibrated-1", "families": [],
                       "fallback": {"id": "f", "e_prefill": 1, "e_cache": 1, "e_decode": 1, "status": "MEASURED"}}, fh)
        os.environ["PEGADA_COEFFICIENTS"] = path
        self.assertEqual(load_coefficients().version, "calibrated-1")


if __name__ == "__main__":
    unittest.main()
