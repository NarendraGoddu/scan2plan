"""Tests for plane segmentation and the horizontal-surface consolidation.

Synthetic planes stand in for real scans here so the assertions can be exact;
the end-to-end behaviour on the real archives is checked separately in
scripts/run_segmentation.py.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.geometry import fit_plane_ransac
from scan2plan.segment import (
    Plane,
    PlaneObservation,
    canonical_plane,
    classify_planes,
    cluster_planes,
    consolidate_horizontal,
    fit_frame_planes,
    orient_from_horizontal_planes,
)

DATA = os.environ.get(
    "SCAN2PLAN_SAMPLE_DATA", r"C:\Users\adity\OneDrive\Desktop\Narendra\Sample Data"
)
ARCHIVES = {
    "single_room": "single_room.zip",
    "floor_only": "single_scan_floor_only.zip",
    "with_ceiling": "single_scan_with_ceiling.zip",
}
needs_data = pytest.mark.skipif(
    not os.path.isdir(DATA), reason="sample data not present"
)


def make_box_points(rng, half_x, half_z, y, n=6000):
    """Points on the four walls of a rectangular room at height `y`."""
    pts = []
    for sign in (-1, 1):
        x = rng.uniform(-half_x, half_x, n // 2)
        z = sign * half_z + rng.normal(0, 0.002, n // 2)
        pts.append(np.column_stack([x, np.full(n // 2, y), z]))
        z2 = rng.uniform(-half_z, half_z, n // 2)
        x2 = sign * half_x + rng.normal(0, 0.002, n // 2)
        pts.append(np.column_stack([x2, np.full(n // 2, y), z2]))
    return np.concatenate(pts)


def test_canonical_plane_is_sign_stable():
    n1, d1 = canonical_plane(np.array([0.0, -1.0, 0.0]), 2.5)
    n2, d2 = canonical_plane(np.array([0.0, 1.0, 0.0]), -2.5)
    assert np.allclose(n1, n2)
    assert d1 == pytest.approx(d2)
    # The same physical plane must not depend on which side it was seen from.
    assert abs(abs(float(n1 @ n2)) - 1.0) < 1e-9


def test_cluster_merges_same_plane_across_frames():
    """Observations of one plane from many frames must become one plane."""
    rng = np.random.default_rng(3)
    obs = []
    for f in range(12):
        n = np.array([0.0, 1.0, 0.0]) + rng.normal(0, 0.004, 3)
        d = -1.50 + rng.normal(0, 0.006)
        pts = np.column_stack([
            rng.uniform(-2, 2, 60), np.full(60, d), rng.uniform(-2, 2, 60)
        ])
        obs.append(PlaneObservation(normal=n, offset=d, frame=f, n_inliers=900,
                                    rms_m=0.004, points=pts))
    planes = cluster_planes(obs, n_frames_total=12)
    assert len(planes) == 1
    assert planes[0].support == 12
    assert planes[0].angle_to(np.array([0.0, 1.0, 0.0])) < 2.0


def test_cluster_separates_parallel_distinct_planes():
    """Two collinear walls at different offsets must not merge."""
    rng = np.random.default_rng(4)
    obs = []
    for f in range(8):
        for d in (-1.5, 3.2):
            pts = np.column_stack([
                rng.uniform(-2, 2, 50), np.full(50, d), rng.uniform(-2, 2, 50)
            ])
            obs.append(PlaneObservation(normal=np.array([0.0, 1.0, 0.0]), offset=d,
                                        frame=f, n_inliers=800, rms_m=0.003, points=pts))
    planes = cluster_planes(obs, n_frames_total=8)
    assert len(planes) == 2
    assert sorted(p.support for p in planes) == [8, 8]


def test_fit_frame_planes_extracts_multiple_planes():
    rng = np.random.default_rng(5)
    pts = np.concatenate([
        np.column_stack([rng.uniform(-2, 2, 2500), np.full(2500, 0.0),
                         rng.uniform(-2, 2, 2500)]),
        np.column_stack([np.full(2500, 2.0), rng.uniform(-2, 2, 2500),
                         rng.uniform(-2, 2, 2500)]),
    ])
    obs = fit_frame_planes(pts, frame=0, max_planes=2, min_inliers=500)
    assert len(obs) >= 2
    assert all(o.n_inliers >= 500 for o in obs)
    assert all(o.points.shape[0] > 0 for o in obs)


def _horizontal_plane(offset, support, kind_unknown=True):
    return Plane(normal=np.array([0.0, 1.0, 0.0]), offset=offset, support=support,
                 n_frames_total=100, rms_m=0.01)


def test_orient_flips_axis_when_planes_are_above_camera():
    """A floor observed with an inverted vertical must still read as below."""
    up = np.array([0.0, 1.0, 0.0])  # actually pointing DOWN in this frame
    # Plane sits at y = +1.4, camera at y = 0. Along `up` that is +1.4, i.e.
    # "above" -- but physically it is the floor, so the axis must flip.
    planes = [_horizontal_plane(1.4, 40), _horizontal_plane(-1.4, 3)]
    v, info = orient_from_horizontal_planes(planes, up, cam_h=0.0)
    assert info["flipped"]
    assert v[1] == pytest.approx(-1.0)


def test_orient_keeps_axis_when_planes_are_below_camera():
    up = np.array([0.0, 1.0, 0.0])
    planes = [_horizontal_plane(-1.4, 40), _horizontal_plane(1.4, 3)]
    v, info = orient_from_horizontal_planes(planes, up, cam_h=0.0)
    assert not info["flipped"]
    assert v[1] == pytest.approx(1.0)


def test_consolidate_folds_fragmented_floor_into_one():
    """Four noisy floor observations must become one floor, not four."""
    up = np.array([0.0, 1.0, 0.0])
    planes = [
        _horizontal_plane(-0.63, 8), _horizontal_plane(-0.99, 19),
        _horizontal_plane(-1.45, 4), _horizontal_plane(-1.57, 10),
    ]
    for p in planes:
        p.kind = "floor"
    merged = consolidate_horizontal(planes, "floor", up)
    assert merged is not None
    assert merged.kind == "floor"
    # The best-supported member anchors the consensus; spread is reported.
    assert merged.n_consensus_members >= 2
    assert merged.member_height_spread_m > 0.0


def test_classify_separates_floor_ceiling_walls():
    up = np.array([0.0, 1.0, 0.0])
    planes = [
        Plane(normal=up, offset=-1.4, support=30, n_frames_total=50, rms_m=0.01),
        Plane(normal=up, offset=1.1, support=25, n_frames_total=50, rms_m=0.01),
        Plane(normal=np.array([1.0, 0.0, 0.0]), offset=2.0, support=20,
              n_frames_total=50, rms_m=0.02),
    ]
    classify_planes(planes, up, camera_height=0.0)
    kinds = {round(p.offset, 2): p.kind for p in planes}
    assert kinds[-1.4] == "floor"
    assert kinds[1.1] == "ceiling"
    assert kinds[2.0] == "wall"


@needs_data
def test_real_capture_yields_plausible_room():
    """End-to-end on a real archive: one floor, walls, sane camera height."""
    from scan2plan.ingest import load_zip
    from scan2plan.segment import segment_capture

    cap = load_zip(os.path.join(DATA, ARCHIVES["single_room"]), frame_stride=8)
    res = segment_capture(cap, 0.0009997, frame_stride=8, point_stride=4)
    planes = res["planes"]
    assert planes, "no planes recovered"

    floors = [p for p in planes if p.kind == "floor"]
    assert len(floors) == 1, f"a room has one floor, got {len(floors)}"
    walls = [p for p in planes if p.kind == "wall"]
    assert len(walls) >= 3, f"expected several walls, got {len(walls)}"

    # Floor must sit at handheld height below the camera, and be horizontal.
    floor = floors[0]
    assert floor.angle_to(res["up"]) < 20.0
    assert 0.6 < abs(float(floor.offset * (floor.normal @ res["up"])) - res["camera_height_m"]) < 2.2


@needs_data
def test_ceiling_height_is_physically_plausible():
    """with_ceiling observes both surfaces; their separation must be sane."""
    from scan2plan.ingest import load_zip
    from scan2plan.segment import segment_capture

    cap = load_zip(os.path.join(DATA, ARCHIVES["with_ceiling"]), frame_stride=8)
    res = segment_capture(cap, 0.0009997, frame_stride=8, point_stride=4)
    planes, up = res["planes"], res["up"]

    floors = [p for p in planes if p.kind == "floor"]
    ceils = [p for p in planes if p.kind == "ceiling"]
    assert floors, "no floor found"
    assert ceils, "no ceiling found"

    f = floors[0]
    c = ceils[0]
    f_h = float(f.offset * (f.normal @ up))
    c_h = float(c.offset * (c.normal @ up))
    height = c_h - f_h
    assert 2.0 < height < 3.2, f"implausible ceiling height {height:.3f} m"

# --- floor selection is constrained by where the camera is ----------------
#
# Every horizontal surface below the camera is classified "floor", which includes
# beds, tables and counters. Choosing among them by support alone is unstable: on
# with_ceiling the best-supported candidate moved from -1.488 m (support 301) over
# the whole walk to -0.908 m (support 147) over the second half alone, putting the
# two halves 801 mm apart on floor height. A surface 0.87 m below the lens is not
# the floor of a room anyone was standing in.


def test_low_surface_cannot_beat_the_real_floor():
    up = np.array([0.0, 1.0, 0.0])
    real_floor = _horizontal_plane(-1.53, 80)
    furniture = _horizontal_plane(-0.87, 147)   # higher support, but too low
    for p in (real_floor, furniture):
        p.kind = "floor"
    merged = consolidate_horizontal([real_floor, furniture], "floor", up,
                                    camera_height_m=0.0)
    assert merged is not None
    assert merged.offset == pytest.approx(-1.53, abs=0.02)


def test_support_still_wins_among_plausible_candidates():
    up = np.array([0.0, 1.0, 0.0])
    weak = _horizontal_plane(-1.40, 10)
    strong = _horizontal_plane(-1.55, 300)
    for p in (weak, strong):
        p.kind = "floor"
    merged = consolidate_horizontal([weak, strong], "floor", up,
                                    camera_height_m=0.0)
    assert merged.offset == pytest.approx(-1.55, abs=0.05)


def test_without_camera_height_the_old_behaviour_is_unchanged():
    """The no-camera path must still pick on support alone, or the synthetic
    unit tests and any caller without odometry change behaviour silently."""
    up = np.array([0.0, 1.0, 0.0])
    low = _horizontal_plane(-0.87, 147)
    high = _horizontal_plane(-1.53, 80)
    for p in (low, high):
        p.kind = "floor"
    merged = consolidate_horizontal([low, high], "floor", up)
    assert merged.offset == pytest.approx(-0.87, abs=0.02)


def test_no_plausible_candidate_falls_back_instead_of_failing():
    """Every surface too low, or too high: return something and say so."""
    up = np.array([0.0, 1.0, 0.0])
    only = _horizontal_plane(-0.30, 40)
    only.kind = "floor"
    merged = consolidate_horizontal([only], "floor", up, camera_height_m=0.0)
    assert merged is not None
    assert merged.offset == pytest.approx(-0.30, abs=0.01)


def test_ceiling_gate_uses_the_opposite_direction():
    up = np.array([0.0, 1.0, 0.0])
    good = _horizontal_plane(1.10, 90)
    too_low = _horizontal_plane(0.20, 200)
    for p in (good, too_low):
        p.kind = "ceiling"
    merged = consolidate_horizontal([good, too_low], "ceiling", up,
                                    camera_height_m=0.0)
    assert merged.offset == pytest.approx(1.10, abs=0.02)
