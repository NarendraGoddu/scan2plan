# Checkpoints

Working log for the Applied AI Engineer assessment. Each checkpoint is one
commit with a stated outcome, so the history shows how the result was reached.

Deadline: **5 Oct, 10:00 IST**. One commit per checkpoint, in order.

## Status

| CP | Stage | State | Notes |
|---|---|---|---|
| CP0 | Git repo + identity | blocked | need author name/email |
| CP1 | Ingest + scale calibration | **done** | 0.9997 mm/unit, verified on 3 archives |
| CP2 | Drift measurement + gravity fix | **done** | no systematic drift found (R²≈0); ~32-44 mm random scatter |
| CP2b | Plane-anchored correction + ablation | todo | on the critical path |
| CP3 | Plane segmentation | todo | floor/ceiling/walls |
| CP4 | Dimensioned geometry | todo | walls, ceiling height, area |
| CP5 | Opening detection | todo | missed and phantom both score as miss |
| CP6 | Confidence intervals | todo | on every measurement |
| CP7 | JSON schema + rendered plan | todo | one command per capture |
| CP8 | Baseline gate measurement | todo | data decides the fix target |
| CP9 | Fix declaration | todo | worst gate + root cause + prediction |
| CP10 | Ship fix, before/after | todo | both regenerable |
| CP11 | Synthetic known rooms | todo | exact LiDAR-tier scoring |
| CP12 | Compliance matrix | todo | |
| CP13 | Capture protocol + device matrix | todo | |
| CP14 | Benchmark + technical report | todo | |
| CP15 | Head-to-head | todo | ScanNet reference substitute |
| CP16 | Photo/video tier paths | todo | your flat, GT 7T |

## Done

### CP1 — Ingest and depth-scale calibration

Decoded the archive format: uint16 depth PNGs at 192x256, confidence in
{0,1,2}, 60 Hz poses with per-frame intrinsics, IMU.

Two format facts that govern every downstream number:

- Depth maps are decimated **7.5x** from the 1920x1440 intrinsics, so the depth
  focal length is `K / 7.5` (~213 px). Missing this scales every point by 7.5.
- Focal length drifts per frame (1581..1618), so per-frame intrinsics are used;
  the static `camera_matrix.csv` is a reference only.

**Depth is uint16 millimetres**, recovered rather than assumed. This cannot be
settled from geometry: a uniform scale error preserves all angles and the
normalised plan shape, so plane fits stay invariant and only absolute lengths
move. The poses are metric, which supplies the missing constraint — fit the
dominant plane in frame *i*, then score how much of frame *j* lands on it.
Nearby frames are essential, since widely separated frames face different
surfaces and compare nothing.

Result: **0.9997 mm/unit**, reproduced independently on all three archives.
Enclosure agrees as an independent check: `floor_only`'s 8.5x8.7 m walk sits
inside an 8.4x9.0 m reconstructed room.

The estimator is data-starved rather than biased, so `calibrate_scale` reports
`reliable` and peak prominence instead of always answering. A stride-80
subsample returns 0.75 mm/unit on a flat 0.033 peak; a test asserts that case
is refused rather than answered confidently. Frame count alone was found to be
a poor reliability signal — the 86-frame scan gave the sharpest peak while a
122-frame subsample answered wrongly — so prominence is what gates it.

11 tests pass: synthetic plane at known offset, quaternion round-trips,
per-frame intrinsics use, enclosure independent of the calibration objective.

## Next

**CP2 — drift correction.** The supplied poses are odometry and accumulate
error; the assessment calls "poses used as-is" an automatic fail and requires
an ablation with correction on and off. This is also the most likely fix-loop
candidate, but CP8 data picks the target rather than assuming it.

## Findings to carry into the report

- 7.5x depth decimation is the central accuracy constraint: ~4.2 mrad/px, so
  ~13 mm lateral per pixel at 3 m. Sub-centimetre accuracy cannot come from
  single pixels, only from fitting planes across many views. This drives the
  architecture.
- Uniform scale error is invisible to angle-based validation. Worth stating
  explicitly as a calibration finding.
- `single_scan_floor_only` and `single_scan_with_ceiling` have near-identical
  path extents (8.5x8.7 vs 8.3x9.1 m) and are probably the same room twice —
  a likely natural repeatability pair. To be verified, not assumed.

## Hardware constraints (resolved)

Confirmed with Siva by email:

- **Photo and video tiers may be captured on Android.** A Realme GT 7T is
  acceptable. This unlocks the photo/video gates with real captures instead of
  leaving them unmet.
- **LiDAR tier uses the supplied sample archives.** No LiDAR on the GT 7T, and
  none is expected.
- **Limitations and trade-offs must be stated plainly in the README** so the
  reviewer can see what was and was not achieved. Accepted as a condition of
  the approach, not fought.

Remaining gaps, stated rather than filled with invented numbers:

| Requirement | Substitute | Residual gap |
|---|---|---|
| LiDAR tier ground truth | synthetic known rooms + internal consistency | no tape-measured scanned room exists |
| Part 3 head-to-head | ScanNet reference reconstruction | not a consumer app export (no magicplan without iOS) |
| LiDAR-tier damage classes | photo tier only | damage not verified against depth |
| TestFlight build | stock-capture protocol (Route 2) | no iOS device to build on |