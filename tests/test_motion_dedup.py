"""Motion-based frame selection.

`motion_dedup` exists because index-based subsampling is the wrong tool for a
handheld capture recorded at 60 Hz. It is *not* the default, because on these
archives it measurably hurts wall selection -- see the negative result recorded
in CHECKPOINTS.md. These tests pin the behaviour it does guarantee.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.ingest import motion_dedup  # noqa: E402


def straight_line(n, step=0.01):
    """Camera walking along +x in `step` metre increments, identity rotation."""
    pos = np.zeros((n, 3))
    pos[:, 0] = np.arange(n) * step
    quat = np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (n, 1))
    return pos, quat


def test_endpoints_are_always_kept():
    """The path must span the whole capture, so first and last always survive."""
    pos, quat = straight_line(10)
    kept = motion_dedup(pos, quat, min_translation_m=1.0)
    assert kept.tolist() == [0, 9]


def test_drops_frames_below_the_translation_threshold():
    # 100 frames, 1 cm apart, threshold 5 cm -> about every fifth frame survives.
    pos, quat = straight_line(100, step=0.01)
    kept = motion_dedup(pos, quat, min_translation_m=0.05, min_rotation_deg=180.0)
    assert 15 <= len(kept) <= 25, len(kept)
    assert kept[0] == 0


def test_keeps_every_frame_when_every_frame_moved_enough():
    pos, quat = straight_line(20, step=0.5)
    kept = motion_dedup(pos, quat, min_translation_m=0.05, min_rotation_deg=180.0)
    assert len(kept) == 20


def test_stationary_camera_keeps_only_the_endpoints():
    """Nothing new is seen, so only the first and last frames survive."""
    pos = np.zeros((50, 3))
    quat = np.tile(np.array([0.0, 0.0, 0.0, 1.0]), (50, 1))
    kept = motion_dedup(pos, quat, min_translation_m=0.05)
    assert kept.tolist() == [0, 49]


def test_rotation_alone_keeps_a_frame():
    """Turning on the spot is a new viewpoint even with no translation."""
    n = 40
    pos = np.zeros((n, 3))
    quat = np.zeros((n, 4))
    for i in range(n):
        ang = np.radians(i * 5.0)  # 5 deg per frame
        quat[i] = [0.0, 0.0, np.sin(ang / 2), np.cos(ang / 2)]
    kept = motion_dedup(pos, quat, min_translation_m=0.05, min_rotation_deg=2.0)
    assert len(kept) == n


def test_quaternion_sign_flip_is_not_treated_as_rotation():
    """q and -q are the same rotation; a sign flip must not look like a turn."""
    n = 20
    pos = np.zeros((n, 3))
    quat = np.zeros((n, 4))
    for i in range(n):
        s = 1.0 if i % 2 == 0 else -1.0
        quat[i] = s * np.array([0.0, 0.0, 0.0, 1.0])
    kept = motion_dedup(pos, quat, min_translation_m=0.05, min_rotation_deg=2.0)
    assert kept.tolist() == [0, n - 1]


def test_indices_restricts_and_preserves_disjointness():
    """The repeatability check dedups each half independently; they must not overlap."""
    pos, quat = straight_line(100, step=0.01)
    first = motion_dedup(pos, quat, min_translation_m=0.05, indices=np.arange(0, 50))
    second = motion_dedup(pos, quat, min_translation_m=0.05, indices=np.arange(50, 100))
    assert set(first).isdisjoint(set(second))
    assert first.max() < 50 <= second.min()


def test_indices_none_covers_everything():
    pos, quat = straight_line(60, step=0.01)
    kept = motion_dedup(pos, quat, min_translation_m=0.05, indices=None)
    assert kept.max() == 59


def test_empty_indices_returns_empty():
    pos, quat = straight_line(5)
    assert motion_dedup(pos, quat, indices=np.array([], dtype=np.int64)).size == 0


def test_output_is_sorted_and_unique():
    pos, quat = straight_line(200, step=0.03)
    kept = motion_dedup(pos, quat, min_translation_m=0.05)
    assert kept.dtype.kind == "i"
    assert np.all(np.diff(kept) > 0), "indices must be strictly increasing"


@pytest.mark.parametrize("threshold", [0.02, 0.05, 0.10])
def test_returned_indices_are_always_valid(threshold):
    pos, quat = straight_line(50, step=0.01)
    kept = motion_dedup(pos, quat, min_translation_m=threshold)
    assert kept.min() >= 0
    assert kept.max() < len(pos)
