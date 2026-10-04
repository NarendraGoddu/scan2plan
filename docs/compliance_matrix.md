# Compliance matrix

Every requirement, what was actually delivered, and the file that proves it. Status
values are **Met**, **Partial**, **Prototype**, or **Not done**. Nothing is listed as
Met on the strength of intent.

Last updated 2026-10-04. All figures reproducible with the commands in
[README](../README.md).

---

## 1. Input tiers

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 1.1 | Depth archive ingest, with correct scale | **Met** | `src/scan2plan/ingest.py`, `tests/test_ingest.py` | Scale 0.9997 mm/unit. Focal is full-res (~1598); `depth_intrinsics()` divides by `DEPTH_DECIMATION=7.5`. Depth is axial, not slant range. |
| 1.2 | Photo ingest | **Met** | `scripts/depth_capture.py` | 238 photos + 11 videos. No EXIF and no GPS survive in this capture; both are reported as absent rather than defaulted. |
| 1.3 | Video ingest | **Met** | `scripts/depth_capture.py` | 10 frames sampled per video on a uniform time grid, 110 total. |
| 1.4 | Multi-room / whole-property | **Not done** | `src/scan2plan/report.py` `LIMITATIONS` | One capture yields one room. Rooms are not stitched. |

## 2. Reconstruction

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 2.1 | Floor plane segmentation | **Met** | `src/scan2plan/segment.py`, `runs/real_baseline.json` | Floor rms 9.7 mm on `single_room`. Repeatability 0.6 mm across disjoint halves. |
| 2.2 | Ceiling plane + height | **Met** | `runs/real_baseline.json` | 2.3835 m, 1σ 9 mm. The one quantity that behaves on real archives. |
| 2.3 | Vertical wall segmentation | **Partial** | `scripts/diag_wall_planes.py`, `scripts/diag_boundary_rule.py` | `classify_planes` labelled all 84 planes in `with_ceiling` as walls. `select_room_boundary` now keeps, per orientation group, the outermost pair among walls the camera path does not straddle *centrally*. Narrowed, not closed: real archives still yield 6–9 walls where a rectangle has 4, and disjoint halves disagree by up to 6.1 m. |
| 2.4 | Room polygon from walls | **Met** on synthetic, **fails** on real without the prior | `src/scan2plan/plan.py`, `tests/test_room_boundary.py`, `tests/test_rectangular_prior.py`, `runs/real_baseline_rect.json` | Synthetic, prior OFF (the only honest setting, since those rooms are rectangular by construction): 4/4 walls in all 5 rooms, span median 4.12 mm / max 12.46 mm, ceiling 3.01 / 3.81 mm, area 0.21 % / 0.46 %. Real archives, prior OFF: 9 / 6 / 8 walls where a rectangle has 4, edges down to 0.166 m. Real archives, prior ON: **4 / 4 / 4** walls and 4 vertices, axes chosen at 89.3° / 88.5° / 88.6° from support alone. The prior assumes a right-angled room; it is opt-in and off by default so the benchmark stays non-circular. |
| 2.5 | Wall length, floor area, perimeter | **Met** | `runs/synthetic_benchmark.json` | Propagated 1σ per wall and for area. |

## 3. Outputs

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 3.1 | Dimensioned floor plan, machine readable | **Met** | `src/scan2plan/report.py`, `scan2plan-plan` | JSON with per-wall 1σ. |
| 3.2 | Dimensioned floor plan, visual | **Met** | `examples/syn_nominal.svg`, `examples/field_site/*.svg` | ASCII-only glyphs, 1 m scale bar, no north arrow (the frame has no compass direction). |
| 3.3 | Per-wall uncertainty | **Met** | `runs/synthetic_benchmark.json` | `rms / sqrt(support)`. |
| 3.4 | Openings: doors, windows | **Prototype** | `src/scan2plan/openings.py`, `scripts/analyse_openings_and_damage.py`, `tests/test_openings.py`, `runs/openings_and_damage.json` | Mask polarity was inverted: a doorway is *farther* than the wall around it, so the old "largest near region" search selected the wall and returned the whole frame — 1.93 m for a door taped at 0.80 m. Fixed. Three-room door **heights** now land −17 mm, +8 mm and −217 mm against a 20 mm gate, so 2 of 3 pass. **Widths still fail**, with a systematic +96 to +119 mm bias: the mask includes the door reveal, and one pair of copies of the same door disagrees by 430 mm, which is flagged rather than averaged away. |
| 3.5 | Room dimensions, real site | **Met** via tape | `examples/field_site/`, `scripts/report_field_capture.py` | Geometry is **measured**, not reconstructed. Every document carries `geometry_is_measured_not_reconstructed: true`. The four-sided room model is additionally corroborated by the capture itself — each room folder holds walls 1–4 and nothing else — recorded per room as `topology_corroboration`, so the shape does not rest on the tape alone. The labels say nothing about dimensions. |

## 4. Damage

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 4.1 | Damage detection from depth | **Not met** | `src/scan2plan/damage.py`, `scripts/benchmark_damage.py` | Plane-residual detector, scored against exact synthetic truth: **0 of 4 damage cases detected, 19 false positives** across four runs including three undamaged rooms. Kept as a measured negative result; nothing in the product path calls it. |
| 4.2 | Validation of damage detection | **Not met** | `scripts/benchmark_damage.py`, `out/damage_benchmark.json` | Validation is now against synthetic ground truth rather than two photographs, so the result is trustworthy: 4 mm crack, 12 mm crack, 30 mm spall and a breach all missed. Two hard limits measured: depth pixel footprint is **11.7 mm at 2.5 m** (so the 4 mm and 12 mm cracks are 0.68 and 1.28 px wide), and segmentation reports wall rms **18-24 mm** on clean walls, which swamps any groove shallower than that. |
| 4.3 | Damage classes / severity | **Partial** | `scan2plan.damage.severity_for`, `synth.damage_rooms` | Taxonomy exists and is exercised: crack, spall and breach are rendered by the generator with exact length, width, depth and world centre, and severity is banded on measured depth. Unvalidated in practice, because the detector it would classify does not work. Band thresholds are stated as conventions, not measurements. |

## 5. Accuracy and verification

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 5.1 | Verified benchmark | **Met** | `runs/synthetic_benchmark.json`, `scripts/verify_synth.py` | 8 self-checks, all passing. Exact truth, 5 rooms. |
| 5.2 | Accuracy gates | **Met** | `scripts/benchmark_synthetic.py` | Span 15 mm, ceiling 15 mm, area 1% gates. Achieved 12.5 / 3.8 mm / 0.46%. |
| 5.3 | Independent real-world reference | **Met** | `data/field_ground_truth.json` | Tape, 3 rooms, plus diagonals. Used for validation only, never to calibrate. |
| 5.4 | Real-world accuracy | **Not met** | `runs/real_baseline.json`, `runs/real_baseline_rect.json` | The LiDAR tier does not reproduce real rooms and is not claimed to. Default path gives 9 / 6 / 8 walls with 0.166 m slivers; the opt-in rectangular prior gives 4 / 4 / 4 walls, but floor-height repeatability across split halves was 0.6 / 189.5 / 801.0 mm; requiring the floor to sit 1.00-2.10 m below the camera (the HorizonNet constraint that floor and ceiling are layout parameters, not free plane fits) brings it to 0.6 / 60.4 / 89.5 mm. Weighting the pooled point refit by support (built as an explicit covariance, not by scaling rows before an SVD, which conditioning turned into −13.5 m) improves it again to 0.8 / 21.2 / 68.4 mm. Still above the 10 mm gate, now for want of observations rather than a wrong surface. Areas (21.6 / 95.6 / 105.7 m²) also exceed the trajectory footprints (17.3 / 73.8 / 75.7 m²) by more than reach alone explains. The split-half *wall* metric was itself defective: it bucketed walls by normal direction modulo 180 degrees, which folds opposite walls together (one bucket on `single_room` averaged offsets -1.108 m and +3.569 m into +1.230 m) and then let each half average a different set of walls into the same key, so its 6071 / 5529 mm output compared surfaces that were never the same wall. Replaced with sign-aware greedy plane matching, the halves agree on 1 of 8 walls on `single_room`, 0 of 6 on `floor_only`, and 1 of 7 on `with_ceiling`. That is a worse finding than the bogus one, not a better one: the halves disagree about *which walls exist*, not merely by how far apart they are. |
| 5.5 | Photo-tier accuracy | **Not met** | `runs/wall_measurements.json`, `runs/wall_consensus.json`, `scripts/wall_perspective_consensus.py` | Median wall error 0.55 m; room area −74% / −50% / +4%. Plane fits are good (rms 5-49 mm); the extents are not. Root cause quantified: only **2 of 12** labelled walls have any photograph within 40° of fronto-parallel — the rest were shot at ≥ 68° incidence, where monocular depth has almost no signal across a wall's width. |
| 5.6 | Tests | **Met** | `python -m pytest tests -q` and `python -m ruff check .` | 148 passing, lint clean. mypy cannot run on this machine (Application Control blocks its DLL), so annotations are unverified by a type checker. |

## 6. Comparison against a commercial tool

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 6.1 | Head-to-head vs Magicplan | **Not done** | `docs/site_day.pdf` outcome box | Magicplan has no Android app and no iOS device was available. No scan exists for any room. Nothing was substituted and no comparison is claimed. |
| 6.2 | Substitute: pipeline vs tape reference | **Met** | `docs/final_report.md` §6 | Same metrics an assessor wants from a head-to-head — accuracy, wall count, time, cost, effort, failure modes — measured against the steel tape instead. This is **not** a Magicplan comparison and is not presented as one. |

## 7. Capture route

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 7.1 | Documented capture protocol | **Met** | `docs/capture_protocol.pdf` | 7-page booklet, route selection and photo counts. |
| 7.2 | On-site runbook | **Met** | `docs/site_day.pdf` | Carried on the day. Outcome box records what was and was not achieved. |
| 7.3 | Site visit executed | **Met** | `data/field_ground_truth.json`, `capture/` | 3 rooms, 238 photos, 11 videos, tape dimensions. |
| 7.4 | Scale reference in frame | **Not met** | `data/field_ground_truth.json` `known_gaps` | No 1 m marker in any frame. This single omission is what caps the photo tier at relative depth. |
| 7.5 | iOS build / TestFlight | **Not done** | — | No macOS or iOS device. Protocol documented instead. |

## 8. Process

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 8.1 | Reproducible install | **Met** | `pyproject.toml`, verified from a fresh clone | Core deps are numpy / pillow / scipy only. |
| 8.2 | Runnable without setup | **Met** | `data/demo_room.zip` | 9.11 MB, exact truth, runs from a clean clone with zero setup. |
| 8.3 | CLI | **Met** | `scan2plan-plan` | |
| 8.4 | Optional heavy deps kept out of core | **Met** | `[depth]` extra | ONNX Runtime (~15 MB) instead of a multi-GB torch install; weights fetched by script, gitignored. |
| 8.5 | Failure analysis | **Met** | `docs/final_report.md` §5, §6, `CHECKPOINTS.md`, git log | Six convention bugs plus one wall-selection regression I introduced, each with the measurement that revealed it. |

---

## Summary

Counted from the tables above, by the status column:

| Status | Count |
|---|---|
| Met | 24 |
| Partial | 2 |
| Prototype | 1 |
| Not met / Not done | 8 |
| **Total requirements** | **35** |

Two of the 24 are qualified and should not be read as clean passes: **2.4** (room
polygon from walls) is Met on synthetic data and fails on real archives, and
**6.2** (tape-reference benchmark) is a substitute for a comparison that was not
performed. Strip those two and it is 22 unqualified.

The two results that matter most, stated plainly:

1. **The automatic pipeline is verified on synthetic rooms and fails on real ones.**
   Span error on synthetic is 12.5 mm worst case, 4/4 walls in all five rooms. On
   real archives the same code reports 6–9 walls where a rectangle has 4, and
   splitting an archive in half yields walls that disagree by up to 6.1 m. Those
   archives ship with no ground truth, so this is stated as self-inconsistency
   rather than as an accuracy figure.
2. **The photo tier is relative, not metric, because no frame contains a scale
   reference.** This is a capture-protocol omission, not a modelling choice, and it
   is the highest-value thing to fix on the next site visit.

One methodological note, because it is the strongest evidence in the project: the
synthetic benchmark could not catch the *original* real-data bug — synthetic rooms
are empty boxes, where every non-horizontal plane genuinely is a wall — but it did
catch the *second* one. A wall-selection rule written to fix the first bug assumed
a room boundary always has the camera path on one side of it, which is false when
the operator walks through a doorway. On the 6.2 × 3.1 m room the path spans 4.53 m
along a 3.11 m wall pair, so both side walls were discarded and the room collapsed
to 0.000 m² with a NaN span. Nothing in the real-data numbers looked wrong, because
there are no real-data truth values to compare against — only the benchmark with
exact truth exposed it.
