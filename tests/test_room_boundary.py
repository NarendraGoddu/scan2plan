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

from scan2plan.plan import (  # noqa: E402
    WALL_CROSS_SLACK_M,
    Wall,
    match_walls,
    plane_offset_residual,
    select_room_boundary,
)


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


# --- matching walls between two independent fits ---------------------------
#
# The split-half repeatability metric used to bucket walls by normal direction modulo
# 180 degrees and average each bucket. A plane's normal sign is arbitrary, so that fold
# merged every pair of opposite walls, and then each fit averaged a *different* set of
# walls into the same key. On `single_room` one bucket held offsets -1.108 m and
# +3.569 m and was reported as +1.230 m, and the resulting "6071 mm disagreement"
# compared two surfaces that were never the same wall.


def test_opposite_walls_match_their_own_counterpart_not_each_other():
    """Two walls facing opposite ways must not be paired with one another."""
    # +x wall far out, -x wall near the origin: 4.6 m apart.
    first = [wall([1, 0, 0], 4.60), wall([-1, 0, 0], 0.005)]
    # Second fit sees the same two surfaces, normals as fitted independently.
    second = [wall([-1, 0, 0], -0.005), wall([1, 0, 0], 4.62)]
    pairs, unmatched_a, unmatched_b = match_walls(first, second)
    assert len(pairs) == 2
    assert (unmatched_a, unmatched_b) == (0, 0)
    # first[0] is the +x wall, so it must land on second[1], not second[0].
    assert (pairs[0][0], pairs[0][1]) == (0, 1)
    assert all(resid < 0.05 for _, _, resid in pairs)


def test_plane_offset_residual_is_sign_aware():
    """`n·p = d` and `-n·p = -d` are the same plane, so offsets must flip with it."""
    same = wall([1, 0, 0], 4.60)
    flipped = wall([-1, 0, 0], -4.58)
    assert plane_offset_residual(same, flipped) == pytest.approx(0.02, abs=1e-9)
    # Comparing raw offsets, as the old metric did, gives 9.18 m for the same pair.
    assert abs(same.offset - flipped.offset) == pytest.approx(9.18, abs=1e-9)


def test_a_wall_missing_from_one_fit_is_reported_not_averaged_away():
    """One fit sees three walls, the other two. That is the finding, not noise.

    The old metric put all three in one bucket and averaged to 3.07 m against the
    other fit's 1.02 m, reporting 2.05 m of "wall disagreement" that was really just
    one undetected wall.
    """
    first = [wall([1, 0, 0], 4.60), wall([0, 1, 0], 0.005), wall([-1, 0, 0], -3.07)]
    second = [wall([1, 0, 0], 4.61), wall([0, 1, 0], 0.01)]
    pairs, unmatched_a, unmatched_b = match_walls(first, second)
    assert len(pairs) == 2
    assert (unmatched_a, unmatched_b) == (1, 0)


def test_distinct_parallel_walls_do_not_match():
    """Same direction, 4 m apart, is two surfaces and not one."""
    pairs, unmatched_a, unmatched_b = match_walls(
        [wall([1, 0, 0], 4.60)], [wall([1, 0, 0], 0.60)]
    )
    assert pairs == []
    assert (unmatched_a, unmatched_b) == (1, 1)


def test_each_wall_is_matched_at_most_once():
    """One wall in B cannot be the counterpart of two walls in A."""
    pairs, _, _ = match_walls(
        [wall([1, 0, 0], 4.60), wall([1, 0, 0], 4.64)], [wall([1, 0, 0], 4.62)]
    )
    assert len(pairs) == 1
    assert pairs[0][1] == 0


def test_perpendicular_walls_do_not_match():
    pairs, _, _ = match_walls([wall([1, 0, 0], 2.0)], [wall([0, 1, 0], 2.0)])
    assert pairs == []
