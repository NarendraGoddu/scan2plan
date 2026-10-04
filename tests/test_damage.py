"""Tests for the damage ground-truth generator and the resolution floor.

Deliberately *not* tested: `detect_damage` finding damage. It does not, and a test
asserting either success or failure would be pinning a number that changes for
unrelated reasons. The measured result (0 of 4 detected, 19 false positives) is
recorded in `scripts/benchmark_damage.py` output and in `docs/final_report.md`.

What is tested here is the part that is durable and that any future attempt needs:
the generator must report exact truth, and the resolution floor must be correct,
because together they are what makes a miss explicable rather than mysterious.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.damage import MIN_LOCAL_EXCESS_M, SEVERITY_BANDS_M, pixel_footprint_m, severity_for
from scan2plan.synth import (
    DAMAGE_KINDS,
    DEPTH_SCALE_M,
    Room,
    _damage_offset,
    _plane_hit,
    damage_rooms,
    standard_rooms,
)


# --- generator: truth must be exact ---------------------------------------


def test_damage_ground_truth_reports_exact_extents():
    room = Room(
        4.0, 3.0, 2.7,
        damage=[{"face": "x1", "kind": "crack", "u0": 1.0, "u1": 1.015,
                 "v0": 0.5, "v1": 1.7, "depth_m": 0.012}],
    )
    (d,) = room.ground_truth()["damage"]
    assert d["kind"] == "crack"
    # Long axis is the vertical extent here, and both are reported unrounded.
    assert d["length_m"] == pytest.approx(1.2, abs=1e-6)
    assert d["width_m"] == pytest.approx(0.015, abs=1e-6)
    assert d["depth_m"] == pytest.approx(0.012, abs=1e-6)
    assert d["area_m2"] == pytest.approx(0.018, abs=1e-6)
    # Centre must be on the face it belongs to, or localisation cannot be scored.
    assert d["centre_world"] == pytest.approx([4.0, 1.1, 1.0075], abs=1e-6)


def test_standard_rooms_have_no_damage_so_the_geometry_benchmark_is_unchanged():
    """Damage cases must not leak into the published 5-room geometry benchmark."""
    for name, room in standard_rooms().items():
        assert room.damage == [], f"{name} gained damage"


def test_damage_rooms_are_separate_from_standard_rooms():
    assert set(damage_rooms()) & set(standard_rooms()) == set()


@pytest.mark.parametrize(
    "spec,message",
    [
        ({"face": "nope", "u0": 0, "u1": 1, "v0": 0, "v1": 1}, "damage face"),
        ({"face": "x1", "u0": 1, "u1": 0, "v0": 0, "v1": 1}, "u-range"),
        ({"face": "x1", "u0": 0, "u1": 1, "v0": 0, "v1": 99}, "v-range"),
        ({"face": "x1", "kind": "melt", "u0": 0, "u1": 1, "v0": 0, "v1": 1}, "kind"),
        ({"face": "x1", "u0": 0, "u1": 1, "v0": 0, "v1": 1, "depth_m": -1}, "depth"),
    ],
)
def test_bad_damage_spec_is_rejected(spec, message):
    with pytest.raises(ValueError, match=message):
        Room(4.0, 3.0, 2.7, damage=[spec])


# --- renderer: a groove must move the range, a breach must not return ------


def test_groove_pushes_the_range_behind_the_wall_plane():
    room = damage_rooms()["damaged"]
    lo, hi = room.bounds
    origin = np.array([3.0, 1.45, 1.80])          # facing the x0 spall
    direction = np.array([-1.0, 0.0, 0.0])
    t, axis, hit = _plane_hit(origin, direction[None, :], lo, hi)
    extra, breach = _damage_offset(room, axis, hit)
    assert t[0] == pytest.approx(3.0)
    assert extra[0] == pytest.approx(0.030)
    assert not breach[0]


def test_breach_returns_nothing_rather_than_a_deep_range():
    """A breach must be indistinguishable from a door reveal, which is the point."""
    room = damage_rooms()["damaged"]
    lo, hi = room.bounds
    # The z0 breach is at u 2.20-2.35, and on a z face u is world x, so the ray has
    # to sit at x ~ 2.275 heading -z. Aiming at x=1.0 misses it entirely.
    origin = np.array([2.275, 1.50, 2.0])
    direction = np.array([0.0, 0.0, -1.0])
    t, axis, hit = _plane_hit(origin, direction[None, :], lo, hi)
    extra, breach = _damage_offset(room, axis, hit)
    assert breach[0]
    assert extra[0] == pytest.approx(0.0)


def test_a_ray_missing_all_damage_is_untouched():
    room = damage_rooms()["damaged"]
    lo, hi = room.bounds
    origin = np.array([3.0, 0.30, 0.20])          # low and far from every defect
    direction = np.array([-1.0, 0.0, 0.0])
    t, axis, hit = _plane_hit(origin, direction[None, :], lo, hi)
    extra, breach = _damage_offset(room, axis, hit)
    assert extra[0] == pytest.approx(0.0)
    assert not breach[0]


def test_all_damage_kinds_are_known():
    assert {"crack", "spall", "breach"} == DAMAGE_KINDS


# --- the resolution floor --------------------------------------------------


class _FakeCapture:
    def __init__(self, focal_full: float) -> None:
        self.focal = np.array([[focal_full, focal_full]])


def test_pixel_footprint_is_range_over_focal_on_the_depth_stream():
    from scan2plan.ingest import DEPTH_DECIMATION

    cap = _FakeCapture(1600.0)
    # 11.7 mm at 2.5 m is the figure quoted in the report and in damage.py; if this
    # changes, every "w/px" column in the benchmark changes with it.
    assert pixel_footprint_m(cap, 0, 2.5) == pytest.approx(2.5 / (1600.0 / DEPTH_DECIMATION))
    assert pixel_footprint_m(cap, 0, 2.5) * 1000 == pytest.approx(11.72, abs=0.05)


def test_footprint_grows_with_range_so_far_walls_are_worse():
    cap = _FakeCapture(1600.0)
    assert pixel_footprint_m(cap, 0, 4.0) > pixel_footprint_m(cap, 0, 2.0)


def test_synthetic_cracks_are_subpixel_and_the_spall_is_not():
    """The claim the whole negative result rests on, pinned against the generator."""
    cap = _FakeCapture(1600.0)
    fp = pixel_footprint_m(cap, 0, 2.5)
    dmg = damage_rooms()["damaged"].ground_truth()["damage"]
    # Keyed by depth, not by kind: there are two cracks and a dict keyed on "crack"
    # would silently keep only the second one.
    widths = {round(d["depth_m"], 4): d["width_m"] for d in dmg}
    assert widths[0.004] / fp < 1.0    # 4 mm crack: 0.68 px, unobservable
    assert widths[0.012] / fp < 2.0    # 12 mm crack: 1.28 px, marginal
    assert widths[0.030] / fp > 10.0   # 30 mm spall: 25.6 px, and still missed


# --- severity triage -------------------------------------------------------


@pytest.mark.parametrize(
    "depth_mm,expected",
    [
        (0.0, "hairline"),
        (SEVERITY_BANDS_M[0] * 1000 - 1e-3, "hairline"),
        (SEVERITY_BANDS_M[0] * 1000, "minor"),
        (SEVERITY_BANDS_M[1] * 1000, "moderate"),
        (SEVERITY_BANDS_M[2] * 1000, "severe"),
        (500.0, "severe"),
    ],
)
def test_severity_bands_are_ordered_and_inclusive_at_the_bottom(depth_mm, expected):
    assert severity_for(depth_mm / 1000) == expected


def test_local_excess_floor_exceeds_single_frame_depth_noise():
    """MIN_LOCAL_EXCESS_M exists because one frame cannot resolve less than this.

    Depth noise is 4 mm at 1 m growing as 4*(1+0.55t), so ~9.5 mm at 2.5 m. A floor
    below that would flag noise; the constant is 15 mm, deliberately above it.
    """
    noise_at_2p5_m = 0.004 * (1.0 + 0.55 * 2.5)
    assert noise_at_2p5_m < MIN_LOCAL_EXCESS_M


def test_depth_scale_is_a_millimetre_so_generated_truth_is_in_the_right_units():
    assert pytest.approx(0.001) == DEPTH_SCALE_M
