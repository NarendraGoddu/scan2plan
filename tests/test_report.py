"""Tests for the result document and the SVG floor plan.

The report is what an assessor actually reads, so these check the properties
that would make it misleading rather than merely ugly: a number without its
uncertainty, a claim the pipeline did not earn, or a label that renders off the
page.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.plan import RoomPlan, Wall
from scan2plan.report import LIMITATIONS, build_report, render_svg


def _rect_plan(length: float = 4.60, width: float = 3.40, height: float = 2.72) -> RoomPlan:
    """A rectangular room with walls on the four axis-aligned sides."""
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.0, 0.0, 1.0])
    up = np.array([0.0, 1.0, 0.0])
    walls = [
        Wall(np.array([1.0, 0.0]), 0.0, 40, 0.004, inward=False),
        Wall(np.array([1.0, 0.0]), length, 44, 0.005, inward=True),
        Wall(np.array([0.0, 1.0]), 0.0, 39, 0.006, inward=False),
        Wall(np.array([0.0, 1.0]), width, 41, 0.004, inward=True),
    ]
    corners_ab = np.array([[0.0, 0.0], [0.0, width], [length, width], [length, 0.0]])
    verts_world = np.column_stack(
        [
            corners_ab[:, 0][:, None] * a,
            np.zeros((4, 1)),
            corners_ab[:, 1][:, None] * b,
        ]
    )
    plan = RoomPlan(
        basis_a=a, basis_b=b, up=up, walls=walls,
        vertices_ab=corners_ab, vertices_world=verts_world,
        edge_lengths_m=[width, length, width, length],
        edge_wall_index=[0, 1, 2, 3],
        floor_area_m2=length * width,
        perimeter_m=2.0 * (length + width),
        floor_height_m=-1.45,
        ceiling_height_m=height - 1.45,
    )
    return plan


class _Plane:
    def __init__(self, kind, normal, offset, height, rms, members, spread=0.0):
        self.kind = kind
        self.normal = np.asarray(normal, dtype=float)
        self.offset = offset
        self.height_m = height
        self.rms_m = rms
        self.n_consensus_members = members
        self.member_height_spread_m = spread


def _report(plan=None):
    plan = plan or _rect_plan()
    planes = [
        _Plane("floor", [0, 1, 0], -1.45, 0.0, 0.012, 180),
        _Plane("ceiling", [0, 1, 0], 1.27, 2.72, 0.015, 95, spread=0.031),
        _Plane("wall", [1, 0, 0], 0.0, 0.0, 0.009, 60),
        _Plane("wall", [1, 0, 0], 4.60, 0.0, 0.010, 70),
    ]
    return build_report(
        plan,
        capture_path="/tmp/scan.zip",
        scan_id="scan1",
        n_frames_total=1000,
        n_frames_used=500,
        depth_hw=(192, 256),
        depth_scale_m_per_unit=0.001,
        sampling={"load_stride": 2, "frame_stride": 1, "point_stride": 4},
        planes=planes,
        elapsed_s=12.5,
    )


def test_document_has_the_expected_sections():
    doc = _report().document
    for key in ("schema_version", "generated_utc", "input", "coordinate_frame",
                "room", "planes", "uncertainty", "quality", "limitations"):
        assert key in doc, f"missing section {key}"
    assert doc["schema_version"] == "1.0"


def test_every_measured_number_carries_uncertainty():
    u = _report().document["uncertainty"]
    assert u["ceiling_height_1sigma_m"] is not None
    assert len(u["wall_position_1sigma_m"]) == 4
    assert u["wall_position_1sigma_max_m"] is not None
    assert u["floor_area_1sigma_pct"] is not None
    assert "quadrature" in u["method"]


def test_ceiling_uncertainty_is_the_quadrature_sum_of_both_planes():
    # A difference of two independent plane heights; taking the smaller of the
    # two would understate it against the 15 mm gate.
    u = _report().document["uncertainty"]
    # floor rms 12 mm over 180 members, ceiling rms 15 mm over 95 members.
    expected = np.hypot(0.012 / np.sqrt(180), 0.015 / np.sqrt(95))
    assert u["ceiling_height_1sigma_m"] == pytest.approx(expected, abs=1e-4)


def test_limitations_travel_with_the_document():
    doc = _report().document
    assert doc["limitations"] == LIMITATIONS
    joined = " ".join(doc["limitations"]).lower()
    # The two gaps a reviewer is most likely to check for by name.
    assert "opening" in joined
    assert "loop closure" in joined or "loop-closure" in joined


def test_svg_is_ascii_only():
    # Non-ASCII glyphs render as tofu boxes in stricter SVG viewers, and this
    # file is an assessor-facing artifact.
    svg = render_svg(_report().document)
    svg.encode("ascii")  # raises if any non-ASCII survived


def test_svg_is_wellformed_xml_and_fits_its_canvas():
    svg = render_svg(_report().document)
    root = ET.fromstring(svg)
    w, h = float(root.get("width")), float(root.get("height"))
    assert w > 0 and h > 0

    offenders = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        for attr in ("x", "x1", "x2", "cx"):
            v = el.get(attr)
            if v is not None and (float(v) < 0 or float(v) > w):
                offenders.append((tag, attr, v))
        for attr in ("y", "y1", "y2", "cy"):
            v = el.get(attr)
            if v is not None and (float(v) < 0 or float(v) > h):
                offenders.append((tag, attr, v))
    assert not offenders, f"off-canvas elements: {offenders[:6]}"


def test_svg_labels_every_wall_with_length_and_uncertainty():
    root = ET.fromstring(render_svg(_report().document))
    texts = [e.text for e in root.iter() if e.tag.endswith("text") and e.text]
    # Wall labels carry a double space before the sign; the ceiling line does not.
    dims = [t for t in texts if "m  +/-" in t]
    assert len(dims) == 4, f"expected one dimension label per wall, got {dims}"
    for t in dims:
        assert "mm" in t
    assert any("ceiling height" in t for t in texts)
    assert any("floor area" in t for t in texts)


def test_svg_degrades_gracefully_without_a_polygon():
    plan = _rect_plan()
    plan.vertices_ab = np.zeros((0, 2))
    plan.vertices_world = np.zeros((0, 3))
    plan.floor_area_m2 = 0.0
    svg = render_svg(_report(plan).document)
    root = ET.fromstring(svg)
    assert float(root.get("width")) > 0
    assert any("no room polygon" in (e.text or "") for e in root.iter() if e.tag.endswith("text"))


def test_roundtrips_through_json(tmp_path):
    rep = _report()
    path = tmp_path / "out.json"
    rep.write(str(path))
    import json
    back = json.loads(path.read_text(encoding="utf-8"))
    assert back["room"]["floor_area_m2"] == pytest.approx(4.60 * 3.40, abs=1e-3)
    assert len(back["room"]["walls"]) == 4