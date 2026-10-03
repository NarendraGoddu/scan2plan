"""One command from a capture archive to a dimensioned floor plan.

    python scripts/plan_room.py "C:/path/to/scan.zip" --out out/

Writes `<scan_id>.json` (the result document) and `<scan_id>.svg` (a dimensioned
plan view), and prints a short summary. Exits non-zero if no room could be
recovered, so it is usable as a build step rather than only by hand.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np

from scan2plan.ingest import load_zip
from scan2plan.plan import build_room_plan
from scan2plan.report import build_report
from scan2plan.segment import segment_capture
from scan2plan.unproject import calibrate_scale

DEPTH_SCALE_M = 0.001


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("archive", help="path to a capture .zip")
    ap.add_argument("--out", default="out", help="output directory (default: out)")
    ap.add_argument("--load-stride", type=int, default=1,
                    help="frame stride applied when loading (default: 1)")
    ap.add_argument("--point-stride", type=int, default=4,
                    help="pixel subsample within each frame (default: 4)")
    ap.add_argument("--frame-stride", type=int, default=1,
                    help="frame stride applied during segmentation (default: 1)")
    ap.add_argument("--max-frames", type=int, default=None, help="stop after N loaded frames")
    ap.add_argument("--scale", type=float, default=DEPTH_SCALE_M,
                    help="metres per raw depth unit (default: 0.001)")
    ap.add_argument("--calibrate-scale", action="store_true",
                    help="search for a depth-scale factor instead of trusting the default")
    ap.add_argument("--json-only", action="store_true", help="skip the SVG")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.archive):
        print(f"error: no such archive: {args.archive}", file=sys.stderr)
        return 2

    t0 = time.time()
    print(f"[1/3] loading {os.path.basename(args.archive)}")
    cap = load_zip(args.archive, frame_stride=args.load_stride, max_frames=args.max_frames)
    print(f"      {cap.depth.shape[0]} frames, depth {cap.depth.shape[1]}x{cap.depth.shape[2]}, "
          f"scale {args.scale} m/unit")

    scale = float(args.scale)
    if args.calibrate_scale:
        print("[2/3] calibrating depth scale")
        cal = calibrate_scale(cap)
        scale = float(cal.get("scale_m", scale))
        print(f"      best factor {cal.get('factor', 1.0):.6f} -> {scale:.9f} m/unit "
              f"({cal.get('reliable')})")

    print("[3/3] segmenting and solving the room")
    seg = segment_capture(cap, scale, frame_stride=args.frame_stride,
                          point_stride=args.point_stride)
    plan = build_room_plan(seg["planes"], seg["up"], cap.positions)

    rep = build_report(
        plan,
        capture_path=os.path.abspath(args.archive),
        scan_id=cap.scan_id or os.path.splitext(os.path.basename(args.archive))[0],
        n_frames_total=cap.depth.shape[0],
        n_frames_used=int(seg["n_frames_used"]),
        depth_hw=(cap.depth.shape[1], cap.depth.shape[2]),
        depth_scale_m_per_unit=scale,
        sampling={"load_stride": args.load_stride, "frame_stride": args.frame_stride,
                  "point_stride": args.point_stride},
        planes=seg["planes"],
        elapsed_s=time.time() - t0,
    )

    os.makedirs(args.out, exist_ok=True)
    base = rep.document["input"]["scan_id"]
    json_path = os.path.join(args.out, f"{base}.json")
    rep.write(json_path)
    print(f"      wrote {json_path}")

    if not args.json_only:
        svg_path = os.path.join(args.out, f"{base}.svg")
        rep.write_svg(svg_path)
        print(f"      wrote {svg_path}")

    _summarise(rep)
    return 0 if len(plan.vertices_ab) >= 3 else 1


def _summarise(rep) -> None:
    q = rep.document["quality"]
    u = rep.document["uncertainty"]
    print("\n--- result ---")
    print(f"  walls          {q['n_walls']}   vertices {q['n_vertices']}")
    print(f"  floor area     {q['floor_area_m2']:.3f} m2"
          + (f"  +/- {u['floor_area_1sigma_pct']:.2f}% (1 sigma)" if u.get("floor_area_1sigma_pct") else ""))
    print(f"  perimeter      {q['perimeter_m']:.3f} m")
    if len(q["edge_lengths_m"]) == 4:
        sides = sorted(q["edge_lengths_m"])
        print(f"  spans          {sides[0]:.3f} m x {sides[-1]:.3f} m")
    ch = q.get("ceiling_height_m")
    if ch is not None:
        sig = u.get("ceiling_height_1sigma_m")
        print(f"  ceiling height {ch:.3f} m"
              + (f"  +/- {sig * 1000:.0f} mm (1 sigma)" if sig is not None else ""))
    else:
        print("  ceiling height not recovered (no ceiling plane found)")
    wmax = u.get("wall_position_1sigma_max_m")
    if wmax is not None:
        print(f"  wall position  +/- {wmax * 1000:.0f} mm worst case (1 sigma)")
    for note in q["notes"]:
        print(f"  note: {note}")
    print("\n  known gaps carried in the document:")
    for lim in rep.document["limitations"][:3]:
        print(f"    - {lim.split('.')[0]}.")


if __name__ == "__main__":
    raise SystemExit(main())