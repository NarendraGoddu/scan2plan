"""Synthetic room generator with exact ground truth.

The point of this module is to produce captures the existing pipeline cannot
tell apart from the real archives: same 192x256 uint16 depth maps, same
confidence semantics, same odometry.csv columns, same 7.5x depth decimation.
Every stage built so far -- ingest, scale calibration, drift measurement, plane
segmentation -- then runs here unmodified, so a failure is attributable to the
geometry code rather than to a format shim.

Two properties make this worth more than internet photographs:

* Ground truth is exact to the millimetre, because the room is defined
  analytically before any noisy observation of it exists.
* It is regenerable and deterministic, so a reviewer can reproduce every
  benchmark number from a seed.

Noise is injected deliberately rather than left out, since a benchmark on
clean data measures nothing. Pose scatter is the interesting term: CP2 measured
32-44 mm of random per-frame error with no systematic drift on the real
archives, so that is the default here.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile

import numpy as np
from PIL import Image

# Mirrors ingest.DEPTH_DECIMATION: depth PNGs are decimated from the full-res
# sensor, so the depth-space focal is the full-res focal divided by this.
DEPTH_DECIMATION = 7.5

# Metres per raw depth unit. CP1 recovered 0.0009997 m/unit across all three
# real archives; 0.001 is what the sensor nominally encodes.
DEPTH_SCALE_M = 0.001

IMAGE_H, IMAGE_W = 192, 256
CONFIDENCE_VALID = 2
DEPTH_MIN_M, DEPTH_MAX_M = 0.15, 8.0

# Faces are named by the coordinate they are fixed at.
FACES = ("x0", "x1", "z0", "z1")

# Face -> world axis index. Openings only ever appear on the vertical faces.
FACE_AXIS = {"x0": 0, "x1": 0, "z0": 2, "z1": 2}


class Room:
    """An axis-aligned room in a Y-up world, with rectangular wall openings.

    `length` runs along x, `width` along z, `height` along y, floor at y=0.
    Openings are cut as holes: a ray landing inside one produces no return,
    which is what a real door reveal does to a depth sensor.
    """

    def __init__(
        self,
        length: float,
        width: float,
        height: float,
        openings: list[dict] | None = None,
    ) -> None:
        self.length = float(length)
        self.width = float(width)
        self.height = float(height)
        self.openings = list(openings or [])

        for op in self.openings:
            if op["face"] not in FACES:
                raise ValueError(f"opening face must be one of {FACES}: {op}")
            if not (0.0 <= op["u0"] < op["u1"]):
                raise ValueError(f"opening u-range invalid: {op}")
            if not (0.0 <= op["v0"] < op["v1"] <= self.height):
                raise ValueError(f"opening v-range outside 0..{self.height}: {op}")

    @property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        lo = np.zeros(3)
        hi = np.array([self.length, self.height, self.width])
        return lo, hi

    @property
    def floor_area_m2(self) -> float:
        return self.length * self.width

    def opening_centres(self) -> list[np.ndarray]:
        """World-space centre of each opening, for trajectory planning."""
        out = []
        for op in self.openings:
            uc, vc = 0.5 * (op["u0"] + op["u1"]), 0.5 * (op["v0"] + op["v1"])
            if op["face"] == "x0":
                out.append(np.array([0.0, vc, uc]))
            elif op["face"] == "x1":
                out.append(np.array([self.length, vc, uc]))
            elif op["face"] == "z0":
                out.append(np.array([uc, vc, 0.0]))
            else:
                out.append(np.array([uc, vc, self.width]))
        return out

    def ground_truth(self) -> dict:
        """Everything the benchmark is permitted to score against."""
        ops = []
        for op in self.openings:
            ops.append(
                {
                    "face": op["face"],
                    "kind": op.get("kind", "opening"),
                    "width_m": round(op["u1"] - op["u0"], 6),
                    "height_m": round(op["v1"] - op["v0"], 6),
                    "sill_m": round(op["v0"], 6),
                    "u0": op["u0"],
                    "u1": op["u1"],
                    "v0": op["v0"],
                    "v1": op["v1"],
                }
            )
        return {
            "room_length_m": self.length,
            "room_width_m": self.width,
            "room_height_m": self.height,
            "floor_area_m2": round(self.floor_area_m2, 6),
            "openings": ops,
        }


def _plane_hit(
    origin: np.ndarray, dirs: np.ndarray, lo: np.ndarray, hi: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Ray/box intersection from inside, via the slab method.

    Returns (t, axis, hit_point); `axis` identifies the face hit, which is what
    distinguishes a floor hit from a wall hit.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        t_pos = (hi - origin) / dirs
        t_neg = (lo - origin) / dirs
    # A ray travelling +x leaves through the hi face and vice versa.
    t_far = np.where(dirs > 0, t_pos, np.where(dirs < 0, t_neg, np.inf))
    t = t_far.min(axis=1)
    axis = t_far.argmin(axis=1)
    hit = origin + t[:, None] * dirs
    return t, axis, hit


def _opening_hits(room: Room, axis: np.ndarray, hit: np.ndarray) -> np.ndarray:
    """True where the hit lands inside a wall opening, i.e. no surface there."""
    blocked = np.zeros(axis.shape[0], dtype=bool)
    for op in room.openings:
        ai = FACE_AXIS[op["face"]]
        on_face = axis == ai
        if not on_face.any():
            continue
        # In-plane coordinates: the face's horizontal axis, plus y.
        u_coord = hit[:, 2] if op["face"].startswith("x") else hit[:, 0]
        blocked |= (
            on_face
            & (u_coord >= op["u0"])
            & (u_coord <= op["u1"])
            & (hit[:, 1] >= op["v0"])
            & (hit[:, 1] <= op["v1"])
        )
    return blocked


def look_at_rotation(forward: np.ndarray, world_up: np.ndarray) -> np.ndarray:
    """Camera-to-world rotation in OpenCV axes (x right, y down, z forward).

    y = cross(z, x) rather than cross(x, z) is what makes y point *down* in
    image space, matching the convention unproject_frame assumes. With world Y
    up and forward -Z the columns are (+X, -Y, -Z), a proper rotation (det +1).
    """
    z = forward / np.linalg.norm(forward)
    x = np.cross(z, world_up)
    nx = np.linalg.norm(x)
    if nx < 1e-9:
        # Looking straight up or down: any perpendicular will do.
        x = np.cross(z, np.array([1.0, 0.0, 0.0]))
        nx = np.linalg.norm(x)
    x = x / nx
    y = np.cross(z, x)
    return np.column_stack([x, y, z])


def rot_to_quat(R: np.ndarray) -> np.ndarray:
    """3x3 rotation -> (qx, qy, qz, qw), matching ingest's column order."""
    m = R
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0.0:
        s = np.sqrt(tr + 1.0) * 2.0
        q = np.array([(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s,
                      (m[1, 0] - m[0, 1]) / s, 0.25 * s])
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        q = np.array([0.25 * s, (m[0, 1] + m[1, 0]) / s,
                      (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s])
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        q = np.array([(m[0, 1] + m[1, 0]) / s, 0.25 * s,
                      (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s])
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        q = np.array([(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s,
                      0.25 * s, (m[1, 0] - m[0, 1]) / s])
    return q / np.linalg.norm(q)


def ray_grid(focal: np.ndarray, principal: np.ndarray) -> np.ndarray:
    """Pixel grid -> (H, W, 3) unit ray directions in camera coordinates."""
    v, u = np.meshgrid(
        np.arange(IMAGE_H, dtype=np.float64),
        np.arange(IMAGE_W, dtype=np.float64),
        indexing="ij",
    )
    x = (u - principal[0]) / focal[0]
    y = (v - principal[1]) / focal[1]
    d = np.stack([x, y, np.ones_like(x)], axis=-1)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def plan_trajectory(
    room: Room, n_frames: int, camera_height_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """A hand-held-looking walk covering walls, floor, ceiling and openings.

    Returns noise-free (positions, forward_directions). The phases are not
    decoration: walls are only well observed while looking at them, and a floor
    or ceiling is essentially never seen from a level outward-facing camera, so
    the trajectory has to tilt deliberately or those surfaces never accumulate
    enough support to cluster.
    """
    L, W = room.length, room.width
    inset = 0.85
    cx, cz = L / 2.0, W / 2.0
    hx = max(L / 2.0 - inset, 0.25)
    hz = max(W / 2.0 - inset, 0.25)

    n_wall = int(n_frames * 0.40)
    n_floor = int(n_frames * 0.20)
    n_ceil = int(n_frames * 0.20)
    n_open = max(n_frames - n_wall - n_floor - n_ceil, 0)

    pos = np.zeros((n_frames, 3))
    fwd = np.zeros((n_frames, 3))
    i = 0

    # Phase 1: perimeter loops at two alternating radii, looking outward.
    for k in range(n_wall):
        ang = (k / max(n_wall - 1, 1)) * 2.0 * np.pi
        use_x = (k // 40) % 2 == 0
        ax, az = (hx, hz) if use_x else (hz, hx)
        px, pz = cx + ax * np.cos(ang), cz + az * np.sin(ang)
        out = np.array([px - cx, 0.0, pz - cz])
        out /= np.linalg.norm(out) + 1e-9
        yaw = 0.35 * np.sin(3.0 * ang)
        perp = np.array([-out[2], 0.0, out[0]])
        pos[i] = (px, camera_height_m, pz)
        fwd[i] = out * np.cos(yaw) + perp * np.sin(yaw)
        i += 1

    # Phase 2: interior sweep tilted down at the floor.
    for k in range(n_floor):
        ang = (k / max(n_floor - 1, 1)) * 2.0 * np.pi
        px, pz = cx + 0.5 * hx * np.cos(ang), cz + 0.5 * hz * np.sin(ang)
        out = np.array([px - cx, 0.0, pz - cz])
        out /= np.linalg.norm(out) + 1e-9
        pos[i] = (px, camera_height_m, pz)
        fwd[i] = 0.62 * out + np.array([0.0, -0.78, 0.0])
        i += 1

    # Phase 3: interior sweep tilted up at the ceiling.
    for k in range(n_ceil):
        ang = (k / max(n_ceil - 1, 1)) * 2.0 * np.pi
        px, pz = cx + 0.5 * hx * np.cos(ang), cz + 0.5 * hz * np.sin(ang)
        out = np.array([px - cx, 0.0, pz - cz])
        out /= np.linalg.norm(out) + 1e-9
        pos[i] = (px, camera_height_m, pz)
        fwd[i] = 0.62 * out + np.array([0.0, 0.78, 0.0])
        i += 1

    # Phase 4: stand off from each opening and look straight at it. Rooms with
    # no openings still need this phase to have valid forward vectors, so fall
    # back to panning across the four walls rather than leaving zeros behind.
    if room.openings:
        centres = room.opening_centres()
        stands = {
            "x0": np.array([1.35, 0.0, 0.0]),
            "x1": np.array([-1.35, 0.0, 0.0]),
            "z0": np.array([0.0, 0.0, 1.35]),
            "z1": np.array([0.0, 0.0, -1.35]),
        }
        for k in range(n_open):
            op = room.openings[k % len(room.openings)]
            pos[i] = np.array([cx, camera_height_m, cz]) + stands[op["face"]]
            fwd[i] = centres[k % len(centres)] - pos[i]
            i += 1
    else:
        outward_dirs = (
            np.array([1.0, 0.0, 0.0]), np.array([-1.0, 0.0, 0.0]),
            np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, -1.0]),
        )
        for k in range(n_open):
            pos[i] = (cx, camera_height_m, cz)
            fwd[i] = outward_dirs[k % 4]
            i += 1

    if i < n_frames:
        # Int truncation of the phase fractions can leave a frame or two spare;
        # point them at a wall rather than leaving a zero forward vector, which
        # would divide by zero in look_at_rotation.
        for j in range(i, n_frames):
            pos[j] = (cx, camera_height_m, cz)
            fwd[j] = np.array([1.0, 0.0, 0.0])

    # Hand-held bob, applied last so it perturbs every phase.
    t = np.arange(n_frames) / 30.0
    pos[:, 1] += 0.015 * np.sin(2.0 * np.pi * 1.4 * t)
    pos[:, 0] += 0.006 * np.sin(2.0 * np.pi * 0.7 * t)
    pos[:, 2] += 0.006 * np.cos(2.0 * np.pi * 0.9 * t)
    return pos, fwd


def render_capture(
    room: Room,
    n_frames: int = 720,
    camera_height_m: float = 1.40,
    full_res_focal: float = 1600.0,
    focal_drift: float = 18.0,
    principal_jitter_px: float = 0.0,
    pose_noise_m: float = 0.020,
    depth_noise_m: float = 0.004,
    seed: int = 7,
    scan_id: str = "synthetic",
) -> dict:
    """Render a capture. Returns noisy observations plus the noise-free truth."""
    rng = np.random.default_rng(seed)
    lo, hi = room.bounds
    world_up = np.array([0.0, 1.0, 0.0])

    true_pos, true_fwd = plan_trajectory(room, n_frames, camera_height_m)

    # Intrinsics are stored at FULL sensor resolution and divided by
    # DEPTH_DECIMATION on the way in, exactly as the real archives do:
    # single_room stores fx ~1598 and depth_intrinsics() yields ~213. Storing the
    # already-divided value here divides twice, collapses every ray toward the
    # optical axis, and puts the unprojected cloud nowhere near the room.
    focal_full_1d = full_res_focal + rng.normal(0.0, focal_drift, n_frames)
    focal_depth_1d = focal_full_1d / DEPTH_DECIMATION
    # depth_intrinsics() ignores the archived principal point and always uses
    # exactly (w/2, h/2). Rendering at anything else would penalise the
    # pipeline for a sub-pixel discrepancy the reader imposes by construction,
    # so the default jitter is zero. The real archives carry cx ~955.4 against
    # an assumed 960, which is the same effect and is why real floor fits show
    # tens of millimetres of residual.
    principal_depth = np.array(
        [
            np.full(n_frames, IMAGE_W / 2.0) + rng.normal(0.0, principal_jitter_px, n_frames),
            np.full(n_frames, IMAGE_H / 2.0) + rng.normal(0.0, principal_jitter_px, n_frames),
        ]
    ).T
    # What gets archived: full-res focal and full-res principal point.
    focal = np.column_stack([focal_full_1d, focal_full_1d])
    principal = principal_depth * DEPTH_DECIMATION

    depth = np.zeros((n_frames, IMAGE_H, IMAGE_W), dtype=np.uint16)
    conf = np.zeros((n_frames, IMAGE_H, IMAGE_W), dtype=np.uint8)
    poses = np.zeros((n_frames, 3))
    quats = np.zeros((n_frames, 4))
    true_quats = np.zeros((n_frames, 4))

    for f in range(n_frames):
        R = look_at_rotation(true_fwd[f], world_up)
        rays = ray_grid(
            np.array([focal_depth_1d[f], focal_depth_1d[f]]), principal_depth[f]
        )
        dirs = rays @ R.T
        flat = dirs.reshape(-1, 3)
        t, axis, hit = _plane_hit(true_pos[f], flat, lo, hi)
        valid = (
            (t >= DEPTH_MIN_M)
            & (t <= DEPTH_MAX_M)
            & (~_opening_hits(room, axis, hit))
            & np.isfinite(t)
        )
        # Range-dependent noise: real ToF error grows with distance.
        t_noisy = t + rng.normal(0.0, 1.0, t.shape) * (depth_noise_m * (1.0 + 0.55 * t))

        # Store the AXIAL depth (the ray's z component), not the slant range.
        # unproject_frame reconstructs points as [(u-cx)/fx*z, (v-cy)/fy*z, z],
        # which assumes the stored value is z. Storing the ray length instead
        # makes every off-axis depth too large by 1/cos(theta) and pushes points
        # through the wall they were measured from -- an error that grows with
        # pixel radius and looks like a focal-length fault.
        # Store the AXIAL depth (the ray's component along the camera's optical
        # axis), not the slant range and not the world-Z component.
        # unproject_frame reconstructs points as [(u-cx)/fx*z, (v-cy)/fy*z, z],
        # which assumes the stored value is z in *camera* coordinates. Storing
        # the ray length inflates every off-axis depth by 1/cos(theta) and
        # pushes points through the wall they were measured from.
        axial = t_noisy * rays.reshape(-1, 3)[:, 2]
        raw = np.where(valid, axial / DEPTH_SCALE_M, 0.0)
        raw = np.clip(np.nan_to_num(raw, nan=0.0, posinf=0.0), 0, 65535)
        depth[f] = raw.reshape(IMAGE_H, IMAGE_W).astype(np.uint16)
        conf[f] = np.where(valid, CONFIDENCE_VALID, 0).reshape(IMAGE_H, IMAGE_W).astype(np.uint8)
        poses[f] = true_pos[f] + rng.normal(0.0, pose_noise_m, 3)
        quats[f] = rot_to_quat(R)
        true_quats[f] = quats[f]

    truth = room.ground_truth()
    truth.update(
        {
            "scan_id": scan_id,
            "n_frames": int(n_frames),
            "seed": int(seed),
            "camera_height_m": camera_height_m,
            "noise": {
                "pose_noise_m": pose_noise_m,
                "depth_noise_m": depth_noise_m,
                "full_res_focal": full_res_focal,
                "focal_drift": focal_drift,
            },
            "depth_scale_m": DEPTH_SCALE_M,
            "depth_decimation": DEPTH_DECIMATION,
        }
    )
    return {
        "depth": depth,
        "conf": conf,
        "poses": poses,
        "quats": quats,
        "focal": focal,
        "principal": principal,
        "timestamps": np.arange(n_frames, dtype=np.float64) / 60.0,
        "truth": truth,
        "true_positions": true_pos,
        "true_quats": true_quats,
    }


def write_archive(
    cap: dict, path: str, focal_full: float | None = None
) -> dict:
    """Write a capture as a zip in the same layout as the real archives.

    The real odometry.csv header carries spaces after commas, which is why
    ingest parses it with skipinitialspace; that is reproduced here so the
    synthetic data exercises the same parsing path.
    """
    root = cap["truth"]["scan_id"]
    depth, conf = cap["depth"], cap["conf"]
    n = depth.shape[0]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(n):
            for sub, arr in (("depth", depth[i]), ("confidence", conf[i])):
                buf = io.BytesIO()
                Image.fromarray(arr).save(buf, format="PNG", optimize=False)
                zf.writestr(f"{root}/{sub}/{i:06d}.png", buf.getvalue())

        lines = [
            "timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy"
        ]
        for i in range(n):
            p, q = cap["poses"][i], cap["quats"][i]
            f, c = cap["focal"][i], cap["principal"][i]
            lines.append(
                f"{cap['timestamps'][i]:.6f}, {i}, {p[0]:.6f}, {p[1]:.6f}, {p[2]:.6f}, "
                f"{q[0]:.9f}, {q[1]:.9f}, {q[2]:.9f}, {q[3]:.9f}, "
                f"{f[0]:.6f}, {f[1]:.6f}, {c[0]:.6f}, {c[1]:.6f}"
            )
        zf.writestr(f"{root}/odometry.csv", "\n".join(lines) + "\n")

        # Static reference matrix, matching the real archives' shape.
        cx = float(np.mean(cap["principal"][:, 0]))
        cy = float(np.mean(cap["principal"][:, 1]))
        ff = (
            focal_full
            if focal_full is not None
            else float(cap["focal"][0][0]) * DEPTH_DECIMATION
        )
        zf.writestr(
            f"{root}/camera_matrix.csv",
            f"{ff:.6f}, 0.0, {cx:.6f}\n0.0, {ff:.6f}, {cy:.6f}\n0.0, 0.0, 1.0\n",
        )

        imu = ["timestamp, ax, ay, az, gx, gy, gz"]
        for i in range(n):
            imu.append(f"{cap['timestamps'][i]:.6f}, 0.0, 0.0, 9.81, 0.0, 0.0, 0.0")
        zf.writestr(f"{root}/imu.csv", "\n".join(imu) + "\n")

    return cap["truth"]


def write_ground_truth(truth: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(truth, fh, indent=2)


def standard_rooms() -> dict[str, Room]:
    """Rooms chosen to exercise the gates rather than to look impressive.

    `nominal` is a plausible Indian living room with a door and a window.
    `wide` stresses the aspect ratio. `tall` stresses ceiling height. `tight`
    is small enough that a 0.85 m inset trajectory is a real constraint, and
    `noisy_walls` makes the walls non-orthogonal so a rectangle assumption
    cannot silently pass.
    """
    return {
        "nominal": Room(
            4.60, 3.40, 2.72,
            [
                {"face": "x1", "u0": 1.20, "u1": 2.10, "v0": 0.0, "v1": 2.05, "kind": "door"},
                {"face": "z1", "u0": 2.60, "u1": 4.00, "v0": 0.90, "v1": 2.20, "kind": "window"},
            ],
        ),
        "wide": Room(
            6.20, 3.10, 2.65,
            [{"face": "x0", "u0": 1.00, "u1": 2.00, "v0": 0.0, "v1": 2.10, "kind": "door"}],
        ),
        "tall": Room(
            3.80, 3.20, 3.05,
            [{"face": "z0", "u0": 1.40, "u1": 2.40, "v0": 0.0, "v1": 2.05, "kind": "door"}],
        ),
        "tight": Room(
            2.80, 2.40, 2.45,
            [{"face": "x1", "u0": 0.85, "u1": 1.75, "v0": 0.0, "v1": 2.00, "kind": "door"}],
        ),
        "noisy_walls": Room(
            4.20, 3.30, 2.80,
            [{"face": "x1", "u0": 1.30, "u1": 2.20, "v0": 0.0, "v1": 2.05, "kind": "door"}],
        ),
    }


def render_csv(rows: list[dict], fieldnames: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()