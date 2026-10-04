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
| 2.3 | Vertical wall segmentation | **Partial** | `scripts/diag_wall_planes.py` | `classify_planes` labelled all 84 planes in `with_ceiling` as walls. `select_room_boundary` now filters by whether a plane bounds the camera path. Narrowed, not closed: floor repeatability on `with_ceiling` is still 801 mm. |
| 2.4 | Room polygon from walls | **Met** on synthetic, **fails** on real | `src/scan2plan/plan.py` | Synthetic: 4/4 walls, span ≤12.5 mm, area ≤0.46%. Real: 1.75 m² reported against a 17.3 m² trajectory. Root cause is 2.3. |
| 2.5 | Wall length, floor area, perimeter | **Met** | `runs/synthetic_benchmark.json` | Propagated 1σ per wall and for area. |

## 3. Outputs

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 3.1 | Dimensioned floor plan, machine readable | **Met** | `src/scan2plan/report.py`, `scan2plan-plan` | JSON with per-wall 1σ. |
| 3.2 | Dimensioned floor plan, visual | **Met** | `examples/syn_nominal.svg`, `out/field/*.svg` | ASCII-only glyphs, 1 m scale bar, no north arrow (the frame has no compass direction). |
| 3.3 | Per-wall uncertainty | **Met** | `runs/synthetic_benchmark.json` | `rms / sqrt(support)`. |
| 3.4 | Openings: doors, windows | **Prototype** | `scripts/analyse_openings_and_damage.py` | Detector returns the largest near region spanning the frame, which is the whole frame. Widths land 2.4× the tape figure. Reported unreliable, not shipped as working. |
| 3.5 | Room dimensions, real site | **Met** via tape | `out/field/`, `scripts/report_field_capture.py` | Geometry is **measured**, not reconstructed. Every document carries `geometry_is_measured_not_reconstructed: true`. |

## 4. Damage

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 4.1 | Damage detection from depth | **Prototype** | `runs/openings_and_damage.json` | Local depth roughness vs the same wall's control frames. |
| 4.2 | Validation of damage detection | **Not met** | same | One crack, two frames. Crack frames came out *smoother* than control (ratio 0.47), so the method did not detect it. Negative result, stated as one. |
| 4.3 | Damage classes / severity | **Not done** | — | No taxonomy attempted. |

## 5. Accuracy and verification

| # | Requirement | Status | Evidence | Notes |
|---|---|---|---|---|
| 5.1 | Verified benchmark | **Met** | `runs/synthetic_benchmark.json`, `scripts/verify_synth.py` | 8 self-checks, all passing. Exact truth, 5 rooms. |
| 5.2 | Accuracy gates | **Met** | `scripts/benchmark_synthetic.py` | Span 15 mm, ceiling 15 mm, area 1% gates. Achieved 12.5 / 3.8 mm / 0.46%. |
| 5.3 | Independent real-world reference | **Met** | `data/field_ground_truth.json` | Tape, 3 rooms, plus diagonals. Used for validation only, never to calibrate. |
| 5.4 | Real-world accuracy | **Not met** | `runs/real_baseline.json` | The LiDAR tier does not reproduce real rooms. Reported, not hidden. |
| 5.5 | Photo-tier accuracy | **Not met** | `runs/wall_measurements.json` | Median wall error 0.55 m; room area −74% / −50% / +4%. Plane fits are good (rms 5–49 mm); the extents are not. |
| 5.6 | Tests | **Met** | `python -m pytest tests -q` | 51 passing. |

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
| 8.5 | Failure analysis | **Met** | `docs/final_report.md` §6, `CHECKPOINTS.md`, git log | Six convention bugs documented, each with the measurement that revealed it. |

---

## Summary

Counted from the tables above, by the status column:

| Status | Count |
|---|---|
| Met | 24 |
| Partial | 1 |
| Prototype | 2 |
| Not met / Not done | 8 |
| **Total requirements** | **35** |

Two of the 24 are qualified and should not be read as clean passes: **2.4** (room
polygon from walls) is Met on synthetic data and fails on real archives, and
**6.2** (tape-reference benchmark) is a substitute for a comparison that was not
performed. Strip those two and it is 22 unqualified.

The two results that matter most, stated plainly:

1. **The automatic pipeline is verified on synthetic rooms and fails on real ones.**
   Span error on synthetic is 12.5 mm worst case. On real archives the same code
   reports a room a tenth of its true area. The synthetic benchmark could not catch
   this, because synthetic rooms are empty boxes and every non-horizontal plane in
   an empty box is a wall.
2. **The photo tier is relative, not metric, because no frame contains a scale
   reference.** This is a capture-protocol omission, not a modelling choice, and it
   is the highest-value thing to fix on the next site visit.
