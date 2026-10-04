"""Capture ingest: decode the raw LiDAR archive format into numpy arrays.

Archive layout (one scan per top-level hash directory):

    <hash>/depth/NNNNNN.png       uint16, decimated depth, scale TBD (see calibrate.py)
    <hash>/confidence/NNNNNN.png  uint8, {0,1,2}
    <hash>/odometry.csv           per-frame pose + intrinsics at 60 Hz
    <hash>/imu.csv                raw accel + gyro
    <hash>/camera_matrix.csv      static 3x3 intrinsics (reference only)
    <hash>/rgb.mp4                camera stream

Notes that matter downstream:

* The depth maps are 192x256 while `odometry.csv` intrinsics are for the full
  1920x1440 frame. That is a 7.5x decimation, so the depth focal length is
  K_full / 7.5. Getting this wrong scales every reconstructed point by 7.5.
* Intrinsics drift per frame (fx varies ~1581..1618 across a scan, consistent
  with a rolling-shutter / AF sensor). Always use the per-frame values; the
  static camera_matrix.csv is only a sanity reference.
* Poses are metric (metres) and come from the capture rig's odometry. They are
  NOT ground truth -- see drift.py for why they need correction.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from dataclasses import dataclass

import numpy as np
from PIL import Image

# Depth PNGs are decimated relative to the full-resolution intrinsics.
DEPTH_DECIMATION = 7.5

CONFIDENCE_VALID = 2


@dataclass
class Capture:
    """One decoded capture, all arrays aligned by frame index."""

    scan_id: str
    depth: np.ndarray  # (N, 192, 256) uint16 raw counts
    confidence: np.ndarray  # (N, 192, 256) uint8
    positions: np.ndarray  # (N, 3) metres, rig odometry
    quats: np.ndarray  # (N, 4) xyzw
    focal: np.ndarray  # (N, 2) full-res fx, fy
    principal: np.ndarray  # (N, 2) full-res cx, cy
    timestamps: np.ndarray  # (N,) seconds

    @property
    def n_frames(self) -> int:
        return self.depth.shape[0]

    @property
    def depth_hw(self) -> tuple[int, int]:
        return self.depth.shape[1], self.depth.shape[2]

    def subset(self, frames: "list[int] | range | np.ndarray") -> "Capture":
        """A view of this capture over `frames`, keeping every array aligned.

        Needed to measure repeatability: segmenting two disjoint halves of the
        same capture and comparing the fits measures how stable the estimator is
        without needing any ground truth. Slicing the arrays independently would
        be a bug waiting to happen, so every per-frame array goes through here.
        """
        idx = np.asarray(frames, dtype=np.int64)
        if idx.size == 0:
            raise ValueError("subset() needs at least one frame")
        if idx.min() < 0 or idx.max() >= self.n_frames:
            raise IndexError(
                f"frame indices out of range: got {idx.min()}..{idx.max()}, "
                f"archive has {self.n_frames}"
            )
        return Capture(
            scan_id=self.scan_id,
            depth=self.depth[idx],
            confidence=self.confidence[idx],
            positions=self.positions[idx],
            quats=self.quats[idx],
            focal=self.focal[idx],
            principal=self.principal[idx],
            timestamps=self.timestamps[idx],
        )

    def depth_intrinsics(self, frame: int) -> np.ndarray:
        """Intrinsics matched to the decimated depth resolution, frame `frame`."""
        h, w = self.depth_hw
        fx = self.focal[frame, 0] / DEPTH_DECIMATION
        fy = self.focal[frame, 1] / DEPTH_DECIMATION
        return np.array(
            [[fx, 0.0, w / 2.0], [0.0, fy, h / 2.0], [0.0, 0.0, 1.0]], dtype=np.float64
        )


def motion_dedup(
    positions: np.ndarray,
    quats: np.ndarray,
    min_translation_m: float = 0.05,
    min_rotation_deg: float = 2.0,
    indices: np.ndarray | range | None = None,
) -> np.ndarray:
    """Keep frames that add a genuinely new viewpoint.

    Index-based subsampling is the wrong tool for a handheld capture. These
    archives are recorded at 60 Hz while the operator walks, so consecutive frames
    are 8-9 mm apart and 95-98% of them move the camera less than 2 cm. Frame *n*
    and frame *n+1* see almost the same thing from almost the same place.

    That redundancy is not harmless, because plane `support` counts observations.
    A surface the operator stood in front of for three seconds collects hundreds of
    near-identical observations, while a real wall they glanced at collects a few.
    Ranking candidate walls by support then ranks them by how long someone lingered,
    and furniture the operator paused beside outranks the wall behind it. Keeping
    only frames that actually moved removes that bias, so support means roughly
    "distinct viewpoints that saw this surface".

    Not the same as dropping duplicate frames -- there are none. Measured on the
    three supplied archives: 0 byte-identical depth frames out of 1715 / 5251 /
    9745, while a 5 cm motion threshold still discards 71% / 71% / 70% of them as
    redundant. See scripts/diag_duplicate_frames.py.

    `indices` restricts the scan to a subset (used for the disjoint-halves
    repeatability check), so each half is deduplicated independently and the two
    halves stay disjoint.

    The first and last frame of the pool are always kept. Without that, a capture
    whose final frames are all within the threshold of the last kept one loses its
    endpoint, and the path extent used downstream comes up short.
    """
    pos = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    quat = np.asarray(quats, dtype=np.float64).reshape(-1, 4)
    pool = np.arange(len(pos)) if indices is None else np.asarray(indices, dtype=np.int64)
    if pool.size == 0:
        return pool

    cos_limit = float(np.cos(np.radians(min_rotation_deg)))
    kept: list[int] = [int(pool[0])]
    last_pos = pos[pool[0]]
    last_quat = quat[pool[0]]
    for i in pool[1:]:
        moved = float(np.linalg.norm(pos[i] - last_pos))
        # |dot| handles quaternion sign ambiguity: q and -q are the same rotation.
        turned = abs(float(quat[i] @ last_quat))
        if moved >= min_translation_m or turned < cos_limit:
            kept.append(int(i))
            last_pos = pos[i]
            last_quat = quat[i]
    if int(pool[-1]) not in kept:
        kept.append(int(pool[-1]))
    return np.asarray(kept, dtype=np.int64)


def quat_to_rot(q: np.ndarray) -> np.ndarray:
    """Quaternion (x, y, z, w) -> 3x3 rotation matrix."""
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    return np.array(
        [
            [1 - (yy + zz), xy - wz, xz + wy],
            [xy + wz, 1 - (xx + zz), yz - wx],
            [xz - wy, yz + wx, 1 - (xx + yy)],
        ]
    )


def poses_to_matrices(positions: np.ndarray, quats: np.ndarray) -> np.ndarray:
    """(N,3) + (N,4) -> (N,4,4) camera-to-world transforms."""
    n = positions.shape[0]
    out = np.tile(np.eye(4), (n, 1, 1))
    out[:, :3, :3] = np.stack([quat_to_rot(q) for q in quats])
    out[:, :3, 3] = positions
    return out


def _scan_root(zf: zipfile.ZipFile) -> str:
    roots = {n.split("/", 1)[0] for n in zf.namelist() if "/" in n}
    if len(roots) != 1:
        raise ValueError(f"expected exactly one scan dir, found {sorted(roots)}")
    return roots.pop()


def _read_odometry(zf: zipfile.ZipFile, root: str) -> tuple[np.ndarray, ...]:
    """Parse odometry.csv. Header has spaces after commas, hence skipinitialspace."""
    with zf.open(f"{root}/odometry.csv") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8")
        rows = list(csv.DictReader(text, skipinitialspace=True))
    rows = [
        {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
        for r in rows
    ]

    def col(name, fn):
        return np.array([fn(float(r[name])) for r in rows], dtype=np.float64)

    positions = np.stack([col("x", float), col("y", float), col("z", float)], axis=1)
    quats = np.stack([col("q" + a, float) for a in ("x", "y", "z", "w")], axis=1)
    focal = np.stack([col("fx", float), col("fy", float)], axis=1)
    principal = np.stack([col("cx", float), col("cy", float)], axis=1)
    ts = col("timestamp", float)
    order = np.argsort(ts, kind="stable")
    return positions[order], quats[order], focal[order], principal[order], ts[order]


def _frame_key(name: str) -> int:
    return int(os.path.splitext(os.path.basename(name))[0])


def load_zip(path: str, frame_stride: int = 1, max_frames: int | None = None) -> Capture:
    """Decode a capture archive.

    `frame_stride` subsamples frames (the source is 60 Hz, far denser than the
    plane-fitting stage needs). `max_frames` caps work for quick runs.
    """
    with zipfile.ZipFile(path) as zf:
        root = _scan_root(zf)
        positions, quats, focal, principal, ts = _read_odometry(zf, root)

        depth_names = sorted(
            (n for n in zf.namelist() if n.startswith(f"{root}/depth/")),
            key=_frame_key,
        )
        conf_names = sorted(
            (n for n in zf.namelist() if n.startswith(f"{root}/confidence/")),
            key=_frame_key,
        )
        if len(depth_names) != len(positions):
            raise ValueError(
                f"depth frames ({len(depth_names)}) != pose rows ({len(positions)})"
            )

        idx = np.arange(0, len(depth_names), frame_stride)
        if max_frames is not None:
            idx = idx[:max_frames]

        depths, confs = [], []
        for i in idx:
            depths.append(np.array(Image.open(io.BytesIO(zf.read(depth_names[i])))))
            confs.append(
                np.array(Image.open(io.BytesIO(zf.read(conf_names[i]))), dtype=np.uint8)
            )
        depth = np.stack(depths)
        conf = np.stack(confs)

    return Capture(
        scan_id=root,
        depth=depth,
        confidence=conf,
        positions=positions[idx],
        quats=quats[idx],
        focal=focal[idx],
        principal=principal[idx],
        timestamps=ts[idx],
    )