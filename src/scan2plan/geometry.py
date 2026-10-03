"""Geometry primitives: plane fitting, RANSAC, and frame transforms.

Numpy-only on purpose. The core path must run on a clean machine with nothing
but numpy + Pillow, so no scipy/OpenCV/PCL dependency is allowed here.
"""

from __future__ import annotations

import numpy as np


def orthonormalize(n: np.ndarray) -> np.ndarray:
    """Unit normal, sign-stable (largest-magnitude component positive)."""
    n = n / (np.linalg.norm(n) + 1e-12)
    if n[np.argmax(np.abs(n))] < 0:
        n = -n
    return n


def fit_plane_ransac(
    points: np.ndarray,
    inlier_thresh: float = 0.015,
    max_iterations: int = 400,
    min_samples: int = 3,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, float, np.ndarray]:
    """Find the dominant plane.

    Returns (normal, offset, inlier_mask) where the plane is `normal . p = offset`
    and `normal` is unit length. Uses a deterministic seed by default so the
    repeatability gate is not polluted by RNG noise.
    """
    rng = rng or np.random.default_rng(0)
    n_pts = points.shape[0]
    if n_pts < min_samples:
        return np.array([0.0, 0.0, 1.0]), 0.0, np.zeros(n_pts, dtype=bool)

    best_inliers = np.zeros(n_pts, dtype=bool)
    best_count = 0
    best_normal = np.array([0.0, 0.0, 1.0])
    best_offset = 0.0

    for _ in range(max_iterations):
        idx = rng.choice(n_pts, size=min_samples, replace=False)
        p0, p1, p2 = points[idx[0]], points[idx[1]], points[idx[2]]
        n = np.cross(p1 - p0, p2 - p0)
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n /= norm
        d = float(n @ p0)
        resid = np.abs(points @ n - d)
        inliers = resid < inlier_thresh
        count = int(inliers.sum())
        if count > best_count:
            best_count, best_inliers = count, inliers
            best_normal, best_offset = n, d

    if best_count >= min_samples:
        # Refit on inliers by total-least-squares for sub-threshold accuracy.
        pts = points[best_inliers]
        centroid = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
        n = orthonormalize(vt[2])
        d = float(n @ centroid)
        resid = np.abs(points @ n - d)
        best_inliers = resid < inlier_thresh

    return best_normal, best_offset, best_inliers


def plane_quality(points: np.ndarray, normal: np.ndarray, offset: float) -> dict:
    """Residual statistics for a plane -- the raw material for confidence bands."""
    resid = np.abs(points @ normal - offset)
    return {
        "n_points": int(points.shape[0]),
        "rms_mm": float(np.sqrt(np.mean(resid**2)) * 1000.0),
        "p50_mm": float(np.median(resid) * 1000.0),
        "p95_mm": float(np.percentile(resid, 95) * 1000.0),
        "max_mm": float(resid.max() * 1000.0),
    }


def angle_between(n1: np.ndarray, n2: np.ndarray) -> float:
    """Angle in degrees between two (unit) normals."""
    c = float(np.clip(abs(n1 @ n2) / (np.linalg.norm(n1) * np.linalg.norm(n2)), -1.0, 1.0))
    return float(np.degrees(np.arccos(c)))


def segment_connected(mask2d: np.ndarray, min_size: int = 4) -> list[np.ndarray]:
    """4-connected components of a boolean 2D mask, returned as index arrays.

    Small mask sizes make a BFS cheaper and dependency-free versus scipy.
    """
    h, w = mask2d.shape
    seen = np.zeros((h, w), dtype=bool)
    out: list[np.ndarray] = []
    for sy, sx in zip(*np.nonzero(mask2d)):
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]
        seen[sy, sx] = True
        comp = []
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask2d[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(comp) >= min_size:
            out.append(np.array(comp, dtype=np.int32))
    return out


def fit_line_2d(points2d: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Total-least-squares line in 2D. Returns (centroid, unit_direction, rms)."""
    c = points2d.mean(axis=0)
    centred = points2d - c
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    d = vt[0]
    resid = np.abs(centred @ np.array([-d[1], d[0]]))
    return c, d, float(np.sqrt(np.mean(resid**2)))