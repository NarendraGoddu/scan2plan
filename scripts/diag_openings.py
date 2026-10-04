"""Why does measure_opening return the whole frame?

One hypothesis, one falsification test. No production code is changed here.

H1 (REFUTED): the near-region mask selects the FLOOR, not the doorway.
  Predicted the bbox to touch the bottom edge with the near mask concentrated in
  the lower half. Observed the opposite: 3 of 5 bboxes touch top AND bottom, and
  the near fraction is *higher* in the upper half for 4 of 5 photos.

H2 (being tested): the mask polarity is inverted. A doorway seen from inside a
  room is a region of GREATER depth than the wall around it -- you are looking
  through it into the next space. The measured band means bear this out: bottom
  2.3-3.6 m, upper-mid 6.3-6.7 m, so the middle of the frame is the farthest thing
  in it. Searching for the NEAREST 55% therefore selects the wall, which surrounds
  and connects around the opening, so the largest component is the whole frame with
  a doorway-shaped hole in it.

  Falsifiable prediction if H2 holds: masking depth ABOVE a wall-depth estimate
  will yield one tall narrow component mid-frame, with aspect near
  0.8/2.0 = 0.4 and height_frac well below 1.0.

Wall depth is estimated from the image BORDER, which is the wall the camera faces,
rather than from a global percentile, so that the estimate cannot be dragged by the
opening itself.
"""

from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analyse_openings_and_damage import (  # noqa: E402
    DOOR_MIN_HEIGHT_FRAC,
    depth_path,
    load_depth,
    measure_opening,
    to_metres,
)
from scan2plan.capture_manifest import scan_capture  # noqa: E402

TRUTH = {"master_bedroom": 2.57, "second_bedroom": 2.57, "main_hall": 2.48}
BORDER = 0.08  # fraction of width/height treated as the surrounding wall


def far_opening_candidate(dm: np.ndarray, margin_m: float = 0.0) -> dict:
    """Test H2: find the opening as a region FARTHER than the surrounding wall."""
    return _pick(dm, margin_m, require_enclosed=False)


def enclosed_opening(dm: np.ndarray, drop_floor: bool = True) -> dict:
    """Test H3: the opening is the far region that does NOT touch the border.

    Parameter-free, which matters: the margin in `far_opening_candidate` was found
    by looking at tape, and tuning a constant on validation data is exactly what
    this project refuses to do elsewhere.

    `drop_floor` excludes the bottom band from the wall-depth estimate, because the
    bottom border is the floor a few feet from the lens, not the wall being
    measured, and including it drags the median down (main_door (2) estimated its
    own wall at 1.27 m where every other photo saw 2.7 m).
    """
    return _pick(dm, 0.0, require_enclosed=True, drop_floor=drop_floor)


def _wall_depth(dm: np.ndarray, drop_floor: bool) -> float:
    h, w = dm.shape[:2]
    by, bx = max(1, int(h * BORDER)), max(1, int(w * BORDER))
    bands = [dm[:by].ravel(), dm[:, :bx].ravel(), dm[:, -bx:].ravel()]
    if not drop_floor:
        bands.append(dm[-by:].ravel())
    return float(np.median(np.concatenate(bands)))


def _pick(dm, margin_m, require_enclosed, drop_floor=True) -> dict:
    h, w = dm.shape[:2]
    wall = _wall_depth(dm, drop_floor)
    far = (dm > wall + margin_m).astype(np.uint8)
    far = cv2.morphologyEx(far, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _c = cv2.connectedComponentsWithStats(far, connectivity=8)
    best, best_area = None, 0
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if ch <= DOOR_MIN_HEIGHT_FRAC * h:
            continue
        if require_enclosed:
            # reject anything reaching the frame edge: that is wall, or floor
            if x <= 1 or y <= 1 or (x + cw) >= (w - 1) or (y + ch) >= (h - 1):
                continue
            if area < 0.5 * cw * ch:  # a real opening is solid, not a sliver
                continue
        if area > best_area:
            best, best_area = (int(x), int(y), int(cw), int(ch), int(area)), area
    if best is None:
        return {"wall_depth_m": round(wall, 3), "found": False}
    x, y, cw, ch, area = best
    return {
        "wall_depth_m": round(wall, 3),
        "found": True,
        "bbox_px": [x, y, cw, ch],
        "height_frac_of_frame": round(ch / h, 4),
        "aspect_w_over_h": round(cw / max(ch, 1), 4),
        "area_frac": round(area / (h * w), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", default=os.path.join(ROOT, os.pardir, "capture"))
    ap.add_argument("--depth-dir", default=os.path.join(ROOT, "runs", "depth"))
    args = ap.parse_args()

    capture_dir = os.path.abspath(args.capture)
    items = scan_capture(capture_dir)
    doors = [i for i in items if i.surface == "door"]

    print(f"{len(doors)} door photographs\n")
    for it in sorted(doors, key=lambda x: x.path):
        dp = depth_path(capture_dir, args.depth_dir, it)
        if not os.path.isfile(dp):
            print(f"{it.filename}: no depth map")
            continue
        dm = to_metres(load_depth(dp))
        h, w = dm.shape[:2]
        m = measure_opening(dm)

        thr = np.percentile(dm, 55)
        near = (dm <= thr).astype(np.uint8)
        near = cv2.morphologyEx(near, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        rowfrac = near.mean(axis=1)
        upper = float(rowfrac[: h // 2].mean())
        lower = float(rowfrac[h // 2 :].mean())

        print(f"--- {it.filename}  ({w}x{h})")
        print(f"    depth range {dm.min():.2f} .. {dm.max():.2f} m, "
              f"median {np.median(dm):.2f}, p55 {thr:.2f}")
        if m is None:
            print("    measure_opening -> None")
            continue
        x, y, bw, bh = m["bbox_px"]
        touches_bottom = (y + bh) >= h - 2
        touches_top = y <= 2
        print(f"    bbox {m['bbox_px']}  height_frac {m['height_frac_of_frame']:.3f} "
              f"aspect {m['aspect_w_over_h']:.3f}")
        print(f"    touches bottom: {touches_bottom}   touches top: {touches_top}")
        print(f"    near fraction upper half {upper:.3f} vs lower half {lower:.3f} "
              f"(ratio {lower / max(upper, 1e-9):.2f})")
        print("    mean depth by band: "
              + "  ".join(
                  f"{lab} {dm[a:b].mean():.2f}m"
                  for lab, a, b in (
                      ("top", 0, h // 4),
                      ("upper-mid", h // 4, h // 2),
                      ("lower-mid", h // 2, 3 * h // 4),
                      ("bottom", 3 * h // 4, h),
                  )
              ))
        est_h = m["height_frac_of_frame"] * TRUTH.get(it.room_id, 0)
        print(f"    => height {est_h:.2f} m of a {TRUTH.get(it.room_id, 0):.2f} m room; "
              f"DOOR_MIN_HEIGHT_FRAC={DOOR_MIN_HEIGHT_FRAC}")

        # H2: the opening should be a region FARTHER than the surrounding wall.
        for margin in (0.0, 0.5, 1.0):
            r = far_opening_candidate(dm, margin)
            if not r["found"]:
                print(f"    H2 margin {margin:>4.1f} m: wall {r['wall_depth_m']:.2f} m, "
                      f"no tall far region")
                continue
            fh = r["height_frac_of_frame"] * TRUTH.get(it.room_id, 0)
            fw = r["aspect_w_over_h"] * fh
            print(f"    H2 margin {margin:>4.1f} m: wall {r['wall_depth_m']:.2f} m  "
                  f"bbox {r['bbox_px']}  -> {fw:.2f} x {fh:.2f} m  "
                  f"(aspect {r['aspect_w_over_h']:.2f})")

        # H3: parameter-free -- the opening is the far region that is fully enclosed.
        for drop in (True, False):
            r = enclosed_opening(dm, drop_floor=drop)
            tag = "floor dropped" if drop else "all borders "
            if not r["found"]:
                print(f"    H3 {tag}: wall {r['wall_depth_m']:.2f} m, no enclosed far region")
                continue
            fh = r["height_frac_of_frame"] * TRUTH.get(it.room_id, 0)
            fw = r["aspect_w_over_h"] * fh
            print(f"    H3 {tag}: wall {r['wall_depth_m']:.2f} m  "
                  f"bbox {r['bbox_px']}  -> {fw:.2f} x {fh:.2f} m  "
                  f"(aspect {r['aspect_w_over_h']:.2f})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
