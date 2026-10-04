"""Why do 84 wall-classified planes produce a 0.58 m2 "room"?

classify_planes calls any plane within VERTICAL_TOL_DEG of vertical a wall, with
no test of size or position. In an empty synthetic box that is harmless. In a real
bedroom the same rule accepts the bed, the wardrobe, the door leaf, the curtains
and the kitchen units, and merge_wall_fragments then averages furniture into the
room boundary.

This dumps every wall-classified plane with the quantities that should separate a
room boundary from a piece of furniture, so the separating rule can be read off
the data rather than guessed:

  support / frame_fraction   how often it was seen at all
  extent_m                   largest inlier spread along the plane, i.e. is this
                             surface room-scale or object-scale
  v_extent_m                 inlier spread along the vertical, which separates a
                             floor-to-ceiling wall from a bed or a countertop
  cam_dist_m                 distance from the camera path centroid to the plane
  cam_on_both_sides          cameras seen on both sides of the plane. A room
                             boundary has the whole trajectory on one side; an
                             object in the middle of the room does not.
"""

from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip
from scan2plan.segment import segment_capture

DATA = os.environ.get(
    "SCAN2PLAN_SAMPLE_DATA", r"C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data")
SCALE = 0.0009997
FRAME_STRIDE = 8


def main() -> None:
    zip_name = sys.argv[1] if len(sys.argv) > 1 else "single_scan_with_ceiling.zip"
    cap = load_zip(os.path.join(DATA, zip_name), frame_stride=1)
    print(f"{zip_name}: {cap.n_frames} frames, segmenting every {FRAME_STRIDE}th")

    res = segment_capture(cap, SCALE, frame_stride=FRAME_STRIDE, point_stride=4)
    up = res["up"]
    up = up / np.linalg.norm(up)

    cam = cap.positions[::FRAME_STRIDE]
    cam_centre = np.median(cam, axis=0)
    cam_radius = float(np.percentile(np.linalg.norm(cam - cam_centre, axis=1), 95))
    print(f"camera path: {len(cam)} sampled points, radius {cam_radius:.2f} m\n")

    # Build an in-plane basis per plane and measure the inlier spreads.
    rows = []
    for p in res["planes"]:
        if p.kind != "wall":
            continue
        pts = np.asarray(p.inlier_points, dtype=np.float64)
        if pts.shape[0] < 8:
            continue
        n = p.normal / np.linalg.norm(p.normal)
        # Two axes spanning the plane.
        t = np.array([1.0, 0.0, 0.0])
        if abs(n @ t) > 0.9:
            t = np.array([0.0, 1.0, 0.0])
        e1 = np.cross(n, t)
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(n, e1)
        a = pts @ e1
        b = pts @ e2
        v_axis = up if abs(n @ up) < 0.5 else e1
        v = pts @ (v_axis / np.linalg.norm(v_axis))
        extent = float(max(a.max() - a.min(), b.max() - b.min()))
        v_extent = float(v.max() - v.min())

        d = cam @ n - p.offset
        both = bool(d.min() < 0 < d.max())
        cam_dist = abs(float(p.normal @ cam_centre - p.offset))

        rows.append({
            "n": tuple(np.round(n, 2)),
            "offset": float(p.offset),
            "support": int(p.support),
            "frac": float(p.frame_fraction),
            "npts": int(pts.shape[0]),
            "extent": extent,
            "v_extent": v_extent,
            "cam_dist": cam_dist,
            "both": both,
            "rms": float(p.rms_m),
        })

    rows.sort(key=lambda r: -r["extent"])
    print(f"{len(rows)} wall planes\n")
    print(f"{'extent':>7}{'v_ext':>7}{'cam_d':>7}{'sup':>5}{'frac':>6}"
          f"{'npts':>7}{'rms_mm':>8}  both  normal          offset")
    print("-" * 92)
    for r in rows:
        print(f"{r['extent']:7.2f}{r['v_extent']:7.2f}{r['cam_dist']:7.2f}"
              f"{r['support']:5d}{r['frac'] * 100:5.0f}%{r['npts']:7d}"
              f"{r['rms'] * 1000:8.1f}  {'YES' if r['both'] else ' no':>4}  "
              f"{str(r['n']):>16}  {r['offset']:+7.3f}")

    ext = np.array([r["extent"] for r in rows])
    vx = np.array([r["v_extent"] for r in rows])
    cd = np.array([r["cam_dist"] for r in rows])
    nb = sum(1 for r in rows if r["both"])
    print()
    print(f"  extent_m     min {ext.min():.2f}  median {np.median(ext):.2f}  max {ext.max():.2f}")
    print(f"  v_extent_m   min {vx.min():.2f}  median {np.median(vx):.2f}  max {vx.max():.2f}")
    print(f"  cam_dist_m   min {cd.min():.2f}  median {np.median(cd):.2f}  max {cd.max():.2f}")
    print(f"  cameras on both sides: {nb} of {len(rows)}")
    print()
    print("  A room boundary should stand out as: large extent, vertical extent")
    print("  spanning most of the room height, distance from the camera centroid")
    print("  comparable to the camera path radius, and never cameras on both sides.")
    print(f"  Only {len(rows) - nb} planes have cameras on one side only.")


if __name__ == "__main__":
    main()
