"""Why does select_room_boundary discard boundary walls?

Run for one synthetic room. Mirrors build_room_plan's own sequence --

    wall_from_plane -> merge_wall_fragments -> select_room_boundary
                    -> intersect_halfplanes

-- and prints, for every merged wall, the camera-path signed distances the
crossing test uses. The rule can then be judged on evidence instead of intuition.

Strides match benchmark_synthetic.evaluate() defaults; if they drift apart the
diagnostic reports a different segmentation than the benchmark and the two cannot
be compared.

    python scripts/diag_boundary_rule.py --room wide
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from scan2plan import synth  # noqa: E402
from scan2plan.ingest import load_zip  # noqa: E402
from scan2plan.plan import (  # noqa: E402
    WALL_CROSS_SLACK_M,
    WALL_OUTSIDE_SLACK_M,
    build_room_plan,
    floor_basis,
    intersect_halfplanes,
    merge_wall_fragments,
    select_room_boundary,
    wall_from_plane,
)
from scan2plan.segment import segment_capture  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "synthetic")
SCALE = 0.001
LOAD_STRIDE = 2
POINT_STRIDE = 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="wide", choices=sorted(synth.standard_rooms()))
    args = ap.parse_args()

    path = os.path.join(OUT, f"{args.room}.zip")
    if not os.path.exists(path):
        print(f"missing {path}; generate the synthetic archives first")
        return 1

    cap = load_zip(path, frame_stride=LOAD_STRIDE)
    seg = segment_capture(cap, SCALE, frame_stride=1, point_stride=POINT_STRIDE)

    pos = np.asarray(cap.positions, dtype=np.float64).reshape(-1, 3)
    u = np.asarray(seg["up"], dtype=np.float64)
    u = u / (np.linalg.norm(u) + 1e-12)
    a, b = floor_basis(u, pos)
    centre = np.median(pos, axis=0)
    interior = np.array([float(centre @ a), float(centre @ b)])
    cam = np.column_stack([pos @ a, pos @ b])

    raw = [w for w in (wall_from_plane(p, a, b, interior) for p in seg["planes"])
           if w is not None]
    merged = merge_wall_fragments(raw)

    print(f"room {args.room!r}  frames {cap.n_frames}  "
          f"raw walls {len(raw)}  merged {len(merged)}")
    print(f"camera path in floor basis: {cam.shape[0]} poses, "
          f"x {cam[:, 0].min():.2f}..{cam[:, 0].max():.2f}  "
          f"y {cam[:, 1].min():.2f}..{cam[:, 1].max():.2f}")
    print()
    print(f"{'off':>8} {'sup':>5} {'dmin':>8} {'dmax':>8} {'span':>8}  verdict")

    for w in sorted(merged, key=lambda x: -x.support):
        d = cam @ w.normal - w.offset
        crosses = d.min() < -WALL_CROSS_SLACK_M and d.max() > WALL_CROSS_SLACK_M
        side = "path one side" if (d.min() > 0) == (d.max() > 0) else "STRADDLES"
        verdict = "DISCARD (crossed)" if crosses else f"keep ({side})"
        print(f"{w.offset:+8.2f} {w.support:5d} {d.min():8.3f} {d.max():8.3f} "
              f"{d.max() - d.min():8.3f}  {verdict}")

    kept, notes = select_room_boundary(merged, cam)
    print(f"\nslack: cross={WALL_CROSS_SLACK_M} outside={WALL_OUTSIDE_SLACK_M}")
    print(f"kept {len(kept)} of {len(merged)}")
    for n in notes:
        print(f"  {n}")

    poly = intersect_halfplanes(kept, interior) if kept else None
    if poly is not None and len(poly) >= 3:
        xs, ys = poly[:, 0], poly[:, 1]
        print(f"  polygon {len(poly)} verts  span "
              f"{xs.max() - xs.min():.2f} x {ys.max() - ys.min():.2f} m")
    else:
        print("  half-planes do NOT bound a region")

    plan = build_room_plan(seg["planes"], seg["up"], cap.positions)
    print(f"\nbuild_room_plan -> {len(plan.walls)} walls, "
          f"{len(plan.vertices_ab)} verts, area {plan.floor_area_m2:.3f} m2")
    for n in plan.notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
