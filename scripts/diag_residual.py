"""Decompose the unprojection residual by axis and by surface."""

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip, poses_to_matrices
from scan2plan.synth import standard_rooms
from scan2plan.unproject import frame_points_world

room = standard_rooms()["nominal"]
lo, hi = room.bounds
cap = load_zip(os.path.join(ROOT, "data", "synthetic", "nominal.zip"), frame_stride=8)
T_cw = poses_to_matrices(cap.positions, cap.quats)

print(f"room lo {lo} hi {hi}")
print(f"camera x range {cap.positions[:,0].min():.2f}..{cap.positions[:,0].max():.2f}")
print(f"camera y range {cap.positions[:,1].min():.2f}..{cap.positions[:,1].max():.2f}")
print(f"camera z range {cap.positions[:,2].min():.2f}..{cap.positions[:,2].max():.2f}")

for f in [2, 10, 20]:
    pts = frame_points_world(cap, f, 0.001, T_cw, stride=1)
    if pts.shape[0] == 0:
        print(f"frame {f}: empty")
        continue
    # Signed offset to each of the six planes, per axis.
    sx0 = pts[:, 0] - lo[0]
    sx1 = pts[:, 0] - hi[0]
    sy0 = pts[:, 1] - lo[1]
    sy1 = pts[:, 1] - hi[1]
    sz0 = pts[:, 2] - lo[2]
    sz1 = pts[:, 2] - hi[2]
    signed = np.stack([sx0, sx1, sy0, sy1, sz0, sz1])
    names = ["x-lo", "x-hi", "y-lo", "y-hi", "z-lo", "z-hi"]
    best = np.argmin(np.abs(signed), axis=0)
    dist = np.abs(signed[best, np.arange(pts.shape[0])])
    print(f"\nframe {f}: {pts.shape[0]} pts, median |dist| {np.median(dist)*1000:.1f} mm")
    for i, nm in enumerate(names):
        m = best == i
        if m.sum() < 20:
            continue
        s = signed[i][m]
        print(f"  {nm}: n={m.sum():6d}  median {np.median(s)*1000:+8.1f} mm  "
              f"mean {s.mean()*1000:+8.1f}  p05 {np.percentile(s,5)*1000:+8.1f}  "
              f"p95 {np.percentile(s,95)*1000:+8.1f}")

    # Is the residual correlated with distance from camera (i.e. a focal error)?
    cam = cap.positions[f]
    r = np.linalg.norm(pts - cam, axis=1)
    ok = dist < 0.30
    if ok.sum() > 50:
        A = np.column_stack([r[ok], np.ones(ok.sum())])
        coef, *_ = np.linalg.lstsq(A, dist[ok], rcond=None)
        print(f"  |dist| vs range: slope {coef[0]*1000:.2f} mm/m, "
              f"intercept {coef[1]*1000:.1f} mm  (focal error would show slope)")
        near = ok & (r < np.percentile(r[ok], 33))
        far = ok & (r > np.percentile(r[ok], 67))
        print(f"  median |dist| near {np.median(dist[near])*1000:.1f} mm, "
              f"far {np.median(dist[far])*1000:.1f} mm")