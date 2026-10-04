"""Wall-boundary selection: a doorway is not evidence of an interior wall.

Regression tests for a rule that was wrong in a way no synthetic room caught.

`select_room_boundary` used to discard any wall whose line the camera path
crossed by more than `WALL_CROSS_SLACK_M` on both sides, on the assumption that a
room boundary always has the camera entirely on one side. That is false whenever
the operator walks through a doorway or reaches in from outside: the path
legitimately crosses the wall plane through the opening.

On the 6.2 x 3.1 m synthetic room the operator's path spans 4.53 m along the
normal of the 3.11 m wall pair, so both side walls were discarded. Two unparallel
walls cannot bound a region, so the room collapsed to zero area and the benchmark
reported 0.000 m2 with a NaN span for that room.

The rule now discards a crossed wall only when an *uncrossed* wall stands further
out in the same direction, which is the furniture case the rule was written for.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.plan import WALL_CROSS_SLACK_M, Wall, select_room_boundary  # noqa: E402


def wall(normal, offset, support=100):
    n = np.asarray(normal, dtype=np.float64)
    return Wall(normal=n / np.linalg.norm(n), offset=float(offset),
                support=support, rms_m=0.005)


def rectangle(length=6.2, width=3.1):
    """Four walls of a rectangle in floor coordinates."""
    return [
        wall((1, 0), 0.0),        # x = 0
        wall((1, 0), length),     # x = length
        wall((0, 1), 0.0),        # y = 0
        wall((0, 1), width),      # y = width
    ]


def path(points):
    return np.asarray(points, dtype=np.float64)


def test_plain_interior_path_keeps_four_walls():
    cam = path([(x, y) for x in (0.4, 3.0, 5.8) for y in (0.4, 2.7)])
    kept, notes = select_room_boundary(rectangle(), cam)
    assert len(kept) == 4
    assert notes == []


def test_doorway_exit_keeps_all_four_walls():
    """The regression: the path leaves through the opening in the x = 0 wall.

    It crosses that wall by more than the slack on both sides, which the old rule
    read as "interior object". It also reaches past the y = width wall, as an
    operator does when they lean through a doorway.
    """
    cam = path([
        (3.0, 1.5),
        (-0.8, 1.5),    # stepped out through the doorway in x = 0
        (3.0, 1.5),
        (3.0, 3.9),     # leaned past the far wall
        (3.0, 1.0),
    ])
    d = cam @ np.array([1.0, 0.0]) - 0.0
    assert d.min() < -WALL_CROSS_SLACK_M, "test must actually straddle the wall"
    assert d.max() > WALL_CROSS_SLACK_M

    kept, _notes = select_room_boundary(rectangle(), cam)
    assert len(kept) == 4, "a doorway must not cost the room its walls"


def test_path_wider_than_the_room_keeps_all_four_walls():
    """Path spans 4.53 m along a normal whose wall pair is 3.11 m apart."""
    xs = np.linspace(-0.72, 3.81, 40)
    cam = path([(float(x), 1.5) for x in xs])
    kept, _notes = select_room_boundary(rectangle(), cam)
    assert len(kept) == 4


def x_offsets(kept):
    """Offsets of the walls whose normal lies along +x."""
    return sorted(round(float(w.offset), 2) for w in kept
                  if abs(abs(float(w.normal[0])) - 1.0) < 1e-6)


def test_crossed_wall_in_the_middle_of_the_room_is_discarded():
    """The furniture case: a wardrobe between the real walls, walked around."""
    walls = rectangle() + [wall((1, 0), 3.1, support=180)]
    cam = path([(x, 1.5) for x in np.linspace(1.0, 5.0, 30)])
    kept, notes = select_room_boundary(walls, cam)
    # The wardrobe at x = 3.1 is gone; the real walls at 0.0 and 6.2 remain.
    assert x_offsets(kept) == [0.0, 6.2]
    assert len(kept) == 4
    assert any("3.10 m" in n for n in notes), notes


def test_interior_parallel_wall_between_the_outer_pair_is_discarded():
    """An uncrossed wall between the outer pair is interior, by the outer-pair rule."""
    walls = rectangle() + [wall((1, 0), 2.4, support=90)]
    cam = path([(x, y) for x in (0.4, 3.0, 5.8) for y in (0.4, 2.7)])
    kept, notes = select_room_boundary(walls, cam)
    assert len(kept) == 4
    assert x_offsets(kept) == [0.0, 6.2]
    assert any("3.10 m" in n or "2.40 m" in n for n in notes), notes


def test_fewer_than_three_walls_is_passed_through_untouched():
    walls = [wall((1, 0), 0.0), wall((0, 1), 0.0)]
    cam = path([(1.0, 1.0), (-2.0, 3.0)])
    kept, notes = select_room_boundary(walls, cam)
    assert len(kept) == 2
    assert notes == []


@pytest.mark.parametrize("gap", [0.2, 0.5, 1.0])
def test_single_sided_straddle_is_never_crossed(gap):
    """A wall the path only approaches on one side is a wall, always.

    Walking up to a wall and leaning against it must never remove it.
    """
    cam = path([(1.0 + gap, 1.5), (3.0, 1.5), (5.0, 1.5)])
    kept, _notes = select_room_boundary(rectangle(), cam)
    assert len(kept) == 4
