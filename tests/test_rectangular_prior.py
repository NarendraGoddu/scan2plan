"""The opt-in right-angled-room prior.

**Read the circularity warning in `select_rectangular_boundary` before quoting any
number produced with this on.** Every synthetic room in `synth.standard_rooms()` is
rectangular by construction, so running the benchmark with the prior enabled proves
nothing about whether the pipeline can find a rectangle on its own. The prior exists
for the real sample archives, where 63 consensus wall planes fragment into 8-9
boundary walls including 0.166 m slivers and there is no other way to choose.

The tests below therefore check two separate things:

  * that the prior does what it claims on a room it is allowed to assume is
    rectangular, and
  * that the default path is untouched, so the benchmark cannot silently acquire
    the assumption.
"""

from __future__ import annotations

import inspect
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.plan import (  # noqa: E402
    RoomPlan,
    Wall,
    build_room_plan,
    orientation_groups,
    select_rectangular_boundary,
    select_room_boundary,
)

# A 6.0 x 4.0 m room. The camera path stays well inside, so it never straddles a
# true wall -- it only straddles the clutter we plant in the middle.
ROOM_X, ROOM_Y = 3.0, 2.0
CAM = np.column_stack(
    [np.linspace(-1.0, 1.0, 9), np.linspace(-0.5, 0.5, 9)]
)


def wall(nx, ny, offset, support=10, inward=True):
    n = np.array([nx, ny], dtype=np.float64)
    return Wall(normal=n / np.linalg.norm(n), offset=offset, support=support,
                rms_m=0.01, inward=inward)


def true_walls():
    """The four walls of the room, as `wall_from_plane` would emit them."""
    return [
        wall(1, 0, -ROOM_X), wall(1, 0, ROOM_X),
        wall(0, 1, -ROOM_Y), wall(0, 1, ROOM_Y),
    ]


def clutter():
    """Furniture and slivers: what a real capture actually produces."""
    out = [
        # Straddled by the camera path, so furniture standing in the room.
        wall(1, 0, 0.0, support=40),
        wall(0, 1, 0.0, support=35),
        # Near-duplicates just inside each true wall.
        wall(1, 0, -ROOM_X + 0.1, support=8),
        wall(0, 1, ROOM_Y - 0.1, support=7),
        # The failure mode being fixed: extra orientation groups well away from
        # both true axes. These must clear PARALLEL_TOL_DEG (12 deg) or they are
        # merely absorbed into the x/y groups and change nothing -- which is itself
        # worth knowing, and is why the shape-agnostic overcount needs angles this
        # far out to reproduce at all.
        wall(math.cos(math.radians(25)), math.sin(math.radians(25)), 1.4, support=6),
        wall(math.cos(math.radians(-30)), math.sin(math.radians(-30)), -1.1, support=5),
    ]
    return out


# --- the prior does what it claims ----------------------------------------


def test_prior_returns_exactly_four_walls():
    kept, _notes = select_rectangular_boundary(true_walls() + clutter(), CAM)
    assert len(kept) == 4


def test_shape_agnostic_path_is_the_one_that_overcounts():
    """The problem being fixed, asserted so the prior cannot be quietly dropped."""
    kept, _notes = select_room_boundary(true_walls() + clutter(), CAM)
    assert len(kept) > 4


def test_prior_keeps_the_outermost_wall_on_each_side():
    kept, _notes = select_rectangular_boundary(true_walls() + clutter(), CAM)
    offsets = sorted(round(w.offset, 3) for w in kept)
    assert offsets == sorted([-ROOM_X, ROOM_X, -ROOM_Y, ROOM_Y])


def test_prior_discards_furniture_straddled_by_the_path():
    _kept, notes = select_rectangular_boundary(true_walls() + clutter(), CAM)
    assert any("middle of the room" in n for n in notes)


def test_prior_chooses_axes_that_are_nearly_perpendicular():
    kept, notes = select_rectangular_boundary(true_walls() + clutter(), CAM)
    assert len(kept) == 4
    # Two axes, recovered from support alone -- not told the answer.
    axes = orientation_groups(kept)
    assert len(axes) == 2
    angle = math.degrees(
        math.acos(min(1.0, abs(float(axes[0][0].normal @ axes[1][0].normal))))
    )
    assert abs(angle - 90.0) < 5.0


def test_prior_states_that_it_is_an_assumption():
    _kept, notes = select_rectangular_boundary(true_walls() + clutter(), CAM)
    assert any("assumes a right-angled room" in n for n in notes)


def test_prior_on_a_clean_rectangle_changes_nothing():
    """With no clutter to remove, the prior is a no-op -- it is not magic."""
    kept, _notes = select_rectangular_boundary(true_walls(), CAM)
    assert len(kept) == 4


# --- it must fail loudly, not silently -------------------------------------


def test_falls_back_when_there_is_only_one_orientation():
    flat = [wall(1, 0, o) for o in (-3.0, 0.0, 3.0)]
    kept, notes = select_rectangular_boundary(flat, CAM)
    assert any("needs two orientations" in n for n in notes)
    assert len(kept) >= 1


def test_falls_back_when_no_pair_is_perpendicular():
    skew = [wall(math.cos(math.radians(a)), math.sin(math.radians(a)), o)
            for a, o in ((0, -3.0), (10, -1.0), (20, 1.0), (30, 3.0))]
    _kept, notes = select_rectangular_boundary(skew, CAM)
    assert any("perpendicular" in n for n in notes)


def test_too_few_walls_is_handled_without_crashing():
    """Degenerate input must not raise. One wall cannot bound a room, so the
    prior declines and the shape-agnostic path returns what it always did."""
    one = [wall(1, 0, -3.0)]
    kept, notes = select_rectangular_boundary(one, CAM)
    assert len(kept) == 1
    assert any("NOT applied" in n for n in notes)


# --- the default must stay untouched ---------------------------------------


def test_build_room_plan_defaults_the_prior_off():
    """The single most important test in this file.

    If the prior were on by default, every synthetic benchmark number in the report
    would silently be circular without anyone noticing.
    """
    default = inspect.signature(build_room_plan).parameters["rectangular"].default
    assert default is False


def test_plan_reports_whether_the_prior_was_applied():
    plan = RoomPlan(basis_a=np.array([1.0, 0, 0]), basis_b=np.array([0, 1.0, 0]),
                    up=np.array([0.0, 0.0, 1.0]))
    assert plan.to_dict()["rectangular_prior_applied"] is False
    plan.rectangular_prior = True
    assert plan.to_dict()["rectangular_prior_applied"] is True


def test_wall_equality_is_not_used_for_membership():
    """`w in list` would raise on a Wall, because == compares numpy arrays.

    Caught as a real ValueError ("truth value of an array is ambiguous") the first
    time the prior ran; pinned here so it cannot come back.
    """
    import pytest as _pytest

    with _pytest.raises(ValueError):
        _ = wall(1, 0, -3.0) in [wall(1, 0, 3.0)]
