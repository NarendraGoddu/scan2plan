"""Measure each labelled wall of each room from its own depth maps.

The photograph labels solve the problem that defeated the blind pass. Earlier,
every image was searched for any wall-like plane and a wall turned up in only 39
of 197. But `m_wall_2.*` and `2_room_wall_1 (*)` are not ambiguous: the dominant
plane in those frames *is* the named wall, so the search becomes "measure the
largest plane" instead of "find a wall among furniture".

Scale without a reference object. Depth Anything's output is relative, and no
EXIF or scale marker survives in this capture. The wall supplies its own scale: a
wall spans floor to ceiling, so its extent measured along the vertical *is* the
room height in relative units. One tape-measured height therefore converts every
wall in that room to metres:

    length_m = extent_horizontal_rel * height_tape_m / extent_vertical_rel

That is an assumption -- that the photograph reaches both floor and ceiling, and
that the camera is upright enough for the image's vertical axis to be the wall's
vertical -- and it is the single largest error source here. It is checked rather
than assumed: every photo of a wall reports its own vertical extent, and photos
whose vertical extent disagrees with the rest of their wall by more than the
tolerance are listed as outliers instead of being averaged in silently.

Photos are pooled per wall and the *maximum* extent is used, because a wall
photographed in several overlapping frames is only fully visible in the frame
that happens to contain its far corner. Averaging would systematically shorten
every wall.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.capture_manifest import scan_capture  # noqa: E402
from scan2plan.depth_geometry import (  # noqa: E402
    anchor_scale,
    classify_depth_planes,
    estimate_vertical,
    extract_planes,
    unproject_depth,
)

ASSUMED_HFOV_DEG = 69.0
NOMINAL_MEDIAN_M = 3.0
UNPROJECT_STRIDE = 4
INLIER_M = 0.10
DEPTH_CLIP = (2.0, 95.0)
DEPTH_MAX_FACTOR = 4.0

# A photo's vertical extent must agree with its wall's median to within this
# factor before it is allowed to set the wall length.
VERTICAL_TOLERANCE = 0.25


@dataclass
class WallObservation:
    """One photo's view of one labelled wall."""

    source: str
    room_id: str
    wall_index: int
    horizontal_rel_m: float
    vertical_rel_m: float
    support: int
    rms_m: float
    n_planes: int
    outliers: list[str] = field(default_factory=list)


def focal_from_hfov(width_px: int, hfov_deg: float = ASSUMED_HFOV_DEG) -> float:
    return 0.5 * width_px / np.tan(np.radians(hfov_deg) / 2.0)


def load_depth(path: str) -> np.ndarray:
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise FileNotFoundError(path)
    if raw.dtype == np.uint16:
        return raw.astype(np.float32) / 65535.0
    return raw.astype(np.float32)


def dominant_wall(depth_rel: np.ndarray, seed: int = 0):
    """Return (plane, up) for the largest wall-like plane in one depth map.

    "Largest" wins because the filename already says which wall is being
    photographed, so the target is not in question -- only its extent is.
    """
    lo, hi = np.percentile(depth_rel, DEPTH_CLIP)
    depth_m = np.minimum(
        anchor_scale(np.clip(depth_rel, lo, hi), NOMINAL_MEDIAN_M),
        DEPTH_MAX_FACTOR * NOMINAL_MEDIAN_M,
    )
    focal = focal_from_hfov(depth_m.shape[1])
    pts, uv = unproject_depth(depth_m, focal, stride=UNPROJECT_STRIDE)
    planes = extract_planes(pts, inlier_m=INLIER_M, rng=np.random.default_rng(seed), uv=uv)
    up, _src = estimate_vertical(planes, pts)
    planes = classify_depth_planes(planes, up)
    if not planes:
        return None, up, 0
    walls = [p for p in planes if p.kind == "wall"]
    pool = walls or planes
    pool.sort(key=lambda p: -p.support)
    return pool[0], up, len(pts)


def split_extent(plane, up_cam: np.ndarray) -> tuple[float, float]:
    """In-plane extents split into (along-wall, vertical) using the camera's up.

    The vertical axis of the image is a good proxy for the world vertical when a
    camera is pointed at a wall, which is what a wall photograph is. Projecting
    the image-up direction into the plane and taking the axis most aligned with it
    gives the wall's vertical, and the other axis lies along the wall.
    """
    pts = plane.points
    if len(pts) < 3:
        return 0.0, 0.0
    n = plane.normal / max(np.linalg.norm(plane.normal), 1e-12)
    img_up = np.array([0.0, -1.0, 0.0])
    v = img_up - float(img_up @ n) * n
    nv = np.linalg.norm(v)
    if nv < 1e-6:
        v = np.cross(n, np.array([1.0, 0.0, 0.0]))
        nv = np.linalg.norm(v)
        if nv < 1e-6:
            return 0.0, 0.0
    v = v / nv
    h = np.cross(n, v)
    a, b = pts @ v, pts @ h
    vert = float(b.max() - b.min())
    horiz = float(a.max() - a.min())
    return horiz, vert


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", default=os.path.join(ROOT, os.pardir, "capture"))
    ap.add_argument("--depth-dir", default=os.path.join(ROOT, "runs", "depth"))
    ap.add_argument("--truth", default=os.path.join(ROOT, "data", "field_ground_truth.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "runs"))
    args = ap.parse_args()

    capture_dir = os.path.abspath(args.capture)
    items = scan_capture(capture_dir)

    with open(args.truth, encoding="utf-8") as fh:
        truth = json.load(fh)
    heights = {r["id"]: r["height_m"] for r in truth["rooms"]}

    wall_items = [
        it for it in items
        if it.surface == "wall" and it.wall_index is not None and it.room_id in heights
    ]
    print(f"{len(wall_items)} wall-labelled photos in rooms with tape ground truth")

    obs: list[WallObservation] = []
    for k, it in enumerate(sorted(wall_items, key=lambda x: x.path), 1):
        # it.path is folder-relative while capture_dir is absolute, so the relative
        # path has to be rebuilt from the folder name rather than from it.path.
        rel = os.path.join(it.folder, it.filename)
        stem = os.path.splitext(rel)[0]
        dp = os.path.join(args.depth_dir, stem.replace(os.sep, "__") + ".png")
        if not os.path.isfile(dp):
            continue
        try:
            plane, up, npts = dominant_wall(load_depth(dp))
        except FileNotFoundError:
            continue
        if plane is None or len(plane.points) < 50:
            continue
        horiz, vert = split_extent(plane, up)
        if vert < 1e-3:
            continue
        obs.append(WallObservation(
            source=it.filename, room_id=it.room_id, wall_index=it.wall_index,
            horizontal_rel_m=horiz, vertical_rel_m=vert, support=int(plane.support),
            rms_m=float(plane.rms_m), n_planes=npts,
        ))
        if k % 10 == 0:
            print(f"  {k}/{len(wall_items)}")

    # Pool per (room, wall): the frame that contains the far corner gives the true
    # length, so the maximum horizontal extent is the estimator.
    by_wall: dict[tuple[str, int], list[WallObservation]] = {}
    for o in obs:
        by_wall.setdefault((o.room_id, o.wall_index), []).append(o)

    results = []
    for (room, wall), group in sorted(by_wall.items()):
        h_tape = float(heights[room])
        typical_vert = float(np.median([g.vertical_rel_m for g in group]))
        kept, dropped = [], []
        for g in group:
            if typical_vert > 0 and abs(g.vertical_rel_m - typical_vert) / typical_vert > VERTICAL_TOLERANCE:
                dropped.append(g.source)
            else:
                kept.append(g)
        if not kept:
            kept = group
        best = max(kept, key=lambda g: g.horizontal_rel_m)
        length_m = best.horizontal_rel_m * h_tape / best.vertical_rel_m
        results.append({
            "room_id": room,
            "wall_index": wall,
            "n_photos": len(group),
            "n_used": len(kept),
            "outlier_photos": dropped,
            "height_tape_m": h_tape,
            "vertical_rel_m": round(float(best.vertical_rel_m), 4),
            "horizontal_rel_m": round(float(best.horizontal_rel_m), 4),
            "length_m": round(float(length_m), 3),
            "rms_m": round(float(best.rms_m), 4),
            "best_photo": best.source,
            "photo_lengths_m": [
                round(float(g.horizontal_rel_m * h_tape / g.vertical_rel_m), 3) for g in group
            ],
        })

    out_path = os.path.join(args.out, "wall_measurements.json")
    os.makedirs(args.out, exist_ok=True)

    print("\n=== per wall ===")
    print("%-16s %5s %6s %9s %8s %8s" % (
        "room", "wall", "photos", "length_m", "rms_m", "best_photo"))
    for r in results:
        print("%-16s %5d %6d %9.3f %8.4f %8s" % (
            r["room_id"], r["wall_index"], r["n_photos"], r["length_m"],
            r["rms_m"], r["best_photo"][:28]))

    # ---- room level: a rectangle has exactly two distinct side lengths ----
    # Per-photo estimates are noisy and individually biased, because no single
    # frame contains both corners of a wall. Pooling every photo of every wall and
    # solving for the two side lengths at once uses a fact the per-wall estimator
    # cannot: in a rectangular room the opposite walls are equal. That constraint
    # averages four noisy measurements into two, and it also exposes a bad
    # measurement as the wall that disagrees with its opposite rather than letting
    # it corrupt a room dimension silently.
    truth_by_room = {r["id"]: r for r in truth["rooms"]}
    print("\n=== room level (rectangle constraint) ===")
    print("%-16s %14s %14s %8s %8s %7s" % (
        "room", "measured LxW", "tape LxW", "dL", "dW", "area%"))
    room_results = []
    for room in sorted({r["room_id"] for r in results}):
        rs = [r for r in results if r["room_id"] == room]
        t = truth_by_room[room]

        # Reduce each wall to its median first. Individual photos disagree by up to
        # a metre because no frame contains both corners of a wall, so the four
        # per-wall medians are the quantity that carries signal.
        wall_meds = [float(np.median(r["photo_lengths_m"])) for r in rs if r["photo_lengths_m"]]
        if len(wall_meds) < 2:
            continue

        # Two-means on the wall medians: a rectangle has exactly two side lengths.
        # A max-gap split was tried first and is fragile -- it keys on whichever gap
        # happens to be widest, which an outlier can widen, and it reported the
        # master bedroom as 0.91 x 2.59 m.
        c = [float(np.percentile(wall_meds, 25)), float(np.percentile(wall_meds, 75))]
        for _ in range(50):
            groups = [[v for v in wall_meds if abs(v - c[0]) <= abs(v - c[1])],
                      [v for v in wall_meds if abs(v - c[0]) > abs(v - c[1])]]
            new = []
            for g, centre in zip(groups, c, strict=False):
                new.append(float(np.mean(g)) if g else centre)
            if max(abs(a - b) for a, b in zip(new, c, strict=False)) < 1e-6:
                c = new
                break
            c = new
        lo_side, hi_side = sorted(c)

        measured_area = lo_side * hi_side
        tape_area = t["length_m"] * t["width_m"]
        room_results.append({
            "room_id": room,
            "side_a_m": round(lo_side, 3),
            "side_b_m": round(hi_side, 3),
            "tape_length_m": t["length_m"],
            "tape_width_m": t["width_m"],
            "area_m2": round(measured_area, 3),
            "tape_area_m2": round(tape_area, 3),
            "area_error_pct": round(100.0 * (measured_area - tape_area) / tape_area, 2),
            "wall_medians_m": [round(v, 3) for v in sorted(wall_meds)],
        })
        print("%-16s %14s %14s %8.3f %8.3f %6.1f%%" % (
            room,
            f"{lo_side:.2f} x {hi_side:.2f}",
            f"{t['length_m']:.2f} x {t['width_m']:.2f}",
            lo_side - t["width_m"], hi_side - t["length_m"],
            100.0 * (measured_area - tape_area) / tape_area))

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "method": (
                "dominant plane per wall-labelled photo; scale from the wall's own "
                "floor-to-ceiling extent against tape height; room dimensions from "
                "the rectangular constraint that opposite walls are equal"
            ),
            "assumed_hfov_deg": ASSUMED_HFOV_DEG,
            "vertical_tolerance": VERTICAL_TOLERANCE,
            "rooms": room_results,
            "walls": results,
        }, fh, indent=2)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
