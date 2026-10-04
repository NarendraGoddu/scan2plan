"""Verify ingest + scale calibration on real sample data."""
import os
import sys
import time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from scan2plan.ingest import load_zip, poses_to_matrices
from scan2plan.unproject import calibrate_scale, gravity_axis, frame_points_world

SD = r'C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data'
CACHES = {}

def cached(zf, stride, max_frames):
    key = (zf, stride, max_frames)
    if key not in CACHES:
        CACHES[key] = load_zip(os.path.join(SD, zf), frame_stride=stride, max_frames=max_frames)
    return CACHES[key]

for zf in ['single_room.zip', 'single_scan_floor_only.zip', 'single_scan_with_ceiling.zip']:
    cap = cached(zf, 40, None)
    print('=' * 78)
    print(f'{zf}  scan={cap.scan_id} frames_loaded={cap.n_frames} (of depth map depth={cap.depth.shape})')
    T = poses_to_matrices(cap.positions, cap.quats)
    print(f'  raw count range {cap.depth.min()}..{cap.depth.max()}  median {np.median(cap.depth):.0f}')
    print(f'  per-frame depth focal (decimated) fx = {cap.depth_intrinsics(0)[0,0]:.3f}')

    T_cw = poses_to_matrices(cap.positions, cap.quats)
    up = gravity_axis(cap.positions, cap.quats)
    print(f'  gravity axis (world) = ({up[0]:+.3f},{up[1]:+.3f},{up[2]:+.3f})')

    idx = np.linspace(0, cap.n_frames - 1, 6).astype(int)
    t0 = time.time()
    res = calibrate_scale(cap)
    print(f'  calibration took {time.time()-t0:.1f}s')

    # After calibration: what does the point cloud actually look like?
    s = res['scale_m_per_unit']
    pts = np.concatenate([frame_points_world(cap, int(f), s, T_cw, stride=4) for f in idx[:6]], axis=0)
    print(f'  cloud from 6 frames: {pts.shape[0]} pts  '
          f'extent = {np.ptp(pts,axis=0).round(3)} m')
    lo, hi = pts.min(0), pts.max(0)
    print(f'  along-gravity extent = {np.ptp(pts @ up).round(3)} m')
    print()
