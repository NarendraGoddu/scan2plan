"""Opening detection, against synthetic frames where the answer is known exactly.

The real photographs have no scale reference, so they can only ever be checked
against tape after the fact. Synthetic frames pin the geometry itself: if a
function claims the opening spans 60% of frame height, the frame was built with a
60%-tall opening.

The polarity test is the important one. It is a regression guard for a bug that
shipped once: the opening is FARTHER than the surrounding wall, and an earlier
version searched for the nearest region, which selected the wall wrapping around
the opening and returned the entire frame -- 1.93 m for a door taped at 0.80 m.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.openings import (  # noqa: E402
    COPY_DISAGREEMENT_M,
    DOOR_MIN_HEIGHT_FRAC,
    consensus,
    measure_opening,
    to_metres,
    wall_depth_m,
)

H, W = 200, 300
WALL = 3.0
THROUGH = 6.0


def frame_with_opening(x0, y0, bw, bh, wall=WALL, through=THROUGH):
    """A flat wall with a rectangular hole showing a further space behind it."""
    d = np.full((H, W), wall, dtype=np.float32)
    d[y0 : y0 + bh, x0 : x0 + bw] = through
    return d


def frame_flat(wall=WALL):
    return np.full((H, W), wall, dtype=np.float32)


def test_wall_depth_recovers_a_flat_wall():
    assert wall_depth_m(frame_flat()) == pytest.approx(WALL, abs=0.01)


def test_wall_depth_is_not_dragged_by_the_opening():
    """The opening is 12% of the frame; a global statistic would be pulled by it."""
    d = frame_with_opening(120, 40, 60, 120)
    assert wall_depth_m(d) == pytest.approx(WALL, abs=0.05)


def test_finds_a_known_opening_at_the_right_size():
    d = frame_with_opening(120, 40, 60, 120)
    m = measure_opening(d)
    assert m is not None
    assert m["height_frac_of_frame"] == pytest.approx(0.60, abs=0.02)
    assert m["aspect_w_over_h"] == pytest.approx(0.50, abs=0.02)
    assert m["bbox_px"][0] == pytest.approx(120, abs=3)
    assert m["bbox_px"][1] == pytest.approx(40, abs=3)


def test_the_opening_is_farther_than_the_wall():
    """Polarity, stated directly: through the hole is deeper than the wall."""
    d = frame_with_opening(120, 40, 60, 120)
    assert d[100, 150] > wall_depth_m(d)
    assert wall_depth_m(d) == pytest.approx(WALL, abs=0.05)


def test_a_near_blob_is_not_reported_as_an_opening():
    """Regression guard for the inverted mask.

    A region CLOSER than the wall -- clutter, or the floor at the photographer's
    feet -- is not a doorway. The old near-region search selected exactly this.
    """
    d = frame_flat()
    d[80:160, 130:190] = 1.0
    assert measure_opening(d) is None


def test_a_flat_wall_has_no_opening():
    assert measure_opening(frame_flat()) is None


def test_an_opening_too_short_to_be_a_door_is_rejected():
    d = frame_with_opening(120, 90, 60, int(H * 0.20))
    assert measure_opening(d) is None


def test_opening_at_the_door_height_threshold_is_accepted():
    bh = int(H * (DOOR_MIN_HEIGHT_FRAC + 0.05))
    d = frame_with_opening(120, 40, 60, bh)
    assert measure_opening(d) is not None


def test_shallower_space_than_the_margin_is_not_an_opening():
    """A recess shallower than the margin is a niche, not a doorway through."""
    d = frame_with_opening(120, 40, 60, 120, through=WALL + 0.3)
    assert measure_opening(d) is None


def test_to_metres_uses_the_given_wall_height():
    m = {"height_frac_of_frame": 0.8, "aspect_w_over_h": 0.4}
    w, h = to_metres(m, 2.5)
    assert h == pytest.approx(2.0)
    assert w == pytest.approx(0.8)


def _meas(w, h, src):
    return {"width_est_m": w, "height_est_m": h, "source": src}


def test_consensus_of_one_is_that_one():
    c = consensus([_meas(0.8, 2.0, "a.jpg")])
    assert c["n_photos"] == 1
    assert (c["width_est_m"], c["height_est_m"]) == (0.8, 2.0)
    assert c["copies_agree"] is True


def test_consensus_of_two_averages_the_middles():
    """Conventional median: with two copies, their mean.

    The obvious `sorted(v)[len(v) // 2]` returns the UPPER middle here, so with two
    copies of a door it always reported the larger one -- the wild copy capturing
    the answer, which is the opposite of why a median is used.
    """
    c = consensus([_meas(0.8, 2.0, "a.jpg"), _meas(1.6, 2.0, "b.jpg")])
    assert c["width_est_m"] == pytest.approx(1.2)
    assert c["width_spread_m"] == pytest.approx(0.8)
    assert c["copies_agree"] is False


def test_one_wild_copy_cannot_capture_the_answer():
    c = consensus([
        _meas(0.80, 2.00, "a.jpg"),
        _meas(0.82, 2.01, "b.jpg"),
        _meas(1.60, 2.00, "c.jpg"),
    ])
    assert c["width_est_m"] == pytest.approx(0.82)
    assert c["copies_agree"] is False


def test_consensus_flags_disagreeing_copies():
    c = consensus([_meas(1.02, 1.92, "a.jpg"), _meas(1.45, 1.83, "b.jpg")])
    assert c["copies_agree"] is False
    assert c["width_spread_m"] == pytest.approx(0.43)
    assert sorted(c["sources"]) == ["a.jpg", "b.jpg"]


def test_consensus_agrees_when_copies_are_close():
    c = consensus([_meas(0.92, 2.01, "a.jpg"), _meas(0.92, 2.00, "b.jpg")])
    assert c["copies_agree"] is True
    assert c["width_spread_m"] <= COPY_DISAGREEMENT_M


def test_consensus_takes_no_truth_argument():
    """It must be structurally impossible to select on validation data.

    The version this replaced picked whichever copy landed nearest the taped value,
    which meant the reported number was partly chosen by the answer key.
    """
    import inspect

    params = list(inspect.signature(consensus).parameters)
    assert params == ["measurements"]
