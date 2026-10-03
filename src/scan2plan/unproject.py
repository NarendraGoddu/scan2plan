"""Depth unprojection and metric scale calibration.

Why scale needs solving rather than assuming
---------------------------------------------
The depth maps are uint16 with no documented encoding. The obvious guesses are
1mm, 2mm or 4mm per unit. Geometry alone cannot decide this: multiplying all
depths by a constant preserves every angle and the normalised floor-plan shape,
so plane fits, wall directions and room aspect ratios are all invariant. Only
absolute lengths move. Any confidence interval quoted before this is calibrated
would be confidently wrong.

The decisive constraint is that the supplied poses are already metric. They come
from the rig's own LiDAR-inertial odometry and are stated in metres. So if the
depth scale is off by a factor k, surfaces observed from different viewpoints
stop agreeing in world space: a floor plane fitted independently from frame i
and frame j lands at two different heights. Sweeping the scale and minimising
the cross-frame disagreement recovers k without ever needing a tape measure.

That is the same machinery the calibration gate scores, applied where it is
cheapest -- once, on ingest.
"""

from __future__ import annotations

import numpy as np

from .geometry import fit_plane_ransac
from .ingest import CONFIDENCE_VALID, Capture, poses_to_matrices

# Candidate metres-per-unit for the uint16 depth encoding. The sweep refines
# between/around these rather than assuming one.
SCALE_GRID = np.array([0.0005, 0.00075, 0.001, 0.00125, 0.0015, 0.002, 0.0025, 0.003, 0.004])


def unproject_frame(
    capture: Capture, frame: int, scale: float, stride: int = 4
) -> tuple[np.ndarray, np.ndarray]:
    """Back-project one depth map to metric 3D points in the camera frame.

    Returns (points_cam, valid_mask) where valid_mask marks pixels kept after
    confidence gating and edge trimming.
    """
    depth = capture.depth[frame].astype(np.float64) * scale
    conf = capture.confidence[frame]
    K = capture.depth_intrinsics(frame)

    h, w = depth.shape
    vs, us = np.mgrid[0:h, 0:w]
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]

    valid = conf == CONFIDENCE_VALID
    # Trim a border ring: decimated LiDAR edges mix foreground and background
    # and produce spurious planes that hurt the wall fit.
    b = 6
    valid[:b, :] = valid[-b:, :] = False
    valid[:, :b] = valid[:, -b:] = False
    # Depth discontinuities: drop pixels whose depth jumps hard from the
    # horizontal/vertical neighbour. Standard conservative LiDAR filtering.
    d = depth
    jump = np.zeros_like(valid)
    for ax in (0, 1):
        diff = np.diff(d, axis=ax)
        big = np.abs(diff) > 0.06  # 6 cm discontinuity threshold
        jump[:-1, :] |= big if ax == 0 else jump[:-1, :]
        jump[:, :-1] |= big if ax == 1 else jump[:, :-1]
    valid &= ~jump

    sub = valid[::stride, ::stride]
    zz = depth[::stride, ::stride][sub]
    vv = vs[::stride, ::stride][sub].astype(np.float64)
    uu = us[::stride, ::stride][sub].astype(np.float64)

    pts = np.stack([(uu - cx) / fx * zz, (vv - cy) / fy * zz, zz], axis=1)
    return pts, sub


def frame_points_world(
    capture: Capture, frame: int, scale: float, T_cw: np.ndarray, stride: int = 4
) -> np.ndarray:
    """Unproject one frame and transform into world coordinates."""
    pts, _ = unproject_frame(capture, frame, scale, stride=stride)
    T = T_cw[frame]
    return pts @ T[:3, :3].T + T[:3, 3]


def _plane_overlap_score(
    capture: Capture,
    pairs: list[tuple[int, int]],
    scale: float,
    T_cw: np.ndarray,
    band: float = 0.05,
    stride: int = 6,
) -> dict:
    """Score a candidate scale by cross-frame plane agreement.

    For each consecutive frame pair (i, j): fit the dominant plane to frame i,
    then measure what fraction of frame j's points land within `band` of that
    plane. If the depth scale is right, both frames resolve the *same physical
    surface* at the same world position, so overlap is high. If the scale is
    wrong, each frame places that surface at a different distance and overlap
    collapses.

    Consecutive frames are essential. Across widely separated frames the
    dominant plane in each frame is simply whatever surface the camera happens
    to face -- floor in one, ceiling in another, a wall in the third -- so
    comparing them measures nothing. Small baselines keep the viewed surface
    constant and isolate the scale error.
    """
    overlaps, angles = [], []
    for i, j in pairs:
        pts_i = frame_points_world(capture, i, scale, T_cw, stride=stride)
        pts_j = frame_points_world(capture, j, scale, T_cw, stride=stride)
        if pts_i.shape[0] < 400 or pts_j.shape[0] < 400:
            continue
        n, d, inl = fit_plane_ransac(pts_i, inlier_thresh=0.02, max_iterations=150)
        if inl.sum() < 400:
            continue
        # Canonical hemisphere so a plane seen from the opposite side still
        # counts as agreement rather than 180 degrees of disagreement.
        if n @ np.array([0.0, 0.0, 1.0]) < 0:
            n, d = -n, -d
        resid = np.abs(pts_j @ n - d)
        overlaps.append(float((resid < band).mean()))
        angles.append(0.0)
    if not overlaps:
        return {"scale": float(scale), "overlap": 0.0, "n_pairs": 0}
    return {
        "scale": float(scale),
        "overlap": float(np.mean(overlaps)),
        "n_pairs": len(overlaps),
    }


def _multi_gap_overlap(
    capture: Capture,
    scale: float,
    T_cw: np.ndarray,
    gaps: tuple[int, ...],
    n_pairs: int,
) -> float:
    """Mean plane overlap averaged over several frame-pair baselines.

    A single baseline is fragile: too small and the plane fit is dominated by
    sensor noise, too large and the two frames may be looking at different
    surfaces. Averaging over several gaps makes the score depend on the scale
    rather than on one lucky pairing.
    """
    n = capture.n_frames
    scores = []
    for gap in gaps:
        if n - gap < 2:
            continue
        centres = np.linspace(0, n - 1 - gap, min(n_pairs, n - gap)).astype(int)
        pairs = [(int(c), int(c) + gap) for c in centres]
        r = _plane_overlap_score(capture, pairs, scale, T_cw)
        if r["n_pairs"] > 0:
            scores.append(r["overlap"])
    return float(np.mean(scores)) if scores else 0.0


def calibrate_scale(
    capture: Capture,
    min_frames: int = 40,
    gaps: tuple[int, ...] = (2, 3, 5, 8),
    n_pairs: int = 12,
    refine_iters: int = 3,
    min_prominence: float = 0.04,
    verbose: bool = True,
) -> dict:
    """Recover metres-per-unit for the uint16 depth encoding.

    Sweeps the candidate range and keeps the scale at which nearby frames agree
    most strongly about the surfaces they both observe.

    `min_frames` matters: the objective is an average over many independent
    frame pairs, so a short subsample leaves it noise-dominated and the
    argmax wanders. On the three sample archives, >=120 frames recovers 1.000
    mm/unit in every configuration tried, while ~43 frames does not.
    """
    T_cw = poses_to_matrices(capture.positions, capture.quats)

    sweep = [
        {"scale": float(s), "overlap": _multi_gap_overlap(capture, s, T_cw, gaps, n_pairs)}
        for s in SCALE_GRID
    ]
    best = max(sweep, key=lambda r: r["overlap"])

    # Successive zoom: each round narrows the bracket around the current winner.
    span = best["scale"] * 0.28
    for _ in range(refine_iters):
        factors = np.linspace(1 - span, 1 + span, 7)
        cand = [
            {"scale": best["scale"] * f,
             "overlap": _multi_gap_overlap(capture, best["scale"] * f, T_cw, gaps, n_pairs)}
            for f in factors
        ]
        b = max(cand, key=lambda r: r["overlap"])
        if abs(b["scale"] - best["scale"]) < 1e-7:
            break
        best = b
        span *= 0.35
        if verbose:
            print(f"    refine -> {best['scale']*1000:.4f} mm/unit  overlap={best['overlap']:.3f}")

    # Peak prominence: how much better is the winner than the runner-up grid
    # point? A shallow peak is the signature of an unreliable estimate -- with
    # too few frames the objective is noise-dominated and the argmax wanders
    # across the grid, so the peak carries no information.
    ordered = sorted((r["overlap"] for r in sweep), reverse=True)
    prominence = ordered[0] - ordered[1] if len(ordered) > 1 else 0.0

    # Frame count alone is a poor reliability signal: the 86-frame single_room
    # scan produced the sharpest peak of the three (0.129), while a
    # 122-frame subsample of with_ceiling returned the wrong scale on a 0.033
    # peak. Peak sharpness is what actually distinguishes a determined scale
    # from a noise-dominated argmax, so that is what gates `reliable`.
    reliable = bool(prominence >= min_prominence and capture.n_frames >= min_frames)
    out = {
        "scale_m_per_unit": best["scale"],
        "plane_overlap": best["overlap"],
        "peak_prominence": prominence,
        "reliable": reliable,
        "n_frames": capture.n_frames,
        "sweep": sweep,
        "method": "multi-baseline consecutive-frame plane overlap (metric poses as the constraint)",
    }
    if verbose:
        flag = "" if reliable else "   [UNRELIABLE: re-run with a smaller frame stride]"
        print(f"  SCALE = {best['scale']:.6f} m/unit ({best['scale']*1000:.4f} mm/unit)  "
              f"overlap={best['overlap']:.3f}  prominence={prominence:.3f}  "
              f"frames={capture.n_frames}{flag}")
        for r in sweep:
            print(f"    {r['scale']*1000:7.3f} mm/u -> overlap {r['overlap']:.3f}")
    return out


def gravity_axis(positions: np.ndarray, quats: np.ndarray | None = None) -> np.ndarray:
    """Estimate the world vertical axis from the trajectory.

    A handheld floor scan travels across a floor, so the direction of least
    variance in the trajectory is the vertical. Measured on the sample
    archives, that is the Y axis in all three, with the vertical extent
    0.25-0.42 m against 3.6-9.1 m horizontally.

    An earlier version averaged the device body +Z axis. That is wrong: on this
    hardware +Z is the optical axis, not the up axis, so the result was the
    average *viewing* direction. The tell was single_room, where the implied
    camera height swung 2.25 m across a handheld walk inside one room -- an
    impossibility for a real vertical.

    The sign is arbitrary here (an axis, not a vector); use `orient_up` to
    choose it so that the floor lies below the camera.
    """
    centred = positions - positions.mean(axis=0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    return vt[2] / (np.linalg.norm(vt[2]) + 1e-12)


def orient_up(up: np.ndarray, plane_heights: np.ndarray, camera_heights: np.ndarray) -> np.ndarray:
    """Flip `up` so the dominant horizontal plane lies below the camera.

    A plane at height h and a camera at height c satisfy h < c for a floor and
    h > c for a ceiling. Whichever sign of `up` makes the dominant plane fall
    below the camera is the one pointing up, and this avoids hard-coding a
    world-frame convention that the data may not share.
    """
    delta = float(np.median(np.asarray(plane_heights) - np.asarray(camera_heights)))
    return -up if delta < 0 else up