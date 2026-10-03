"""Tests for ingest and depth-scale calibration.

These run against the real sample archives, which are fetched rather than
committed (see scripts/fetch_sample_data.py).
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.geometry import fit_plane_ransac
from scan2plan.ingest import DEPTH_DECIMATION, load_zip, poses_to_matrices, quat_to_rot

DATA = os.environ.get(
    "SCAN2PLAN_SAMPLE_DATA", r"C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data"
)
ARCHIVES = {
    "single_room": "single_room.zip",
    "floor_only": "single_scan_floor_only.zip",
    "with_ceiling": "single_scan_with_ceiling.zip",
}
needs_data = pytest.mark.skipif(
    not os.path.isdir(DATA), reason="sample data not present; run scripts/fetch_sample_data.py"
)


def test_quaternion_identity():
    q = np.array([0.0, 0.0, 0.0, 1.0])
    assert np.allclose(quat_to_rot(q), np.eye(3), atol=1e-12)


def test_quaternion_90deg_about_z():
    q = np.array([0.0, 0.0, np.sin(np.pi / 4), np.cos(np.pi / 4)])
    r = quat_to_rot(q)
    assert np.allclose(r @ np.array([1.0, 0, 0]), [0, 1, 0], atol=1e-9)


def test_ransac_recovers_known_plane():
    """A synthetic plane at a known offset must be recovered exactly."""
    rng = np.random.default_rng(7)
    n_true = np.array([0.0, 0.0, 1.0])
    pts = np.column_stack([
        rng.uniform(-2, 2, 4000),
        rng.uniform(-2, 2, 4000),
        2.75 + rng.normal(0, 0.002, 4000),
    ])
    n, d, inl = fit_plane_ransac(pts, inlier_thresh=0.01, max_iterations=300)
    assert abs(d - 2.75) < 0.005
    assert abs(abs(n @ n_true) - 1.0) < 1e-3
    assert inl.mean() > 0.9


@needs_data
@pytest.mark.parametrize("key", list(ARCHIVES))
def test_ingest_shapes_and_alignment(key):
    cap = load_zip(os.path.join(DATA, ARCHIVES[key]), frame_stride=200)
    assert cap.n_frames > 1
    assert cap.depth.shape[1:] == (192, 256)
    assert cap.confidence.shape == cap.depth.shape
    assert cap.positions.shape == (cap.n_frames, 3)
    assert cap.quats.shape == (cap.n_frames, 4)
    assert np.all(np.diff(cap.timestamps) > 0), "poses must be time-ordered"
    assert set(np.unique(cap.confidence)).issubset({0, 1, 2})


@needs_data
def test_depth_intrinsics_account_for_decimation():
    """The 7.5x depth decimation must be applied or every point is 7.5x wrong."""
    cap = load_zip(os.path.join(DATA, ARCHIVES["single_room"]), frame_stride=500)
    k = cap.depth_intrinsics(0)
    assert k[0, 0] == pytest.approx(cap.focal[0, 0] / DEPTH_DECIMATION)
    # Full-res focal is ~1600, so decimated must land near 213.
    assert 200 < k[0, 0] < 225


@needs_data
def test_per_frame_intrinsics_are_used_not_static():
    """Intrinsics drift frame to frame; the static file is only a reference."""
    cap = load_zip(os.path.join(DATA, ARCHIVES["with_ceiling"]), frame_stride=200)
    assert np.ptp(cap.focal[:, 0]) > 1.0, "expected per-frame focal drift"


@needs_data
def test_scale_calibration_recovers_one_millimetre():
    """Depth is uint16 millimetres. Verified independently on all three archives.

    A uniform scale error is invisible to plane fits (angles and the normalised
    plan shape are scale-invariant), so this is the only stage that can pin it.
    """
    from scan2plan.unproject import calibrate_scale

    for key in ARCHIVES:
        cap = load_zip(os.path.join(DATA, ARCHIVES[key]), frame_stride=20)
        res = calibrate_scale(cap, verbose=False)
        assert res["reliable"], f"{key}: calibration reported itself unreliable"
        assert res["scale_m_per_unit"] == pytest.approx(0.001, rel=0.02), (
            f"{key}: got {res['scale_m_per_unit']*1000:.4f} mm/unit"
        )


@needs_data
def test_calibration_declines_when_over_subsampled():
    """Too few frames leaves the objective noise-dominated; it must say so.

    Guarding against confidently reporting a wrong scale is the whole point of
    the reliability flag -- a subsampled with_ceiling returns 0.75 mm/unit on a
    flat 0.033 peak, which is a wrong answer dressed as a confident one.
    """
    from scan2plan.unproject import calibrate_scale

    cap = load_zip(os.path.join(DATA, ARCHIVES["with_ceiling"]), frame_stride=80)
    res = calibrate_scale(cap, verbose=False)
    assert not res["reliable"]
    assert res["peak_prominence"] < 0.04


@needs_data
def test_reconstructed_room_contains_camera_path():
    """Enclosure sanity check on the calibrated cloud.

    Independent of the plane-overlap objective: if the depth scale were wrong the
    room would be far larger (or smaller) than the walk that produced it.
    """
    from scan2plan.unproject import frame_points_world

    cap = load_zip(os.path.join(DATA, ARCHIVES["floor_only"]), frame_stride=40)
    T_cw = poses_to_matrices(cap.positions, cap.quats)
    idx = np.linspace(0, cap.n_frames - 1, 24).astype(int)
    cloud = np.concatenate(
        [frame_points_world(cap, int(f), 0.001, T_cw, stride=4) for f in idx], axis=0
    )
    path = cap.positions
    room = cloud.max(0) - cloud.min(0)
    walk = path.max(0) - path.min(0)
    # Every horizontal axis of the room must exceed the walk extent (the camera
    # cannot leave the room it is scanning).
    horiz = [a for a in range(3) if a != 1]
    for a in horiz:
        assert room[a] > walk[a] * 0.9, f"axis {a}: room {room[a]:.2f} m vs walk {walk[a]:.2f} m"