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
| Room span | 4.1 mm | 12.5 mm | 20 mm |
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

## The doorway regression

The most useful thing in this log, because it is the one that nearly shipped.

`select_room_boundary` was written to stop furniture being read as walls. Its
first rule was: *a room boundary has the camera path entirely on one side, so
discard any wall whose line the path crosses.* That is false whenever the
operator walks through a doorway, which is most rooms.

It cost the 6.2 × 3.1 m synthetic room both of its side walls. The operator's path
spans 4.53 m along the normal of the 3.11 m wall pair — the extra 1.4 m is the
doorway — so both walls looked straddled. Two unparallel walls cannot bound a
region, so the room reported **0.000 m² with a NaN span**, and the benchmark's
worst-case area error became meaningless.

What made this worth logging rather than quietly patching:

- **Nothing in the real data looked wrong.** There are no real-data ground truth
  values to compare against, so a 0 m² room reads the same as any other number.
  Only the synthetic benchmark, where truth is exact, exposed it.
- **The first repair was also wrong.** Comparing a wall's offset against the
  coordinate origin made a wall at offset 0 look permanently innermost, so it was
  discarded even when a genuine wall stood beyond it. Caught by unit tests written
  before the change was trusted, not by the benchmark.
- **The rule that shipped compares the two excursions**, not their presence. A
  wall is interior only when the path passes far beyond it on *both* sides by a
  comparable amount — `WALL_CROSS_CENTRAL_RATIO = 0.5`. Furniture in the middle of
  a room qualifies. A doorway does not: it leaves the wall near one edge of the
  path's extent. On the failing room the ratio was 0.19.

`tests/test_room_boundary.py` pins all of it, including the doorway case that
started this.

One crash was introduced on the way: when every wall in an orientation group is
straddled, the survivor list was empty and indexing it raised `IndexError`. It now
keeps the outermost pair and records that the crossing test was inconclusive,
because dropping the group would silently delete a room dimension.

| | before | after |
|---|---|---|
| Synthetic rooms at 4/4 walls | 4 of 5 | **5 of 5** |
| `wide` room area | 0.000 m² (NaN span) | 19.26 m² (+0.21 %) |
| `single_room` reported area | 19.27 m² | 12.08 m² |
| `floor_only` reported area | 85.11 m² | 46.08 m² |
| `with_ceiling` reported area | 93.54 m² | 27.60 m² |
| Split-half wall disagreement | 6071 / 5529 mm | 6071 / 5529 mm (unchanged) |

The real-data areas moved in the right direction — they now sit below the camera
trajectory footprint instead of far above it — but there is no ground truth for
those archives, so that is a sanity bound and not a result. The split-half
disagreement is unchanged, which is the honest summary: **the real-data geometry
is still unsolved.**

## "There are duplicate images" — tested, and it is not that

A reasonable challenge to the wall-selection work: if the same frame is counted
many times, plane `support` stops meaning "how well seen" and starts meaning "how
many times", so a surface the operator lingered on outranks the wall behind it.
Duplicates would explain too many walls.

**Measured (`scripts/diag_duplicate_frames.py`): there are no duplicates.**

| Archive | Frames | Unique depth | Byte-identical repeats | Median pose step |
|---|---|---|---|---|
| `single_room` | 1715 | 1715 | **0** | 7.9 mm |
| `floor_only` | 5251 | 5251 | **0** | 8.8 mm |
| `with_ceiling` | 9745 | 9745 | **0** | 9.1 mm |

Zero repeats, by depth hash and by pose. Every frame is distinct.

What *is* true is the mechanism behind the intuition: the captures are recorded
at 60 Hz, so consecutive frames are 8–9 mm apart and 95–98% of them move the
camera under 2 cm. The operator's path is 14.5 m / 54.2 m / 99.8 m long, but there
are only about 470 / 1300 / 2500 independent viewpoints in it. So `support` really
does mostly measure dwell time.

So the hypothesis was right about the *problem* and wrong about the *cause*. The
fix for it was implemented anyway — `motion_dedup()` in `ingest.py`, keeping only
frames that moved at least a threshold since the last kept frame, or rotated —
and then measured against the stride baseline
(`scripts/compare_baselines.py`):

| Metric | index stride | motion dedup 5 cm | better? |
|---|---|---|---|
| `floor_only` floor rms | 44.8 mm | **11.9 mm** | motion |
| `with_ceiling` floor rms | 160.0 mm | **73.4 mm** | motion |
| `with_ceiling` split-half wall delta | 5529 mm | **2916 mm** | motion |
| `single_room` walls | 9 | 9 | neither |
| `floor_only` walls | **6** | 8 | stride |
| `with_ceiling` walls | **8** | 9 | stride |
| `with_ceiling` area | **27.60 m²** | 8.52 m² | stride |
| `single_room` split-half walls | 2 matched | **none matched** | stride |

Genuinely mixed, and **not adopted as the default.** It clearly helps the
horizontal planes — fewer correlated near-identical frames to drag a plane fit
around — and clearly hurts wall selection, where fewer frames mean more fragments
(104 wall consensus planes on `with_ceiling` instead of a handful) and the
outermost-pair rule then picks badly. `with_ceiling`'s area collapsing to 8.52 m²
against a 75.7 m² trajectory footprint is decisive against it.

Kept as `motion_dedup()` with `--motion-dedup` on the baseline driver, tested, and
documented as a negative result, because "we tried it and it did not work" is worth
more than a silent omission. The conclusion stands: **redundancy is not why there
are too many walls.** The cause is still that real surfaces are not clean planes —
clutter, partial views and fragmented fits — and the fix is still to solve for the
minimal enclosing set of planes around the trajectory rather than filter a
consensus set.

## The wall/perspective labels

The photographer labelled each wall and numbered the viewpoints of that wall, in
two conventions:

    m_wall_1.1 .. m_wall_1.4        dot form,   master bedroom
    m_wall_2.1 .. m_wall_2.5
    2_room_wall_1 (1) .. (3)        paren form, second bedroom  (a "copy" in
    2_room_wall_2 (1) .. (4)                   Windows terms, but semantically
    hall_side_1 (1) .. (8)                      a viewpoint index)

So `m_wall_2.5` is the fifth viewpoint of wall 2, and `2_room_wall_2 (4)` the
fourth. `capture_manifest.py` already parsed both into `wall_index` and
`frame_index`; the dot form needed `_DOT_INDEX` anchored with a literal `.` so that
`2_room_wall_1` is not read as frame 1 of an unnumbered wall.

**What that buys: topology, for free.** Each room folder contains walls 1, 2, 3, 4
and nothing else. That is a fact about the capture, not the tape, so the
four-sided room model no longer rests on the tape alone. Every field document now
carries `topology_corroboration` recording it.

**What it does not buy: dimensions.** Knowing four photos share a wall means we can
pick the best of four views. It cannot manufacture a good view. Measuring per view:

| Room | Labelled walls | Views usable (< 40° incidence) | Best incidence |
|---|---|---|---|
| `master_bedroom` | 4 | 1 (wall 4, at 0.0°) | 0.0° |
| `main_hall` | 4 | 0 | 8.9°, rejected: vertical extent inconsistent |
| `second_bedroom` | 4 | 0 | 68.5° |

Only **2 of 12 walls** are measurable at all, and both come out near 45 % of the
tape value. The other ten were only ever photographed at ≥ 68° incidence — nearly
edge-on, where monocular depth has almost no signal across the wall's width. That
is the quantified reason the photo tier is not metric, which is a better answer
than the earlier "median 0.55 m error" with no mechanism attached.

Incidence angle is the angle between the fitted wall normal and the optical axis.
It is computable per view with no camera pose, which is what makes it usable here.

### A real bug this uncovered

`scan_capture()` read only files *inside* subdirectories of the capture root. The
2026-10-04 capture is laid out as `capture/<room>/…`, so it worked — but a capture
delivered as a flat folder parsed as **zero labelled photos**, which is
indistinguishable from "this capture has no labels" and silently disables every
stage that depends on them. It now reads the root as well, with `recursive=True`
for deeper trees. Six tests cover both layouts.

## Openings: the mask was on the wrong half of the depth range

The detector selected the largest region **nearer** than the 55th percentile. That
is backwards, and the band means say so directly. In the five door photographs the
floor at the bottom of the frame sits at 2.3-3.6 m while the middle of the frame sits
at 6.3-6.7 m: a doorway is a hole you look *through* into the next space, so it is
farther than the wall around it. Masking the near region selects the wall, which
wraps around the opening and connects across the frame, so the largest component came
back as the entire image — **1.93 m for a door taped at 0.80 m.**

Two hypotheses were tested, and the first was wrong:

| Hypothesis | Prediction | Result |
|---|---|---|
| H1: the mask is picking up the floor | bbox touches the **bottom**, near mass in the **lower** half | **Refuted** — 3 of 5 bboxes touch top *and* bottom; near fraction is *higher* in the upper half for 4 of 5 |
| H2: the polarity is inverted | far-region mask gives a tall narrow mid-frame component | **Confirmed** — 0.92 m against 0.80 m taped |
| H3: the far region is fully enclosed by wall, so no threshold is needed | a parameter-free criterion exists | **Refuted** — the opening reaches the frame edge in all five photos |

A fourth bug surfaced while fixing this. The wall depth was a median over the top and
side borders, which mixes depths whenever the wall is oblique; on `main_door (2)` that
put the wall at 1.22 m where the other photograph of the same door saw 2.55 m, and
the width estimate followed it to 1.59 m. Estimating from the ring of pixels
immediately around the opening cannot be dragged by the opening it surrounds.

| Room | Estimate | Tape | Width | Height |
|---|---|---|---|---|
| `master_bedroom` | 0.896 × 1.983 m | 0.80 × 2.00 m | +96 mm | **−17 mm** |
| `second_bedroom` | 0.919 × 2.008 m | 0.80 × 2.00 m | +119 mm | **+8 mm** |
| `main_hall` | 1.234 × 1.873 m | 1.03 × 2.09 m | +204 mm | −217 mm |

**Heights pass the 20 mm gate on two of three doors. Widths pass on none.** The
width bias is systematic and explained: the mask includes the door reveal, the few
centimetres of jamb that genuinely belong to the opening. `main_hall` is flagged, not
hidden — its two copies disagree by 430 mm.

### The one that was about method, not code

The per-room line read:

    best = min(mine, key=lambda o: abs(o["height_est_m"] - d["height_m"]))

It picked whichever copy of a door landed nearest the **taped** height. That is
selecting on validation data, and it had been quietly producing a flattering 1.02 m
for `main_hall` where the truth-free median is 1.234 m. A number chosen by how close it
lands to the answer key is not a measurement.

`consensus()` now takes a median and has **no truth parameter at all**;
`tests/test_openings.py` asserts its only parameter is the measurement list, so this
cannot be reintroduced by accident. Writing the median out longhand also caught a
second bug: the obvious `sorted(v)[len(v) // 2]` returns the *upper* middle for an even
count, so with two copies it always reported the larger one — the wild copy capturing
the answer, which is the opposite of why a median is used.

Moving this logic out of `scripts/` into `src/scan2plan/openings.py` is what made the
synthetic-frame tests possible at all: the real photographs have no scale reference, so
they can only ever be checked against tape afterwards, but a synthetic frame can be
built with a known 60%-tall opening and checked directly.

## The rectangular-room prior, and why it is off by default

The real sample archives give 9, 6 and 8 walls where a rectangle has 4, with edges
down to 0.166 m — noise fragments promoted to boundary walls.

**The capture labels cannot fix this, and it is worth being precise about why.** The
photographs are *deliberately* labelled (`wall_1.1` … `wall_4.1`, decimal =
viewpoint). The LiDAR archives are a continuous walk with frame-index filenames:

    c00a170fe1/confidence/001099.png
    c00a170fe1/odometry.csv
    c00a170fe1/imu.csv
    c00a170fe1/camera_matrix.csv
    c00a170fe1/rgb.mp4

There is one session folder and no room or wall subdivision. Nobody ever decided
where "wall 1" was, so there is nothing to read.

**What transfers is the prior the labels imply.** `select_rectangular_boundary`
(`--rectangular`) picks the most nearly perpendicular pair of wall orientations by
support, then keeps the outermost wall on each of the four sides:

| Archive | Default | With prior | Axes chosen | Area |
|---|---|---|---|---|
| `single_room` | 9 walls / 8 vertices | **4 / 4** | 89.3° | 21.64 m² |
| `floor_only` | 6 walls / 5 vertices | **4 / 4** | 88.5° | 95.57 m² |
| `with_ceiling` | 8 walls / 5 vertices | **4 / 4** | 88.6° | 105.65 m² |

**It is an assumption, not a measurement, and it is off by default.** Every synthetic
room in the benchmark is rectangular *by construction*, so a benchmark run with the
prior on is circular and proves nothing about finding a rectangle unaided. No
benchmark number in the report is quoted with it enabled, and
`tests/test_rectangular_prior.py` asserts the default is `False` so the benchmark
cannot silently acquire the assumption.

The axes coming out at 89.3° / 88.5° / 88.6°, chosen from support alone, are worth more
than the wall counts: that is independent corroboration of the four-wall topology from
a different sensor than the labels.

Not fixed: floor-height repeatability is still 0.6 / 189.5 / **801.0 mm** across split
halves, and areas exceed the trajectory footprints by more than reach explains.

### Two bugs found while testing it

Both fallback paths appended the reason the prior had bailed out and then returned the
*fallback's* notes, discarding it. The report would have claimed a rectangular result
with no mention that the assumption had not been applied — the worst possible failure
mode for a function whose whole purpose is to be transparent about its assumptions.

`if w not in assigned` raised `ValueError: truth value of an array is ambiguous`,
because `Wall` is a dataclass holding numpy arrays, so `==` compares elementwise. The
shape-agnostic path uses identity (`is`) for exactly this reason; the new code now
does too, and a test pins it.

Writing the tests also showed the shape-agnostic path returns 4 walls for clutter
angled 9° from an axis, because that is inside `PARALLEL_TOL_DEG = 12°` and gets
absorbed into the existing group. Reproducing the real 9-wall failure needed angles
past that tolerance — worth knowing, because it means modest orientation error is not
what breaks real scans.