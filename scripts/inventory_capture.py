"""Inventory the field capture: counts, EXIF, video specs, and image quality.

Written before anything is committed to the repo, for two reasons.

1. An assessment needs an honest statement of what the capture contains, and
   that means measuring rather than assuming -- per-room counts, whether the
   photos are originals or re-compressed, whether focus and exposure are usable.
2. Phone photos carry GPS EXIF by default. Committing a real home's coordinates
   to a public repository is a privacy leak, so GPS presence is reported
   explicitly and must be dealt with before any of these files are staged.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np
from PIL import Image, ExifTags

try:
    from PIL import Image as _I
    HAVE_VIDEO = True
except Exception:
    HAVE_VIDEO = False

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "capture")

TAGS = {v: k for k, v in ExifTags.TAGS.items()}


def exif_of(path: str) -> dict:
    out = {}
    try:
        with Image.open(path) as im:
            raw = im.getexif()
            out["w"], out["h"] = im.size
            out["mode"] = im.mode
            for key, name in TAGS.items():
                if key in raw:
                    out[name] = raw[key]
            if raw.get(0x8769) is not None:
                ex = raw.get_ifd(0x8769)
                for key, val in ex.items():
                    out[TAGS.get(key, str(key))] = val
    except Exception as e:
        out["error"] = str(e)
    return out


def laplacian_var(path: str) -> float:
    """Focus proxy. Low variance means a soft or motion-blurred frame."""
    try:
        with Image.open(path) as im:
            g = np.asarray(im.convert("L").resize((320, 240)), dtype=np.float64)
            k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
            from numpy.lib.stride_tricks import sliding_window_view
            win = sliding_window_view(g, (3, 3))
            return float((win * k).sum(axis=(-1, -2)).var())
    except Exception:
        return float("nan")


def main() -> None:
    root = os.path.abspath(ROOT)
    if not os.path.isdir(root):
        print(f"no such directory: {root}")
        return

    folders = sorted(
        d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))
    )
    report = {"root": root, "folders": {}}
    gps_hits, dates, dims = [], [], Counter()

    print(f"capture root: {root}\n")
    for folder in folders:
        fdir = os.path.join(root, folder)
        files = sorted(os.listdir(fdir))
        photos = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".heic", ".png"))]
        videos = [f for f in files if f.lower().endswith((".mp4", ".mov", ".mkv"))]
        size = sum(
            os.path.getsize(os.path.join(fdir, f)) for f in files
            if os.path.isfile(os.path.join(fdir, f))
        )
        print(f"=== {folder} ===")
        print(f"  {len(photos)} photos, {len(videos)} videos, {size / 1e6:8.1f} MB")

        entry = {"n_photos": len(photos), "n_videos": len(videos),
                 "bytes": size, "photos": [], "videos": []}

        blur = []
        for p in photos:
            fp = os.path.join(fdir, p)
            ex = exif_of(fp)
            lv = laplacian_var(fp)
            blur.append(lv)
            gps = {k: v for k, v in ex.items() if "GPS" in k}
            if gps:
                gps_hits.append((folder, p))
            for k in ("DateTimeOriginal", "DateTime"):
                if k in ex:
                    dates.append(str(ex[k]))
                    break
            if "w" in ex:
                dims[(ex["w"], ex["h"])] += 1
            entry["photos"].append({
                "name": p,
                "bytes": os.path.getsize(fp),
                "w": ex.get("w"), "h": ex.get("h"),
                "model": ex.get("Model"), "make": ex.get("Make"),
                "software": ex.get("Software"),
                "datetime": ex.get("DateTimeOriginal") or ex.get("DateTime"),
                "orientation": ex.get("Orientation"),
                "gps": bool(gps),
                "sharpness": round(lv, 1),
            })

        if blur:
            b = np.array([x for x in blur if x == x])
            if b.size:
                print(f"  sharpness  min {b.min():8.1f}  median {np.median(b):8.1f}  max {b.max():8.1f}")
                soft = [photos[i] for i, x in enumerate(blur)
                        if x == x and x < max(np.median(b) * 0.35, 20)]
                print(f"  soft frames (< 35% of median): {len(soft)}")
                for s in soft[:8]:
                    print(f"     {s}")
        entry["sharpness_median"] = round(float(np.median(b)), 1) if b.size else None

        for v in videos:
            vp = os.path.join(fdir, v)
            entry["videos"].append({"name": v, "bytes": os.path.getsize(vp)})
        if videos:
            print(f"  videos:")
            for v in entry["videos"]:
                print(f"     {v['name']:<34} {v['bytes'] / 1e6:7.1f} MB")

        report["folders"][folder] = entry
        print()

    print("=== summary ===")
    tp = sum(v["n_photos"] for v in report["folders"].values())
    tv = sum(v["n_videos"] for v in report["folders"].values())
    print(f"  photos {tp}, videos {tv}")
    print(f"  image sizes: {dict(dims)}")
    if dates:
        print(f"  capture window: {min(dates)}  ->  {max(dates)}")
    models = Counter(
        p.get("model") for v in report["folders"].values() for p in v["photos"]
    )
    print(f"  camera models: {dict(models)}")
    software = Counter(
        p.get("software") for v in report["folders"].values() for p in v["photos"]
        if p.get("software")
    )
    print(f"  software tags: {dict(software) or 'none'}")

    print(f"\n  *** GPS EXIF present in {len(gps_hits)} of {tp} photos ***"
          if gps_hits else "\n  GPS EXIF: none found")
    if gps_hits:
        print("      These are real coordinates of a real home. Strip EXIF GPS")
        print("      before staging anything into a public repository.")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_inventory.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
