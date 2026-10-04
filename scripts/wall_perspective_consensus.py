"""Per-wall measurement using the capture's own wall/perspective labels.

What the labels give us, and what they do not.

The capture labels each photo with a wall number and a perspective index --
`m_wall_2.5` is the fifth viewpoint of wall 2, `2_room_wall_2 (4)` the fourth. That
yields two independent facts:

  GIVEN      every room has exactly 4 walls. This is a property of the capture, not
             of the tape, so it corroborates the room model on its own.
  NOT GIVEN  the walls' dimensions. The perspective index says which photos share a
             wall; it says nothing about scale, and no frame carries a scale
             reference.

So the labels fix the topology and not the metrology. This script uses them to do
exactly that, and reports honestly which walls can be measured at all.

Why most cannot: length comes from a wall photographed at an oblique angle, and
monocular depth has almost no signal across a surface seen near edge-on. The
incidence angle -- between the fitted wall normal and the optical axis -- is
computable per view without any camera pose, and it says which views are usable:

  < 40 deg   usable. The frame spans the wall's width.
  >= 68 deg  unusable. The frame contains a sliver of width and the depth model has
             little to say about it.

Measured on this capture, 9 of the 12 walls have no view better than 68 degrees, so
the photo tier cannot produce a metric length for them at all.

    python scripts/wall_perspective_consensus.py --capture <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

from scan2plan.capture_manifest import group_by_wall, scan_capture  # noqa: E402
from diag_wall_perspectives import _depth_path, angle_to_optical  # noqa: E402
from measure_walls_from_depth import dominant_wall, load_depth, split_extent  # noqa: E402

TRUTH = os.path.join(ROOT, "data", "field_ground_truth.json")

# Below this a view spans the wall's width; above it the wall is edge-on.
USABLE_INC_DEG = 40.0
# A view whose vertical extent is far from its wall's median has cropped the floor
# or the ceiling, so its scale anchor is wrong and it cannot measure length.
VERT_TOLERANCE = 0.25


def measure_wall(room: str, wall: int, views, tape_h: float | None, depth_dir: str):
    rows = []
    for it in views:
        dp = _depth_path(it, depth_dir)
        if dp is None:
            continue
        plane, up, npts = dominant_wall(load_depth(dp))
        if plane is None or len(plane.points) < 50:
            continue
        horiz, vert = split_extent(plane, up)
        if vert <= 0:
            continue
        rows.append({
            "photo": it.filename,
            "frame_index": it.frame_index,
            "inc_deg": round(angle_to_optical(plane.normal), 1),
            "horiz_rel": round(float(horiz), 4),
            "vert_rel": round(float(vert), 4),
            "length_m": round(float(horiz) / float(vert) * tape_h, 3) if tape_h else None,
        })
    if not rows:
        return {"room": room, "wall": wall, "n_views": 0, "usable": False,
                "reason": "no plane recovered from any view"}

    vmed = float(np.median([r["vert_rel"] for r in rows]))
    for r in rows:
        r["vert_vs_median"] = round(r["vert_rel"] / vmed, 3)

    # A view is usable if it is not edge-on and its scale anchor agrees with the
    # rest of its wall's views.
    for r in rows:
        r["usable"] = (r["inc_deg"] <= USABLE_INC_DEG
                       and abs(r["vert_vs_median"] - 1.0) <= VERT_TOLERANCE)

    good = [r for r in rows if r["usable"]]
    rec = {
        "room": room,
        "wall": wall,
        "n_views": len(rows),
        "inc_min_deg": min(r["inc_deg"] for r in rows),
        "inc_median_deg": round(float(np.median([r["inc_deg"] for r in rows])), 1),
        "usable": bool(good),
        "n_usable_views": len(good),
    }
    if good:
        best = min(good, key=lambda x: x["inc_deg"])
        rec["best_view"] = best["photo"]
        rec["length_m"] = best["length_m"]
        rec["spread_m"] = round(max(r["length_m"] for r in good)
                                - min(r["length_m"] for r in good), 3)
    else:
        rec["length_m"] = None
        rec["reason"] = (
            f"no view within {USABLE_INC_DEG:.0f} deg "
            f"(best {rec['inc_min_deg']:.1f} deg)"
            if rec["inc_min_deg"] > USABLE_INC_DEG else
            "every view's vertical extent disagrees with the wall's median"
        )
    rec["views"] = rows
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", default=os.environ.get(
        "SCAN2PLAN_CAPTURE", r"C:\Users\adity\OneDrive\Desktop\Narendra\capture"))
    ap.add_argument("--depth-dir", default=os.path.join(ROOT, "runs", "depth"))
    ap.add_argument("--out", default=os.path.join(ROOT, "runs", "wall_consensus.json"))
    args = ap.parse_args()

    with open(TRUTH, encoding="utf-8") as fh:
        truth = json.load(fh)
    heights = {r["id"]: float(r["height_m"]) for r in truth["rooms"]}
    dims = {r["id"]: (float(r["length_m"]), float(r["width_m"]))
            for r in truth["rooms"] if "length_m" in r}

    items = [it for it in scan_capture(args.capture) if it.labelled and it.wall_index]
    groups = group_by_wall(items)
    if not groups:
        print(f"no labelled wall photos under {args.capture}")
        return 1

    walls = [measure_wall(room, wall, views, heights.get(room), args.depth_dir)
             for (room, wall), views in sorted(groups.items())]

    by_room: dict[str, list[dict]] = {}
    for w in walls:
        by_room.setdefault(w["room"], []).append(w)

    print(f"capture: {args.capture}")
    print(f"labelled wall photos: {len(items)}   wall groups: {len(walls)}\n")
    print("A room with four labelled walls is a four-sided room. That is a fact about")
    print("the capture, independent of the tape.\n")

    rooms_out = {}
    for room, ws in sorted(by_room.items()):
        n_walls = len(ws)
        usable = [w for w in ws if w["usable"]]
        tape = dims.get(room)
        print(f"=== {room}: {n_walls} labelled walls, "
              f"{len(usable)} with a usable view")
        if tape:
            print(f"    tape {tape[0]:.2f} x {tape[1]:.2f} m")
        for w in sorted(ws, key=lambda x: x["wall"]):
            if w["usable"]:
                print(f"    wall {w['wall']}: {w['length_m']:.3f} m "
                      f"from {w['best_view']} (inc {w['inc_min_deg']:.1f} deg, "
                      f"{w['n_usable_views']}/{w['n_views']} views, "
                      f"spread {w['spread_m']:.3f} m)")
            else:
                print(f"    wall {w['wall']}: not measurable -- {w['reason']}")
        verdict = ("all walls measurable" if len(usable) == n_walls else
                   f"{n_walls - len(usable)} of {n_walls} walls not measurable")
        print(f"    -> {verdict}\n")
        rooms_out[room] = {
            "labelled_walls": n_walls,
            "walls_measurable": len(usable),
            "tape_length_m": tape[0] if tape else None,
            "tape_width_m": tape[1] if tape else None,
            "walls": ws,
        }

    total = len(walls)
    ok = sum(1 for w in walls if w["usable"])
    print(f"total: {ok} of {total} labelled walls have a view usable for measurement")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"capture": args.capture,
                   "usable_incidence_deg": USABLE_INC_DEG,
                   "rooms": rooms_out}, fh, indent=2)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
