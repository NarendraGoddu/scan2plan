"""Measure damage detection against exact synthetic truth.

Sensitivity on its own is meaningless: a detector that flags every wall as damaged
scores 100%. So every run includes undamaged control rooms and the false-positive
count is reported first.

Scored per damage case:
  detected      a detection whose centre lies within MATCH_RADIUS_M of the truth
  localisation  distance between the two centres
  length        detected long-axis extent against the true long axis
  depth         detected mean groove against the true depth

Reported alongside, because it bounds everything else:
  pixel footprint  metres per depth pixel at working range, i.e. the finest groove
                   this sensor can localise at all
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.damage import detect_damage, pixel_footprint_m  # noqa: E402
from scan2plan.ingest import load_zip, poses_to_matrices  # noqa: E402
from scan2plan.segment import segment_capture  # noqa: E402
from scan2plan.synth import (  # noqa: E402
    damage_rooms,
    render_capture,
    standard_rooms,
    write_archive,
)

SCALE = 0.001  # nominal; CP1 recovered 0.0009997 on the real archives
MATCH_RADIUS_M = 0.60


def _match(truths: list[dict], dets: list, radius: float = MATCH_RADIUS_M) -> list[dict | None]:
    """Greedy nearest-detection assignment, deepest truth first.

    Deepest first so a shallow case cannot steal the detection belonging to a deep
    one sitting nearby.
    """
    used: set[int] = set()
    out: list[dict | None] = [None] * len(truths)
    order = sorted(range(len(truths)), key=lambda i: -float(truths[i].get("depth_m", 0.0)))
    for i in order:
        t = truths[i]
        tc = np.asarray(t["centre_world"], dtype=np.float64)
        best, best_d = None, np.inf
        for j, d in enumerate(dets):
            if j in used:
                continue
            dist = float(np.linalg.norm(np.asarray(d.centre_world) - tc))
            if dist < best_d:
                best, best_d = j, dist
        if best is not None and best_d <= radius:
            used.add(best)
            out[i] = {"truth": t, "det": dets[best], "dist_m": best_d}
    return out


def run(name: str, room, frames: int, depth_noise_m: float, seed: int, workdir: str) -> dict:
    cap = render_capture(
        room, n_frames=frames, depth_noise_m=depth_noise_m, seed=seed, scan_id=name
    )
    # Round-trip through the real archive format and the real loader rather than
    # hand-feeding renderer output to the detector. Otherwise this benchmark would
    # measure a path no user ever takes, and the depth quantisation and intrinsics
    # handling that have already caused real bugs would be silently skipped.
    path = os.path.join(workdir, f"{name}.zip")
    truth = write_archive(cap, path)
    loaded = load_zip(path, frame_stride=1)
    T_cw = poses_to_matrices(loaded.positions, loaded.quats)
    seg = segment_capture(loaded, SCALE, frame_stride=1, point_stride=4)
    dets = detect_damage(loaded, SCALE, seg, T_cw, stride=1)

    truths = truth.get("damage", [])
    matched = _match(truths, dets)
    n_extra = len(dets) - sum(1 for m in matched if m is not None)
    footprint = pixel_footprint_m(loaded, 0, 2.5)

    cases: list[dict] = []
    for m, t in zip(matched, truths, strict=True):
        row = {
            "kind": t["kind"],
            "face": t["face"],
            "true_length_m": t["length_m"],
            "true_width_mm": round(t["width_m"] * 1000, 1),
            "true_depth_mm": round(t["depth_m"] * 1000, 2),
            "width_over_footprint": round(t["width_m"] / footprint, 2),
        }
        if m is None:
            row["detected"] = False
        else:
            d = m["det"]
            row.update(
                {
                    "detected": True,
                    "localisation_mm": round(m["dist_m"] * 1000, 1),
                    "det_length_m": round(d.length_m, 4),
                    "length_err_mm": round((d.length_m - t["length_m"]) * 1000, 1),
                    "det_width_mm": round(d.width_m * 1000, 1),
                    "det_depth_mm": round(d.mean_depth_m * 1000, 2),
                    "severity": d.severity,
                    "n_cells": d.n_cells,
                    "mean_obs": d.mean_obs,
                }
            )
        cases.append(row)

    return {
        "room": name,
        "n_frames": frames,
        "depth_noise_mm": round(depth_noise_m * 1000, 2),
        "pixel_footprint_mm_at_2.5m": round(footprint * 1000, 2),
        "n_walls": sum(1 for p in seg.get("planes", []) if getattr(p, "kind", "") == "wall"),
        "n_detections": len(dets),
        "n_false_positives": int(n_extra),
        "n_truth_damage": len(truths),
        "detected": sum(1 for c in cases if c["detected"]),
        "cases": cases,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=240)
    ap.add_argument("--noise-mm", type=float, default=4.0)
    ap.add_argument("--out", default="out/damage_benchmark.json")
    ap.add_argument("--workdir", default="out/damage_archives")
    args = ap.parse_args()

    os.makedirs(args.workdir, exist_ok=True)
    report: dict = {
        "note": (
            "width_over_footprint > 1 means the groove is wider than one depth pixel "
            "and is localisable in principle; below 1 it is sub-pixel, so no amount of "
            "frame averaging recovers it, because every pixel it touches also contains "
            "wall."
        ),
        "controls": [],
        "cases": [],
    }

    print("=== controls: undamaged rooms, false positives only ===")
    for name in ("nominal", "wide", "tight"):
        r = run(
            f"control_{name}", standard_rooms()[name], args.frames, args.noise_mm / 1000, 3,
            args.workdir,
        )
        report["controls"].append(r)
        print(
            f"  {name:14s} walls={r['n_walls']:2d} detections={r['n_detections']:2d} "
            f"false_positives={r['n_false_positives']:2d}"
        )

    print("\n=== damaged room ===")
    r = run(
        "damaged", damage_rooms()["damaged"], args.frames, args.noise_mm / 1000, 11, args.workdir
    )
    report["cases"].append(r)
    print(
        f"  frames={r['n_frames']} noise={r['depth_noise_mm']}mm "
        f"footprint={r['pixel_footprint_mm_at_2.5m']}mm/px "
        f"detections={r['n_detections']} false_positives={r['n_false_positives']}"
    )
    print(
        f"  {'kind':8s}{'face':6s}{'found':7s}{'true mm':>9s}{'det mm':>9s}"
        f"{'len err':>10s}{'loc mm':>9s}{'w/px':>7s}  severity"
    )
    for c in r["cases"]:
        if c["detected"]:
            print(
                f"  {c['kind']:8s}{c['face']:6s}{'yes':7s}"
                f"{c['true_depth_mm']:9.1f}{c['det_depth_mm']:9.1f}"
                f"{c['length_err_mm']:10.1f}{c['localisation_mm']:9.1f}"
                f"{c['width_over_footprint']:7.2f}  {c['severity']}"
            )
        else:
            print(
                f"  {c['kind']:8s}{c['face']:6s}{'NO':7s}"
                f"{c['true_depth_mm']:9.1f}{'-':>9s}{'-':>10s}{'-':>9s}"
                f"{c['width_over_footprint']:7.2f}  (sub-pixel or below noise)"
            )

    total_fp = sum(c["n_false_positives"] for c in report["controls"]) + r["n_false_positives"]
    report["total_false_positives"] = int(total_fp)
    print(f"\n  total false positives across all runs: {total_fp}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
