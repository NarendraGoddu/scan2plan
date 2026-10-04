"""Run monocular depth over every photo and video in a capture directory.

Photos are processed at full resolution. Video frames are extracted on a fixed
time grid and downscaled to a 1920 px long side first: Depth Anything's output is
already upsampled from a 518 px working resolution, so feeding it 4K frames costs
CPU without adding information, and the point here is room structure rather than
texture detail.

Two outputs, deliberately kept apart:

  runs/depth/*.png   16-bit depth maps, one per photo and per video frame
  runs/depth_capture.json  per-image statistics

The statistics are triage, not measurement. Depth Anything predicts *relative*
inverse depth with a per-image arbitrary scale, so numbers from two photographs
are not comparable until something anchors them. `compare_rooms` is where that
anchoring happens, and it is a separate step on purpose.
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

from scan2plan.monodepth import (  # noqa: E402
    DepthStats,
    infer_depth,
    load_session,
    save_depth_png,
)

PHOTO_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi"}
VIDEO_LONG_SIDE = 1920

# Depth maps are written at this long side. The network predicts on a 518 px
# working resolution and the result is bilinearly upsampled, so a full-resolution
# 16-bit PNG is roughly 25 MB of interpolated values per frame and several GB
# across the capture, for detail the model never produced.
DEPTH_SAVE_LONG_SIDE = 1024


def list_media(capture_dir: str) -> list[tuple[str, str]]:
    """Every photo and video under the capture directory, as (kind, path)."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(capture_dir):
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext in PHOTO_EXT:
                out.append(("photo", os.path.join(dirpath, name)))
            elif ext in VIDEO_EXT:
                out.append(("video", os.path.join(dirpath, name)))
    return out


def video_frames(path: str, n_target: int) -> list[np.ndarray]:
    """Sample `n_target` frames spread evenly across a video's duration.

    Uniform time sampling rather than every Nth frame, so a clip that pans
    quickly still contributes views of both ends of the room.
    """
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if total <= 0 or fps <= 0:
        cap.release()
        return []
    picks = np.linspace(0, total - 1, num=n_target).astype(int)
    frames, seen = [], set()
    for idx in picks:
        if idx in seen:
            continue
        seen.add(int(idx))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        if max(h, w) > VIDEO_LONG_SIDE:
            s = VIDEO_LONG_SIDE / float(max(h, w))
            frame = cv2.resize(
                frame, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA
            )
        frames.append(frame)
    cap.release()
    return frames


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--capture",
        default=os.path.join(ROOT, os.pardir, "capture"),
        help="capture directory containing photo and video folders",
    )
    ap.add_argument("--out", default=os.path.join(ROOT, "runs"), help="output directory")
    ap.add_argument("--frames-per-video", type=int, default=10)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument(
        "--limit", type=int, default=0, help="stop after N photos (debugging only)"
    )
    ap.add_argument(
        "--no-save-depth", action="store_true", help="skip writing depth PNGs"
    )
    args = ap.parse_args()

    capture_dir = os.path.abspath(args.capture)
    depth_dir = os.path.join(args.out, "depth")
    os.makedirs(depth_dir, exist_ok=True)

    media = list_media(capture_dir)
    photos = [p for k, p in media if k == "photo"]
    videos = [p for k, p in media if k == "video"]
    if args.limit:
        photos = photos[: args.limit]
    print(f"capture {capture_dir}")
    print(f"  {len(photos)} photos, {len(videos)} videos")

    session = load_session(threads=args.threads)
    records: list[dict] = []
    t_start = time.time()
    n_done = 0

    def record(src: str, frame_index, depth, extra=None):
        rel = os.path.relpath(src, capture_dir)
        stem = os.path.splitext(rel)[0].replace(os.sep, "__")
        if not args.no_save_depth:
            name = f"{stem}.png" if frame_index is None else f"{stem}__f{frame_index:04d}.png"
            h, w = depth.shape[:2]
            if max(h, w) > DEPTH_SAVE_LONG_SIDE:
                s = DEPTH_SAVE_LONG_SIDE / float(max(h, w))
                small = cv2.resize(
                    depth, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA
                )
            else:
                small = depth
            save_depth_png(small, os.path.join(depth_dir, name))
        st = DepthStats.from_depth(rel, depth)
        rec = {
            "source": rel,
            "kind": extra or "photo",
            "height": st.height,
            "width": st.width,
            "p01": st.p01,
            "p50": st.p50,
            "p99": st.p99,
            "range": st.p99 - st.p01,
        }
        if frame_index is not None:
            rec["frame_index"] = frame_index
        records.append(rec)

    for path in photos:
        img = cv2.imread(path)
        if img is None:
            print(f"  UNREADABLE {os.path.basename(path)}")
            continue
        depth = infer_depth(session, img)
        record(path, None, depth, "photo")
        n_done += 1
        if n_done % 10 == 0:
            el = time.time() - t_start
            print(f"  {n_done}/{len(photos)} photos  {el:.0f}s elapsed")

    for path in videos:
        frames = video_frames(path, args.frames_per_video)
        if not frames:
            print(f"  NO FRAMES {os.path.basename(path)}")
            continue
        for i, frame in enumerate(frames):
            depth = infer_depth(session, frame)
            record(path, i, depth, "video_frame")
        print(f"  {os.path.basename(path)}: {len(frames)} frames")

    out_json = os.path.join(args.out, "depth_capture.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "capture_dir": capture_dir,
                "n_photos": len(photos),
                "n_videos": len(videos),
                "frames_per_video": args.frames_per_video,
                "model": "depth-anything-v2-small (ONNX, fp32)",
                "units": "relative inverse depth, arbitrary scale per image",
                "records": records,
            },
            fh,
            indent=2,
        )
    print(f"\n{len(records)} depth maps in {time.time() - t_start:.0f}s")
    print(f"wrote {out_json}")
    if not args.no_save_depth:
        print(f"wrote depth PNGs to {depth_dir}")


if __name__ == "__main__":
    main()
