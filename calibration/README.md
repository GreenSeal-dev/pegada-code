# Calibration

`src/pegada/data/coefficients.json` is generated. Do not edit it by hand; change the inputs here and regenerate.

```sh
python3 -m venv .venv-calib
.venv-calib/bin/pip install -r calibration/requirements.txt   # ecologits==0.11.1 (calibration only; pegada itself has no dependencies)
.venv-calib/bin/python calibration/derive_coefficients.py          # regenerate
.venv-calib/bin/python calibration/derive_coefficients.py --check  # verify the committed file is reproducible
```

- `derive_coefficients.py` holds all inputs (first-principles constants with their sources, and the family ↔
  EcoLogits model mapping), and calls EcoLogits' own impact functions for decode, per-response and embodied values.
- `data/ecologits-models-2b36303.json` is EcoLogits' model repository at commit
  `2b3630389e31414155328d1e6d0a91faa36e9409`, vendored and checked against its SHA-256. The 0.11.1 release
  predates the Claude 5 models, and the energy code at this commit is identical to 0.11.1.
- To follow a new EcoLogits release: bump `requirements.txt`, replace the vendored model file (and its commit
  and hash in the script), add or re-map families, regenerate, and bump `COEFFICIENTS_VERSION`.

Method and rationale: [METHODOLOGY.md §4](../METHODOLOGY.md#4-energy-and-emissions-model).
