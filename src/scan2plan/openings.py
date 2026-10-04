"""Door and window openings from a single photograph.

Kept in the package rather than in a driver script so it can be tested against
synthetic frames, where the answer is known exactly.

The polarity here is the whole trick and it is easy to get backwards. A doorway
photographed from inside a room is a hole you look *through* into the next space,
so it is FARTHER than the wall around it, not nearer. Selecting the nearest 55% of
the image selects the wall, which wraps around the opening and connects across the
frame, and the largest connected component comes back as the entire image. That is
not a hypothetical: it is what the first version of this code did, and it reported
a 1.93 m wide door for a door taped at 0.80 m.

There is no scale reference in these photographs, so nothing here produces metres on
its own. `measure_opening` returns fractions of the frame; converting to metres
requires a known wall height, and the caller owns that.
"""

from __future__ import annotations

import cv2
import numpy as np

# The opening must span at least this fraction of the image height to count as a
# door. Anything shorter is a window or a view through a gap.
DOOR_MIN_HEIGHT_FRAC = 0.35

# The opening must be at least this much FARTHER than the surrounding wall.
# It has to clear both the door reveal (the jamb is a few cm deep and genuinely
# part of the opening) and the monocular depth error of the wall plane itself.
# Measured sensitivity across the five 2026-10-04 door photos moves the estimate by
# <0.15 m over 0.5-1.5 m; the residual error is dominated by the reveal, which this
# constant cannot remove, not by the constant itself.
OPENING_DEPTH_MARGIN_M = 1.0

# Fraction of the width/height treated as the surrounding wall by the fallback
# border estimator. The bottom band is excluded: it is the floor a metre from the
# lens, not the wall being measured.
WALL_BORDER_FRAC = 0.08

# Two photographs of the same door should agree to within this. Above it the pair is
# flagged rather than averaged away.
COPY_DISAGREEMENT_M = 0.10


def wall_depth_m(depth_m: np.ndarray) -> float:
    """Depth of the wall the camera faces.

    Estimated from the ring of pixels immediately surrounding the far region,
    because that ring IS the wall around the opening. Taking a median over the top
    and side borders instead mixes depths whenever the wall is oblique: on
    main_door (2) that put the wall at 1.22 m where the other photograph of the
    same door saw 2.55 m, which in turn inflated the estimated door width to
    1.59 m. Measuring the ring cannot be dragged by the opening it surrounds.

    Falls back to the border median when the far region is too small to ring.
    """
    h, w = depth_m.shape[:2]
    coarse = (depth_m > np.median(depth_m)).astype(np.uint8)
    coarse = cv2.morphologyEx(coarse, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    k = max(3, int(0.03 * max(h, w)))
    ring = (cv2.dilate(coarse, np.ones((k, k), np.uint8)) - coarse) > 0
    if ring.sum() >= 0.01 * h * w:
        return float(np.median(depth_m[ring]))
    by, bx = max(1, int(h * WALL_BORDER_FRAC)), max(1, int(w * WALL_BORDER_FRAC))
    border = np.concatenate(
        [depth_m[:by].ravel(), depth_m[:, :bx].ravel(), depth_m[:, -bx:].ravel()]
    )
    return float(np.median(border))


def measure_opening(depth_m: np.ndarray) -> dict | None:
    """Locate a doorway and size it as a fraction of the frame.

    Returns the bbox, the opening's share of frame height, its aspect ratio, and
    the wall depth it was measured against. Returns None when no region is both
    tall enough and far enough to be an opening.
    """
    h, w = depth_m.shape[:2]
    wall = wall_depth_m(depth_m)
    far = (depth_m > wall + OPENING_DEPTH_MARGIN_M).astype(np.uint8)
    far = cv2.morphologyEx(far, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _labels, stats, _c = cv2.connectedComponentsWithStats(far, connectivity=8)
    best, best_area = None, 0
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area > best_area and bh > DOOR_MIN_HEIGHT_FRAC * h:
            best, best_area = (int(x), int(y), int(bw), int(bh), int(area)), area
    if best is None:
        return None
    x, y, bw, bh, area = best
    return {
        "bbox_px": [x, y, bw, bh],
        "height_frac_of_frame": round(bh / h, 4),
        "aspect_w_over_h": round(bw / max(bh, 1), 4),
        "area_frac": round(area / (h * w), 4),
        "wall_depth_m": round(wall, 3),
    }


def to_metres(measurement: dict, wall_height_m: float) -> tuple[float, float]:
    """Convert a frame-relative measurement to metres given a known wall height."""
    height = measurement["height_frac_of_frame"] * wall_height_m
    width = measurement["aspect_w_over_h"] * height
    return round(width, 3), round(height, 3)


def _median(values: list[float]) -> float:
    """Conventional median: the middle value, or the mean of the two middles.

    Written out because the obvious `sorted(v)[len(v) // 2]` returns the UPPER middle
    for an even count, which with two copies of a door means always reporting the
    larger of the two -- precisely the wild copy capturing the answer that the
    median is here to prevent.
    """
    v = sorted(values)
    n = len(v)
    mid = n // 2
    return round(v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2.0, 3)


def consensus(measurements: list[dict]) -> dict:
    """Combine several photographs of the SAME opening.

    Deliberately takes no truth argument. An earlier version used
    `min(..., key=distance to the taped height)`, which meant the reported number
    was partly chosen by how close it happened to land to the answer key --
    selecting on validation data. The median is used instead and the spread is
    reported, so a pair that disagrees says so instead of quietly resolving.
    """
    widths = [m["width_est_m"] for m in measurements]
    heights = [m["height_est_m"] for m in measurements]
    w_spread = round(max(widths) - min(widths), 3)
    h_spread = round(max(heights) - min(heights), 3)
    return {
        "n_photos": len(measurements),
        "width_est_m": _median(widths),
        "height_est_m": _median(heights),
        "width_spread_m": w_spread,
        "height_spread_m": h_spread,
        "copies_agree": max(w_spread, h_spread) <= COPY_DISAGREEMENT_M,
        "sources": [m["source"] for m in measurements],
    }
