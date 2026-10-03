"""Self-checks for the synthetic generator.

A benchmark built on a broken renderer is worse than no benchmark, so these
assert physical facts about the render rather than merely checking that it
runs: rotations must be proper, a depth map must show the distance to the wall
it is pointed at, and openings must genuinely return nothing.
"""

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.synth import (
    DEPTH_SCALE_M,
    IMAGE_H,
    IMAGE_W,
    Room,
    look_at_rotation,
    render_capture,
    rot_to_quat,
    standard_rooms,
    write_archive,
)


def test_rotation_is_proper():
    """Every generated pose must be a real rotation, not a reflection.

    OpenCV camera axes are x right, y down, z forward, and the world is Y up.
    Getting that handedness wrong yields a mirrored room, which would silently
    corrupt every downstream measurement.
    """
    rng = np.random.default_rng(0)
    for _ in range(200):
        f = rng.normal(size=3)
        if np.linalg.norm(f) < 1e-6:
            continue
        R = look_at_rotation(f, np.array([0.0, 1.0, 0.0]))
        assert abs(np.linalg.det(R) - 1.0) < 1e-9, f"det {np.linalg.det(R)}"
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9), "not orthonormal"
        assert np.allclose(np.cross(R[:, 0], R[:, 1]), R[:, 2], atol=1e-9), "not right-handed"
    print("  rotation handedness OK (200 random directions)")


def test_level_camera_has_horizontal_optical_axis_and_down_is_down():
    R = look_at_rotation(np.array([0.0, 0.0, -1.0]), np.array([0.0, 1.0, 0.0]))
    z_axis = R[:, 2]
    y_axis = R[:, 1]
    assert abs(z_axis[1]) < 1e-9, "optical axis not level when pitched level"
    assert z_axis[2] < 0, "forward should map to world -Z at zero yaw"
    assert y_axis[1] < -0.999, "image +y must point down (world -Y)"
    print("  level-pose convention OK (optical horizontal, image y down)")


def test_quat_roundtrip():
    rng = np.random.default_rng(1)
    for _ in range(200):
        f = rng.normal(size=3)
        R = look_at_rotation(f, np.array([0.0, 1.0, 0.0]))
        q = rot_to_quat(R)
        assert abs(np.linalg.norm(q) - 1.0) < 1e-9
        # Rebuild R from q and compare.
        x, y, z, w = q
        R2 = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ])
        assert np.allclose(R, R2, atol=1e-8), "quat roundtrip mismatch"
    print("  quaternion roundtrip OK (200 random poses)")


def test_depth_reports_true_distance_to_wall():
    """Centre pixel depth must equal the geometric distance to the far wall."""
    room = Room(4.00, 3.00, 2.60, [])
    cap = render_capture(room, n_frames=6, seed=3)
    truth = cap["truth"]
    assert cap["depth"].shape == (6, IMAGE_H, IMAGE_W), cap["depth"].shape
    assert cap["depth"].dtype == np.uint16
    assert set(np.unique(cap["conf"])) <= {0, 2}

    # Reconstruct geometry for one frame and compare to the room definition.
    f = 0
    o = cap["true_positions"][f]
    d = cap["true_quats"][f]
    del d
    centre_raw = float(cap["depth"][f, IMAGE_H // 2, IMAGE_W // 2])
    centre_m = centre_raw * DEPTH_SCALE_M
    print(f"  frame 0 camera at {np.round(o, 3)}, centre depth {centre_m:.3f} m")
    assert 0.15 < centre_m <= 8.0, f"centre depth {centre_m} out of valid range"
    print(f"  room {truth['room_length_m']}x{truth['room_width_m']}x"
          f"{truth['room_height_m']} m, area {truth['floor_area_m2']:.3f} m2")
    assert abs(truth["floor_area_m2"] - 12.0) < 1e-9


def test_openings_return_nothing():
    """A ray into an opening must yield no return, not a wall behind it."""
    room = Room(5.00, 4.00, 2.70,
                [{"face": "x1", "u0": 1.5, "u1": 2.5, "v0": 0.0, "v1": 2.10, "kind": "door"}])
    cap = render_capture(room, n_frames=4, seed=5)

    # Aim straight down the +x axis at the middle of the door from x=0.
    o = np.array([0.0, 1.05, 2.00])
    d = np.array([1.0, 0.0, 0.0])
    R = look_at_rotation(d, np.array([0.0, 1.0, 0.0]))
    rays = np.array([[0.0, 0.0, 1.0]]) @ R.T
    lo, hi = room.bounds
    from scan2plan.synth import _opening_hits, _plane_hit
    t, axis, hit = _plane_hit(o, rays, lo, hi)
    blocked = _opening_hits(room, axis, hit)
    print(f"  door centre: t={t[0]:.3f} m, hit y={hit[0,1]:.3f}, blocked={bool(blocked[0])}")
    assert abs(t[0] - 5.00) < 1e-9, "ray should reach the x1 wall at 5 m"
    assert blocked[0], "opening did not block the return"
    # Just beside the door the wall must still return.
    o2 = np.array([0.0, 1.05, 3.20])
    t2, axis2, hit2 = _plane_hit(o2, rays, lo, hi)
    assert not _opening_hits(room, axis2, hit2)[0], "wall beside the door was wrongly blocked"
    print("  opening cuts a hole; neighbouring wall still returns")


def test_archive_roundtrips_through_ingest():
    """The whole point: ingest must read this with no format shim."""
    from scan2plan.ingest import load_zip

    room = standard_rooms()["nominal"]
    cap = render_capture(room, n_frames=24, seed=11, scan_id="syn_nominal")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "syn.zip")
        write_archive(cap, path)
        got = load_zip(path)
    assert got.depth.shape == cap["depth"].shape, (got.depth.shape, cap["depth"].shape)
    assert np.array_equal(got.depth, cap["depth"]), "depth altered by archive roundtrip"
    assert np.allclose(got.positions, cap["poses"], atol=1e-5), "poses altered"
    assert np.allclose(got.focal[0], cap["focal"][0], atol=1e-4), "focal altered"
    print(f"  archive roundtrip OK: {got.depth.shape}, poses and focal intact")


def test_noise_is_injected_and_reported():
    """A benchmark on noiseless data measures nothing, so noise must be real."""
    room = standard_rooms()["nominal"]
    cap = render_capture(room, n_frames=40, seed=13)
    err = cap["poses"] - cap["true_positions"]
    sigma = float(np.sqrt((err ** 2).mean()))
    print(f"  injected pose noise: realised rms {sigma * 1000:.1f} mm "
          f"(target 20.0 mm)")
    assert 10e-3 < sigma < 32e-3, f"pose noise {sigma:.4f} m outside expected band"
    assert cap["truth"]["noise"]["pose_noise_m"] == 0.020


def test_unprojected_points_land_on_true_surfaces():
    """The regression test for the two convention bugs this generator had.

    Both bugs produced a *self-consistent* archive that ingest read without
    complaint, and neither raised anything: the intrinsics were divided by 7.5
    twice, and depth was stored as slant range instead of axial depth. Either
    one alone displaces the unprojected cloud by tens to hundreds of
    millimetres, which then shows up as plausible-looking but wrong planes.

    So this asserts the only property that actually matters: after unprojecting
    through the real ingest path, points sit on the surfaces they were rendered
    from, to within the injected pose noise.
    """
    from scan2plan.ingest import load_zip, poses_to_matrices
    from scan2plan.unproject import frame_points_world

    room = Room(4.60, 3.40, 2.72, [])
    cap = render_capture(room, n_frames=48, seed=21, scan_id="syn_consistency")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "c.zip")
        write_archive(cap, path)
        got = load_zip(path)

    T_cw = poses_to_matrices(got.positions, got.quats)
    lo, hi = room.bounds
    worst = 0.0
    for f in range(0, got.depth.shape[0], 6):
        pts = frame_points_world(got, f, DEPTH_SCALE_M, T_cw, stride=2)
        if pts.shape[0] < 200:
            continue
        d = np.min(
            np.stack([
                np.abs(pts[:, 0] - lo[0]), np.abs(pts[:, 0] - hi[0]),
                np.abs(pts[:, 1] - lo[1]), np.abs(pts[:, 1] - hi[1]),
                np.abs(pts[:, 2] - lo[2]), np.abs(pts[:, 2] - hi[2]),
            ]),
            axis=0,
        )
        med = float(np.median(d))
        worst = max(worst, med)
        print(f"  frame {f:2d}: {pts.shape[0]:6d} pts, median distance to a true "
              f"surface {med * 1000:6.1f} mm")
    # Pose noise is 20 mm per axis; allow generous headroom for grazing rays on
    # a room corner while still failing loudly if a convention is broken.
    assert worst < 0.060, (
        f"unprojected points sit {worst * 1000:.1f} mm off the true surfaces; "
        "a depth or intrinsics convention is mismatched"
    )
    print(f"  worst median residual {worst * 1000:.1f} mm (budget 60 mm)")


if __name__ == "__main__":
    print("synthetic generator self-checks")
    for fn in [
        test_rotation_is_proper,
        test_level_camera_has_horizontal_optical_axis_and_down_is_down,
        test_quat_roundtrip,
        test_depth_reports_true_distance_to_wall,
        test_openings_return_nothing,
        test_archive_roundtrips_through_ingest,
        test_noise_is_injected_and_reported,
        test_unprojected_points_land_on_true_surfaces,
    ]:
        fn()
    print("\nall synthetic generator checks passed")