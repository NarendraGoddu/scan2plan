"""Measure how much wall surface a capture actually images.

This is the limit that governs damage detection, and it is worth measuring rather
than assuming. On the synthetic trajectory, the depth camera's vertical field of
view is about +-24 degrees and the trajectory aims at floor and ceiling targets in
order to accumulate those planes. The consequence, measured on the synthetic
`damaged` room:

  * the band of wall surface from roughly 0.0-0.1 m and 2.35-2.78 m is imaged
    (the two junctions),
  * the band from about 1.3 m to 1.6 m -- ordinary eye level, where cracks and
    spalls actually occur -- receives **zero** observations.

A damage detector cannot be evaluated on wall surface the capture never saw, and
cannot find damage there either. This script reports the observed fraction of each
wall so that claim is measured rather than asserted.

Reported per wall:
  height_range_m     the vertical extent of the wall
  observed_frac      fraction of cells with at least MIN_OBS observations
  observed_bands_m   contiguous vertical bands that are observed
  largest_gap_m      the tallest unobserved vertical gap

A wall that is 40% observed has 60% of its area where damage would be invisible,
and no threshold on any detector changes that.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.damage import RESIDUAL_BAND_M, _wall_frame, pixel_footprint_m  # noqa: E402
from scan2plan.ingest import load_zip, poses_to_matrices  # noqa: E402
from scan2plan.segment import segment_capture  # noqa: E402
from scan2plan.unproject import frame_points_world  # noqa: E402

SCALE = 0.001
MIN_OBS = 4


def wall_coverage(
    capture, scale: float, seg: dict, T_cw: np.ndarray, *, stride: int = 2
) -> list[dict]:
    """Vertical observation profile for every wall plane in `seg`."""
    up = np.asarray(seg["up"], dtype=np.float64)
    walls = [p for p in seg.get("planes", []) if getattr(p, "kind", "") == "wall"]
    if not walls:
        return []
    frames = list(range(len(T_cw)))[:: max(stride, 1)]

    out: list[dict] = []
    for wi, wall in enumerate(walls):
        normal = np.asarray(wall.normal, dtype=np.float64)
        offset = float(wall.offset)
        horiz, vert = _wall_frame(normal, up)
        vs: list[np.ndarray] = []
        for f in frames:
            try:
                pts = frame_points_world(capture, f, scale, T_cw, stride=4)
            except (IndexError, ValueError, ZeroDivisionError):
                continue
            if pts.size == 0:
                continue
            near = np.abs(pts @ normal - offset) <= RESIDUAL_BAND_M
            if not near.any():
                continue
            vs.append(pts[near] @ vert)
        if not vs:
            continue
        v = np.concatenate(vs)
        lo, hi = float(v.min()), float(v.max())
        if hi - lo < 1e-6:
            continue

        nbins = 120
        edges = np.linspace(lo, hi, nbins + 1)
        counts, _ = np.histogram(v, bins=edges)
        observed = counts >= MIN_OBS

        # Contiguous observed bands, reported in metres so they can be compared
        # against where damage occurs in a room.
        bands: list[list[float]] = []
        start = None
        for i, flag in enumerate(list(observed) + [False]):
            if flag and start is None:
                start = i
            elif not flag and start is not None:
                bands.append([round(float(edges[start]), 3), round(float(edges[i]), 3)])
                start = None

        gaps: list[float] = []
        prev = lo
        for b0, b1 in bands:
            if b0 - prev > 1e-6:
                gaps.append(b0 - prev)
            prev = max(prev, b1)
        if hi - prev > 1e-6:
            gaps.append(hi - prev)

        out.append(
            {
                "wall_index": wi,
                "offset_m": round(offset, 3),
                "height_range_m": [round(lo, 3), round(hi, 3)],
                "observed_frac": round(float(observed.mean()), 4),
                "observed_bands_m": bands,
                "largest_gap_m": round(max(gaps), 3) if gaps else 0.0,
                "n_points": int(v.size),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    loaded = load_zip(args.archive, frame_stride=args.stride)
    T_cw = poses_to_matrices(loaded.positions, loaded.quats)
    # point_stride=4, not 8. At stride 8 the wall planes are not recovered at all
    # on these archives and every coverage number would be zero for the wrong
    # reason, which is indistinguishable from a capture that saw no walls.
    seg = segment_capture(loaded, SCALE, frame_stride=1, point_stride=4)
    cov = wall_coverage(loaded, SCALE, seg, T_cw, stride=args.stride)

    fp = pixel_footprint_m(loaded, 0, 2.5)
    print(f"archive: {os.path.basename(args.archive)}  frames={loaded.n_frames}")
    print(f"depth pixel footprint at 2.5 m: {fp * 1000:.1f} mm")
    print(f"walls: {len(cov)}")
    for c in cov:
        bands = ", ".join(f"{a:.2f}-{b:.2f}" for a, b in c["observed_bands_m"]) or "none"
        print(
            f"  wall{c['wall_index']} offset {c['offset_m']:+.3f} m  "
            f"height {c['height_range_m'][0]:.2f}-{c['height_range_m'][1]:.2f} m  "
            f"observed {c['observed_frac'] * 100:5.1f}%  "
            f"largest_gap {c['largest_gap_m']:.2f} m"
        )
        print(f"           bands: {bands}")

    worst = max((c["largest_gap_m"] for c in cov), default=0.0)
    print(
        f"\n  largest unobserved vertical band on any wall: {worst:.2f} m. "
        "Damage inside such a band is undetectable by any method on this capture."
    )

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "archive": os.path.basename(args.archive),
                    "pixel_footprint_mm_at_2.5m": round(fp * 1000, 2),
                    "min_obs": MIN_OBS,
                    "largest_gap_m": round(worst, 3),
                    "walls": cov,
                },
                fh,
                indent=2,
            )
        print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
