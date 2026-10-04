# scan2plan

Turns a depth capture of a room into a **dimensioned floor plan**, a measured
ceiling height, and a photographic record of damage — as JSON and a drawn SVG.

The pipeline is honest about uncertainty. Every measured number is reported with
a 1σ, and known gaps are carried *inside* the result document rather than left
for a reader to discover.

```
python scripts/plan_room.py data/demo_room.zip --out out/
```

That works on a clean clone with no setup beyond the dependencies below. It
prints a summary and writes `out/demo_room.json` and `out/demo_room.svg`.

---

## What you get

For the included demo room (exact ground truth 4.60 × 3.40 × 2.72 m):

| Quantity | Measured | Truth | Error |
|---|---|---|---|
| Long span | 4.605 m | 4.600 m | +5 mm |
| Short span | 3.400 m | 3.400 m | 0 mm |
| Ceiling height | 2.721 m | 2.720 m | +1 mm |
| Floor area | 15.675 m² | 15.640 m² | +0.22 % |

Across five synthetic rooms with exact truth (four walls recovered in each):

| Metric | Median | Max | Assessment gate |
|---|---|---|---|
| Room span | 3.8 mm | 12.5 mm | 20 mm |
| Ceiling height | 3.0 mm | 3.8 mm | 15 mm |
| Floor area | 0.21 % | 0.46 % | — |

Reported uncertainty is deliberately pessimistic. The demo room's ceiling height
is quoted as ±30 mm against an actual error of 1 mm, because the floor plane's
residual is inflated by pose noise. Understating uncertainty is the failure that
matters against a tight gate.

## Install

```
pip install -e ".[analysis,depth]"         # core + tests + monocular depth
python scripts/fetch_depth_model.py        # 99 MB ONNX weights, once
python -m pytest tests -q                  # 51 tests, ~60 s
```

`depth` pulls in ONNX Runtime and OpenCV (~15 MB between them). Neither core nor
analysis pulls in a deep-learning framework: the depth network runs through an
exported ONNX graph rather than PyTorch, because this is a CPU-only path and the
torch wheel would be multi-gigabyte. See the trade-offs below.

Nothing above is needed for the automatic pipeline — `data/demo_room.zip` runs with
`numpy`, `pillow` and `scipy` alone.

## Using it on your own capture

The CLI expects a capture archive laid out as:

```
<scan_id>/depth/NNNNNN.png        uint16, depth along the camera optical axis
<scan_id>/confidence/NNNNNN.png   uint8
<scan_id>/odometry.csv            timestamp, frame, x,y,z, qx,qy,qz,qw, fx,fy,cx,cy
<scan_id>/imu.csv
<scan_id>/camera_matrix.csv
<scan_id>/rgb.mp4
```

```bash
python scripts/plan_room.py CAPTURE.zip --out results/

  --load-stride N     subsample frames when loading      (default 1)
  --frame-stride N    subsample frames for segmentation  (default 1)
  --point-stride N    subsample pixels within a frame    (default 4)
  --calibrate-scale   search for a depth-scale factor instead of trusting 0.001
```

Use `--load-stride` to trade accuracy for speed on long captures. **Do not set
both** `--load-stride` and `--frame-stride` above 1: the two compose, and
subsampling twice silently throws away most of the archive.

Exit status is non-zero when no room was recovered, so this works as a build
step.

## Photographs and a real site

Ordinary photos and video go through a different route. Depth is predicted with
Depth Anything V2, planes are fitted to the lifted point cloud, and labels in the
filenames select which surface each frame is evidence for.

```bash
python scripts/depth_capture.py --skip-existing      # depth for every photo + video
python scripts/measure_walls_from_depth.py           # per-wall, per-room dimensions
python scripts/analyse_openings_and_damage.py        # openings and damage
python scripts/report_field_capture.py --out out/field   # tape-assisted site plans
```

**Two things to know before trusting any number from this route.**

Depth is *relative*. Depth Anything predicts inverse depth with a per-image
arbitrary scale and shift, so nothing is a measurement until an external scale
anchors it. This capture has no scale reference in any of its 238 frames and no
surviving EXIF, which is what caps the photo tier.

`report_field_capture.py` exists because of that. Its dimensions come from the
steel tape in `data/field_ground_truth.json`, and every document it writes carries
`geometry_is_measured_not_reconstructed: true`. The photographs are attached as
evidence per wall and are not used to derive any dimension. Do not read those
plans as reconstruction results.

Filename labels (`m_wall_2.5.jpg`, `2_room_wall_1 (3).jpg`, `wall_1_crack (1).jpg`)
are used to *select and evaluate* evidence. They are not a production input: a
client's photos will be named `IMG_20261004_103746.jpg`, and pixel-only wall
detection remains open.

## Repository map

| Path | What it is |
|---|---|
| `src/scan2plan/ingest.py` | Archive reading, intrinsics, pose matrices |
| `src/scan2plan/unproject.py` | Depth → world points, scale calibration, gravity |
| `src/scan2plan/segment.py` | Per-frame RANSAC peeling → cross-frame clustering → classification |
| `src/scan2plan/plan.py` | Floor basis, wall merging, half-plane polygon, dimensions |
| `src/scan2plan/report.py` | Result document and SVG rendering |
| `src/scan2plan/synth.py` | Seeded synthetic room generator with exact truth |
| `src/scan2plan/drift.py` | Drift measurement against a reference plane |
| `src/scan2plan/monodepth.py` | Depth Anything V2 via ONNX Runtime, relative-depth handling |
| `src/scan2plan/depth_geometry.py` | Plane fitting on lifted depth, vertical recovery from the floor |
| `src/scan2plan/capture_manifest.py` | Room / surface / wall labels recovered from filenames |
| `scripts/plan_room.py` | The CLI |
| `scripts/benchmark_synthetic.py` | Scores the pipeline against known rooms |
| `scripts/verify_synth.py` | Generator self-checks, including a geometry round-trip |
| `scripts/depth_capture.py` | Depth over every photo and video in a capture |
| `scripts/measure_walls_from_depth.py` | Per-wall measurement from wall-labelled photos |
| `scripts/analyse_openings_and_damage.py` | Openings and damage-roughness stages |
| `scripts/report_field_capture.py` | Tape-assisted plans for a real site |
| `scripts/measure_real_baseline.py` | Real-archive baseline and disjoint-halves repeatability |
| `docs/final_report.md` | **Start here** — results, negative results, next steps |
| `docs/compliance_matrix.md` | Requirement-by-requirement status with evidence |
| `docs/capture_protocol.pdf` | Field capture protocol for collecting new data |
| `docs/site_day.pdf` | Short on-site runbook actually followed: tape, room photos, damage. Its Magicplan step could not be executed — see Limitations. |
| `CHECKPOINTS.md` | Working log: what was tried, what broke, what it cost |

## How it works

1. **Read** the archive. Depth is **axial** (the component along the optical
   axis), so unprojection is `[(u-cx)/fx*z, (v-cy)/fy*z, z]`. `Capture.focal`
   holds the **full-resolution** focal; `depth_intrinsics()` is the only
   supported way to read it, because it applies `DEPTH_DECIMATION` for you.
2. **Segment** planes: fit planes per frame, cluster them across frames by
   normal and offset, refit each cluster with inlier-weighted total least
   squares, then classify against gravity. A room has one floor and one ceiling,
   so horizontally-oriented clusters are consolidated and the best-supported
   member anchors the rest.
3. **Solve** the room: take a basis in the floor plane, express each wall as a
   line in it, merge fragments that are parallel and close, then intersect the
   inward half-planes for the polygon.
4. **Report** every dimension with its uncertainty, and state what is missing.

## Limitations and trade-offs

These are the decisions worth arguing with. Each was made for a reason.

**Openings are not detected.** Doors and windows are the highest-accuracy gate in
the assessment (≤20 mm) and they are not implemented. Wall runs are reported as
continuous, so any opening is currently absorbed into the wall length. This is
the largest open gap. Openings are also the first casualty of 7.5× depth
decimation, so this may need to be solved at capture time rather than in
software.

**No loop closure or pose-graph optimisation.** Drift was measured before
deciding this: the supplied archives show no systematic drift at all
(R² ≈ 0.000–0.003) and instead show random per-frame scatter of 32–44 mm. That is
not a trajectory-optimisation problem, so plane anchoring is the right lever and a
solver would be solving a fault that is not present. Poses are consumed as given,
which means pose error flows straight into the wall uncertainties — and that is
reported rather than hidden.

**Scale comes from the device, not a survey.** The 1.000 m marker photographed in
the scene is the intended independent reference. Until it is used, any error in
the device depth scale is a uniform multiplicative error in every length here.

**No deep-learning dependencies.** Monocular metric geometry needs either a
multi-gigabyte depth model or a SfM/COLMAP stack. Both would break installation
on a clean machine, which is a property the assessment explicitly values. The
LiDAR/depth tier is therefore fully implemented and the photo tier is not — see
below.

**Multi-room layouts are not stitched.** One capture yields one room.

**The vertical axis is inferred, not sensed.** Gravity gives an axis but not a
sign; the sign is fixed by the fact that a handheld camera is above the floor.
This is a convention, and it is applied per capture.

## Not achieved

Stated plainly rather than papered over.

- **Photo and video tiers.** No monocular metric geometry is implemented — no
  depth model is available in this environment, and adding one was judged to
  break clean-machine installation. The capture protocol for these tiers is
  delivered; the reconstruction path is not.
- **Multi-room photo stitching** to ±8 %.
- **Damage classification verified against depth.** Damage is captured
  photographically with scale references, but not verified from depth.
- **A scanned room with surveyed ground truth.** Accuracy is measured against
  synthetic rooms with exact truth, not against a physical room measured twice.

A named gap costs far less than a quietly wrong number.
