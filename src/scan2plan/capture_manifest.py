"""Structured labels recovered from capture filenames.

The field survey named files by what they show, and that turns out to be the
highest-quality annotation available for this project -- better than anything
that could be inferred from the imagery alone, because it was written by someone
standing in the room:

  master_bed_room/m_wall_2.5.jpg     master bedroom, wall 2, frame 5
  2nd_bed_room/2_room_wall_1 (3).jpg second bedroom, wall 1, frame 3
  main_hall/hall_side_2 (4).jpg      hall, side (wall) 2, frame 4
  main_hall/hall_celling (7).jpg     hall, ceiling, frame 7
  2nd_bed_room/wall_1_crack (1).jpg  second bedroom, wall 1, CRACK, frame 1
  2nd_bed_room/2_room_total (2).jpg  second bedroom, whole-room view, frame 2

Two naming conventions are in use and both must parse: a dot separator
(`m_wall_1.1`) and a parenthesised copy index (`2_room_wall_1 (1)`). The
`ALL ROOMS PHOTOS` folder is timestamp-only and yields no labels beyond its
folder, which `parse_filename` reports honestly rather than guessing at.

This is used to *select* evidence, not to produce measurements. Knowing a photo
shows wall 2 lets the geometry stage fit only wall 2 to it, which is what makes
per-wall extraction tractable: the earlier blind search found a wall in only 39
of 197 images, because most of them are ceiling close-ups. Nothing here infers a
dimension from a filename; dimensions come from the depth maps.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict

# Room folder name -> canonical room id. Kept explicit rather than slugified so
# the mapping to data/field_ground_truth.json stays obvious.
ROOM_IDS = {
    "master_bed_room": "master_bedroom",
    "2nd_bed_room": "second_bedroom",
    "main_hall": "main_hall",
    "all rooms photos": "unlabelled_walkthrough",
}

# Surface vocabulary -> normalised surface.
#
# Order matters and is not cosmetic: `wall_1_crack (1).jpg` contains both "wall"
# and "crack", so a wall-first list classifies every damage photograph as an
# ordinary wall and the damage stage silently sees nothing to do. Damage is
# therefore tested first, and `parse_filename` re-checks it explicitly below.
SURFACE_PATTERNS = [
    (r"crack|damage|breach|hole", "damage"),
    (r"celling|ceiling", "ceiling"),
    (r"door", "door"),
    (r"window", "window"),
    (r"wall|side", "wall"),
    (r"total|_tot\b|tot$", "room_overview"),
    (r"floor", "floor"),
]

# Damage keywords are checked independently of the ordered list, because a damage
# filename may name the surface it damages ("wall_1_crack", "ceiling_crack").
_DAMAGE_PATTERN = re.compile(r"crack|damage|breach|hole", re.IGNORECASE)

_WALL_INDEX = re.compile(r"(?:wall|side)[_\s]*([1-9])", re.IGNORECASE)
_COPY_INDEX = re.compile(r"\((\d+)\)")
# A literal dot, matching the master-bedroom convention `m_wall_2.5`. Matching an
# underscore instead would read `2_room_wall_1` -- where the trailing 1 is the wall
# number, not a frame number -- as frame 1.
_DOT_INDEX = re.compile(r"\.([1-9])$")


@dataclass
class CaptureItem:
    """One photo or video with whatever labels its name carries."""

    path: str
    folder: str
    room_id: str
    filename: str
    surface: str | None = None
    wall_index: int | None = None
    frame_index: int | None = None
    is_damage: bool = False
    labelled: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.filename


def parse_filename(folder: str, filename: str) -> CaptureItem:
    """Recover room, surface, wall number and frame number from a filename.

    Unrecognised names are returned with `labelled=False` rather than being
    forced into a category. A walkthrough folder of timestamped photos has no
    labels, and pretending otherwise would corrupt every per-wall aggregate
    downstream.
    """
    room_id = ROOM_IDS.get(folder.strip().lower(), folder.strip().lower())
    stem = os.path.splitext(filename)[0]
    low = stem.lower().replace("-", "_")

    item = CaptureItem(
        path=os.path.join(folder, filename),
        folder=folder,
        room_id=room_id,
        filename=filename,
    )

    # " - Copy" and "_01" are phone/editor duplicates of the same shot. They are
    # still real images, so they are kept, but the copy index is stripped first
    # so `wall_1_crack (1) - Copy` is not read as a different surface.
    work = re.sub(r"\s*-\s*copy\s*$", "", stem, flags=re.IGNORECASE)
    work = re.sub(r"_\d{2}$", "", work) if not _DOT_INDEX.search(work) else work

    # Damage wins over whatever surface it is recorded on.
    if _DAMAGE_PATTERN.search(work):
        item.is_damage = True
        item.surface = "damage"
        m = _WALL_INDEX.search(work.lower())
        if m:
            item.wall_index = int(m.group(1))
    else:
        for pattern, surface in SURFACE_PATTERNS:
            if re.search(pattern, low):
                item.surface = surface
                break

    if item.surface == "wall":
        m = _WALL_INDEX.search(work.lower())
        if m:
            item.wall_index = int(m.group(1))

    m = _COPY_INDEX.search(work)
    if m:
        item.frame_index = int(m.group(1))
    else:
        m = _DOT_INDEX.search(work)
        if m:
            item.frame_index = int(m.group(1))

    item.labelled = item.surface is not None
    if not item.labelled:
        item.notes.append("no surface keyword in filename")
    if item.surface == "wall" and item.wall_index is None:
        item.notes.append("wall keyword present but no wall number found")
    return item


def scan_capture(capture_dir: str, recursive: bool = False) -> list[CaptureItem]:
    """Parse every photo and video in a capture directory.

    Covers both layouts seen in practice: photos sitting directly in the capture
    root, and photos grouped into per-room subfolders. The original version read
    only subfolders, so a capture delivered as a flat folder parsed as zero
    labelled photos -- which looks identical to "no labels exist", and quietly
    disables every downstream stage that depends on them.

    Not recursive by default: a capture directory often sits beside unrelated trees
    (a git repo, node_modules) whose filenames would be parsed for nothing. Pass
    recursive=True to descend.
    """
    photo_ext = {".jpg", ".jpeg", ".png"}
    video_ext = {".mp4", ".mov", ".m4v", ".avi"}
    items: list[CaptureItem] = []

    def _add(folder: str, names: list[str]) -> None:
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in photo_ext | video_ext:
                items.append(parse_filename(folder, name))

    root_name = os.path.basename(os.path.abspath(capture_dir))
    try:
        entries = sorted(os.listdir(capture_dir))
    except OSError:
        return items
    _add(root_name, [n for n in entries
                     if os.path.isfile(os.path.join(capture_dir, n))])

    for folder in entries:
        fdir = os.path.join(capture_dir, folder)
        if not os.path.isdir(fdir):
            continue
        if recursive:
            for sub, _dirs, files in os.walk(fdir):
                _add(os.path.relpath(sub, capture_dir), files)
        else:
            _add(folder, os.listdir(fdir))
    return items


def group_by_wall(items: list[CaptureItem]) -> dict[tuple[str, int], list[CaptureItem]]:
    """Photos labelled as showing a specific wall, keyed by (room, wall number)."""
    out: dict[tuple[str, int], list[CaptureItem]] = {}
    for it in items:
        if it.surface == "wall" and it.wall_index is not None:
            out.setdefault((it.room_id, it.wall_index), []).append(it)
    return out


def group_by_surface(items: list[CaptureItem]) -> dict[tuple[str, str], list[CaptureItem]]:
    """Labelled photos keyed by (room, surface)."""
    out: dict[tuple[str, str], list[CaptureItem]] = {}
    for it in items:
        if it.surface:
            out.setdefault((it.room_id, it.surface), []).append(it)
    return out


def to_dicts(items: list[CaptureItem]) -> list[dict]:
    return [asdict(i) for i in items]
