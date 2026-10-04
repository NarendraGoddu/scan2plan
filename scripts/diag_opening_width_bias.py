"""Why are opening widths biased +96 to +119 mm?

Hypothesis (H_reveal, currently documented): the mask includes the door reveal, so
the opening reads wide.

Hypothesis (H_threshold): the reveal is a few centimetres deep and the mask selects
everything more than `OPENING_DEPTH_MARGIN_M = 1.0 m` *behind* the wall, so a 1.0 m
margin strictly excludes the reveal and it cannot be the cause. The real cause would be
that monocular depth is spatially smooth: a hard threshold on a gradual ramp cuts past
the true aperture edge, inflating the bounding box.

These predict opposite responses to raising the margin:
  H_reveal     -> little change; the reveal is excluded either way.
  H_threshold  -> width shrinks monotonically with the margin, with an optimum.

Sweeping the margin on the three doors that have tape truth decides it. This only
monkeypatches a module constant to run the experiment; it changes no committed default.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

import analyse_openings_and_damage as A  # noqa: E402
from scan2plan import openings as O  # noqa: E402
from scan2plan.capture_manifest import scan_capture  # noqa: E402

CAPTURE = os.path.abspath(os.path.join(ROOT, os.pardir, "capture"))
DEPTH = os.path.join(ROOT, "runs", "depth")
TRUTH = os.path.join(ROOT, "data", "field_ground_truth.json")

MARGINS = [0.2, 0.4, 0.6, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5]


def main() -> None:
    with open(TRUTH, encoding="utf-8") as fh:
        truth = json.load(fh)
    truth_by_id = {r["id"]: r for r in truth["rooms"]}
    items = scan_capture(CAPTURE)
    doors = [i for i in items if i.surface == "door" and i.room_id in truth_by_id]

    print(f"{len(doors)} door photographs with tape truth\n")
    header = "  ".join(f"m={m:4.1f}" for m in MARGINS)
    print(f"  {'photo / tape width':<34}{header}")

    for it in sorted(doors, key=lambda x: x.path):
        dp = A.depth_path(CAPTURE, DEPTH, it)
        if not os.path.isfile(dp):
            continue
        dm = A.to_metres(A.load_depth(dp))
        h_tape = truth_by_id[it.room_id]["height_m"]
        door_tape = [d for d in truth_by_id[it.room_id]["openings"] if d["kind"] == "door"]
        w_tape = door_tape[0]["width_m"] if door_tape else float("nan")
        row = []
        for m in MARGINS:
            O.OPENING_DEPTH_MARGIN_M = m
            meas = O.measure_opening(dm)
            if meas is None:
                row.append("  --  ")
                continue
            w, _h = O.to_metres(meas, h_tape)
            row.append(f"{w:6.3f}")
        print(f"  {it.filename[:26]:<26} tape {w_tape:4.2f}  " + "  ".join(row))

    O.OPENING_DEPTH_MARGIN_M = 1.0
    print("\n  (values are estimated door width in metres; tape is the truth)")

    # --- is the excess symmetric, or only on the obliquely-viewed side? -------
    #
    # Monocular depth hallucinates a plausible continuation of the room beyond an
    # aperture onto the surrounding wall. If that is what inflates the mask, the
    # excess should be roughly symmetric for a head-on shot and lopsided for an
    # oblique one, because the obliquity is what exposes the side wall.
    #
    # Hand-set width at 0.80 m on a wall 2.0 m deep, rendered with a plausible jamb, so
    # the only free variable is how far off-axis the camera stands.
    print("\n  synthetic aperture, true width 0.80 m, camera yaw by angle:")
    print(f"  {'yaw':>6}  {'est width':>10}  {'excess':>8}  {'left/right split':>18}")
    for yaw_deg in (0, 5, 10, 20, 30):
        yaw = np.radians(yaw_deg)
        # Camera on a circle of radius 2.0 m about the wall centre at -x.
        cam = np.array([[2.0 * np.sin(yaw), 1.50, -2.0 * np.cos(yaw)]])
        f, cx, cy = 300.0, 160.0, 120.0
        xs = np.linspace(-1.6, 1.6, 321)
        ys = np.linspace(-1.3, 1.3, 261)
        gx, gy = np.meshgrid(xs, ys)
        d = np.sqrt((gx - cam[0, 0]) ** 2 + (gy - cam[0, 1]) ** 2 + cam[0, 2] ** 2)
        u = f * gx / d + cx
        v = -f * gy / d + cy
        in_ap = (np.abs(gx) <= 0.40) & (np.abs(gy) <= 1.00)
        depth = np.where(in_ap, 4.0, 2.0).astype(np.float32)
        u8 = np.clip(u, 0, 319).astype(np.uint8)
        v8 = np.clip(v, 0, 239).astype(np.uint8)
        img = depth[v8, u8]
        meas = O.measure_opening(img)
        if meas is None:
            print(f"  {yaw_deg:5d}       no opening found")
            continue
        est_w, _h = O.to_metres(meas, 2.50)
        # Which side of the mask carries the excess? The camera is at +x yaw, so the
        # visible jamb is the one the view grazes.
        x, y, bw, bh = meas["bbox_px"]
        print(f"  {yaw_deg:5d}       {est_w:8.3f}   {est_w - 0.80:+7.3f}"
              f"   bbox x={x} w={bw} (frame 320)")


if __name__ == "__main__":
    main()
