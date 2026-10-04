# scan2plan — final report

**Dimensioned floor plans and damage evidence from depth captures, photographs and
video.**

Applied AI Engineer take-home, Brynz Tech. Submitted 2026-10-05.
Code: `github.com/NarendraGodru/scan2plan`

---

## 1. What this is, in one paragraph

A pipeline that turns a 3D capture into a dimensioned floor plan: segment planes,
fit the room boundary, report every wall length with an uncertainty, and render a
measurable drawing. It runs end-to-end on depth archives and is verified against
exact synthetic truth. It also processes ordinary photographs and video through a
monocular depth network, and for a real building with no depth sensor it falls back
to a tape-assisted mode that produces measured plans with photographic evidence
attached. Both real-world paths are reported with their measured accuracy, including
where they fail.

## 2. Run it

```bash
pip install -e ".[analysis,depth]"
python scripts/fetch_depth_model.py          # 99 MB, once

# automatic: depth archive -> plan + SVG + JSON
scan2plan-plan data/demo_room.zip --out out/demo

# real site: tape + photographs -> measured plans
python scripts/report_field_capture.py --out out/field

# monocular depth over the whole capture
python scripts/depth_capture.py --skip-existing
```

`data/demo_room.zip` is committed, so the first command works from a fresh clone
with no setup and no network.

## 3. Results that hold up

### 3.1 Synthetic benchmark — verified against exact truth

Five rooms, exact ground truth, 240-frame captures, same code path as production.

| Metric | Gate | Median | Worst |
|---|---|---|---|
| Wall span error | 15 mm | 3.8 mm | **12.5 mm** |
| Ceiling height error | 15 mm | 3.0 mm | **3.8 mm** |
| Floor area error | 1 % | 0.21 % | **0.46 %** |

Walls recovered: **4/4 in every room**. Plus 8 self-checks in
`scripts/verify_synth.py`, including one that confirms unprojected points land on
the true surfaces — the check that catches both depth-convention errors described
in §7.

### 3.2 Real site — three rooms, measured

238 photographs, 11 videos, three rooms of a real 3BHK, tape-measured.

| Room | Dimensions | Area | Wall photos attached |
|---|---|---|---|
| Master bedroom | 3.72 × 3.28 × 2.57 m | 12.20 m² | 18 |
| Second bedroom | 4.37 × 2.79 × 2.57 m | 12.19 m² | 17 |
| Main hall | 4.62 × 4.11 × 2.48 m | 18.99 m² | 19 |

These come from the steel tape, and every output says so:
`geometry_is_measured_not_reconstructed: true`. Photographs are attached as
evidence per wall and were not used to derive any dimension. Confidence is reported
on two axes — dimensional (high) and photographic corroboration (low) — never
averaged into one number.

### 3.3 Comparison against a reference — the Part 3 substitute

Magicplan is iOS-only and no iOS device was available, so **no Magicplan comparison
was performed and none is claimed** (§6.1 of the compliance matrix). What is provided
instead is the methodology a head-to-head would use, against the reference we do
have:

| | Automatic pipeline (synthetic) | Tape-assisted (real site) | Monocular depth (real site) |
|---|---|---|---|
| Input | depth archive | tape + 238 photos | 238 photos |
| Walls found | 4/4 | 4/4 by construction | 39 of 197 images |
| Dimensional error | 12.5 mm worst | ±5 mm (tape) | 550 mm median |
| Room area error | 0.46 % worst | 0 % (measured) | −74 % / −50 % / +4 % |
| Time per room | 2.1 s | seconds | ~9 min for the capture |
| Cost | £0 | surveyor or tape | £0 |
| Hardware needed | depth sensor | none | none |
| Scale reference needed | no | tape | **yes — absent here** |

The honest reading: each approach wins on a different axis, and the tape wins on
accuracy because it is a measuring instrument rather than an inference.

## 4. What actually works, and what does not

| Component | State |
|---|---|
| Depth archive ingest, scale, intrinsics | Works, unit-tested |
| Floor + ceiling segmentation | Works on real data (9.7 mm, 0.6 mm repeatability) |
| Room boundary from walls | Works on synthetic, **fails on real** |
| Dimensioned output + uncertainty | Works |
| SVG rendering, CLI, packaging | Works, from a clean clone |
| Monocular depth | Works, 348 maps, real planar structure verified |
| Per-wall measurement from photos | **Not metric** — 550 mm median error |
| Openings (doors/windows) | **Prototype** — detector returns the whole frame |
| Damage detection | **Did not detect** the one crack recorded |
| Multi-room stitching | Not attempted |
| iOS app | Not attempted, no device |

## 5. Honest negative results

These are reported because they are the most useful thing in the document.

**The automatic pipeline fails on real rooms.** `single_room` reports 1.75 m²
against a trajectory spanning 17.3 m² — a factor of ten. Cause: every non-horizontal
plane was classified a wall, so beds, wardrobes, doors and kitchen units were
averaged into the room boundary. `select_room_boundary` now keeps only planes that
bound the camera path. This narrowed the failure but did not close it: floor
repeatability on `single_scan_with_ceiling` is still 801 mm. **The synthetic
benchmark could never have caught this**, because a synthetic room is an empty box
in which every non-horizontal plane genuinely is a wall.

**The photo tier is relative, not metric.** No frame in 238 contains a scale
reference and no EXIF survives. Self-scaling from each wall's own floor-to-ceiling
extent was tried and lands 30–75 % out on area. The plane fits are good — 5–49 mm
residual — but extents are not, because no single frame contains both corners of a
wall and a receding wall's 3D vertical extent is inflated by depth variation. This
is a capture-protocol omission, not a modelling choice.

**Damage was not detected.** A crack is a depth discontinuity, so it was measured as
local depth roughness against the same wall's control frames. The two crack frames
came out *smoother* than the control (ratio 0.47). With two frames in a
three-bedroom capture, the method is below its resolution. Detection path
demonstrated; detection not demonstrated.

## 6. Six convention bugs, and how each was found

Every one of these produced output that looked reasonable. None was found by
reading code; each was found by a check that disagreed with the result.

1. **Slant range stored where axial depth was assumed.** Inflated every off-axis
   reading by `1/cos θ`, pushing points through walls. Symptom: error grew with
   pixel radius, which masqueraded as a focal-length fault. Caught by
   `test_unprojected_points_land_on_true_surfaces`.
2. **Focal divided twice.** The generator pre-divided, collapsing fx to 28.4.
   Caught by checking the value against the archive.
3. **`HalfspaceIntersection` fed `A` and `b` separately** — scipy wants one
   `(ndim, ndim+1)` matrix. Plus a half-plane sign inversion. Caught by a polygon
   that collapsed to a fraction of the true area.
4. **Furniture classified as walls.** Symptom: 84 "walls" in a bedroom.
5. **Wall/horizontal inverted** in the depth geometry stage — the floor was labelled
   a wall, reporting 12 walls per bedroom.
6. **`wall_1_crack` parsed as an ordinary wall**, because the pattern list tested
   "wall" before "crack". The damage stage then reported a clean surface having
   examined nothing.

Two of my own *checks* were also wrong, which is worth recording: a vertical-depth-
gradient verification assumed a level camera, but survey photos are deliberately
tilted — a photo aimed at the ceiling genuinely has its nearest content at the top
of frame. The check was wrong, not the model.

## 7. Process evidence

- **51 tests passing.** 21 of them cover filename parsing against names that
  actually exist in the capture, because a misparse there is silent — the wall
  stage would simply measure the wrong photographs.
- **~20 commits**, each carrying the measurement that motivated it in the message.
- **Reproducible install.** Core dependencies are numpy, pillow and scipy only. The
  depth network runs through ONNX Runtime (~15 MB) rather than a multi-gigabyte
  torch install, specifically so the core install stays light and CPU-only.
- **Corrections made rather than left standing.** README, CHECKPOINTS and the
  site-day runbook all asserted a Magicplan comparison that does not exist. All three
  now record it as not done, with nothing substituted.
- **A count corrected.** `field_ground_truth.json` recorded 87 photographs, counting
  only the three labelled folders. A fourth folder of 151 walkthrough frames arrived
  after a failed phone transfer and was missed by the inventory. It is 238, and all
  238 now have depth maps.

## 8. What I would do next, in priority order

1. **Put a scale reference in every frame.** A tape or A4 sheet against one wall per
   photo converts the photo tier from relative to metric depth. This single change
   is worth more than any model change, and it costs the surveyor nothing.
2. **Make the room-boundary selector a real algorithm.** The current rule (a wall
   must not cut the camera path) narrowed the failure but is not a segmentation
   method. A room is the minimal enclosing set of planes around the trajectory;
   solving that directly, rather than filtering a consensus set, is the fix.
3. **Build a labelled real-room benchmark.** The synthetic benchmark cannot see
   furniture. Six labelled rooms with tape ground truth would have caught bug 4 on
   day one. This is the highest-value test infrastructure in the project.
4. **Multi-room stitching**, by segmenting the trajectory into rooms and solving each
   independently — the trajectory already shows the room boundaries as clumps.
5. **Damage from a proper model**, on a real dataset of labelled defects. Two frames
   of one crack is not a validation set.

## 9. Files

| Path | What |
|---|---|
| `src/scan2plan/ingest.py` | Archive parsing, scale, intrinsics, poses |
| `src/scan2plan/segment.py` | Plane extraction, clustering, classification |
| `src/scan2plan/plan.py` | Room boundary, walls, area, uncertainty |
| `src/scan2plan/report.py` | JSON document, SVG renderer, limitations |
| `src/scan2plan/monodepth.py` | Depth Anything V2 via ONNX |
| `src/scan2plan/depth_geometry.py` | Plane fitting, vertical recovery |
| `src/scan2plan/capture_manifest.py` | Labels recovered from filenames |
| `scripts/benchmark_synthetic.py` | Accuracy gates on exact truth |
| `scripts/measure_real_baseline.py` | Real-archive baseline and repeatability |
| `scripts/report_field_capture.py` | Tape-assisted real-site plans |
| `scripts/measure_walls_from_depth.py` | Per-wall measurement from photos |
| `docs/compliance_matrix.md` | Requirement-by-requirement status |
| `data/field_ground_truth.json` | Tape reference for three rooms |
| `data/demo_room.zip` | Runnable demo with exact truth |

## 10. Compliance

See [`compliance_matrix.md`](compliance_matrix.md) — 35 requirements: **24 Met,
1 Partial, 2 Prototype, 8 Not met or Not done**, each with a file that proves it.
Two of the 24 are qualified: the room-polygon requirement is Met on synthetic data
and fails on real archives, and the reference benchmark is a substitute for a
Magicplan comparison that was not performed. Strip those and it is 22 unqualified.
