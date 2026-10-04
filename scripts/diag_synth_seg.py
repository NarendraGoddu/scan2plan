"""Diagnose why segmentation finds no floor on synthetic data."""

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip, poses_to_matrices
from scan2plan.segment import (
    cluster_planes, fit_frame_planes, orient_from_horizontal_planes,
)
from scan2plan.unproject import frame_points_world, gravity_axis

path = os.path.join(ROOT, "data", "synthetic", "nominal.zip")
cap = load_zip(path, frame_stride=8)
n = cap.depth.shape[0]
print(f"frames loaded: {n}")

valid = cap.confidence == 2
per_frame = valid.sum(axis=(1, 2))
print(f"valid depth fraction overall: {valid.mean():.3f}")
print(f"per-frame valid pixels: min {per_frame.min()}, median "
      f"{int(np.median(per_frame))}, max {per_frame.max()}")
empty = np.where(per_frame < 500)[0]
print(f"frames with <500 valid pixels: {len(empty)} -> {empty[:20]}")

d = cap.depth[valid].astype(np.float64) * 0.001
print(f"valid depth range: {d.min():.3f} .. {d.max():.3f} m  (median {np.median(d):.3f})")

SCALE = 0.001
T_cw = poses_to_matrices(cap.positions, cap.quats)
u = gravity_axis(cap.positions)
u = u / np.linalg.norm(u)
print(f"\ngravity axis (sign arbitrary): {np.round(u, 4)}")
print(f"camera y range: {cap.positions[:, 1].min():.3f} .. "
      f"{cap.positions[:, 1].max():.3f}")

obs = []
counts = []
for f in range(n):
    pts = frame_points_world(cap, f, SCALE, T_cw, stride=4)
    if pts.shape[0] < 800:
        counts.append((f, pts.shape[0], 0))
        continue
    got = fit_frame_planes(pts, frame=f, max_planes=6, min_inliers=400)
    counts.append((f, pts.shape[0], len(got)))
    for o in got:
        obs.append(o)

print(f"\nframes with <800 unprojected points: "
      f"{sum(1 for _, p, _ in counts if p < 800)}")
print(f"total plane observations: {len(obs)}")
print("first 8 frames (frame, npoints, nplanes):")
for c in counts[:8]:
    print("   ", c)

if obs:
    angs = np.array([np.degrees(np.arccos(min(1.0, abs(float(o.normal @ u)))))
                     for o in obs])
    print(f"\nangle-to-gravity (deg): min {angs.min():.1f} p10 "
          f"{np.percentile(angs, 10):.1f} median {np.median(angs):.1f} "
          f"max {angs.max():.1f}")
    print(f"  <=20 deg from horizontal (floor/ceiling candidates): {(angs <= 20).sum()}")
    print(f"  >=65 deg from horizontal (wall candidates)          : {(angs >= 65).sum()}")
    print(f"  in between (would classify 'other')                 : "
          f"{((angs > 20) & (angs < 65)).sum()}")

    planes = cluster_planes(obs, n_frames_total=len(obs))
    print(f"\nclusters: {len(planes)}")
    for p in planes[:14]:
        ang = np.degrees(np.arccos(min(1.0, abs(float(p.normal @ u)))))
        h = p.offset * (p.normal @ u)
        print(f"  sup={p.support:3d} ang={ang:6.1f}d height={h:+7.3f} "
              f"off={p.offset:+8.3f} rms={p.rms_m * 1000:6.1f}mm "
              f"n=({p.normal[0]:+.2f},{p.normal[1]:+.2f},{p.normal[2]:+.2f})")

    v, info = orient_from_horizontal_planes(planes, u, 0.0)
    print(f"\norient: flipped={info['flipped']} confidence="
          f"{info.get('confidence')}")
    print(f"  up = {np.round(v, 4)}")

print("\nTRUTH: floor at height 0, ceiling +2.720, walls x=0/4.6, z=0/3.4")
print("        camera y ~1.40, so floor must read ~1.4 BELOW camera")
