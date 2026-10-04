"""Why does wall-offset repeatability read ~6 m?

Hypothesis: `_wall_offsets` keys a *signed* distance by an orientation taken modulo
180 degrees, so opposite walls share a key and get averaged together; each half then
averages a different set of walls into that key. The metric is ill-posed rather than
the segmenter being wrong.

Test: dump the per-key members for both halves of one archive and show what is being
averaged. If the spread within a key is metres, the hypothesis holds.
"""

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.ingest import load_zip
from scan2plan.plan import build_room_plan
from scan2plan.segment import segment_capture

DATA = os.environ.get(
    "SCAN2PLAN_SAMPLE_DATA",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "..", "Sample Data"),
)
SCALE = 0.001
POINT_STRIDE = 4


def key_of(normal) -> float:
    ang = round(float(np.degrees(np.arctan2(normal[1], normal[0]))), 0)
    return float(((ang % 180) // 15) * 15)


def dump(plan, label):
    buckets: dict[float, list[float]] = {}
    for w in plan.walls:
        buckets.setdefault(key_of(w.normal), []).append(float(w.offset))
    print(f"  {label}: {len(plan.walls)} walls -> {len(buckets)} keys")
    for k in sorted(buckets):
        v = buckets[k]
        spread = max(v) - min(v)
        mean = float(np.mean(v))
        flag = "  <-- metres apart" if spread > 0.5 else ""
        print(f"    key {k:5.1f}: n={len(v)} mean={mean:+8.3f} spread={spread:7.3f}"
              f"  members={[round(x, 3) for x in v]}{flag}")
    return {k: float(np.mean(v)) for k, v in buckets.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive", default="single_room.zip")
    ap.add_argument("--frame-stride", type=int, default=2)
    args = ap.parse_args()

    cap = load_zip(os.path.join(DATA, args.archive), frame_stride=1)
    idx = list(range(0, cap.n_frames, args.frame_stride))
    mid = len(idx) // 2

    means = {}
    for label, frames in (("first", idx[:mid]), ("second", idx[mid:])):
        sub = cap.subset(frames)
        r = segment_capture(sub, SCALE, frame_stride=1, point_stride=POINT_STRIDE, verbose=False)
        p = build_room_plan(r["planes"], r["up"], sub.positions)
        means[label] = dump(p, label)

    print("\n  reported metric compares these means:")
    common = set(means["first"]) & set(means["second"])
    for k in sorted(common):
        a, b = means["first"][k], means["second"][k]
        print(f"    key {k:5.1f}: {a:+8.3f} vs {b:+8.3f}  delta={abs(a - b) * 1000:8.1f} mm")


if __name__ == "__main__":
    main()
