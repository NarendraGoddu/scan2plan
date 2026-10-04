"""Room geometry from monocular depth maps.

Depth Anything predicts relative inverse depth, so every image needs a scale
before it describes a room. That scale is anchored from outside -- a tape
measurement or a known camera height -- never from the depth map itself. See
`anchor_scale`.

What can be measured *without* scale is the shape of the room: which surfaces are
planar, which of them are vertical, and how large they are relative to one
another. Those are what this module extracts, so the un-anchored result is
honest about being shape-only.

Verification is built in rather than assumed. A depth map of a real room has
strong planar structure, so `extract_planes` finding a handful of planes with low
residual is itself evidence the model produced real geometry. Random or blurred
predictions do not survive a RANSAC plane fit, which is the check used to
validate a whole capture before trusting anything downstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class DepthPlane:
    """One plane fitted to a point cloud lifted from a depth map."""

    normal: np.ndarray  # unit, 3-vector
    offset: float  # such that normal . x = offset
    support: int
    rms_m: float
    kind: str = "unknown"  # wall | horizontal | slant
    extent_m: float = 0.0  # largest inlier spread along the plane
    points: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 3)))
    uv: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 2)))

    def distance_to(self, p: np.ndarray) -> np.ndarray:
        return self.normal @ np.asarray(p, dtype=np.float64).reshape(-1, 3) - self.offset


def anchor_scale(
    depth: np.ndarray, median_distance_m: float, near_m: float | None = None,
    far_m: float | None = None,
) -> np.ndarray:
    """Turn one image's relative inverse depth into pseudo-metres.

    `median_distance_m` sets the scene's typical distance; `near_m`/`far_m`, when
    both are given, anchor two percentiles instead and produce a genuinely scaled
    map. Either way the result is only as trustworthy as the number supplied --
    this function does no estimation of its own, deliberately, so that a guessed
    scale cannot masquerade as a measured one.
    """
    d = np.asarray(depth, dtype=np.float64)
    if near_m is not None and far_m is not None:
        lo, hi = np.percentile(d, [1, 99])
        if hi - lo < 1e-9:
            return np.full_like(d, near_m)
        a = (1.0 / far_m - 1.0 / near_m) / (hi - lo)
        b = 1.0 / near_m - a * lo
        return 1.0 / np.clip(a * d + b, 1e-6, None)
    med = float(np.median(d))
    if med < 1e-9:
        return np.full_like(d, median_distance_m)
    return median_distance_m * med / np.maximum(d, 1e-9)


def unproject_depth(
    depth_m: np.ndarray, focal_px: float, stride: int = 4, near: float = 0.05
) -> tuple[np.ndarray, np.ndarray]:
    """Lift a depth map to a point cloud via the pinhole model.

    The camera is assumed to look along +Z with the image plane parallel to XY,
    and the principal point at the image centre. Focal length is a required
    input rather than a default because no EXIF survives in this capture, so any
    focal used here is an assumption and the caller has to own it.

    Returns the points and the pixel coordinates they came from.
    """
    h, w = depth_m.shape[:2]
    ys, xs = np.mgrid[0:h:stride, 0:w:stride]
    z = np.asarray(depth_m[0:h:stride, 0:w:stride], dtype=np.float64)
    cx, cy = w / 2.0, h / 2.0
    x = (xs - cx) * z / focal_px
    y = (ys - cy) * z / focal_px
    keep = z > near
    pts = np.column_stack([x[keep], y[keep], z[keep]])
    uv = np.column_stack([xs[keep], ys[keep]]).astype(np.float64)
    return pts, uv


def _fit_plane_pca(pts: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Least-squares plane by PCA on the point set.

    Returns (unit normal, offset, rms). The smallest principal axis is the plane
    normal; taking the largest eigenvalue direction as the normal would fit a line
    instead, which is the classic way to get a confident wrong answer here.
    """
    c = pts.mean(axis=0)
    centred = pts - c
    # Covariance via SVD on the centred data: cheaper and stabler than forming
    # the covariance matrix explicitly for large point sets.
    _u, s, vt = np.linalg.svd(centred, full_matrices=False)
    normal = vt[-1]
    offset = float(normal @ c)
    resid = centred @ normal
    rms = float(np.sqrt(np.mean(resid**2)))
    return normal / max(np.linalg.norm(normal), 1e-12), offset, rms


def extract_planes(
    pts: np.ndarray,
    inlier_m: float = 0.12,
    max_planes: int = 8,
    min_fraction: float = 0.02,
    dedupe_deg: float = 15.0,
    rng: np.random.Generator | None = None,
    uv: np.ndarray | None = None,
) -> list[DepthPlane]:
    """Greedy RANSAC plane extraction, largest support first.

    Each pass fits the best plane to what remains, removes its inliers, and
    repeats. Points are subsampled per trial for speed and the final plane is
    refitted on *all* its inliers, so the reported normal and residual come from
    every point that voted for the plane rather than from the three that seeded
    it.

    Deduplication is not optional here. Without it the loop refits the *same*
    surface many times over: a real room produced eleven planes whose normals
    agreed to within a degree and whose offsets differed by 2 cm, because the
    refitted plane never quite consumed its own inliers at a tight threshold.
    Since classification is relative to a chosen reference plane, that also made
    every surface look like the reference one. A candidate is therefore rejected
    if an already-accepted plane has a similar normal *and* a nearby offset --
    normal alone is not enough, since a room legitimately has two parallel walls.
    """
    rng = rng or np.random.default_rng(0)
    remaining = np.arange(len(pts))
    planes: list[DepthPlane] = []
    floor_count = max(min_fraction * len(pts), 200)
    cos_dedupe = math.cos(math.radians(dedupe_deg))
    merge_dist = 2.0 * inlier_m

    while len(planes) < max_planes and len(remaining) >= max(floor_count, 30):
        pool = pts[remaining]
        if len(pool) > 6000:
            pool = pool[rng.choice(len(pool), 6000, replace=False)]
        best = None
        best_inliers = None
        for _ in range(40):
            sel = rng.choice(len(pool), 3, replace=False)
            p3 = pool[sel]
            nvec = np.cross(p3[1] - p3[0], p3[2] - p3[0])
            nn = np.linalg.norm(nvec)
            if nn < 1e-9:
                continue
            nvec /= nn
            off = float(nvec @ p3[0])
            inl = np.abs(pool @ nvec - off) < inlier_m
            if best is None or inl.sum() > best.sum():
                best, best_inliers = inl, np.where(inl)[0]
        if best is None or best_inliers.size < floor_count:
            break

        # Refit on the full inlier set, then consume every point the refit accepts.
        cand_idx = remaining[best_inliers]
        normal, offset, rms = _fit_plane_pca(pts[cand_idx])
        keep = np.abs(pts[remaining] @ normal - offset) < inlier_m
        if keep.sum() < floor_count:
            break

        duplicate = any(
            abs(float(q.normal @ normal)) >= cos_dedupe
            and abs(q.offset - offset) < merge_dist
            for q in planes
        )
        if not duplicate:
            plane = DepthPlane(
                normal=normal, offset=offset, support=int(keep.sum()), rms_m=rms
            )
            plane.points = pts[remaining[keep]]
            plane.extent_m = _inplane_extent(plane.points, normal)
            if uv is not None:
                plane.uv = uv[remaining[keep]]
            planes.append(plane)
        remaining = remaining[~keep]

    return planes


def _inplane_extent(pts: np.ndarray, normal: np.ndarray) -> float:
    """Largest spread of points measured within the plane."""
    if len(pts) < 2:
        return 0.0
    t = np.array([1.0, 0.0, 0.0])
    if abs(normal @ t) > 0.9:
        t = np.array([0.0, 1.0, 0.0])
    e1 = np.cross(normal, t)
    e1 /= max(np.linalg.norm(e1), 1e-12)
    e2 = np.cross(normal, e1)
    a, b = pts @ e1, pts @ e2
    return float(max(a.max() - a.min(), b.max() - b.min()))


def estimate_vertical(planes: list[DepthPlane], pts: np.ndarray) -> tuple[np.ndarray, str]:
    """Recover the vertical direction from the floor plane in a single view.

    The image's own y-axis is *not* the world vertical -- a survey photo is
    usually tilted up at the ceiling or down at the floor. Anchoring "up" to the
    camera frame therefore mislabels every surface: one bedroom photo aimed at the
    ceiling has its nearest content at the top of the frame, which is the opposite
    of the level-camera assumption and produced a vertical-gradient check that
    appeared to fail on correct depth.

    The floor is the one surface that is reliably large, planar, and present: it
    occupies the lower part of the frame whatever the camera pitch. Two conditions
    pick it out. Its points sit low in the image, and it is *not* fronto-parallel
    -- a plane whose normal is the optical axis is a wall facing the camera, which
    is the flattest, best-supported plane in the scene and, scored on residual
    alone, wins every time.

    Neither condition is sufficient alone: plenty of walls reach the bottom of
    frame, and a near-vertical wall seen from close range can be very flat. Both
    together identify the floor without assuming a pose.
    """
    if not planes:
        return np.array([0.0, -1.0, 0.0]), "default (no planes)"

    best, best_score = None, -1.0
    for p in planes:
        if len(p.uv) == 0:
            continue
        n = p.normal / max(np.linalg.norm(p.normal), 1e-12)
        if abs(float(n[2])) > 0.9:
            continue  # fronto-parallel: a wall facing the camera, not the floor
        v = p.uv[:, 1]
        low = float(np.mean(v > (2.0 / 3.0) * v.max()))
        if low < 0.3:
            continue
        score = float(p.support) * low
        if score > best_score:
            best, best_score = p, score
    if best is None:
        return np.array([0.0, -1.0, 0.0]), "default (no candidate)"

    n = best.normal / max(np.linalg.norm(best.normal), 1e-12)
    if len(pts) and float(n @ pts.mean(axis=0)) < 0:
        n = -n
    return n, f"floor plane, support {best.support}, rms {best.rms_m:.3f} m"


def classify_depth_planes(
    planes: list[DepthPlane], up: np.ndarray, floor_height_m: float | None = None
) -> list[DepthPlane]:
    """Label planes as walls or horizontal surfaces using the vertical direction.

    A floor or ceiling has its normal *along* the vertical; a wall has its normal
    *perpendicular* to it. Labelling these the wrong way round -- calling anything
    near-vertical a wall -- reports every floor as a wall, which is exactly the
    first version of this function: 12 "walls" per bedroom image, which is just
    the floor, the ceiling and the furniture.
    """
    u = np.asarray(up, dtype=np.float64)
    u = u / max(np.linalg.norm(u), 1e-12)
    for p in planes:
        tilt = math.degrees(
            math.acos(min(1.0, abs(float(p.normal @ u))))
        )
        if tilt >= 60.0:
            p.kind = "wall"
        elif tilt <= 30.0:
            p.kind = "horizontal"
        else:
            p.kind = "slant"
    return planes


def summarise_planes(planes: list[DepthPlane]) -> dict:
    """Compact, JSON-friendly summary."""
    return {
        "n_planes": len(planes),
        "n_walls": sum(1 for p in planes if p.kind == "wall"),
        "n_horizontal": sum(1 for p in planes if p.kind == "horizontal"),
        "median_wall_rms_m": (
            float(np.median([p.rms_m for p in planes if p.kind == "wall"]))
            if any(p.kind == "wall" for p in planes)
            else None
        ),
        "planes": [
            {
                "normal": [round(float(c), 4) for c in p.normal],
                "offset_m": round(float(p.offset), 4),
                "support": int(p.support),
                "rms_m": round(float(p.rms_m), 5),
                "extent_m": round(float(p.extent_m), 3),
                "kind": p.kind,
            }
            for p in planes
        ],
    }
