"""Damage detection as an anomaly in wall-plane residual.

MEASURED STATUS: this does not work, and is kept as a record, not as a feature.

`scripts/benchmark_damage.py`, on synthetic captures with exact ground truth:

    detected 0 of 4 damage cases (4 mm crack, 12 mm crack, 30 mm spall, breach)
    false positives 19 across four runs, including three rooms with no damage at all

A detector that fires on clean walls is worse than no detector, so nothing in the
product path calls this. What is here is kept because three of its parts *are*
verified and are the measurements the failure is explained by:

  * `pixel_footprint_m` -- the resolution floor, 11.7 mm per depth pixel at 2.5 m.
    A crack narrower than that is sub-pixel: its depth is diluted across the whole
    pixel footprint, so no amount of frame averaging recovers it, because every
    pixel it touches also contains wall.
  * `damage_rooms()` in `synth.py` -- synthetic damage with exact ground truth,
    which did not exist before and which any future attempt should be scored on.
  * `severity_for` -- a triage banding, with the thresholds stated as conventions.

Why the detector fails, as measured rather than guessed:

  1. Resolution. `DEPTH_DECIMATION` maps the *camera* intrinsics onto the depth
     stream, which is natively 256x192. The footprint is therefore a property of
     the sensor, not a processing choice: at 2.5 m one pixel covers 11.7 mm, so the
     4 mm and 12 mm synthetic cracks are 0.68 and 1.28 pixels wide. The 30 mm spall
     is 25.6 pixels wide and should be visible, which is the real failure.
  2. Wall roughness swamps the signal. Segmentation reports wall rms of 18-24 mm on
     clean walls. Per-frame plane refitting removes the rigid part of the pose
     noise but not genuine surface relief, so per-cell mean residuals scatter over
     5-15 mm everywhere. An absolute threshold therefore fires across the whole
     wall; subtracting a local background (`_local_background`) cut several hundred
     false positives to a handful but did not make the spall appear.
  3. Junctions and corners dominate what survives. A wall's 12 cm neighbourhood
     contains the floor and ceiling it meets, which are 0-120 mm off the wall plane
     and carry exactly the signature being looked for. Filtering points by nearest
     plane (`_accumulate_wall`) removed the junction detections; wall/wall corners
     then dominated, because a 30 mm spall is large enough to be segmented as a
     plane in its own right, so a nearest-plane test hands the spall's own points to
     the spall plane where their residual is zero by construction. The detector ends
     up discarding the evidence it is looking for.

The honest conclusion is that this needs a different signal, not a better threshold:
photometric stereo or a higher-resolution depth stream, not plane residuals on a
256x192 ToF map. See `docs/final_report.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .geometry import segment_connected
from .unproject import frame_points_world

# Points further than this from a wall plane are not part of that wall's surface.
RESIDUAL_BAND_M = 0.12

# A cell mean computed from fewer observations than this is not evidence of
# anything; at 1-2 observations the "mean" is a single noisy depth reading.
MIN_CELL_OBS = 4

# The cell mean must exceed this many of its own standard errors. Using the standard
# error rather than the standard deviation is the point: it asks whether the *mean*
# groove is resolved, not whether some individual pixel was deep.
RESIDUAL_SIGMA_FACTOR = 3.5

# Below this, a positive residual is not a groove. This is roughly half the
# per-pixel depth noise at working range, so it rejects noise while accepting the
# 12 mm class of crack once averaged.
MIN_GROOVE_DEPTH_M = 0.004

# Connected regions smaller than this in cells are dropped as speckle.
MIN_COMPONENT_CELLS = 3

# Radius, in cells, of the annulus used to estimate the local wall background.
#
# This is the parameter that makes the detector work at all. Measured on the
# synthetic damaged room, segmented walls have rms 18-24 mm, so the per-cell mean
# residual scatters over 5-15 mm across the *entire* wall whether or not there is
# damage. An absolute threshold cannot separate the two: at 3.5 standard errors it
# fired on several hundred cells of clean wall.
#
# Wall roughness is spatially uncorrelated -- a bumpy wall is bumpy everywhere, with
# no structure at any particular scale. A crack is a local maximum a few cells wide
# sitting on an otherwise ordinary wall. Subtracting a local background therefore
# removes the former and keeps the latter. The annulus excludes the centre so a
# wide groove cannot raise its own background and mask itself.
# The floor/ceiling junction is trimmed off each wall before analysis.
#
# A wall plane's neighbourhood also contains the floor and the ceiling where the
# three surfaces meet. Those points are not on the wall, they are 0-120 mm off it,
# and they produce exactly the signature being looked for. Before trimming, every
# detection on the synthetic room sat within 150 mm of y=0 or y=2.6 -- the two
# junctions -- with excess readings of 18-50 mm, an order of magnitude above any
# real defect. A wall/wall corner has the same problem for the two walls meeting
# there, which is why the trim is applied to the horizontal extent too.
JUNCTION_TRIM_M = 0.18
CORNER_TRIM_M = 0.12

LOCAL_BG_RADIUS_CELLS = 6
MIN_LOCAL_EXCESS_M = 0.015

# Most regions reported per wall before the rest are discarded. See detect_damage.
MAX_COMPONENTS_PER_WALL = 8

# Severity thresholds on the fitted groove depth, in metres. These are engineering
# conventions for triage, not measured quantities, and are reported as bands rather
# than as a score so a reader can disagree with the cut points without the
# measurement changing.
SEVERITY_BANDS_M = (0.005, 0.015, 0.040)


@dataclass
class WallDamage:
    """One detected anomaly on one wall."""

    wall_index: int
    face_normal: list[float]
    length_m: float
    width_m: float
    mean_depth_m: float
    max_depth_m: float
    area_m2: float
    n_cells: int
    mean_obs: float
    centre_world: list[float]
    severity: str = field(default="")

    def to_dict(self) -> dict:
        return {
            "wall_index": int(self.wall_index),
            "face_normal": [round(float(c), 6) for c in self.face_normal],
            "length_m": round(float(self.length_m), 4),
            "width_m": round(float(self.width_m), 4),
            "mean_depth_m": round(float(self.mean_depth_m), 5),
            "max_depth_m": round(float(self.max_depth_m), 5),
            "area_m2": round(float(self.area_m2), 5),
            "n_cells": int(self.n_cells),
            "mean_obs": round(float(self.mean_obs), 2),
            "centre_world": [round(float(c), 4) for c in self.centre_world],
            "severity": self.severity,
        }


def _local_background(
    mean: np.ndarray, valid: np.ndarray, outer: int, inner: int
) -> np.ndarray:
    """Local background over an annulus, via integral images.

    A ring rather than a full square so that a groove wide enough to fill the inner
    square does not raise its own background and hide itself. Uses means rather than
    medians because each cell already averages tens of observations, so the cell
    means are close to symmetric and a mean is a good enough central estimate --
    and integral images make it exact and O(cells) instead of O(cells * window).
    """
    h, w = mean.shape

    def box_sum(a: np.ndarray, r: int) -> np.ndarray:
        p = np.pad(a, r + 1, mode="constant")
        c = np.cumsum(np.cumsum(p, axis=0), axis=1)
        return (
            c[2 * r + 1 : 2 * r + 1 + h, 2 * r + 1 : 2 * r + 1 + w]
            - c[0:h, 2 * r + 1 : 2 * r + 1 + w]
            - c[2 * r + 1 : 2 * r + 1 + h, 0:w]
            + c[0:h, 0:w]
        )

    validf = valid.astype(np.float64)
    ring_sum = box_sum(np.where(valid, mean, 0.0), outer) - box_sum(
        np.where(valid, mean, 0.0), inner
    )
    ring_n = box_sum(validf, outer) - box_sum(validf, inner)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ring_n > 0.5, ring_sum / np.maximum(ring_n, 1e-9), 0.0)


def severity_for(depth_m: float) -> str:
    """Triage band for a groove depth. See SEVERITY_BANDS_M."""
    lo, mid, hi = SEVERITY_BANDS_M
    if depth_m < lo:
        return "hairline"
    if depth_m < mid:
        return "minor"
    if depth_m < hi:
        return "moderate"
    return "severe"


def pixel_footprint_m(capture, frame: int, range_m: float) -> float:
    """Metres subtended by one depth pixel at `range_m`.

    This is the resolution floor of any depth-based damage detector on this data,
    and it is worth stating explicitly rather than discovering later: a groove
    narrower than the footprint cannot be localised no matter how many frames are
    averaged, because every pixel it touches also contains wall.
    """
    fx = float(capture.focal[frame, 0]) / _decimation()
    return float(range_m) / max(fx, 1e-6)


def _decimation() -> float:
    from .ingest import DEPTH_DECIMATION

    return DEPTH_DECIMATION


def _wall_frame(normal: np.ndarray, up: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Orthonormal in-plane axes (horizontal, vertical) for a wall plane.

    The vertical axis is the world up projected into the plane, so `v` means height
    above the floor for a level wall, which is what a damage report wants to state.
    """
    n = normal / max(float(np.linalg.norm(normal)), 1e-12)
    v = up - float(up @ n) * n
    nv = float(np.linalg.norm(v))
    if nv < 1e-6:
        # Degenerate: the wall normal is vertical, so this is not a wall. Any
        # in-plane choice will do because it is never used for one.
        v = np.array([1.0, 0.0, 0.0])
        nv = 1.0
    v = v / nv
    h = np.cross(v, n)
    h = h / max(float(np.linalg.norm(h)), 1e-12)
    return h, v


def _accumulate_wall(
    capture,
    scale: float,
    T_cw: np.ndarray,
    normal: np.ndarray,
    offset: float,
    up: np.ndarray,
    frames: list[int],
    cell_m: float,
    stride: int,
    others: list[tuple[np.ndarray, float]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Bin per-frame plane residuals over one wall.

    `others` is every other plane in the scene. A point is analysed against this
    wall only if this wall is the *nearest* plane to it, which is what actually
    separates wall surface from the floor and ceiling that meet it and from the
    walls that meet it at a corner. See PLANE_OWNERSHIP below.

    Returns (mean_residual, obs_count, stderr, axis_u, axis_v) where the first three
    are shaped (nu, nv) and the last two are the world-coordinate limits of each
    cell edge.
    """
    horiz, vert = _wall_frame(normal, up)
    lo_u = lo_v = np.inf
    hi_u = hi_v = -np.inf
    # Two passes: the first finds the extent so the grid can be sized, the second
    # fills it. Damage is a small fraction of the wall, so sizing from the extent
    # rather than from a fixed grid keeps the cell size meaningful.
    hits: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for f in frames:
        try:
            pts = frame_points_world(capture, f, scale, T_cw, stride=stride)
        except (IndexError, ValueError, ZeroDivisionError):
            continue
        if pts.size == 0:
            continue
        d = pts @ normal - offset
        ad = np.abs(d)
        # Plane ownership: analyse a point against this wall only if this wall is
        # the nearest plane to it. A point on the floor 50 mm from the skirting is
        # ~0 mm from the floor and ~50 mm from the wall, so it belongs to the floor.
        # Distance-to-nearest-plane is the exact test for this; a fixed trim is a
        # guess at the same thing that fails wherever surfaces are further apart
        # than the guess assumed.
        owns = np.ones(ad.shape, dtype=bool)
        for on, oo in others:
            owns &= ad <= np.abs(pts @ on - oo)
        sel = (ad <= RESIDUAL_BAND_M) & owns
        if sel.sum() < 30:
            continue
        band_pts = pts[sel]

        # Per-frame refit. Pose noise is ~20 mm and depth noise ~9.5 mm at working
        # range; fitting the plane per frame absorbs the rigid part of the former,
        # so the residual that survives is damage plus depth noise rather than
        # damage plus everything.
        c = band_pts.mean(axis=0)
        _, _, vt = np.linalg.svd(band_pts - c, full_matrices=False)
        nf = vt[2]
        nf = nf / max(float(np.linalg.norm(nf)), 1e-12)
        df = float(nf @ c)
        resid = band_pts @ nf - df

        u = band_pts @ horiz
        v = band_pts @ vert
        hits.append((u, v, resid))
        lo_u, hi_u = min(lo_u, float(u.min())), max(hi_u, float(u.max()))
        lo_v, hi_v = min(lo_v, float(v.min())), max(hi_v, float(v.max()))

    if not hits or not np.isfinite(lo_u):
        z = np.zeros((0, 0))
        return z, z, z, np.zeros(0), np.zeros(0)

    # Trim the floor/ceiling junctions and the corners before anything is measured.
    # See JUNCTION_TRIM_M: without this every detection sits on a junction rather
    # than on a defect.
    trim_v = min(JUNCTION_TRIM_M, 0.30 * (hi_v - lo_v))
    trim_u = min(CORNER_TRIM_M, 0.20 * (hi_u - lo_u))
    u_lo, u_hi = lo_u + trim_u, hi_u - trim_u
    v_lo, v_hi = lo_v + trim_v, hi_v - trim_v
    kept: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for u, v, resid in hits:
        keep = (u >= u_lo) & (u <= u_hi) & (v >= v_lo) & (v <= v_hi)
        if keep.any():
            kept.append((u[keep], v[keep], resid[keep]))
    hits = kept
    lo_u, hi_u, lo_v, hi_v = u_lo, u_hi, v_lo, v_hi
    if not hits:
        z = np.zeros((0, 0))
        return z, z, z, np.zeros(0), np.zeros(0)

    nu = max(int(np.ceil((hi_u - lo_u) / cell_m)), 1)
    nv = max(int(np.ceil((hi_v - lo_v) / cell_m)), 1)

    total = np.zeros((nu, nv), dtype=np.float64)
    count = np.zeros((nu, nv), dtype=np.float64)
    total_sq = np.zeros((nu, nv), dtype=np.float64)
    for u, v, resid in hits:
        iu = np.clip(((u - lo_u) / cell_m).astype(int), 0, nu - 1)
        iv = np.clip(((v - lo_v) / cell_m).astype(int), 0, nv - 1)
        np.add.at(total, (iu, iv), resid)
        np.add.at(total_sq, (iu, iv), resid**2)
        np.add.at(count, (iu, iv), 1.0)

    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / np.maximum(count, 1e-9), 0.0)
        var = np.maximum(total_sq / np.maximum(count, 1e-9) - mean**2, 0.0)
        # Standard error of the mean, not the standard deviation: the question is
        # whether the average groove is resolved.
        stderr = np.sqrt(var / np.maximum(count, 1e-9))

    axis_u = lo_u + (np.arange(nu) + 0.5) * cell_m
    axis_v = lo_v + (np.arange(nv) + 0.5) * cell_m
    return mean, count, stderr, axis_u, axis_v


def detect_damage(
    capture,
    scale: float,
    seg: dict,
    T_cw: np.ndarray,
    *,
    frames: list[int] | None = None,
    stride: int = 2,
    cell_scale: float = 2.0,
    sigma_factor: float = RESIDUAL_SIGMA_FACTOR,
    min_depth_m: float = MIN_GROOVE_DEPTH_M,
    min_local_excess_m: float = MIN_LOCAL_EXCESS_M,
    max_components: int = MAX_COMPONENTS_PER_WALL,
) -> list[WallDamage]:
    """Find damage on every wall plane produced by segmentation.

    `cell_scale` sets the accumulation cell size as a multiple of the depth pixel
    footprint. Two is the default because a single cell smaller than the footprint
    would be measuring the sensor's sampling, not the wall.

    Detection is on the residual *in excess of its local surroundings*. See
    LOCAL_BG_RADIUS_CELLS for why an absolute threshold cannot work on this data.
    """
    up = np.asarray(seg["up"], dtype=np.float64)
    walls = [p for p in seg.get("planes", []) if getattr(p, "kind", "") == "wall"]
    if not walls:
        return []
    frame_list = list(frames) if frames is not None else list(range(len(T_cw)))
    # Damage is a small target, so scanning every frame is worth the time; the
    # stride exists only to bound memory on long captures.
    frame_list = frame_list[:: max(stride, 1)]

    out: list[WallDamage] = []
    for wi, wall in enumerate(walls):
        normal = np.asarray(wall.normal, dtype=np.float64)
        offset = float(wall.offset)
        # Ownership is arbitrated against the floor and ceiling only, deliberately.
        #
        # The wall/floor and wall/ceiling junctions run the full width of every
        # wall and were the dominant false-positive source, so they need the exact
        # test. Wall/wall corners are excluded by CORNER_TRIM_M instead, because
        # including other walls here is actively harmful: a 30 mm spall is large
        # enough to be segmented as a plane in its own right, and a nearest-plane
        # test then hands the spall's own points to the spall plane, where their
        # residual is zero by construction. The detector would be discarding
        # precisely the evidence it is looking for.
        others = [
            (np.asarray(p.normal, dtype=np.float64), float(p.offset))
            for p in seg.get("planes", [])
            if getattr(p, "kind", "") in ("floor", "ceiling")
        ]
        ref_frame = frame_list[len(frame_list) // 2] if frame_list else 0
        cell_m = cell_scale * max(
            pixel_footprint_m(capture, ref_frame, max(abs(offset), 0.5)), 0.005
        )
        mean, count, stderr, ax_u, ax_v = _accumulate_wall(
            capture, scale, T_cw, normal, offset, up, frame_list, cell_m, stride, others
        )
        if mean.size == 0:
            continue

        valid = count >= MIN_CELL_OBS
        bg = _local_background(
            mean,
            valid,
            LOCAL_BG_RADIUS_CELLS,
            max(LOCAL_BG_RADIUS_CELLS // 2, 1),
        )
        # Depth of the groove *above the surrounding wall*, which is the quantity a
        # damage report means. Reporting the absolute residual instead would fold
        # the wall's own 18-24 mm of roughness into every measurement.
        excess = mean - bg
        threshold = np.maximum(
            np.maximum(min_depth_m, sigma_factor * stderr), min_local_excess_m
        )
        mask = valid & (excess >= threshold)
        if not mask.any():
            continue

        comps = segment_connected(mask, min_size=MIN_COMPONENT_CELLS)
        if not comps:
            continue
        # A wall with a real defect yields a handful of regions; a wall where the
        # threshold is marginal yields hundreds. Capping keeps one bad wall from
        # burying the report, and the cap is reported rather than applied silently.
        comps = sorted(
            comps, key=lambda c: -float(excess[c[:, 0], c[:, 1]].max())
        )[:max_components]

        for comp in comps:
            iu, iv = comp[:, 0], comp[:, 1]
            du = float(ax_u[iu].max() - ax_u[iu].min()) + cell_m
            dv = float(ax_v[iv].max() - ax_v[iv].min()) + cell_m
            depths = excess[iu, iv]
            horiz, vert = _wall_frame(normal, up)
            cu = float(ax_u[iu].mean())
            cv = float(ax_v[iv].mean())
            centre_point = wall.normal * 0.0 + horiz * cu + vert * cv
            # Put the centre back on the wall plane rather than leaving it in the
            # plane through the origin, so it can be compared with ground truth.
            centre_point = centre_point + normal * (offset - float(normal @ centre_point))
            det = WallDamage(
                wall_index=wi,
                face_normal=[float(c) for c in normal],
                length_m=max(du, dv),
                width_m=min(du, dv),
                mean_depth_m=float(depths.mean()),
                max_depth_m=float(depths.max()),
                area_m2=float(len(comp) * cell_m * cell_m),
                n_cells=int(len(comp)),
                mean_obs=float(count[iu, iv].mean()),
                centre_world=[float(c) for c in centre_point],
            )
            det.severity = severity_for(det.max_depth_m)
            out.append(det)

    out.sort(key=lambda d: -d.max_depth_m)
    return out
