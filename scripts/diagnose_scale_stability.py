"""Is the plane-overlap scale estimator stable, or just lucky at full settings?"""
import os, sys, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from scan2plan.ingest import load_zip
from scan2plan.unproject import _plane_overlap_score
from scan2plan.ingest import poses_to_matrices

DATA = r'C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data'
ARCH = ['single_room.zip', 'single_scan_floor_only.zip', 'single_scan_with_ceiling.zip']
GRID = np.array([0.0005, 0.00075, 0.001, 0.00125, 0.0015, 0.002, 0.0025, 0.003, 0.004])

for stride in (40, 20):
    for zf in ARCH:
        cap = load_zip(os.path.join(DATA, zf), frame_stride=stride)
        T_cw = poses_to_matrices(cap.positions, cap.quats)
        n = cap.n_frames
        for n_pairs, gap in [(10, 3), (14, 3), (20, 5), (30, 8)]:
            if n - gap < 2:
                continue
            centres = np.linspace(0, n - 1 - gap, n_pairs).astype(int)
            pairs = [(int(c), int(c) + gap) for c in centres]
            t0 = time.time()
            ov = [ _plane_overlap_score(cap, pairs, s, T_cw)['overlap'] for s in GRID ]
            best = GRID[int(np.argmax(ov))]
            # peak prominence: best vs second-best local max elsewhere
            print(f'{zf[:26]:26s} stride={stride:3d} frames={n:5d} pairs={n_pairs:3d} gap={gap:2d} '
                  f'-> best={best*1000:.3f}mm  ov={[round(o,3) for o in ov]}  ({time.time()-t0:.1f}s)')
        print()