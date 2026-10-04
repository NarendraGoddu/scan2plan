"""Compare two real_baseline JSONs side by side.

Used to decide whether motion-based frame selection helps or hurts, and to keep
the decision reproducible rather than a claim in prose.

    python scripts/compare_baselines.py runs/real_baseline.json runs/real_baseline_motion.json
"""

from __future__ import annotations

import json
import os
import sys

# Camera-trajectory footprint per archive, from scripts/diag_trajectory_extent.py.
# Not ground truth -- the archives ship no metadata -- but a sanity bound: the
# operator cannot have walked further than the floor they scanned.
TRAJECTORY_M2 = {
    "single_room": 17.3,
    "floor_only": 73.8,
    "with_ceiling": 75.7,
}

ROWS = [
    ("frames segmented", lambda r: r["frames_segmented"], "{:>12}"),
    ("floor rms", lambda r: r["floor"]["rms_mm"], None),
    ("floor height split-half", lambda r: _rep(r, "floor_height_delta_mm"), None),
    ("walls found", lambda r: r["room"]["n_walls"], "{:>12}"),
    ("vertices", lambda r: r["room"]["n_vertices"], "{:>12}"),
    ("area m2", lambda r: r["room"]["area_m2"], None),
    ("wall offset split-half mm", lambda r: _wall_delta(r), None),
]


def _rep(rec: dict, key: str):
    rp = rec.get("repeatability") or {}
    v = rp.get(key)
    return None if v is None else round(float(v), 1)


def _wall_delta(rec: dict):
    rp = rec.get("repeatability") or {}
    w = rp.get("wall_offset_delta_mm") or {}
    v = w.get("max")
    return None if v is None else round(float(v), 1)


def _fmt(v, spec):
    if v is None:
        return f"{'none matched':>12}"
    if isinstance(v, float):
        return f"{v:>12.2f}"
    return f"{v:>12}"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a_path, b_path = sys.argv[1], sys.argv[2]
    with open(a_path, encoding="utf-8") as fh:
        a = json.load(fh)
    with open(b_path, encoding="utf-8") as fh:
        b = json.load(fh)

    print(f"A = {os.path.basename(a_path)}")
    print(f"B = {os.path.basename(b_path)}\n")

    for name in a:
        if name not in b:
            continue
        ra, rb = a[name], b[name]
        if "error" in ra or "error" in rb:
            print(f"== {name}: error  A={ra.get('error')}  B={rb.get('error')}\n")
            continue

        print(f"== {name}")
        print(f"{'metric':<26}{'A':>12}{'B':>12}")
        for label, fn, spec in ROWS:
            print(f"{label:<26}{_fmt(fn(ra), spec)}{_fmt(fn(rb), spec)}")
        t = TRAJECTORY_M2.get(name)
        if t:
            print(f"{'trajectory footprint m2':<26}{t:>12.2f}{t:>12.2f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
