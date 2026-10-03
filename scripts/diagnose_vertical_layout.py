"""Determine the true vertical layout empirically instead of by sign convention.

Prints, for a few frames: camera height along the gravity axis, and the height
distribution of reconstructed points. The floor/ceiling assignment should fall
out of the data rather than out of my assumptions about which way is up.
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from scan2plan.ingest import load_zip, poses_to_matrices
from scan2plan.unproject import gravity_axis, frame_points_world

DATA = r'C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data'
SCALE = 0.0009997

for name, zf in [('floor_only', 'single_scan_floor_only.zip'),
                 ('with_ceiling', 'single_scan_with_ceiling.zip'),
                 ('single_room', 'single_room.zip')]:
    cap = load_zip(os.path.join(DATA, zf), frame_stride=40)
    up = gravity_axis(cap.positions, cap.quats)
    T_cw = poses_to_matrices(cap.positions, cap.quats)

    cam_h = cap.positions @ up
    print('=' * 70)
    print(f'{name}: {cap.n_frames} frames')
    print(f'  up = ({up[0]:+.3f},{up[1]:+.3f},{up[2]:+.3f})')
    print(f'  camera height along up: min={cam_h.min():+.3f} max={cam_h.max():+.3f} '
          f'mean={cam_h.mean():+.3f} spread={np.ptp(cam_h):.3f} m')

    for f in np.linspace(0, cap.n_frames - 1, 4).astype(int):
        pts = frame_points_world(cap, int(f), SCALE, T_cw, stride=4)
        h = pts @ up
        # Look for horizontal planes by histogramming height: a floor and a
        # ceiling show up as sharp spikes; furniture and walls smear out.
        hist, edges = np.histogram(h, bins=60)
        top = np.argsort(hist)[::-1][:3]
        spikes = ', '.join(f'{edges[i]:+.2f}m({hist[i]})' for i in sorted(top))
        print(f'   frame {int(f):4d}: pts_h {h.min():+.2f}..{h.max():+.2f} m  '
              f'cam_h={cam_h[int(f)]:+.2f}  peaks: {spikes}')