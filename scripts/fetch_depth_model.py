"""Download the Depth Anything V2 ONNX weights.

Kept out of git and out of `pip install` because it is 99 MB of binary. The
export used is the fp32 one from `onnx-community/depth-anything-v2-small`: on this
CPU-only machine the int8 variant is not meaningfully faster at this model size,
and fp32 keeps the depth values clean enough to fit planes against.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REPO = "onnx-community/depth-anything-v2-small"
BASE = f"https://huggingface.co/{REPO}/resolve/main/"
FILES = [("onnx/model.onnx", "model.onnx"), ("preprocessor_config.json", "preprocessor_config.json")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dest", default=os.path.join(ROOT, ".models"), help="destination directory"
    )
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    args = ap.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    for remote, local in FILES:
        dst = os.path.join(args.dest, local)
        if os.path.isfile(dst) and not args.force:
            print(f"have {local} ({os.path.getsize(dst) / 1e6:.1f} MB)")
            continue
        print(f"fetching {remote} ...")
        t0 = time.time()
        try:
            urllib.request.urlretrieve(BASE + remote, dst)
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"  {os.path.getsize(dst) / 1e6:.1f} MB in {time.time() - t0:.0f}s")

    print(f"\nweights in {args.dest}")
    print("the core package finds them automatically; override with SCAN2PLAN_DEPTH_MODEL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
