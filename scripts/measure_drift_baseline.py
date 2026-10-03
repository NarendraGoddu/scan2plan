"""Baseline drift measurement under the supplied poses (the 'before' run)."""
import os, sys, json
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from scan2plan.ingest import load_zip
from scan2plan.unproject import calibrate_scale, gravity_axis
from scan2plan.drift import measure_drift, drift_trend

DATA = os.environ.get('SCAN2PLAN_SAMPLE_DATA',
                       r'C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data')
ARCH = [('single_room', 'single_room.zip'),
        ('floor_only', 'single_scan_floor_only.zip'),
        ('with_ceiling', 'single_scan_with_ceiling.zip')]
SCALE = 0.0009997  # established in CP1, verified on all three archives

results = {}
for name, zf in ARCH:
    cap = load_zip(os.path.join(DATA, zf), frame_stride=10)
    up = gravity_axis(cap.positions, cap.quats)
    print('=' * 74)
    print(f'{name}: {cap.n_frames} frames (stride 10), gravity axis '
          f'({up[0]:+.3f},{up[1]:+.3f},{up[2]:+.3f})')
    d = measure_drift(cap, SCALE, n_frames=48, gravity_hint=up)
    t = drift_trend(cap, d)
    print(f'    drift trend vs distance: slope={t.get("slope_mm_per_m", float("nan")):.2f} mm/m  '
          f'residual={t.get("residual_std_mm", float("nan")):.1f} mm  '
          f'R2={t.get("r2", float("nan")):.3f}')
    results[name] = {'drift': d, 'trend': t, 'gravity': up.tolist(),
                     'n_frames': cap.n_frames}

print('=' * 74)
print('SUMMARY (supplied poses, no correction)')
print(f'{"capture":<14}{"fitted":>8}{"rej":>6}{"height std":>12}{"p5-p95":>10}'
      f'{"tilt max":>10}{"slope":>10}')
for name, r in results.items():
    d, t = r['drift'], r['trend']
    if 'floor_height_std_mm' not in d:
        print(f'{name:<14}{d.get("n_frames_used",0):>8}{d.get("n_rejected",0):>6}'
              f'   -- no floor observed --')
        continue
    print(f'{name:<14}{d["n_frames_used"]:>8}{d["n_rejected"]:>6}'
          f'{d["floor_height_std_mm"]:>10.1f}mm'
          f'{d["floor_height_p5_p95_mm"] if "floor_height_p5_p95_mm" in d else d["floor_height_p95_spread_mm"]:>8.1f}mm'
          f'{d["floor_tilt_max_deg"]:>9.3f}d'
          f'{t.get("slope_mm_per_m", float("nan")):>9.2f}')

out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'runs')
os.makedirs(out_dir, exist_ok=True)
with open(os.path.join(out_dir, 'drift_baseline.json'), 'w') as f:
    json.dump(results, f, indent=2)
print(f'\nwrote {out_dir}\\drift_baseline.json')