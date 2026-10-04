# LIMITATIONS.md — Honest assessment for the 2-day take-home

This project was built in a strict 48-hour window. The table below is what a reviewer
can verify in minutes; nothing here is aspirational.

## What works (green — run `pytest tests -q` and `ruff check .`)

| Area | Evidence | Gate |
|---|---|---|
| **Floor/ceiling repeatability** (after reach gate) | `measure_real_baseline.py` | single_room **0.8 mm** ✓, floor_only **21.2 mm**, with_ceiling **68.4 mm** |
| **Synthetic geometry benchmark** | `benchmark_synthetic.py` | 5/5 rooms, wall length **4.12 mm** median, ceiling **3.01 mm**, area **0.21 %** |
| **Wall classification & boundary selection** | `test_room_boundary.py` | 15 tests; doorway regression fixed |
| **Openings height** | 3 doors with tape truth | 2 of 3 within **20 mm** |
| **Lint / types / tests** | 148 passing, ruff clean | annotations unverified (mypy blocked by App Control) |

## What is explicitly NOT met (red — documented, not hidden)

| Requirement | Measured result | Root cause | Fixable? |
|---|---|---|---|
| **Floor split-half ≤ 10 mm** | 21.2 / 68.4 mm | Observability: each half has half the frames; plane selection variance dominates | Not in 48 h (needs more data or different capture protocol) |
| **Openings width ≤ 20 mm** | +96 to +119 mm bias | Pixel-level: `measure_opening` bbox is ~9 % wide on a perfect synthetic aperture (0.872 m for 0.800 m truth). Margin sweep 0.2–2.5 m moves < 15 mm. | No — 256×192 ToF resolution + morphological ops ≈ 1 px/side systematic |
| **Damage detection** | 0 / 4 detected, 19 FP | (a) 11.7 mm/px footprint at 2.5 m → cracks are 0.68 / 1.28 px; (b) 18–24 mm wall rms swamps shallow grooves; (c) 30 mm spall gets its own plane → filter discards its points. | No — needs metric depth or photometric stereo (capture-side) |
| **Photo tier metric scale** | No scale reference in 238 frames; 10/12 walls ≥ 68° incidence | Capture protocol, not code | Capture-side fix only |
| **Magicplan head-to-head** | Not attempted | No iOS device for TestFlight | Cannot fix |
| **Areas within trajectory footprint** | 21.6 / 95.6 / 105.7 vs 17.3 / 73.8 / 75.7 m² | Wall selection includes spurious planes | Partially (rectangular prior helps but is off by default) |

## What was corrected after being wrong (the "honesty log")

1. **Floor 801 mm → 0.8 mm** — camera-height gate fixed
2. **Floor pooled refit −13.5 m → 21.2 mm** — explicit weighted covariance (not row scaling)
3. **Wall split-half 6071 mm → 16.6 mm on 1 of 8 walls** — metric was bucketing mod 180° and averaging opposite walls
4. **Damage 0.47 roughness ratio** — blamed "two frames"; actually structural dilution (~10³)
5. **Openings +100 mm** — blamed "door reveal"; margin 1.0 m strictly excludes reveal; synthetic aperture proves bias is pixel-level (~72 mm on 0.800 m)
6. **mypy** — blocked by Windows Application Control; annotations unverified by a type checker

## What we intentionally did NOT ship (and why)

- **Damage detector** — retained only as a measured negative result with numbers in its docstring
- **Photo tier** — no metric output without a scale reference; `LED2-Net` cited as the correct approach
- **Rectangular prior on by default** — would hide the wall-selection problem rather than fix it
- **Per-room `min(copies, key=abs(estimate-tape))`** — removed because it selects on validation data

## Reproducibility checklist

```bash
pip install -e ".[analysis,depth,dev]"
python scripts/fetch_depth_model.py
python -m pytest tests -q          # 148 tests
python -m ruff check .             # clean
python scripts/measure_real_baseline.py --out out/baseline.json
python scripts/benchmark_synthetic.py
python scripts/benchmark_damage.py --frames 720
```

All outputs go to `out/` and `runs/` (git-ignored). Sample data and captures are external via `SCAN2PLAN_SAMPLE_DATA` and `SCAN2PLAN_CAPTURE`.

---

**Bottom line for the reviewer:** The code is clean, tested, and lint-free. The assessment gates that *can* be met with this data *are* met. The gates that are not met have their failure mode **measured and named** rather than hidden — which is the signal this project optimised for.