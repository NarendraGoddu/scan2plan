"""Openings and damage from the labelled photos.

Two deliverables that the filename labels make possible and that the blind pass
could not attempt.

Openings. `m_door`, `2_room_door`, `main_door` and `hall_side_*` photographs
contain doorways, and the tape recorded every door's width and height. A door is
also the best scale reference in the building: doors are 0.80 x 2.00 m and
1.03 x 2.09 m across the three rooms, a spread of 9 cm on a dimension everyone
builds to a standard. So door dimensions are estimated the same way walls are --
relative extents from the depth map, scaled by the room height -- and reported
against the tape. This is the measurement most likely to be worth having, because
openings are what a floor plan is actually used for and they are the part CP5 had
not implemented at all.

Damage. Exactly one crack was recorded, in the second bedroom, named
`wall_1_crack (1..2)`. A crack is a depth discontinuity, not a colour change, so
it is detected in the depth maps and not in the photographs: local depth
roughness on the wall surface, compared against the same wall's ordinary
photographs. The comparison is what makes it evidence rather than anecdote -- a
rough wall in general would not be interesting, a wall that is rougher in the two
frames named `crack` than in the five named `wall_1` is.

Honesty note carried into the output: with two crack frames in a whole
three-bedroom capture, this demonstrates the detection path and nothing about
damage rates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.capture_manifest import scan_capture  # noqa: E402
from scan2plan.depth_geometry import (  # noqa: E402
    anchor_scale,
    unproject_depth,
)

NOMINAL_MEDIAN_M = 3.0
CLIP = (2.0, 95.0)
MAX_FACTOR = 4.0

# Opening measurement lives in scan2plan.openings so it can be tested against
# synthetic frames with a known answer; this script is only the driver over the
# 2026-10-04 capture.
from scan2plan.openings import (  # noqa: E402,F401
    COPY_DISAGREEMENT_M,
    DOOR_MIN_HEIGHT_FRAC,
    OPENING_DEPTH_MARGIN_M,
    WALL_BORDER_FRAC,
    consensus,
    measure_opening,
    wall_depth_m,
)
from scan2plan.openings import to_metres as opening_metres  # noqa: E402

# Depth roughness thresholds, in metres of local deviation from a planar fit.
ROUGHNESS_SIGMA_FACTOR = 3.0
ROUGHNESS_FLOOR_M = 0.02



def load_depth(path: str) -> np.ndarray:
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise FileNotFoundError(path)
    if raw.dtype == np.uint16:
        return raw.astype(np.float32) / 65535.0
    return raw.astype(np.float32)


def depth_path(capture_dir: str, depth_dir: str, item) -> str:
    rel = os.path.join(item.folder, item.filename)
    stem = os.path.splitext(rel)[0]
    return os.path.join(depth_dir, stem.replace(os.sep, "__") + ".png")


def to_metres(depth_rel: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(depth_rel, CLIP)
    return np.minimum(
        anchor_scale(np.clip(depth_rel, lo, hi), NOMINAL_MEDIAN_M),
        MAX_FACTOR * NOMINAL_MEDIAN_M,
    )


def local_roughness(depth_m: np.ndarray, ksize: int = 9) -> np.ndarray:
    """Local standard deviation of depth: a surface-relief measure.

    Computed as sqrt(E[x^2] - E[x]^2) with box filters rather than as deviation
    from a blurred copy, because a mean filter partly absorbs the feature it is
    supposed to measure and a thin crack is exactly the case where that matters.
    `cv2.medianBlur` would be the natural choice but rejects float32 in OpenCV 5.
    """
    x = depth_m.astype(np.float32)
    k = (ksize, ksize)
    mean = cv2.boxFilter(x, -1, k)
    mean_sq = cv2.boxFilter(x * x, -1, k)
    var = np.maximum(mean_sq - mean * mean, 0.0)
    return np.sqrt(var)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", default=os.path.join(ROOT, os.pardir, "capture"))
    ap.add_argument("--depth-dir", default=os.path.join(ROOT, "runs", "depth"))
    ap.add_argument("--truth", default=os.path.join(ROOT, "data", "field_ground_truth.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "runs"))
    args = ap.parse_args()

    capture_dir = os.path.abspath(args.capture)
    items = scan_capture(capture_dir)
    with open(args.truth, encoding="utf-8") as fh:
        truth = json.load(fh)
    truth_by_id = {r["id"]: r for r in truth["rooms"]}

    # ---------------- openings ----------------
    door_items = [i for i in items if i.surface == "door" and i.room_id in truth_by_id]
    print(f"=== openings: {len(door_items)} door photographs ===")
    openings = []
    summary: dict[str, dict] = {}
    for it in sorted(door_items, key=lambda x: x.path):
        dp = depth_path(capture_dir, args.depth_dir, it)
        if not os.path.isfile(dp):
            continue
        dm = to_metres(load_depth(dp))
        m = measure_opening(dm)
        if m is None:
            print(f"  {it.filename:<28} no doorway region found")
            continue
        h_tape = truth_by_id[it.room_id]["height_m"]
        m["room_id"] = it.room_id
        m["source"] = it.filename
        m["width_est_m"], m["height_est_m"] = opening_metres(m, h_tape)
        openings.append(m)
        print(f"  {it.filename:<28} width {m['width_est_m']:5.2f} m x"
              f" height {m['height_est_m']:5.2f} m   (wall {m['wall_depth_m']:.2f} m,"
              f" room height {h_tape} m)")

    for it_room, t in truth_by_id.items():
        mine = [o for o in openings if o["room_id"] == it_room]
        if not mine:
            continue
        doors = [d for d in t["openings"] if d["kind"] == "door"]
        if not doors:
            continue
        d = doors[0]
        # Consensus across the photographs of this door, WITHOUT consulting the
        # tape. An earlier version used `min(..., key=abs(estimate - tape))`,
        # which meant the reported number was partly chosen by how close it
        # happened to land to truth -- selecting on validation data.
        s = consensus(mine)
        summary[it_room] = s
        flag = "" if s["copies_agree"] else (
            f"   COPIES DISAGREE by "
            f"{max(s['width_spread_m'], s['height_spread_m']):.2f} m"
        )
        print(f"  {it_room:<16} tape door {d['width_m']} x {d['height_m']} m"
              f"   median of {s['n_photos']}:"
              f" {s['width_est_m']:.3f} x {s['height_est_m']:.3f} m"
              f"   (spread {s['width_spread_m']:.2f}/{s['height_spread_m']:.2f} m){flag}")

    # ---------------- damage ----------------
    print("\n=== damage: depth roughness on the cracked wall ===")
    dmg = [i for i in items if i.is_damage]
    dmg_rooms = {i.room_id for i in dmg}
    report = {"openings": openings, "openings_by_room": summary, "damage": []}
    for room in sorted(dmg_rooms):
        crack = [i for i in dmg if i.room_id == room]
        # Same wall, ordinary photographs: the control group.
        ctrl = [
            i for i in items
            if i.room_id == room and i.surface == "wall" and i.wall_index == 1
        ]
        if not ctrl:
            continue

        def roughness_stats(subset):
            vals = []
            for it in subset:
                dp = depth_path(capture_dir, args.depth_dir, it)
                if not os.path.isfile(dp):
                    continue
                dm = to_metres(load_depth(dp))
                r = local_roughness(dm)
                vals.append({
                    "file": it.filename,
                    "sigma_m": round(float(np.std(r)), 5),
                    "frac_rough": round(float(np.mean(r > max(ROUGHNESS_FLOOR_M,
                                                             ROUGHNESS_SIGMA_FACTOR * np.std(r)))), 5),
                    "max_r_m": round(float(np.max(r)), 4),
                })
            return vals

        c = roughness_stats(crack)
        k = roughness_stats(ctrl)
        if not c or not k:
            continue
        c_sig = float(np.median([x["sigma_m"] for x in c]))
        k_sig = float(np.median([x["sigma_m"] for x in k]))
        ratio = c_sig / k_sig if k_sig > 1e-9 else float("nan")
        entry = {
            "room_id": room,
            "wall_index": 1,
            "crack_photos": c,
            "control_photos": k,
            "crack_sigma_m": c_sig,
            "control_sigma_m": k_sig,
            "roughness_ratio": round(ratio, 3),
            "verdict": (
                "crack frames measurably rougher than the same wall's normal frames"
                if ratio > 1.15 else
                "no measurable roughness difference; the crack is below this method's "
                "resolution with only two frames"
            ),
        }
        report["damage"].append(entry)
        print(f"  {room} wall {entry['wall_index']}: crack sigma {c_sig * 1000:.1f} mm"
              f" vs control {k_sig * 1000:.1f} mm   ratio {ratio:.2f}")
        print(f"    {entry['verdict']}")

    out_path = os.path.join(args.out, "openings_and_damage.json")
    os.makedirs(args.out, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
