"""Filename parsing, which every downstream stage depends on.

A misparse here is silent: the wall stage would simply measure the wrong photos,
or the damage stage would find nothing to do and report a clean wall. The cases
below are the real names from the 2026-10-04 capture, including the two
conventions in use and the editor duplicates.
"""

from __future__ import annotations

import pytest

from scan2plan.capture_manifest import (
    group_by_surface,
    group_by_wall,
    parse_filename,
)


@pytest.mark.parametrize(
    "folder,filename,surface,wall",
    [
        # master bedroom: dot-separated wall index, no parenthesised copy number
        ("master_bed_room", "m_wall_1.1.jpg", "wall", 1),
        ("master_bed_room", "m_wall_2.5.jpg", "wall", 2),
        ("master_bed_room", "m_wall_4.4.jpg", "wall", 4),
        ("master_bed_room", "m_celling_1 (3).jpg", "ceiling", None),
        ("master_bed_room", "m_door.jpg", "door", None),
        ("master_bed_room", "m_tot.jpg", "room_overview", None),
        # second bedroom: parenthesised copy index, underscore-numbered walls
        ("2nd_bed_room", "2_room_wall_1 (1).jpg", "wall", 1),
        ("2nd_bed_room", "2_room_wall_4 (6).jpg", "wall", 4),
        ("2nd_bed_room", "2_room_celling (9).jpg", "ceiling", None),
        ("2nd_bed_room", "2_room_total (2).jpg", "room_overview", None),
        # hall uses "side" rather than "wall"
        ("main_hall", "hall_side_1 (1).jpg", "wall", 1),
        ("main_hall", "hall_side_4 (2).jpg", "wall", 4),
        ("main_hall", "hall_celling (7).jpg", "ceiling", None),
        ("main_hall", "main_door (2).jpg", "door", None),
    ],
)
def test_parses_real_capture_names(folder, filename, surface, wall):
    item = parse_filename(folder, filename)
    assert item.surface == surface
    assert item.wall_index == wall
    assert item.labelled


def test_damage_beats_wall_in_the_name():
    """`wall_1_crack` contains both keywords; damage has to win.

    Regression test. With the pattern list ordered wall-first, every crack
    photograph parsed as an ordinary wall and the damage stage reported a clean
    surface while quietly having examined nothing.
    """
    item = parse_filename("2nd_bed_room", "wall_1_crack (1).jpg")
    assert item.surface == "damage"
    assert item.is_damage
    assert item.wall_index == 1


def test_copy_suffix_is_not_a_new_surface():
    item = parse_filename("2nd_bed_room", "wall_1_crack (2) - Copy.jpg")
    assert item.surface == "damage"
    assert item.wall_index == 1


def test_editor_duplicate_suffix_still_parses():
    item = parse_filename("ALL ROOMS PHOTOS", "IMG20261004104322 - Copy.jpg")
    # No surface keyword, and that is reported rather than guessed.
    assert item.surface is None
    assert not item.labelled
    assert item.notes


def test_walkthrough_photos_are_unlabelled():
    item = parse_filename("ALL ROOMS PHOTOS", "IMG20261004103746.jpg")
    assert item.room_id == "unlabelled_walkthrough"
    assert item.surface is None


def test_frame_index_extracted_for_both_conventions():
    assert parse_filename("master_bed_room", "m_wall_2.5.jpg").frame_index == 5
    assert parse_filename("2nd_bed_room", "2_room_wall_2 (4).jpg").frame_index == 4


def test_room_ids_match_ground_truth_keys():
    for folder, room in [
        ("master_bed_room", "master_bedroom"),
        ("2nd_bed_room", "second_bedroom"),
        ("main_hall", "main_hall"),
    ]:
        assert parse_filename(folder, "x.jpg").room_id == room


def test_grouping_helpers():
    items = [
        parse_filename("master_bed_room", "m_wall_1.1.jpg"),
        parse_filename("master_bed_room", "m_wall_1.2.jpg"),
        parse_filename("master_bed_room", "m_wall_2.1.jpg"),
        parse_filename("master_bed_room", "m_door.jpg"),
        parse_filename("ALL ROOMS PHOTOS", "IMG1.jpg"),
    ]
    walls = group_by_wall(items)
    assert len(walls[("master_bedroom", 1)]) == 2
    assert len(walls[("master_bedroom", 2)]) == 1
    surfaces = group_by_surface(items)
    assert len(surfaces[("master_bedroom", "door")]) == 1
    # The unlabelled walkthrough photo must not appear in any group.
    unlabelled = items[-1]
    assert all(unlabelled not in v for v in walls.values())
    assert all(unlabelled not in v for v in surfaces.values())
