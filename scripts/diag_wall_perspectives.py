"""Per-perspective diagnostics for every labelled wall in the field capture.

The capture labels each wall photo with a perspective index -- `m_wall_2.5` is the
fifth viewpoint of wall 2, `2_room_wall_2 (4)` the fourth. So each wall has 2-8
photographs of the same physical surface, and every room has exactly 4 walls.

That structure is the lever. A single oblique photograph of a wall gives a 3D extent
that is a *lower bound* on the wall length, because the frame rarely contains both
corners. Measuring several viewpoints and choosing between them properly should
beat pooling every view with a median.

This prints, per view, the quantities that decide which views are trustworthy:

  inc     angle between the fitted wall normal and the optical axis, in degrees.
          0 deg is fronto-parallel; near 90 deg the wall is seen edge-on and the
          measured extent is meaningless.
  horiz   in-plane extent along the wall, relative units.
  vert    in-plane extent along the vertical, relative units. This is the scale
          anchor: the wall spans floor to ceiling, whose height is on the tape.
  cover   whether the view plausibly contains the full floor-to-ceiling span,
          judged by vert against the median vert of that wall's views.
  length  horiz / vert * tape height -- the naive per-view length.

Run:

    python scripts/diag_wall_perspectives.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scan2plan.capture_manifest import group_by_wall, scan_capture  # noqa: E402
from measure_walls_from_depth import (  # noqa: E402
    dominant_wall,
    focal_from_hfov,
    load_depth,
    split_extent,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUTH = os.path.join(ROOT, "data", "field_ground_truth.json")
CAPTURE = os.environ.get(
    "SCAN2PLAN_CAPTURE", r"C:\Users\adity\OneDrive\Desktop\Narendra")


def angle_to_optical(normal: np.ndarray) -> float:
    n = np.asarray(normal, dtype=np.float64)
    n = n / max(np.linalg.norm(n), 1e-12)
    return float(np.degrees(np.arccos(np.clip(abs(float(n[2])), 0.0, 1.0))))


def main() -> int:
    with open(TRUTH, encoding="utf-8") as fh:
        truth = json.load(fh)
    heights = {r["id"]: float(r["height_m"]) for r in truth["rooms"]}

    items = [it for it in scan_capture(CAPTURE) if it.labelled and it.wall_index]
    groups = group_by_wall(items)
    if not groups:
        print(f"no labelled wall photos found under {CAPTURE}")
        return 1
    depth_dir = os.path.join(ROOT, "runs", "depth")

    summary = []
    for (room, wall), views in sorted(groups.items()):
        tape_h = heights.get(room)
        rows = []
        for it in views:
            dp = _depth_path(it, depth_dir)
            if dp is None:
                continue
            plane, up, npts = dominant_wall(load_depth(dp))
            if plane is None or len(plane.points) < 50:
                rows.append({"name": it.filename, "err": "no plane"})
                continue
            horiz, vert = split_extent(plane, up)
            inc = angle_to_optical(plane.normal)
            rows.append({
                "name": it.filename,
                "npts": int(npts),
                "inc_deg": round(inc, 1),
                "horiz_rel": round(float(horiz), 4),
                "vert_rel": round(float(vert), 4),
                "length_m": (round(float(horiz) / float(vert) * tape_h, 3)
                             if tape_h and vert > 0 else None),
            })

        good = [r for r in rows if r.get("length_m") is not None]
        if not good:
            print(f"\n=== {room} wall {wall}: no usable views")
            continue
        vmed = float(np.median([r["vert_rel"] for r in good]))
        for r in good:
            r["vert_vs_median"] = round(r["vert_rel"] / vmed, 3)

        print(f"\n=== {room} wall {wall}   {len(good)} views   tape height "
              f"{tape_h:.2f} m")
        print(f"  {'photo':<26}{'inc':>7}{'horiz':>9}{'vert':>9}{'vert/med':>10}{'length':>9}")
        for r in sorted(good, key=lambda x: x["inc_deg"]):
            print(f"  {r['name']:<26}{r['inc_deg']:>6.1f}d{r['horiz_rel']:>9.3f}"
                  f"{r['vert_rel']:>9.3f}{r['vert_vs_median']:>10.2f}"
                  f"{r['length_m']:>9.3f}")

        incs = [r["inc_deg"] for r in good]
        lens = [r["length_m"] for r in good]
        best = min(good, key=lambda x: x["inc_deg"])
        summary.append({
            "room": room, "wall": wall, "n_views": len(good),
            "inc_min": round(min(incs), 1), "inc_max": round(max(incs), 1),
            "len_min": round(min(lens), 3), "len_max": round(max(lens), 3),
            "len_median": round(float(np.median(lens)), 3),
            "len_by_best_incidence": best["length_m"],
            "best_view": best["name"],
        })

    print("\n" + "=" * 78)
    print(f"{'room':<17}{'wall':>5}{'views':>7}{'inc min':>9}{'inc max':>9}"
          f"{'len med':>9}{'len best-inc':>13}")
    for s in summary:
        print(f"{s['room']:<17}{s['wall']:>5}{s['n_views']:>7}{s['inc_min']:>8.1f}d"
              f"{s['inc_max']:>8.1f}d{s['len_median']:>9.3f}"
              f"{s['len_by_best_incidence']:>13.3f}")

    out = os.path.join(ROOT, "runs", "wall_perspectives.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"\nwrote {out}")
    return 0


def _depth_path(item, depth_dir: str) -> str | None:
    """Depth map for a capture item.

    Same convention as scripts/depth_capture.py: the photo's path relative to the
    capture root with separators flattened to `__`, under runs/depth.
    """
    stem = os.path.splitext(item.path)[0]
    cand = os.path.join(depth_dir, stem.replace(os.sep, "__") + ".png")
    return cand if os.path.isfile(cand) else None


if __name__ == "__main__":
    raise SystemExit(main())
