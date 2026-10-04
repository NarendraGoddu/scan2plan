"""Verify and analyse every depth map in a capture.

Two jobs, deliberately in one pass because they share the point cloud.

Verification. Before any of this is trusted, the predictions have to be shown to
contain real geometry rather than plausible-looking texture. Three checks:

  * planar structure -- a real room yields a few large, low-residual planes under
    RANSAC. Blurred or arbitrary output does not.
  * vertical gradient -- in any room photo the floor near the bottom of the frame
    is closer than the ceiling at the top, so median depth must *increase*
    upward. A capture where this fails is inverted or the model is wrong.
  * scale consistency -- the ratio between the near and far percentiles should sit
    in a narrow band across images of the same room, because a room has one size.

Geometry. Per image: which surfaces are planar, which are walls, and how big they
are relative to each other. Absolute size needs an external anchor and is not
invented here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.depth_geometry import (  # noqa: E402
    anchor_scale,
    classify_depth_planes,
    estimate_vertical,
    extract_planes,
    summarise_planes,
    unproject_depth,
)

# Horizontal field of view of a typical phone main camera. No EXIF survives in this
# capture, so the focal length has to be assumed; this is the assumption, stated
# in one place so it can be varied and its effect measured rather than hidden.
ASSUMED_HFOV_DEG = 69.0

# Nominal median scene distance used only to put the RANSAC inlier threshold on a
# human scale. It cancels out of every ratio reported below.
NOMINAL_MEDIAN_M = 3.0

UNPROJECT_STRIDE = 6
INLIER_M = 0.12

# Depth Anything's far tail is heavy: sky-like and grazing-angle pixels come back
# with wildly wrong values. Left in, they unproject to points tens of metres away
# and stretch every plane's reported extent -- a bedroom wall measured 67 m long.
# Clipping to these percentiles before unprojecting keeps the cloud inside the
# room, which is the only reason extents are comparable to room dimensions.
CLIP_LO, CLIP_HI = 2.0, 95.0
DEPTH_MAX_FACTOR = 4.0


def focal_from_hfov(width_px: int, hfov_deg: float = ASSUMED_HFOV_DEG) -> float:
    return 0.5 * width_px / np.tan(np.radians(hfov_deg) / 2.0)


def load_depth(path: str) -> np.ndarray:
    """Read a 16-bit depth PNG back into its normalised float range."""
    raw = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise FileNotFoundError(path)
    if raw.dtype == np.uint16:
        return raw.astype(np.float32) / 65535.0
    return raw.astype(np.float32)


def vertical_gradient(depth_m: np.ndarray, band: float = 0.15) -> float:
    """Median(depth in top band) - median(depth in bottom band), in metres.

    Positive means nearer at the bottom, which is what a floor-level camera sees.
    """
    h = depth_m.shape[0]
    k = max(1, int(h * band))
    return float(np.median(depth_m[:k]) - np.median(depth_m[-k:]))


def analyse_one(depth_rel: np.ndarray, seed: int = 0):
    """Unproject, fit planes, and return the geometry summary for one depth map."""
    lo, hi = np.percentile(depth_rel, [CLIP_LO, CLIP_HI])
    depth_rel = np.clip(depth_rel, lo, hi)
    depth_m = anchor_scale(depth_rel, NOMINAL_MEDIAN_M)
    # Hard cap as well: a heavy far tail survives even percentile clipping, and one
    # point 20 m out stretches a plane's reported extent enough to make a bedroom
    # wall look 40 m long.
    depth_m = np.minimum(depth_m, DEPTH_MAX_FACTOR * NOMINAL_MEDIAN_M)
    focal = focal_from_hfov(depth_m.shape[1])
    pts, uv = unproject_depth(depth_m, focal, stride=UNPROJECT_STRIDE)
    planes = extract_planes(
        pts, inlier_m=INLIER_M, rng=np.random.default_rng(seed), uv=uv
    )
    # Recover "up" from the floor rather than assuming the camera is level.
    up, up_src = estimate_vertical(planes, pts)
    planes = classify_depth_planes(planes, up)
    planes.sort(key=lambda p: -p.support)
    total = len(pts)
    return {
        "summary": summarise_planes(planes),
        "n_points": total,
        "best_plane_fraction": round(float(planes[0].support / total), 4) if planes else 0.0,
        "vertical_gradient_m": round(vertical_gradient(depth_m), 4),
        "near_far_ratio": round(
            float(np.percentile(depth_m, 90) / max(np.percentile(depth_m, 5), 1e-6)), 3
        ),
        "up_source": up_src,
        "up": [round(float(c), 4) for c in up],
    }, planes


def wall_normal_consistency(records: list[dict], tol_deg: float = 20.0) -> dict:
    """How concentrated are recovered wall normals within one room?

    This is the verification check that survives tilted, hand-held photographs.
    Individual depth maps are noisy, but if the underlying geometry is real then
    the *walls of one room* seen from many different viewpoints must agree on a
    small number of orientations. Random or per-image scale/shift errors do not
    cluster. The result is the fraction of wall-normal mass landing in the few
    largest orientation clusters.
    """
    normals = []
    for r in records:
        for p in r["summary"]["planes"]:
            if p["kind"] != "wall":
                continue
            n = np.asarray(p["normal"], dtype=np.float64)
            if abs(float(n[1])) > 0.94:  # near the camera's own up: not a wall
                continue
            normals.append((n, float(p["support"])))
    if not normals:
        return {"n_wall_planes": 0, "clusters": 0, "concentration": None}
    cos_tol = float(np.cos(np.radians(tol_deg)))
    clusters: list[list] = []
    for n, w in normals:
        for c in clusters:
            if abs(float(c[0] @ n)) >= cos_tol:
                c[1] += w
                break
        else:
            clusters.append([n, w])
    total = sum(w for _n, w in normals)
    top = sorted((w for _n, w in clusters), reverse=True)[:4]
    return {
        "n_wall_planes": len(normals),
        "clusters": len(clusters),
        "top4_cluster_weight_frac": round(float(sum(top) / max(total, 1e-9)), 4),
        "top_cluster_weight_frac": round(float(top[0] / max(total, 1e-9)), 4),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--depth-dir", default=os.path.join(ROOT, "runs", "depth"))
    ap.add_argument("--out", default=os.path.join(ROOT, "runs"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.depth_dir) if f.lower().endswith(".png"))
    if args.limit:
        files = files[: args.limit]
    os.makedirs(args.out, exist_ok=True)
    print(f"{len(files)} depth maps in {args.depth_dir}")

    # Group by the capture folder encoded in the filename stem, which is the
    # folder name the media came from with separators flattened.
    rooms: dict[str, list] = {}
    records = []
    t0 = time.time()
    for i, name in enumerate(files, 1):
        stem = os.path.splitext(name)[0]
        room = stem.split("__")[0]
        try:
            depth_rel = load_depth(os.path.join(args.depth_dir, name))
        except FileNotFoundError:
            print(f"  missing {name}")
            continue
        res, planes = analyse_one(depth_rel)
        rec = {"file": name, "room": room, **res}
        records.append(rec)
        rooms.setdefault(room, []).append(rec)
        if i % 20 == 0:
            print(f"  {i}/{len(files)}  {time.time() - t0:.0f}s")

    def agg(rs, key, fn=np.mean):
        vals = [r[key] for r in rs if r[key] is not None]
        return round(float(fn(vals)), 4) if vals else None

    summary = {}
    for room, rs in sorted(rooms.items()):
        grads = [r["vertical_gradient_m"] for r in rs]
        fracs = [r["best_plane_fraction"] for r in rs]
        walls = [r["summary"]["n_walls"] for r in rs]
        summary[room] = {
            "n_images": len(rs),
            "vertical_gradient_mean_m": round(float(np.mean(grads)), 4),
            "vertical_gradient_positive_frac": round(float(np.mean(np.array(grads) > 0)), 4),
            "best_plane_fraction_mean": round(float(np.mean(fracs)), 4),
            "images_with_walls": int(sum(1 for w in walls if w > 0)),
            "median_walls_per_image": float(np.median(walls)) if walls else 0.0,
            "median_near_far_ratio": round(
                float(np.median([r["near_far_ratio"] for r in rs])), 3
            ),
            "consistency": wall_normal_consistency(rs),
        }

    out = os.path.join(args.out, "depth_geometry.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "assumed_hfov_deg": ASSUMED_HFOV_DEG,
                "nominal_median_m": NOMINAL_MEDIAN_M,
                "inlier_m": INLIER_M,
                "note": (
                    "Geometry is scale-free: nominal_median_m only fixes the RANSAC "
                    "threshold scale and cancels out of all ratios. Absolute sizes "
                    "require an external anchor."
                ),
                "rooms": summary,
                "records": records,
            },
            fh,
            indent=2,
        )

    print("\n=== per-room verification ===")
    print(f"{'room':<22}{'imgs':>5}{'planefrac':>11}{'walls':>8}{'n/f':>7}{'wcons':>8}{'top4':>7}")
    for room, s in summary.items():
        c = s["consistency"]
        t4 = c.get("top4_cluster_weight_frac")
        print(
            f"{room:<22}{s['n_images']:>5}{s['best_plane_fraction_mean']:>11.3f}"
            f"{s['median_walls_per_image']:>8.1f}{s['median_near_far_ratio']:>7.2f}"
            f"{c.get('n_wall_planes', 0):>8}"
            f"{(f'{t4:.3f}' if t4 is not None else 'n/a'):>7}"
        )
    print(f"\ntotal {time.time() - t0:.0f}s -> {out}")


if __name__ == "__main__":
    main()
