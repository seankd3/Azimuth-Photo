# Camera noise profiles (dt-noise)

> **Behavior reference awaiting V2 adoption.** Preserve measured profile data;
> do not infer that the V1 Develop integration is complete.

Profile-aware denoise for Develop, per `darktable-study/SYNTHESIS.md` P1 #6.

## What landed

- Data table: `web/features/develop/noise_profiles.json`
  - Measured Poisson–Gaussian `(a, b)` rows distilled from darktable
  - Coverage = fixture-library RAW bodies that darktable already calibrated
  - Catalog/EXIF aliases (`EOS R5m2` → `EOS R5 Mark II`, …) live in the JSON —
    adding a body is a harvest/data change, not a lookup-code change
- Lookup: `web/features/develop/noise_profiles.py`
  - Exact ISO, log-ISO interpolation, clamp outside measured range
  - `vst_parameters(...)` → mid-grey variance/σ from `V = a·x + b`
  - `nr_strength_from_vst(...)` maps σ onto existing `LuminanceSmoothing` /
    `ColorNoiseReduction` sliders (no new denoise engine)
  - Unknown cameras leave settings untouched (identical to today's defaults)
- Wired through `resolve_file_defaults` in develop render/routes (already on develop)

## Fixture cameras covered

Queried distinct RAW `camera_model` values from the azimuth-photo catalog DB.
Shipped profiles where darktable has calibration; phones / action cams fall back.

| Catalog model | Profile model | Rows |
|---------------|---------------|------|
| EOS R5 | EOS R5 | 30 |
| EOS R7 | EOS R7 | 27 |
| EOS RP | EOS RP | 30 |
| EOS R6 | EOS R6 | 33 |
| EOS R6m2 | EOS R6 Mark II | 33 |
| EOS R5m2 | EOS R5 Mark II | 30 |
| EOS Rebel T7 | EOS 2000D | 7 |
| ILCE-7RM4A | ILCE-7RM4 | 31 |
| FC3170 | FC3170 | 7 |
| (bonus) EOS R3 | EOS R3 | 31 |
| Pixel / GoPro / Leica / … | — | generic fallback |

## Non-goals honored

No new denoise algorithm, no NR UI beyond existing sliders, no profile-capture tooling.
Anscombe scene-linear VST (SYNTHESIS P1 #7) is a separate follow-up.

## Before / after renders

High-ISO fixture: `/mnt/expansion/Photos/RAWS/2024/2024-10-24/20241024-191116-7.CR3`
(Canon EOS R7, ISO 3200). Shadow-biased 512² crop.

| | Path |
|--|------|
| Before (NR off) | [examples/noise-profiles/20241024-191116-7-R7-ISO3200-before.png](examples/noise-profiles/20241024-191116-7-R7-ISO3200-before.png) |
| After (profile NR) | [examples/noise-profiles/20241024-191116-7-R7-ISO3200-after.png](examples/noise-profiles/20241024-191116-7-R7-ISO3200-after.png) |
| Diff ×8 | [examples/noise-profiles/20241024-191116-7-R7-ISO3200-diff.png](examples/noise-profiles/20241024-191116-7-R7-ISO3200-diff.png) |
| Receipt | [examples/noise-profiles/20241024-191116-7-R7-ISO3200-receipt.json](examples/noise-profiles/20241024-191116-7-R7-ISO3200-receipt.json) |

Profile applied ≈ luma 37.7 / color 21.5 (R7 ISO 3200 σ at 18% grey).

## Tests

```bash
cd web && .venv/bin/python -m unittest test_noise_profiles
# use system Node (/usr/bin/node); Cursor's bundled Node rejects --experimental-default-type
cd web && env PATH="/usr/bin:/bin:/usr/local/bin" .venv/bin/python -m unittest discover -s . -p 'test_develop_*.py'
```

Verified 2026-07-19 on this branch:

| Suite | Result |
|-------|--------|
| `test_noise_profiles` | 10/10 OK |
| `test_develop_*.py` | 170/170 OK, exit 0 |
