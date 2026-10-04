"""Per-room filename listing and MP4 duration/resolution without ffmpeg.

Two gaps in the first inventory that matter for the assessment:

- EXIF came back with no model and no timestamps on all 87 photos, so the
  photos cannot be ordered in time and cannot be tied to today's session by
  metadata. Filenames are descriptive, so they carry that information instead.
- Seven of the eleven videos are from July and August, not today. Only the four
  dated 20261004 belong to this capture, and their durations decide whether they
  are usable as the video tier.

MP4 `mvhd` is parsed directly so this needs no ffmpeg on the machine.
"""

from __future__ import annotations

import os
import struct
import sys
from datetime import timedelta

ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "capture")


def mp4_info(path: str) -> dict:
    """Walk the atom tree for mvhd (duration) and the first video track."""
    info = {"duration_s": None, "timescale": None, "resolution": None, "video_track": False}
    with open(path, "rb") as fh:
        data = fh.read()
    # mvhd lives inside moov; a linear scan is fine at these file sizes.
    i = data.find(b"mvhd")
    if i > 0:
        version = data[i + 4]
        if version == 1:
            timescale = struct.unpack(">I", data[i + 20:i + 24])[0]
            dur = struct.unpack(">Q", data[i + 24:i + 32])[0]
        else:
            timescale = struct.unpack(">I", data[i + 16:i + 20])[0]
            dur = struct.unpack(">I", data[i + 20:i + 24])[0]
        info["timescale"] = timescale
        info["duration_s"] = dur / timescale if timescale else None
    i = data.find(b"tkhd")
    if i > 0:
        version = data[i + 4]
        off = i + 4 + (32 if version == 1 else 20)
        # width/height are the last two 16.16 fixed values in tkhd
        w, h = struct.unpack(">II", data[i + 4 + (84 if version == 1 else 76):][:8])
        info["resolution"] = (w >> 16, h >> 16)
    return info


def main() -> None:
    root = os.path.abspath(ROOT)
    folders = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))

    for folder in folders:
        fdir = os.path.join(root, folder)
        files = sorted(os.listdir(fdir))
        photos = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".heic"))]
        videos = [f for f in files if f.lower().endswith((".mp4", ".mov", ".mkv"))]
        if not photos and not videos:
            continue
        print(f"\n=== {folder} ===")
        if photos:
            print(f"  photo filenames ({len(photos)}):")
            for p in photos:
                stem = os.path.splitext(p)[0]
                print(f"    {stem}")
        if videos:
            print(f"  videos ({len(videos)}):")
            for v in videos:
                vp = os.path.join(fdir, v)
                info = mp4_info(vp)
                dur = info["duration_s"]
                res = info["resolution"]
                stamp = "TODAY " if "20261004" in v else "old   "
                d = str(timedelta(seconds=int(dur))) if dur else "?"
                print(f"    {stamp} {v:<30} {os.path.getsize(vp) / 1e6:7.1f} MB  "
                      f"{d:>9}  {res[0]}x{res[1]}" if res else
                      f"    {stamp} {v:<30} {os.path.getsize(vp) / 1e6:7.1f} MB  {d:>9}  ?")


if __name__ == "__main__":
    main()
