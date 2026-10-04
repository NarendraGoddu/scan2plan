"""Run plane segmentation on the sample archives and inspect the geometry."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
from scan2plan.ingest import load_zip
from scan2plan.segment import segment_capture

DATA = os.environ.get('SCAN2PLAN_SAMPLE_DATA',
                       r'C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data')
SCALE = 0.0009997

for name, zf in [('floor_only', 'single_scan_floor_only.zip'),
                 ('with_ceiling', 'single_scan_with_ceiling.zip'),
                 ('single_room', 'single_room.zip')]:
    cap = load_zip(os.path.join(DATA, zf), frame_stride=8)
    print('=' * 74)
    print(f'{name}: {cap.n_frames} frames loaded (stride 8)')
    res = segment_capture(cap, SCALE, frame_stride=8, point_stride=4)
    planes, up = res['planes'], res['up']
    if not planes:
        print('    NO PLANES')
        continue
    print(f"    up = ({up[0]:+.3f},{up[1]:+.3f},{up[2]:+.3f})  cam_h = {res['camera_height_m']:+.3f} m")

    for kind in ('floor', 'ceiling', 'wall', 'other'):
        sel = [p for p in planes if p.kind == kind]
        if not sel:
            continue
        print(f'    {kind:8s} ({len(sel)}):')
        for p in sel[:6]:
            h = float(p.offset * (p.normal @ up))
            ang = p.angle_to(up)
            print(f'       sup={p.support:4d} ({p.frame_fraction*100:4.1f}% of frames)  '
                  f'height={h:+7.3f} m  angle_to_up={ang:5.1f}d  rms={p.rms_m*1000:6.1f} mm  '
                  f'n=({p.normal[0]:+.2f},{p.normal[1]:+.2f},{p.normal[2]:+.2f})')
        if len(sel) > 6:
            print(f'       ... and {len(sel)-6} more')

    floors = [p for p in planes if p.kind == 'floor']
    ceils = [p for p in planes if p.kind == 'ceiling']
    if floors and ceils:
        f, c = floors[0], ceils[0]
        gap = abs(float(c.offset * (c.normal @ up)) - float(f.offset * (f.normal @ up)))
        print(f'    ==> ceiling height above floor: {gap:.3f} m  '
              f'(floor rms {f.rms_m*1000:.1f} mm, ceiling rms {c.rms_m*1000:.1f} mm)')
