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

The generated real-site outputs are also committed at `examples/field_site/` for
review without running anything: `master_bedroom.svg`, `second_bedroom.svg`,
`main_hall.svg`, their JSON documents, and an `index.html` linking them.

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

**The automatic pipeline still fails on real rooms.** `single_room` originally
reported 1.75 m² against a camera trajectory spanning 17.3 m² — a factor of ten.
Cause: every plane within 25° of vertical was classified a wall, so beds,
wardrobes, doors and kitchen units were averaged into the room boundary.

`select_room_boundary` was written to fix that, and its first version made things
worse in a way only the synthetic benchmark could see. It discarded any wall whose
line the camera path crossed, on the assumption that a room boundary always has
the camera entirely on one side. That is false whenever the operator walks through
a doorway. On the 6.2 × 3.1 m synthetic room the path spans 4.53 m along the
normal of the 3.11 m wall pair — the extra 1.4 m is the doorway — so both side
walls were discarded, two unparallel walls could not bound a region, and that room
reported **0.000 m² with a NaN span**. The rule now discards a wall only when the
path passes through it centrally, on both sides by a comparable amount, which is
the furniture signature; a doorway leaves the wall near one *edge* of the path's
extent. All five rooms recover 4/4 walls again.

Real archives after the fix, against the trajectory footprint as a sanity bound —
these archives ship with no ground truth, so this is not an accuracy measurement:

| Archive | Walls | Vertices | Reported area | Trajectory-implied area | Split-half wall disagreement |
|---|---|---|---|---|---|
| `single_room` | 9 | 8 | 12.08 m² | 17.3 m² | **6071 mm** |
| `floor_only` | 6 | 5 | 46.08 m² | 73.8 m² | n/a |
| `with_ceiling` | 8 | 5 | 27.60 m² | 75.7 m² | **5529 mm** |

The areas came down (from 19.27 / 85.11 / 93.54 m²) and now sit below the
trajectory footprint rather than far above it, which is the right direction but is
not evidence of correctness. The wall counts are still wrong — 6 to 9 where a
rectangle has 4 — and splitting an archive in half still yields walls that disagree
by six metres. **This is unsolved.** The synthetic benchmark could never have caught
the original bug, because a synthetic room is an empty box in which every
non-horizontal plane genuinely is a wall; it caught the second one only because the
doorway made the operator's path wider than the room.

**Too many walls is not caused by duplicate frames.** The obvious explanation is
redundancy: if the same frame counts many times, plane `support` measures how long
the operator lingered rather than how well a surface was seen. Measured, there are
**zero** byte-identical depth frames in any of the three archives — 0 of 1715, 0 of
5251, 0 of 9745. What is true is the mechanism behind the intuition: the captures
run at 60 Hz, consecutive frames are 8–9 mm apart, and 95–98% of them move the
camera under 2 cm, so a 14.5 m path holds only ~470 independent viewpoints.
`motion_dedup()` implements the obvious fix and it is **not** the default, because
measured against the index-strided baseline it halves floor-plane error
(`floor_only` 44.8 → 11.9 mm) while making wall selection worse
(`with_ceiling` 8 → 9 walls, area 27.6 → 8.5 m² against a 75.7 m² trajectory
footprint). Redundancy is real; it is not what produces the extra walls. See
`CHECKPOINTS.md` and `scripts/compare_baselines.py`.

**The photo tier is relative, not metric.** No frame in 238 contains a scale
reference and no EXIF survives. Self-scaling from each wall's own floor-to-ceiling
extent was tried and lands 30–75 % out on area. The plane fits are good — 5–49 mm
residual — but extents are not, because no single frame contains both corners of a
wall and a receding wall's 3D vertical extent is inflated by depth variation. This
is a capture-protocol omission, not a modelling choice.

**What the capture labels do and do not settle.** The photographer labelled each
wall and numbered the viewpoints of that wall — `m_wall_2.5` is the fifth view of
wall 2, `2_room_wall_2 (4)` the fourth — across two filename conventions. That
gives one fact for free: **each room folder contains walls 1, 2, 3 and 4 and
nothing else**, so the four-sided room model is supported by the capture itself and
does not rest on the tape alone. It is now recorded per room as
`topology_corroboration` in every field document.

It does not give dimensions, and measuring them from the labels fails for a
measurable reason. A wall's length comes from a view that spans its width, and the
incidence angle between the fitted wall normal and the optical axis — computable
per view with no camera pose — says whether a view does:

| Room | Walls | With a view under 40° | Best incidence |
|---|---|---|---|
| `master_bedroom` | 4 | 1 | **0.0°** |
| `main_hall` | 4 | 0 | 8.9° (rejected: vertical extent inconsistent) |
| `second_bedroom` | 4 | 0 | 68.5° |

**Only 2 of 12 walls have any view usable for measurement**, and both land near 45 %
of the tape value. The rest were only ever photographed at ≥ 68° incidence, nearly
edge-on, where monocular depth has almost no signal across a wall's width. Knowing
that four photos share a wall lets us pick the best of four bad views; it cannot
manufacture a good one. See `scripts/wall_perspective_consensus.py` and
`scripts/diag_wall_perspectives.py`.

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
| `examples/field_site/` | Committed generated real-site SVG/JSON outputs |
| `scripts/diag_boundary_rule.py` | Per-wall keep/discard evidence for the boundary rule |
| `scripts/diag_wall_planes.py` | Per-plane extent, normal and camera-distance diagnostics |
| `scripts/diag_trajectory_extent.py` | How much floor the camera path actually covered |
| `scripts/diag_duplicate_frames.py` | Duplicate/redundancy audit of the sample archives |
| `scripts/compare_baselines.py` | Side-by-side comparison of two baseline JSONs |
| `WALK_IN.md` | Ten-minute demo script for the walk-in |
| `docs/compliance_matrix.md` | Requirement-by-requirement status |
| `data/field_ground_truth.json` | Tape reference for three rooms |
| `data/demo_room.zip` | Runnable demo with exact truth |

## 10. Compliance

See [`compliance_matrix.md`](compliance_matrix.md) — 35 requirements: **24 Met,
1 Partial, 2 Prototype, 8 Not met or Not done**, each with a file that proves it.
Two of the 24 are qualified: the room-polygon requirement is Met on synthetic data
and fails on real archives, and the reference benchmark is a substitute for a
Magicplan comparison that was not performed. Strip those and it is 22 unqualified.
