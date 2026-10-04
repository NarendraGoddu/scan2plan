"""Is each supplied archive one room or many?

The wall diagnostic showed 82 of 84 planes in with_ceiling having camera
positions on both sides. A wall that bounds a room has the whole trajectory on
one side, so either the walls are furniture, or the trajectory passes through
several spaces and sees every wall from both sides.

The second reading is testable, and it decides what the pipeline should be asked
to do. A single-room capture has a compact trajectory; a whole-flat walkthrough
does not. So: measure the trajectory's extent, project it onto the floor, and
count how many separate clumps of positions there are.
"""

from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip
from scan2plan.unproject import gravity_axis

DATA = os.environ.get(
    "SCAN2PLAN_SAMPLE_DATA", r"C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data")

ARCHIVES = [
    ("single_room", "single_room.zip"),
    ("floor_only", "single_scan_floor_only.zip"),
    ("with_ceiling", "single_scan_with_ceiling.zip"),
]


def n_clusters_1d(x: np.ndarray, gap: float) -> int:
    """Count groups separated by more than `gap`, ignoring order."""
    s = np.sort(x)
    if s.size == 0:
        return 0
    return 1 + int(np.sum(np.diff(s) > gap))


def main() -> None:
    for name, zip_name in ARCHIVES:
        path = os.path.join(DATA, zip_name)
        if not os.path.isfile(path):
            print(f"skip {name}")
            continue
        cap = load_zip(path, frame_stride=1)
        pos = cap.positions
        up = gravity_axis(pos, cap.quats)
        up = up / np.linalg.norm(up)

        # Two axes spanning the floor plane, so the spread is measured in the
        # floor and not inflated by the vertical.
        t = np.array([1.0, 0.0, 0.0])
        if abs(up @ t) > 0.9:
            t = np.array([0.0, 0.0, 1.0])
        e1 = np.cross(up, t)
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(up, e1)
        ab = np.column_stack([pos @ e1, pos @ e2])

        span = ab.max(axis=0) - ab.min(axis=0)
        centre = np.median(ab, axis=0)
        radius = np.linalg.norm(ab - centre, axis=1)

        print("=" * 74)
        print(f"{name}  ({zip_name})")
        print(f"  frames            {cap.n_frames}")
        print(f"  floor extent      {span[0]:6.2f} m x {span[1]:6.2f} m")
        print(f"  trajectory radius p50 {np.percentile(radius, 50):5.2f} m   "
              f"p95 {np.percentile(radius, 95):5.2f} m   max {radius.max():5.2f} m")
        print(f"  vertical range    {pos[:, 1].min():+6.2f} .. {pos[:, 1].max():+6.2f} m"
              if abs(up[1]) > 0.5 else
              f"  vertical range    {pos[:, 0].min():+6.2f} .. {pos[:, 0].max():+6.2f} m")

        # A crude room count: how many clumps along each floor axis.
        for axis, label in ((0, "e1"), (1, "e2")):
            for gap in (1.5, 2.5, 4.0):
                c = n_clusters_1d(ab[:, axis], gap)
                print(f"    {label} axis, {gap:.1f} m gaps -> {c} clump(s)")
        print(f"  implied area if one room: {span[0] * span[1]:.1f} m2")
        print()


if __name__ == "__main__":
    main()
