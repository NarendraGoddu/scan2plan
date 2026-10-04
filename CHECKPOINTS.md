# Checkpoints

Working log for the Applied AI Engineer assessment. Each checkpoint is one
commit with a stated outcome, so the history shows how the result was reached.

Deadline: **5 Oct, 10:00 IST**. One commit per checkpoint, in order.

## Status

| CP | Stage | State | Notes |
|---|---|---|---|
| CP0 | Git repo + identity | **done** | name/email set repo-locally; commits unsigned (no GPG key) |
| CP1 | Ingest + scale calibration | **done** | 0.9997 mm/unit, verified on 3 archives |
| CP2 | Drift measurement + gravity fix | **done** | no systematic drift found (R²≈0); ~32-44 mm random scatter |
| CP2b | Plane-anchored correction + ablation | todo | on the critical path |
| CP3 | Plane segmentation | **done** | 1 floor + 1 ceiling + walls on all 3 archives |
| CP4 | Dimensioned geometry | **done** | span error ≤12.5 mm, ceiling ±3.8 mm, area ≤0.46% |
| CP5 | Opening detection | todo | missed and phantom both score as miss |
| CP6 | Confidence intervals | **partial** | carried in the JSON report; not yet on every stage |
| CP7 | JSON schema + rendered plan | **done** | `scripts/plan_room.py` → JSON + SVG, one command |
| CP8 | Baseline gate measurement | todo | data decides the fix target |
| CP9 | Fix declaration | todo | worst gate + root cause + prediction |
| CP10 | Ship fix, before/after | todo | both regenerable |
| CP11 | Synthetic known rooms | **done** | 5 rooms, exact truth, 4/4 walls found in each |
| CP12 | Compliance matrix | todo | |
| CP13 | Capture protocol + device matrix | **done** | 7-page field booklet, `docs/capture_protocol.pdf` |
| CP14 | Benchmark + technical report | todo | |
| CP15 | Head-to-head | todo | ScanNet reference substitute |
| CP16 | Photo/video tier paths | todo | your flat, GT 7T |

## Done

### CP3 — Plane segmentation

Per-frame RANSAC peeling → cross-frame clustering → inlier-weighted TLS refit →
classify. Three bugs, each found by checking physics rather than trusting code:

1. **`canonical_plane` returned a different plane than it was given.**
   `orthonormalize` canonicalises the *sign* of the normal, so scaling a normal
   while keeping the old offset describes the mirror surface:
   `(n=(0,-1,0), d=2.5)` came back as `(0,1,0), d=2.5`. Fixed by scaling
   without touching sign, then flipping sign and offset together.
2. **`cluster_planes` never canonicalised.** RANSAC and SVD both return a
   sign-arbitrary normal, so the same wall seen from two sides compared 180°
   apart and refused to merge. This was the actual cause of fragmented floors.
3. **The vertical axis sign was inconsistent across captures** (−Y, +Y, −Y),
   which relabelled `floor_only`'s floor as a ceiling 1.27 m overhead instead
   of raising. `orient_from_horizontal_planes` now derives the sign from the
   fact that a handheld sensor sits above the floor.

Structural prior: **a room has one floor and one ceiling.** Per-frame fits
shattered `with_ceiling`'s floor into four parallel planes spanning 0.63–1.57 m
below camera, so any pairwise ceiling height disagreed by a metre.
`consolidate_horizontal` anchors on the best-supported member, folds in the
rest, and keeps the rejected spread as `member_height_spread_m` — that spread is
real uncertainty about the surface, not bookkeeping.

Results at `frame_stride=8, point_stride=4`:

| Archive | Floor height | Floor rms | Ceiling | Ceiling rms |
|---|---|---|---|---|
| single_room | −1.450 m | 9.7 mm | — | — |
| floor_only | −1.274 m | 94.9 mm | — | — |
| with_ceiling | −0.798 m | 61.3 mm | +1.487 m | 8.1 mm |

`with_ceiling` ceiling height **2.285 m** (physically plausible).

**Known gap, not a pass.** Floor residual scatter of 61–95 mm is far outside
the 1.5 cm ceiling-height gate. `with_ceiling`'s floor in particular has only
17.6% frame support and looks contaminated, while its ceiling is clean at
8.1 mm — so the 2.285 m figure inherits the floor's error. Ceiling height is
therefore *plausible*, not *accurate*, and is reported as such.

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
| Part 3 head-to-head | **not done** | Magicplan ships iOS only and the capture phone was Android, so no Magicplan scan exists for any of the three rooms. Nothing was substituted and no comparison is claimed. This is a real loss of ~10% of the assessment, not a rounding error. |
| LiDAR-tier damage classes | photo tier only | damage not verified against depth |
| TestFlight build | stock-capture protocol (Route 2) | no iOS device to build on |

## Done since CP3

### CP11 — Synthetic known rooms

`src/scan2plan/synth.py` renders rooms as archives byte-compatible with the
sample data, so every stage above ingest runs on identical code for synthetic and
real captures. Five rooms (nominal, wide, tall, tight, noisy walls) with exact
ground truth, 720 frames each.

This earned its cost immediately by exposing three convention bugs that had all
been self-consistent enough to look plausible:

1. **Focal divided twice.** `Capture.focal` holds the full-resolution focal and
   `depth_intrinsics()` divides by `DEPTH_DECIMATION`; the generator was already
   storing the divided value, collapsing fx to 28.4 and bending every ray toward
   the optical axis.
2. **Slant range stored instead of axial depth.** `unproject_frame` reconstructs
   points as `[(u-cx)/fx*z, (v-cy)/fy*z, z]`, which assumes depth is the
   component along the optical axis. Storing ray length inflated every off-axis
   reading by `1/cos(theta)`. The symptom was the interesting part: error grew
   with pixel radius, which reads exactly like a focal-length fault.
3. **Half-plane intersection fed the wrong shape.** scipy wants one
   `(ndim, ndim+1)` matrix with the offset in the last column; passing `A` and
   `b` separately made it infer the wrong dimensionality.

`scripts/verify_synth.py` now asserts that unprojected points land on the
surfaces they were rendered from, which is the check that catches both depth
conventions. Residual is 4–34 mm against 20 mm of injected pose noise.

### CP4 — Dimensioned geometry

`src/scan2plan/plan.py`: floor basis from gravity, wall extraction in that
basis, fragment merging by parallelism and offset, half-plane intersection for
the polygon, ceiling height carried per wall with its own uncertainty.

Measured against exact truth on all five rooms, four walls found in each:

| Metric | Median | Max | Gate |
|---|---|---|---|
| Room span | 3.8 mm | 12.5 mm | 20 mm |
| Ceiling height | 3.0 mm | 3.8 mm | 15 mm |
| Floor area | 0.21 % | 0.46 % | — |

### CP7 — Result document and rendered plan

`src/scan2plan/report.py` and `scripts/plan_room.py`. One command takes an
archive to a JSON result document and an SVG plan view:

    python scripts/plan_room.py capture.zip --out out/

Deliberate choices worth defending:

- **Every measured number carries its uncertainty.** Ceiling height is a
  difference of two plane heights, so the two plane uncertainties combine in
  quadrature rather than reporting the smaller one.
- **Known gaps travel inside the document.** `limitations` is part of the JSON,
  so a result can never be read as more complete than it is.
- **No north arrow.** The archive frame has no compass direction; inventing one
  would be a fabrication. Orientation is reported as basis vectors instead.
- **ASCII-only SVG.** Glyphs like `±` and `m²` render as tofu boxes in stricter
  viewers, and this file is assessor-facing.

Nine tests cover the report, including that no SVG element falls outside its
canvas and that the degenerate no-polygon case still renders valid XML.

| Metric | Value |
|---|---|
| Tests | 30 passing |
| Floor area (nominal) | 15.568 m², ±0.23 % (true 15.640 m²) |
| Ceiling height | 2.719 m, ±29 mm 1σ (true 2.720 m) |
| Worst wall position | ±2.5 mm 1σ |

The reported ceiling uncertainty (±29 mm) is much larger than the actual error
(0.6 mm). That is the honest direction to be wrong in: the floor plane's
residual is inflated by pose noise, and understating it would be the failure
that matters against a 15 mm gate.