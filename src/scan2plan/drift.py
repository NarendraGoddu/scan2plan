"""Measure accumulated drift in the supplied poses, before correcting anything.

This is the 'before' side of the drift ablation the assessment requires, and it
decides whether loop closure earns a place in the pipeline at all.

Method, chosen to isolate drift from everything else:

    For each frame independently, fit the dominant plane to that frame's points
    in world coordinates using the supplied poses. The floor is a single global
    plane, so if the poses were perfect every frame would report the same
    offset. The spread of those per-frame offsets *is* the drift, measured in
    millimetres, with no reference geometry required.

    Two spreads are reported:
      - offset spread: how far the floor drifts vertically
      - angular spread: how far it tilts

A floor fit in a single frame is also compared against the global fit, which
isolates per-frame noise from accumulated error.
"""

from __future__ import annotations

import numpy as np

from .geometry import fit_plane_ransac
from .ingest import Capture, poses_to_matrices
from .unproject import frame_points_world, gravity_axis

# A frame is only accepted as observing the floor if the fitted plane is
# genuinely horizontal and well supported. Without this gate, frames where the
# camera faces a wall return a wall fit, and those pollute the drift statistics
# into nonsense -- an early run reported a "floor tilt" of 82 degrees purely
# from mixing floors and walls in one average.
MIN_FLOOR_ALIGNMENT = 0.90  # |n . up| ; 0.90 is ~25 degrees off horizontal
MIN_FLOOR_INLIERS = 800
# A floor must sit meaningfully below the camera. Handheld height is roughly
# 1.0-1.7 m, so anything closer than this to the camera plane is a ledge,
# a table top or a misfit, not the ground.
MIN_FLOOR_DROP_M = 0.40


def _canonical(n: np.ndarray, d: float, ref: np.ndarray) -> tuple[np.ndarray, float]:
    """Fold the plane into the hemisphere whose normal points along `ref`.

    Must use the same axis the height is measured along, otherwise normals get
    folded 180 degrees the wrong way and every tilt statistic is garbage.
    """
    if n @ ref < 0:
        return -n, -d
    return n, d


def _plane_height(n: np.ndarray, d: float, ref: np.ndarray) -> float:
    """Signed position of the plane along `ref`.

    The point of the unit-normal plane closest to the origin is `d * n`, so its
    component along `ref` gives the height. Using this instead of `d / (n . ref)`
    avoids dividing by a near-zero when the plane is poorly aligned.
    """
    return float(d * (n @ ref))


def measure_drift(
    capture: Capture,
    scale: float,
    n_frames: int = 40,
    gravity_hint: np.ndarray | None = None,
    verbose: bool = True,
) -> dict:
    """Per-frame floor fits under the supplied poses.

`gravity_hint` supplies the vertical axis; its sign is ignored, since the floor
is identified by lying below the camera and the axis is re-oriented to match
(see `unproject.orient_up`).
"""
    T_cw = poses_to_matrices(capture.positions, capture.quats)
    n = capture.n_frames
    idx = np.unique(np.linspace(0, n - 1, min(n_frames, n)).astype(int))

    ref = gravity_hint if gravity_hint is not None else gravity_axis(capture.positions)
    ref = ref / (np.linalg.norm(ref) + 1e-12)

    # Pass 1: collect every credible horizontal plane per frame, tagged with
    # its offset from the camera. Which of these is "the floor" is decided
    # from the pooled statistics below rather than assumed per frame, because
    # an upward-tilted scan makes the ceiling its dominant horizontal plane and
    # mixing the two inflates apparent drift by metres.
    candidates = []
    for f in idx:
        pts = frame_points_world(capture, int(f), scale, T_cw, stride=3)
        if pts.shape[0] < 600:
            continue
        cam_h = float(capture.positions[int(f)] @ ref)
        seen: set[tuple[int, int]] = set()
        for _ in range(6):
            nn, dd, inl = fit_plane_ransac(pts, inlier_thresh=0.02, max_iterations=160)
            nn, dd = _canonical(nn, dd, ref)
            n_in = int(inl.sum())
            if abs(nn @ ref) < MIN_FLOOR_ALIGNMENT or n_in < MIN_FLOOR_INLIERS:
                continue
            key = (n_in // 200, int(round(dd * 20)))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "frame": int(f),
                    "normal": nn,
                    "offset": float(dd),
                    "height_m": _plane_height(nn, dd, ref),
                    "camera_height_m": cam_h,
                    "delta_m": _plane_height(nn, dd, ref) - cam_h,
                    "inliers": n_in,
                }
            )

    if len(candidates) < 4:
        return {
            "n_frames_used": 0,
            "n_rejected": len(idx),
            "note": "no credible horizontal planes found",
        }

    # Pass 2: pick the floor cluster. A handheld sensor sits roughly
    # 0.6-2.2 m above the floor, so restrict to that band and require the
    # offset sign to be consistent. This is sign-free: it discovers which way
    # is up from the geometry instead of trusting a world-frame convention.
    deltas = np.array([c["delta_m"] for c in candidates])
    band = (np.abs(deltas) > 0.6) & (np.abs(deltas) < 2.2)
    if band.sum() < 4:
        return {
            "n_frames_used": 0,
            "n_rejected": len(idx),
            "note": f"no plane in handheld-height band (delta range "
                    f"{deltas.min():.2f}..{deltas.max():.2f} m)",
        }
    floor_sign = 1.0 if np.median(deltas[band]) > 0 else -1.0

    per_frame = []
    rejected = []
    for c in candidates:
        if np.sign(c["delta_m"]) != floor_sign or abs(c["delta_m"]) > 2.2:
            continue
        # keep the strongest candidate per frame
        prev = next((p for p in per_frame if p["frame"] == c["frame"]), None)
        if prev is None or c["inliers"] > prev["inliers"]:
            if prev is None:
                per_frame.append(c)
            else:
                per_frame[per_frame.index(prev)] = c

    # Orient `up` so the floor really is below the camera, then re-express.
    if floor_sign > 0:
        ref = -ref
    for p in per_frame:
        p["height_m"] = p["height_m"] if floor_sign < 0 else -p["height_m"]
        p["camera_height_m"] = (
            p["camera_height_m"] if floor_sign < 0 else -p["camera_height_m"]
        )

    if len(per_frame) < 4:
        return {
            "n_frames_used": len(per_frame),
            "n_rejected": len(rejected),
            "note": "too few frames with a consistent floor plane",
        }

    heights = np.array([p["height_m"] for p in per_frame])
    normals = np.array([p["normal"] for p in per_frame])

    # Tilt of each frame's floor relative to the mean floor.
    mean_n = normals.mean(0)
    mean_n /= np.linalg.norm(mean_n) + 1e-12
    tilt = np.degrees(np.arccos(np.clip(np.abs(normals @ mean_n), -1, 1)))

    # Vertical drift as a function of distance travelled, which is how a plan
    # actually gets corrupted: error that grows along the walk.
    steps = np.linalg.norm(np.diff(capture.positions[idx], axis=0), axis=1)
    travelled = np.concatenate([[0.0], np.cumsum(steps)])

    out = {
        "n_frames_used": len(per_frame),
        "n_rejected": len(rejected),
        "floor_height_mean_m": float(heights.mean()),
        "floor_height_std_mm": float(np.std(heights) * 1000.0),
        "floor_height_p95_spread_mm": float(
            (np.percentile(heights, 95) - np.percentile(heights, 5)) * 1000.0
        ),
        "floor_height_range_mm": float((heights.max() - heights.min()) * 1000.0),
        "floor_tilt_mean_deg": float(tilt.mean()),
        "floor_tilt_max_deg": float(tilt.max()),
        "path_length_m": float(travelled[-1]) if len(travelled) else 0.0,
        "per_frame": [
            {
                "frame": p["frame"],
                "height_m": p["height_m"],
                "tilt_deg": float(tilt[i]),
                "inliers": p["inliers"],
            }
            for i, p in enumerate(per_frame)
        ],
    }
    if verbose:
        print(f"    frames fitted / rejected: {out['n_frames_used']} / {out['n_rejected']}")
        print(f"    floor height mean      : {out['floor_height_mean_m']:.3f} m")
        print(f"    floor height std       : {out['floor_height_std_mm']:.1f} mm")
        print(f"    floor height p5-p95    : {out['floor_height_p95_spread_mm']:.1f} mm")
        print(f"    floor height range     : {out['floor_height_range_mm']:.1f} mm")
        print(f"    floor tilt mean / max  : {out['floor_tilt_mean_deg']:.3f} / "
              f"{out['floor_tilt_max_deg']:.3f} deg")
    return out


def drift_trend(capture: Capture, drift: dict) -> dict:
    """Linear trend of floor height against distance walked.

    A strong slope means error accumulates with path length, which is the
    signature of odometry drift rather than per-frame noise. A flat trend with
    wide scatter instead suggests random error, which plane anchoring can absorb
    without loop closure.
    """
    if len(drift.get("per_frame", [])) < 4:
        return {}
    frames = np.array([p["frame"] for p in drift["per_frame"]])
    heights = np.array([p["height_m"] for p in drift["per_frame"]])
    order = np.argsort(frames)
    f, h = frames[order].astype(float), heights[order]
    # Cumulative distance from the first fitted frame, seeded with 0 so it
    # lines up with h rather than being one element short.
    steps = np.linalg.norm(
        np.diff(capture.positions[frames[order].astype(int)], axis=0), axis=1
    )
    travelled = np.concatenate([[0.0], np.cumsum(steps)])
    if len(travelled) != len(h) or np.ptp(travelled) < 1e-6:
        return {}
    slope, intercept = np.polyfit(travelled, h, 1)
    resid = h - (slope * travelled + intercept)
    return {
        "slope_mm_per_m": float(slope * 1000.0),
        "residual_std_mm": float(np.std(resid) * 1000.0),
        "r2": float(1 - np.var(resid) / max(np.var(h), 1e-12)),
    }
