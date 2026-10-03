"""Why do 4 correctly-found walls fail to bound a region?"""

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip
from scan2plan.plan import (
    floor_basis, merge_wall_fragments, wall_from_plane,
)
from scan2plan.segment import segment_capture
from scan2plan.unproject import gravity_axis

cap = load_zip(os.path.join(ROOT, "data", "synthetic", "nominal.zip"), frame_stride=2)
seg = segment_capture(cap, 0.001, frame_stride=1, point_stride=4)
u = seg["up"]
u = u / np.linalg.norm(u)
a, b = floor_basis(u, cap.positions)
centre = np.median(cap.positions, axis=0)
interior = np.array([float(centre @ a), float(centre @ b)])

print(f"up {np.round(u, 4)}   basis a {np.round(a, 4)}  b {np.round(b, 4)}")
print(f"interior point (alpha,beta) = {np.round(interior, 4)}")
print(f"true room: x[0,4.6] z[0,3.4]; camera centre should be well inside\n")

raw = []
for p in seg["planes"]:
    if p.kind != "wall":
        continue
    w = wall_from_plane(p, a, b, interior)
    if w is not None:
        raw.append(w)

print(f"wall candidates before merge: {len(raw)}")
walls = merge_wall_fragments(raw)
print(f"after merge: {len(walls)}\n")

for i, w in enumerate(walls):
    n_ab, off = w.normal, w.offset
    val = float(n_ab @ interior - off)
    A = n_ab if w.inward else -n_ab
    bb = -off if w.inward else off
    feas = float(A @ interior + bb)
    print(f"  wall {i}: n=({n_ab[0]:+.4f},{n_ab[1]:+.4f}) off={off:+8.4f} "
          f"sup={w.support:3d} inward={w.inward}  n.i-off={val:+7.4f}  "
          f"A.i+b={feas:+7.4f} {'OK' if feas < 0 else 'VIOLATED'}")

A = np.array([w.normal if w.inward else -w.normal for w in walls])
bv = np.array([-w.offset if w.inward else w.offset for w in walls])
print(f"\nseed strictly inside all? {bool(np.all(A @ interior + bv < 0))}")
print(f"max A.i+b = {np.max(A @ interior + bv):+.6f}")

# Where are the walls relative to the interior?
print("\nwall lines in floor coords (signed distance of interior to each):")
for i, w in enumerate(walls):
    d = float(w.normal @ interior - w.offset)
    # Unit direction along the wall.
    t = np.array([-w.normal[1], w.normal[0]])
    print(f"  wall {i}: interior is {abs(d):7.4f} m from the line, "
          f"{'inside' if d < 0 else 'OUTSIDE'}")

print("\ntrue walls are 4.6 (along x) and 3.4 (along z) metres apart;")
print("expect signed distances ~ -2.3 and -1.7 for a camera at the centre.")

print("\n--- calling scipy directly, uncaught ---")
import traceback
from scipy.spatial import HalfspaceIntersection

print(f"A shape {np.shape(A)} dtype {np.asarray(A).dtype}")
print(f"b shape {np.shape(bv)}")
print(f"interior shape {np.shape(interior)} dtype {np.asarray(interior).dtype}")
print(f"interior = {interior!r}")
try:
    hsi = HalfspaceIntersection(A, bv, interior)
    print(f"intersections shape {np.asarray(hsi.intersections).shape}")
    print(np.round(hsi.intersections, 4))
except Exception:
    traceback.print_exc()

print("\n--- calling intersect_halfplanes ---")
from scan2plan.plan import intersect_halfplanes, polygon_area

out = intersect_halfplanes(walls, interior)
print(f"returned {None if out is None else np.asarray(out).shape}")
if out is not None and len(out) >= 3:
    print(np.round(np.asarray(out), 4))
    print(f"area {polygon_area(np.asarray(out)):.4f} m2")