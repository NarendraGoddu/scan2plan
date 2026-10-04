"""Do the real archives contain duplicate frames, and does that inflate walls?

Hypothesis under test: the real archives contain duplicate or near-duplicate
frames, so the plane observations are dominated by a few viewpoints. That would
mean `select_room_boundary`'s camera-path extent is computed from a path that is
really much shorter than it looks, and walls the operator never actually stood
inside of would survive as "outermost".

This script reports, per archive:

  * exact duplicates      -- identical depth bytes *and* pose
  * depth-only duplicates -- identical depth, pose may differ
  * pose-only duplicates  -- identical pose, depth may differ
  * near-duplicates       -- pose within a few cm/cm, depth nearly identical
  * the effective number of independent viewpoints

Run:

    python scripts/diag_duplicate_frames.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from scan2plan.ingest import load_zip  # noqa: E402

DEFAULT = [
    ("single_room", "single_room.zip"),
    ("floor_only", "single_scan_floor_only.zip"),
    ("with_ceiling", "single_scan_with_ceiling.zip"),
]


def _hash_frames(depth: np.ndarray) -> list[str]:
    return [hashlib.blake2b(d.tobytes(), digest_size=12).hexdigest() for d in depth]


def _count_groups(keys: list[str]) -> tuple[int, int]:
    """Return (unique keys, frames that are a repeat of an earlier frame)."""
    seen: set[str] = set()
    repeats = 0
    for k in keys:
        if k in seen:
            repeats += 1
        else:
            seen.add(k)
    return len(seen), repeats


def _pose_keys(pos: np.ndarray, quant=1e-4) -> list[str]:
    q = np.round(pos / quant).astype(np.int64)
    return [hashlib.blake2b(r.tobytes(), digest_size=12).hexdigest() for r in q]


def analyse(name: str, path: str, near_m: float) -> None:
    if not os.path.exists(path):
        print(f"\n=== {name}: MISSING {path}")
        return

    cap = load_zip(path, frame_stride=1)
    n = cap.n_frames
    print(f"\n=== {name}   frames {n}   depth {cap.depth.shape[1]}x{cap.depth.shape[2]}")

    d_keys = _hash_frames(cap.depth)
    d_uniq, d_rep = _count_groups(d_keys)
    p_keys = _pose_keys(cap.positions)
    p_uniq, p_rep = _count_groups(p_keys)
    both = _count_groups([a + b for a, b in zip(d_keys, p_keys, strict=False)])

    print(f"  unique depth           {d_uniq:5d}   repeated {d_rep:5d} "
          f"({100.0 * d_rep / n:5.1f}%)")
    print(f"  unique pose            {p_uniq:5d}   repeated {p_rep:5d} "
          f"({100.0 * p_rep / n:5.1f}%)")
    print(f"  unique depth+pose      {both[0]:5d}   repeated {both[1]:5d} "
          f"({100.0 * both[1] / n:5.1f}%)")

    # Near-duplicate poses: consecutive-ish samples the operator never moved
    # between. These inflate plane support without adding a viewpoint.
    pos = cap.positions
    step = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    still = int((step < near_m).sum())
    print(f"  consecutive pose steps < {near_m * 100:.0f} cm : "
          f"{still:5d} of {n - 1} ({100.0 * still / max(n - 1, 1):5.1f}%)")
    if step.size:
        q = np.percentile(step, [50, 90, 99])
        print(f"  step mm  p50 {q[0] * 1000:6.1f}   p90 {q[1] * 1000:6.1f}   "
              f"p99 {q[2] * 1000:6.1f}")

    # How much of the path is genuinely new ground?
    uniq_idx: list[int] = []
    seen: set[str] = set()
    for i, k in enumerate(d_keys):
        if k not in seen:
            seen.add(k)
            uniq_idx.append(i)
    print(f"  independent (unique-depth) frames: {len(uniq_idx)}")

    # Spatial spread of the unique-depth frames vs all frames.
    for label, idx in (("all frames", np.arange(n)), ("unique-depth", np.asarray(uniq_idx))):
        if idx.size == 0:
            continue
        p = pos[idx]
        ext = p.max(axis=0) - p.min(axis=0)
        print(f"  path extent {label:13s} {ext[0]:6.2f} x {ext[1]:6.2f} x {ext[2]:6.2f} m")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--near-cm", type=float, default=2.0,
                    help="pose step below which two frames count as the same viewpoint")
    ap.add_argument("--data", default=None, help="directory holding the archives")
    args = ap.parse_args()

    root = args.data or os.environ.get("SCAN2PLAN_SAMPLE_DATA")
    if not root:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for cand in (os.path.join(here, "..", "Sample Data"),
                     os.path.join(here, "Sample Data")):
            if os.path.isdir(cand):
                root = cand
                break
    if not root or not os.path.isdir(root):
        print("could not locate the sample archives; pass --data")
        return 1

    print(f"archives: {os.path.abspath(root)}")
    for name, fn in DEFAULT:
        analyse(name, os.path.join(root, fn), args.near_cm / 100.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
