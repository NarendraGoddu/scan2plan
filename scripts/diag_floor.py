"""Why does the floor height disagree between the two halves of the walk?

The baseline reports floor-height disagreement of 0.6 / 189.5 / 801.0 mm across
disjoint halves of `single_room` / `floor_only` / `with_ceiling`. 801 mm is not
estimator noise; it is the size of a floor-level change. Before assuming the fit is
unstable, check whether the two halves are even looking at the same floor.

The halves are split by TEMPORAL INDEX -- `idx[:mid]` is the beginning of the walk and
`idx[mid:]` the end -- so a capture that visits two spaces at different levels puts
them in different halves by construction. If that is what is happening, the
measurement is not unstable and the split is the thing that is wrong.

H_A: the two halves cover different floor levels (or different storeys), so the
      801 mm is a real difference and the repeatability figure is meaningless.
H_B: the floor fit itself is unstable within a single space.

Discriminating evidence printed per subset:
  * the floor plane's normal, offset, support, rms
  * `member_height_spread_m` -- how far apart the planes folded into this consensus
    floor were. Large means the consolidation merged different heights.
  * `n_consensus_members` -- how many observations became this one floor
  * the camera-height range, and the XY footprint of each half

If H_A holds the two halves will have disjoint XY footprints and different heights.
If H_B holds they will overlap in XY and differ only in fit quality.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip  # noqa: E402
from scan2plan.plan import floor_basis  # noqa: E402
from scan2plan.segment import segment_capture  # noqa: E402

DATA = os.environ.get(
    "SCAN2PLAN_SAMPLE_DATA", os.path.join(ROOT, os.pardir, "Sample Data")
)
SCALE = 0.001
POINT_STRIDE = 4
ARCHIVES = [
    ("single_room", "single_room.zip", 2),
    ("floor_only", "single_scan_floor_only.zip", 8),
    ("with_ceiling", "single_scan_with_ceiling.zip", 8),
]


def height_of(p, up) -> float:
    return float(p.offset * (p.normal @ up))


def report(label, sub, seg, idx):
    up = seg["up"]
    floors = [p for p in seg["planes"] if p.kind == "floor"]
    pos = np.asarray(sub.positions, dtype=np.float64)
    u = up / np.linalg.norm(up)
    height_along_u = pos @ u
    a, b = floor_basis(u, pos)
    ab = np.column_stack([pos @ a, pos @ b])

    print(f"\n--- {label}: {len(idx)} frames")
    print(f"    camera height along up: "
          f"{height_along_u.min():+.3f} .. {height_along_u.max():+.3f} m "
          f"(median {np.median(height_along_u):+.3f})")
    print(f"    XY footprint: a {ab[:, 0].min():+.2f}..{ab[:, 0].max():+.2f} m, "
          f"b {ab[:, 1].min():+.2f}..{ab[:, 1].max():+.2f} m")
    print(f"    frames segment_capture used: {seg['n_frames_used']}")
    if not floors:
        print("    NO FLOOR PLANE")
        return None
    f = floors[0]
    print(f"    floor: height {height_of(f, up):+.4f} m   rms {f.rms_m * 1000:.1f} mm")
    print(f"           support {f.support}/{f.n_frames_total} frames   "
          f"members {f.n_consensus_members}   "
          f"member height spread {f.member_height_spread_m * 1000:.1f} mm")
    tilt = np.degrees(np.arccos(min(1.0, abs(float(f.normal @ u)))))
    print(f"           tilt from horizontal: {tilt:.2f} deg   "
          f"n_floor_planes_found {len(floors)}")
    return height_of(f, up)


def horizontal_candidates(cap, frames, up):
    """Every plane that `classify_planes` would call floor or ceiling, pre-merge.

    Replicates segment_capture up to (but not including) consolidate_horizontal,
    which is where the one-floor rule is imposed and where the choice is made.
    """
    from scan2plan.ingest import poses_to_matrices
    from scan2plan.plan import floor_basis
    from scan2plan.segment import (
        classify_planes,
        cluster_planes,
        fit_frame_planes,
        frame_points_world,
        orient_from_horizontal_planes,
    )
    from scan2plan.unproject import gravity_axis


    sub = cap.subset(frames)
    T_cw = poses_to_matrices(sub.positions, sub.quats)
    u = gravity_axis(sub.positions)
    u = u / (np.linalg.norm(u) + 1e-12)
    cam_h = float(np.median(sub.positions @ u))
    obs = []
    for f in range(0, sub.n_frames, 1):
        pts = frame_points_world(sub, f, SCALE, T_cw, stride=POINT_STRIDE)
        if pts.shape[0] < 800:
            continue
        obs.extend(fit_frame_planes(pts, f, max_planes=4))
    planes = cluster_planes(obs, n_frames_total=max(sub.n_frames, 1))
    planes = [p for p in planes if p.support >= 3]
    u2, _ = orient_from_horizontal_planes(planes, u, cam_h)
    planes = classify_planes(planes, u2, cam_h)
    return u2, [p for p in planes if p.kind in ("floor", "ceiling")], cam_h


def report_candidates(label, cap, frames):
    up, cands, cam_h = horizontal_candidates(cap, frames, None)
    u = up / np.linalg.norm(up)
    print(f"\n=== {label}: {len(frames)} frames ===")
    print(f"    camera median height along up: {cam_h:+.3f} m")
    if not cands:
        print("    no horizontal candidates")
        return
    rows = []
    for p in cands:
        h = float(p.offset * (p.normal @ u))
        rows.append((p.kind, h, p.support, p.rms_m))
    rows.sort(key=lambda r: r[1])
    for kind, h, sup, rms in rows:
        if kind == "floor":
            reach = cam_h - h
            # A standing operator holds a phone at ~1.4-1.6 m; seated, ~1.0-1.2 m.
            ok = "plausible" if 0.85 <= reach <= 2.10 else "IMPLAUSIBLE as floor"
            print(f"    {kind:8s} {h:+8.3f} m  support {sup:4d}  rms {rms * 1000:7.1f} mm"
                  f"   camera {reach:5.3f} m above it -> {ok}")
        else:
            print(f"    {kind:8s} {h:+8.3f} m  support {sup:4d}  rms {rms * 1000:7.1f} mm"
                  f"   ceiling {h - cam_h:5.3f} m above camera")
    fl = [r for r in rows if r[0] == "floor"]
    if fl:
        plausible = [r for r in fl if 0.85 <= cam_h - r[1] <= 2.10]
        print(f"    -> {len(fl)} floor-kind candidates, "
              f"{len(plausible)} physically plausible as a floor")
        if plausible:
            best = max(plausible, key=lambda r: r[2])
            print(f"    -> best plausible: {best[1]:+.3f} m "
                  f"(support {best[2]}, rms {best[3] * 1000:.1f} mm)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive", default="with_ceiling", choices=[a[0] for a in ARCHIVES])
    ap.add_argument("--candidates", action="store_true",
                    help="also list every pre-merge floor/ceiling candidate")
    args = ap.parse_args()

    name, zip_name, stride = next(a for a in ARCHIVES if a[0] == args.archive)
    path = os.path.join(DATA, zip_name)
    if not os.path.isfile(path):
        print(f"{zip_name} not found under {DATA}")
        return 1

    print(f"=== {name} ({zip_name}) ===")
    cap = load_zip(path, frame_stride=1)
    idx = list(range(0, cap.n_frames, stride))
    mid = len(idx) // 2

    if args.candidates:
        for label, frames in (("full", idx), ("first half", idx[:mid]),
                              ("second half", idx[mid:])):
            report_candidates(label, cap, frames)
        return 0

    hs = {}
    for label, frames in (("full", idx), ("first half", idx[:mid]), ("second half", idx[mid:])):
        sub = cap.subset(frames)
        seg = segment_capture(sub, SCALE, frame_stride=1, point_stride=POINT_STRIDE)
        hs[label] = report(label, sub, seg, frames)

    got = [v for v in hs.values() if v is not None]
    if len(got) >= 2:
        print(f"\nfull {hs['full']:+.4f} m   "
              f"first {hs['first half']:+.4f} m   second {hs['second half']:+.4f} m")
        print(f"half-to-half disagreement: "
              f"{abs(hs['first half'] - hs['second half']) * 1000:.1f} mm")
        print(f"full-to-half disagreement: "
              f"{max(abs(hs['full'] - hs['first half']), abs(hs['full'] - hs['second half'])) * 1000:.1f} mm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
