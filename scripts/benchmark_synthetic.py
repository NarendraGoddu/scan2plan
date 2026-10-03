"""Generate synthetic rooms and score the pipeline against exact ground truth.

This is the benchmark the assessment asks for, and it is deliberately built so
a reviewer can re-run it: every number is derived from the room definition in
synth.py, not from a stored result file.
"""

import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.ingest import load_zip
from scan2plan.plan import build_room_plan
from scan2plan.segment import segment_capture
from scan2plan.synth import render_capture, standard_rooms, write_archive

OUT = os.path.join(ROOT, "data", "synthetic")
SCALE = 0.001  # nominal; CP1 recovered 0.0009997 on real data


def generate(names=None, n_frames=720, seed=7):
    os.makedirs(OUT, exist_ok=True)
    rooms = standard_rooms()
    names = names or list(rooms)
    truths = {}
    for name in names:
        room = rooms[name]
        t0 = time.time()
        cap = render_capture(
            room, n_frames=n_frames, seed=seed, scan_id=f"syn_{name}"
        )
        path = os.path.join(OUT, f"{name}.zip")
        truths[name] = write_archive(cap, path)
        with open(os.path.join(OUT, f"{name}_truth.json"), "w", encoding="utf-8") as fh:
            json.dump(truths[name], fh, indent=2)
        print(f"  generated {name}: {n_frames} frames -> {os.path.basename(path)} "
              f"({time.time() - t0:.1f}s)")
    return truths


def evaluate(names=None, load_stride=2, point_stride=4):
    """Score the pipeline against exact truth.

    Note the two strides are separate knobs. load_zip decodes every frame it is
    given, and segment_capture then subsamples its input. Applying the same
    stride in both places silently squares it: a capture of 720 frames loaded
    at stride 8 and segmented at stride 8 yields 11 usable frames, not 90.
    That bug was present in the real-data runs too, where it discarded 87% of
    floor_only before any plane was fitted.
    """
    rooms = standard_rooms()
    names = names or list(rooms)
    report = {"rooms": {}, "gates": {}}
    length_errs, height_errs, area_errs = [], [], []

    for name in names:
        path = os.path.join(OUT, f"{name}.zip")
        if not os.path.exists(path):
            print(f"  MISSING {path}; run generate() first")
            continue
        with open(os.path.join(OUT, f"{name}_truth.json"), encoding="utf-8") as fh:
            truth = json.load(fh)

        t0 = time.time()
        cap = load_zip(path, frame_stride=load_stride)
        seg = segment_capture(cap, SCALE, frame_stride=1, point_stride=point_stride)
        plan = build_room_plan(seg["planes"], seg["up"], cap.positions)
        elapsed = time.time() - t0

        gt_L, gt_W = truth["room_length_m"], truth["room_width_m"]
        gt_area = truth["floor_area_m2"]
        gt_h = truth["room_height_m"]

        edges = sorted(plan.edge_lengths_m) if plan.edge_lengths_m else []
        gt_edges = sorted([gt_L, gt_W, gt_L, gt_W])
        if len(edges) == 4:
            eL, eW = edges[1], edges[2]  # the two distinct spans
        elif len(edges) >= 2:
            eL = float(np.mean(edges[0::2])) if len(edges) > 2 else edges[0]
            eW = float(np.mean(edges[1::2]))
        else:
            eL = eW = float("nan")

        # Match each estimated span to whichever true dimension is closer, so
        # a rotated floor basis does not read as a large error.
        def best_pair(a, b):
            if abs(a - gt_L) <= abs(a - gt_W):
                return a, b, gt_L, gt_W
            # `a` already pairs with gt_W and `b` with gt_L, so only the truths
            # move. Swapping the estimates too would compare each span against
            # the dimension it is not.
            return a, b, gt_W, gt_L

        e_L, e_W, t_L, t_W = best_pair(eL, eW)

        ch = plan.ceiling_height_m
        rec = {
            "n_frames_used": int(seg["n_frames_used"]),
            "n_walls_found": len(plan.walls),
            "n_vertices": int(len(plan.vertices_ab)),
            "floor_area_m2": plan.floor_area_m2,
            "floor_area_err_m2": plan.floor_area_m2 - gt_area,
            "floor_area_err_pct": 100.0 * (plan.floor_area_m2 - gt_area) / gt_area,
            "span_a_m": e_L, "span_a_true_m": t_L, "span_a_err_mm": (e_L - t_L) * 1000.0,
            "span_b_m": e_W, "span_b_true_m": t_W, "span_b_err_mm": (e_W - t_W) * 1000.0,
            "ceiling_height_m": ch,
            "ceiling_height_err_mm": None if ch is None else (ch - gt_h) * 1000.0,
            "n_floor": sum(1 for p in seg["planes"] if p.kind == "floor"),
            "n_ceiling": sum(1 for p in seg["planes"] if p.kind == "ceiling"),
            "notes": plan.notes,
            "seconds": round(elapsed, 1),
        }
        report["rooms"][name] = rec

        if len(edges) >= 4:
            length_errs += [abs(e_L - t_L), abs(e_W - t_W)]
        if ch is not None:
            height_errs.append(abs(ch - gt_h) * 1000.0)
        if plan.floor_area_m2 > 0:
            area_errs.append(abs(plan.floor_area_m2 - gt_area) / gt_area * 100.0)

        print(f"  {name:12s} walls={len(plan.walls)} verts={len(plan.vertices_ab)} "
              f"area {plan.floor_area_m2:7.3f} m2 ({rec['floor_area_err_pct']:+6.2f}%)  "
              f"span {e_L:5.3f}/{e_W:5.3f} err {rec['span_a_err_mm']:+7.1f}/"
              f"{rec['span_b_err_mm']:+7.1f} mm  "
              f"ceil {('%.3f' % ch) if ch else '  n/a ':>5} "
              f"({('%+.1f' % rec['ceiling_height_err_mm']) if ch else 'n/a':>7} mm)  "
              f"{elapsed:.0f}s")
        for nt in plan.notes:
            print(f"               note: {nt}")

    def summarise(vals):
        if not vals:
            return None
        a = np.array(vals, dtype=np.float64)
        return {
            "n": int(a.size),
            "median_mm": round(float(np.median(a)), 2),
            "p90_mm": round(float(np.percentile(a, 90)), 2),
            "max_mm": round(float(a.max()), 2),
        }

    report["gates"] = {
        "wall_length_error": summarise(length_errs),
        "ceiling_height_error": summarise(height_errs),
        "floor_area_error_pct": summarise(area_errs),
        "assessment_targets": {
            "openings_width_cm": 2.0,
            "ceiling_height_cm": 1.5,
            "repeatability_cm": 1.0,
        },
    }
    return report


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "generate"):
        print("generating synthetic rooms")
        generate()
    if mode in ("all", "evaluate"):
        print("\nscoring pipeline against exact ground truth")
        rep = evaluate()
        os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
        with open(os.path.join(ROOT, "runs", "synthetic_benchmark.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
        print("\nsummary (errors in mm unless noted)")
        for k, v in rep["gates"].items():
            if isinstance(v, dict):
                print(f"  {k:26s} {v}")
        print("\nwrote runs/synthetic_benchmark.json")