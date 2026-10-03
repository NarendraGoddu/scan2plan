"""Is the synthetic depth/unprojection pair self-consistent?

Two questions:
  1. Do unprojected points land on the true box surfaces? If not, the focal
     length or the camera-axis convention disagrees with ingest, and every
     downstream plane is wrong.
  2. Why do a few frames have zero valid pixels?
"""

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip, poses_to_matrices
from scan2plan.synth import DEPTH_SCALE_M, plan_trajectory, standard_rooms
from scan2plan.unproject import frame_points_world

room = standard_rooms()["nominal"]
cap = load_zip(os.path.join(ROOT, "data", "synthetic", "nominal.zip"), frame_stride=8)
SCALE = 0.001
T_cw = poses_to_matrices(cap.positions, cap.quats)
n = cap.depth.shape[0]

print("=== Q1: do unprojected points lie on the true surfaces? ===")
lo, hi = room.bounds
print(f"true room: x[0,{room.length}] y[0,{room.height}] z[0,{room.width}]")
print(f"depth-space focal in capture: fx={cap.focal[0][0]:.3f} "
      f"(full-res would be x{7.5:.1f} = {cap.focal[0][0] * 7.5:.0f})")

for f in [2, 3, 10, 20]:
    pts = frame_points_world(cap, f, SCALE, T_cw, stride=4)
    if pts.shape[0] == 0:
        print(f"  frame {f}: no points")
        continue
    # Distance from each point to the nearest of the six true planes.
    d = np.min(
        np.stack([
            np.abs(pts[:, 0] - lo[0]), np.abs(pts[:, 0] - hi[0]),
            np.abs(pts[:, 1] - lo[1]), np.abs(pts[:, 1] - hi[1]),
            np.abs(pts[:, 2] - lo[2]), np.abs(pts[:, 2] - hi[2]),
        ]),
        axis=0,
    )
    inside = np.all(
        (pts >= lo - 0.05) & (pts <= hi + 0.05), axis=1
    )
    print(f"  frame {f:2d}: {pts.shape[0]:6d} pts  dist-to-nearest-true-surface "
          f"median {np.median(d) * 1000:7.1f} mm  p90 {np.percentile(d, 90) * 1000:7.1f} mm"
          f"  inside-box {inside.mean() * 100:5.1f}%")

print("\n=== Q2: frames with zero valid pixels ===")
per_frame = (cap.confidence == 2).sum(axis=(1, 2))
bad = np.where(per_frame < 500)[0]
print(f"loaded frames with <500 valid: {bad.tolist()}")
tp, tf = plan_trajectory(room, 720, 1.40)
for f in bad.tolist():
    orig = f * 8
    p = tp[orig]
    d = tf[orig]
    inside = bool(np.all(p > lo) and np.all(p < hi))
    print(f"  loaded {f} (orig {orig}): true pos {np.round(p, 3)} "
          f"fwd {np.round(d, 3)} |dir|={np.linalg.norm(d):.3f} inside={inside}")
    print(f"      noisy pos {np.round(cap.positions[f], 3)} "
          f"conf>2 count {per_frame[f]}")

# Also check every original frame for containment.
oob = [i for i in range(720)
       if not (np.all(tp[i] > lo) and np.all(tp[i] < hi))]
print(f"\noriginal frames with true position outside the box: {len(oob)} {oob[:10]}")

zdir = [i for i in range(720) if np.linalg.norm(tf[i]) < 1e-9]
print(f"original frames with zero forward vector: {len(zdir)} {zdir[:10]}")

# How many pixels does a frame aimed squarely at an opening retain?
print("\n=== Q3: opening handling ===")
print(f"room openings: {room.ground_truth()['openings']}")
for f in [300, 310, 320]:
    print(f"  frame {f}: valid {per_frame[f] if f < n else 'n/a'}")