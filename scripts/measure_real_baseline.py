"""CP8: baseline the assessment gates on the three supplied sample archives.

Two things this deliberately does not do.

It does not score accuracy against a known room size, because the archives ship
with no metadata: three zips, no README, nothing stating the dimensions of the
scanned rooms. Any figure quoted as their "true" size would be invented, and an
invented reference makes every downstream accuracy number worthless. So what is
measured here is self-consistency, not accuracy. Accuracy stays on the synthetic
benchmark, where the truth is exact by construction.

It does not subsample twice. The previous version of this driver called
load_zip(frame_stride=8) and then segment_capture(frame_stride=8), so the
5251-frame archive was reduced to 83 frames -- 1.6% of the data -- and every
number it produced described a sample rather than the capture. Subsampling is
still worth doing for runtime, but it happens once, here, and the reduction is
printed.

The repeatability figure is the one the assessment actually gates on. Each
archive is segmented twice over disjoint halves of its frames, and the two
independent fits are compared: floor height, ceiling height, and per-wall offset.
Agreement between halves is a property of the estimator that no ground truth is
needed to measure, and it is what decides whether the accuracy numbers are
trustworthy when they are finally compared to a surveyed room.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip, motion_dedup
from scan2plan.plan import build_room_plan
from scan2plan.segment import segment_capture

DATA = os.environ.get(
    "SCAN2PLAN_SAMPLE_DATA", r"C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data")
SCALE = 0.0009997
POINT_STRIDE = 4

# frame_stride only. load_stride stays at 1 so the reduction happens exactly once.
ARCHIVES = [
    ("single_room", "single_room.zip", 2),
    ("floor_only", "single_scan_floor_only.zip", 8),
    ("with_ceiling", "single_scan_with_ceiling.zip", 8),
]


def _horizontal(planes, kind):
    return [p for p in planes if p.kind == kind]


def _height(p, up) -> float:
    return float(p.offset * (p.normal @ up))


def _wall_offsets(plan) -> dict:
    """Wall offsets keyed by rounded normal, so two fits can be compared."""
    out = {}
    for w in plan.walls:
        ang = round(float(np.degrees(np.arctan2(w.normal[1], w.normal[0]))), 0)
        key = ((ang % 180) // 15) * 15
        out.setdefault(key, []).append(float(w.offset))
    return {k: float(np.mean(v)) for k, v in out.items()}


def analyse(name: str, zip_name: str, frame_stride: int, motion_m: float | None,
            rectangular: bool = False) -> dict:
    path = os.path.join(DATA, zip_name)
    t0 = time.time()
    cap = load_zip(path, frame_stride=1)
    loaded = cap.n_frames
    t_load = time.time() - t0

    if motion_m:
        sel = motion_dedup(cap.positions, cap.quats, min_translation_m=motion_m)
        how = f"motion dedup > {motion_m * 100:.0f} cm"
    else:
        sel = np.arange(0, loaded, frame_stride)
        how = f"frame_stride {frame_stride}"
    view = cap.subset(sel)

    print("=" * 78)
    print(f"{name}  ({zip_name})")
    print(f"  {loaded} frames in archive, {how} -> {len(sel)} frames segmented")
    print(f"  ({100.0 * len(sel) / loaded:.1f}% of the archive)")

    rec: dict = {
        "archive": zip_name,
        "rectangular_prior": bool(rectangular),
        "frames_in_archive": int(loaded),
        "frame_stride": frame_stride if motion_m is None else None,
        "motion_dedup_m": motion_m,
        "load_stride": 1,
        "frames_segmented": int(len(sel)),
        "load_seconds": round(t_load, 1),
    }

    full = segment_capture(view, SCALE, frame_stride=1, point_stride=POINT_STRIDE)
    up = full["up"]
    plan = build_room_plan(full["planes"], up, view.positions, rectangular=rectangular)
    floors, ceils = _horizontal(full["planes"], "floor"), _horizontal(full["planes"], "ceiling")

    print(f"  consensus planes: "
          f"{ {k: sum(1 for p in full['planes'] if p.kind == k) for k in ('floor','ceiling','wall','other')} }")
    print(f"  frames used: {full['n_frames_used']}   camera height {full['camera_height_m']:+.3f} m")

    if floors:
        f = floors[0]
        print(f"  floor    height {_height(f, up):+.4f} m  rms {f.rms_m * 1000:6.1f} mm  "
              f"support {f.support}")
        rec["floor"] = {"height_m": round(_height(f, up), 4),
                        "rms_mm": round(f.rms_m * 1000, 1), "support": int(f.support)}
    if ceils:
        c = ceils[0]
        print(f"  ceiling  height {_height(c, up):+.4f} m  rms {c.rms_m * 1000:6.1f} mm  "
              f"support {c.support}")
        rec["ceiling"] = {"height_m": round(_height(c, up), 4),
                          "rms_mm": round(c.rms_m * 1000, 1), "support": int(c.support)}
    if floors and ceils:
        gap = abs(_height(ceils[0], up) - _height(floors[0], up))
        rms = float(np.hypot(floors[0].rms_m, ceils[0].rms_m))
        print(f"  ==> ceiling height {gap:.4f} m   (gate 15 mm; propagated 1sigma "
              f"{rms / np.sqrt(max(floors[0].support, 1)) * 1000:.0f} mm)")
        rec["ceiling_height_m"] = round(gap, 4)

    print(f"  walls found: {len(plan.walls)}   vertices: {len(plan.vertices_ab)}")
    if len(plan.vertices_ab) >= 3:
        sides = sorted(plan.edge_lengths_m)
        print(f"  room spans: {sides[0]:.3f} m x {sides[-1]:.3f} m   "
              f"area {plan.floor_area_m2:.2f} m2")
        rec["room"] = {"n_walls": len(plan.walls), "n_vertices": int(len(plan.vertices_ab)),
                       "spans_m": [round(s, 3) for s in sides],
                       "area_m2": round(plan.floor_area_m2, 2)}
    for note in plan.notes:
        print(f"    note: {note}")
    rec["notes"] = list(plan.notes)

    # --- repeatability over disjoint halves -------------------------------
    # Split the *selected* frames, so both halves are drawn from the same
    # selection policy and stay disjoint.
    idx = [int(i) for i in sel]
    mid = len(idx) // 2
    halves = {
        "first": list(idx[:mid]),
        "second": list(idx[mid:]),
    }
    fits = {}
    for label, frames in halves.items():
        sub = cap.subset(frames) if hasattr(cap, "subset") else None
        if sub is None:
            print("  (no subset() on Capture; cannot run the half-split)")
            rec["repeatability"] = None
            return rec
        r = segment_capture(sub, SCALE, frame_stride=1, point_stride=POINT_STRIDE)
        p = build_room_plan(r["planes"], r["up"], sub.positions, rectangular=rectangular)
        fl, ce = _horizontal(r["planes"], "floor"), _horizontal(r["planes"], "ceiling")
        fits[label] = {
            "frames": len(frames),
            "floor_height_m": _height(fl[0], r["up"]) if fl else None,
            "ceiling_height_m": _height(ce[0], r["up"]) if ce else None,
            "wall_offsets": _wall_offsets(p),
        }

    rep: dict = {"n_walls_first": len(fits["first"]["wall_offsets"]),
                 "n_walls_second": len(fits["second"]["wall_offsets"])}
    print(f"  repeatability over disjoint halves "
          f"({fits['first']['frames']} vs {fits['second']['frames']} frames):")
    for key, label in (("floor_height_m", "floor height"),
                       ("ceiling_height_m", "ceiling height")):
        a, b = fits["first"][key], fits["second"][key]
        if a is not None and b is not None:
            d = abs(a - b) * 1000
            rep[f"{key}_delta_mm"] = round(d, 1)
            print(f"    {label:16s} {d:6.1f} mm  (gate 10 mm)")
    common = set(fits["first"]["wall_offsets"]) & set(fits["second"]["wall_offsets"])
    if common:
        deltas = [abs(fits["first"]["wall_offsets"][k] - fits["second"]["wall_offsets"][k]) * 1000
                  for k in common]
        rep["wall_offset_delta_mm"] = {"n": len(deltas), "max": round(max(deltas), 1),
                                       "mean": round(float(np.mean(deltas)), 1)}
        print(f"    wall offsets     {max(deltas):6.1f} mm worst of {len(deltas)} matched "
              f"(gate 10 mm or 0.5% of wall)")
    else:
        print("    wall offsets     no wall direction matched across halves")
    rec["repeatability"] = rep
    rec["seconds"] = round(time.time() - t0, 1)
    print(f"  total {rec['seconds']}s")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--motion-dedup", type=float, default=None, metavar="M",
                    help="keep only frames that moved at least M metres since the "
                         "last kept frame, instead of subsampling by index")
    ap.add_argument("--out", default=None, help="output JSON (default runs/real_baseline.json)")
    ap.add_argument("--rectangular", action="store_true",
                    help="assume the room is a right-angled rectangle and keep "
                         "exactly four bounding walls. OFF by default. This is an "
                         "ASSUMPTION, not a measurement, and it must not be used "
                         "when reporting the synthetic benchmark: those rooms are "
                         "rectangular by construction, so the comparison is circular.")
    args = ap.parse_args()

    out = {"_rectangular_prior": bool(args.rectangular)}
    for name, zip_name, stride in ARCHIVES:
        if not os.path.isfile(os.path.join(DATA, zip_name)):
            print(f"skip {name}: {zip_name} not found under {DATA}")
            continue
        try:
            out[name] = analyse(name, zip_name, stride, args.motion_dedup,
                                rectangular=args.rectangular)
        except Exception as e:  # keep going; a partial baseline is still evidence
            print(f"  FAILED {name}: {type(e).__name__}: {e}")
            out[name] = {"archive": zip_name, "error": f"{type(e).__name__}: {e}"}
    runs = os.path.join(ROOT, "runs")
    os.makedirs(runs, exist_ok=True)
    path = args.out or os.path.join(runs, "real_baseline.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
