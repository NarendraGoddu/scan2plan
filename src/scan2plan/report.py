"""Assessor-facing outputs: the result document and an SVG floor plan.

The geometry lives in `plan.py`. This module decides how it is *presented*, and
it is deliberately conservative about what it claims:

- every measured number carries its own uncertainty, so a reader can see which
  figures are load-bearing and which are decoration;
- anything the pipeline inferred rather than observed is labelled as such;
- known gaps travel with the document instead of living in a README, so a
  result can never be read as more complete than it is.

Coordinates are reported three ways because none of them is obviously right on
its own. `world` is the archive's own frame (Z is the optical axis, Y is
vertical). `floor_ab` is the gravity-aligned frame the room polygon is solved
in. `vertices_world` ties the two together so a consumer can place the room in
the archive's frame without re-deriving the basis.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .plan import RoomPlan, polygon_area

SCHEMA_VERSION = "1.0"

# Stated once, carried in every document, so a reader who only has the JSON
# still learns what the pipeline does not do.
LIMITATIONS = [
    "Openings (doors, windows) are not yet detected; wall runs are reported as "
    "continuous segments, so any opening is absorbed into the wall length.",
    "The vertical axis is derived from gravity and the handheld-above-floor "
    "convention, not from a sensor. It is sign-corrected per capture.",
    "Scale comes from the device depth scale, not from a surveyed reference. "
    "Any error in the device scale is a uniform multiplicative error in every "
    "length reported here.",
    "Poses are consumed as given. No loop closure or pose-graph optimisation is "
    "applied, so pose error appears directly in the wall uncertainties.",
    "Multi-room layouts are not stitched; one capture yields one room.",
]


@dataclass
class CaptureReport:
    """Everything one archive produced, ready to serialise."""

    document: dict[str, Any]
    plan: RoomPlan

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.document, indent=indent)

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())
            fh.write("\n")

    def write_svg(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render_svg(self.document))


def _round_list(values: np.ndarray, nd: int = 5) -> list[float]:
    return [round(float(v), nd) for v in np.asarray(values).ravel()]


def build_report(
    plan: RoomPlan,
    *,
    capture_path: str,
    scan_id: str,
    n_frames_total: int,
    n_frames_used: int,
    depth_hw: tuple[int, int],
    depth_scale_m_per_unit: float,
    sampling: dict[str, int],
    planes: list[Any],
    elapsed_s: float | None = None,
) -> CaptureReport:
    """Build the result document for one capture.

    `planes` are the CP3 `Plane` objects; their residuals are the raw material
    for the uncertainty budget, which is why they are summarised here rather
    than dumped wholesale.
    """
    by_kind: dict[str, list[Any]] = {}
    for p in planes:
        by_kind.setdefault(getattr(p, "kind", "unknown"), []).append(p)

    def _best(kind: str) -> dict[str, Any] | None:
        group = by_kind.get(kind)
        if not group:
            return None
        best = max(group, key=lambda p: getattr(p, "n_consensus_members", getattr(p, "support", 0)))
        height = float(getattr(best, "height_m", 0.0) or 0.0)
        rms = float(getattr(best, "rms_m", 0.0) or 0.0)
        support = int(getattr(best, "n_consensus_members", getattr(best, "support", 0)))
        return {
            "plane_normal_world": _round_list(best.normal, 5),
            "offset_m": round(float(best.offset), 4),
            "height_above_floor_m": round(height, 4),
            "residual_rms_m": round(rms, 4),
            "supporting_members": support,
            "member_height_spread_m": round(
                float(getattr(best, "member_height_spread_m", 0.0) or 0.0), 4
            ),
            "height_uncertainty_m": round(rms / math.sqrt(max(support, 1)), 4),
        }

    floor = _best("floor")
    ceiling = _best("ceiling")
    wall_summary = [
        {
            "normal_world": _round_list(p.normal, 5),
            "offset_m": round(float(p.offset), 4),
            "supporting_members": int(getattr(p, "n_consensus_members", getattr(p, "support", 0))),
            "residual_rms_m": round(float(getattr(p, "rms_m", 0.0) or 0.0), 4),
        }
        for p in by_kind.get("wall", [])
    ]

    # Ceiling height is a difference of two plane heights, so its uncertainty
    # is the quadrature sum. Reporting the smaller of the two would understate
    # it, which is exactly the number an assessor checks against the 15 mm gate.
    ceil_unc = None
    if ceiling is not None and floor is not None:
        ceil_unc = round(
            math.hypot(ceiling["height_uncertainty_m"], floor["height_uncertainty_m"]), 4
        )

    wall_unc = [w.uncertainty_m() for w in plan.walls]
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input": {
            "archive": capture_path,
            "scan_id": scan_id,
            "frames_in_archive": int(n_frames_total),
            "frames_used": int(n_frames_used),
            "depth_image_hw": [int(depth_hw[0]), int(depth_hw[1])],
            "depth_scale_m_per_unit": float(depth_scale_m_per_unit),
            "frame_sampling": {k: int(v) for k, v in sampling.items()},
            "elapsed_s": None if elapsed_s is None else round(float(elapsed_s), 1),
        },
        "coordinate_frame": {
            "description": (
                "Archive world frame: Z is the camera optical axis, Y is vertical. "
                "The room is solved in a gravity-aligned floor frame (alpha, beta) "
                "whose origin is the world origin, projected onto the floor plane."
            ),
            "up_world": _round_list(plan.up, 5),
            "basis_a_world": _round_list(plan.basis_a, 5),
            "basis_b_world": _round_list(plan.basis_b, 5),
            "floor_height_world_m": round(float(plan.floor_height_m), 4),
        },
        "room": plan.to_dict(),
        "planes": {"floor": floor, "ceiling": ceiling, "walls": wall_summary},
        "uncertainty": {
            "method": (
                "Residual scatter over sqrt(supporting observations). Ceiling "
                "height is a difference of two plane heights and combines them "
                "in quadrature."
            ),
            "ceiling_height_m": plan.ceiling_height_m,
            "ceiling_height_1sigma_m": ceil_unc,
            "wall_position_1sigma_m": [round(u, 4) for u in wall_unc],
            "wall_position_1sigma_max_m": round(max(wall_unc), 4) if wall_unc else None,
            "floor_area_1sigma_pct": _area_uncertainty_pct(plan, wall_unc),
        },
        "quality": {
            "n_walls": len(plan.walls),
            "n_vertices": int(len(plan.vertices_ab)),
            "floor_area_m2": round(float(plan.floor_area_m2), 4),
            "perimeter_m": round(float(plan.perimeter_m), 4),
            "edge_lengths_m": [round(float(e), 4) for e in plan.edge_lengths_m],
            "ceiling_height_m": (
                None if plan.ceiling_height_m is None else round(float(plan.ceiling_height_m), 4)
            ),
            "notes": list(plan.notes),
        },
        "limitations": LIMITATIONS,
    }
    return CaptureReport(document=doc, plan=plan)


def _area_uncertainty_pct(plan: RoomPlan, wall_unc: list[float]) -> float | None:
    """Propagate per-wall position uncertainty into a floor-area figure.

    A wall displaced by d moves the area by roughly d * (length of the opposite
    side). Summing that over the walls and dividing by the area gives a first
    order relative uncertainty. It ignores the correlation between walls, so it
    is indicative rather than strict -- but an unlabelled area number is worse.
    """
    if not plan.walls or plan.floor_area_m2 <= 0 or len(wall_unc) != len(plan.walls):
        return None
    n = len(plan.walls)
    if len(plan.edge_lengths_m) < n:
        return None
    total = 0.0
    for i, unc in enumerate(wall_unc):
        opp = plan.edge_lengths_m[(i + n // 2) % n]
        total += float(unc) * float(opp)
    return round(100.0 * total / float(plan.floor_area_m2), 3)


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------

def render_svg(doc: dict[str, Any], *, margin: int = 70) -> str:
    """Render a dimensioned floor plan.

    Drawn in the floor frame, so the result is a plan view regardless of how the
    capture was oriented. There is deliberately no north arrow: the archive
    frame has no meaningful compass direction, and inventing one would be a
    fabrication. Orientation is labelled with the basis vectors instead.
    """
    room = doc["room"]
    verts = np.asarray(room.get("vertices_world") or [], dtype=np.float64)
    if verts.shape[0] < 3:
        side = 2 * margin + 120
        return _svg_shell(
            side, side,
            f'<text x="{margin}" y="{margin}" class="warn">'
            "no room polygon: fewer than three vertices</text>",
        )

    basis = doc["coordinate_frame"]
    a = np.asarray(basis["basis_a_world"], dtype=np.float64)
    b = np.asarray(basis["basis_b_world"], dtype=np.float64)
    ab = np.column_stack([verts @ a, verts @ b])

    lo = ab.min(axis=0)
    hi = ab.max(axis=0)
    span = hi - lo
    scale = min(
        (2.0 * margin + 120.0) / max(span[0], 1e-6),
        (2.0 * margin + 120.0) / max(span[1], 1e-6),
    )
    w = int(span[0] * scale + 2 * margin + 120)
    h = int(span[1] * scale + 2 * margin + 150)

    def to_px(p: np.ndarray) -> tuple[float, float]:
        # Flip beta so the drawing is not mirrored.
        return (margin + 60 + (p[0] - lo[0]) * scale, margin + 60 + (hi[1] - p[1]) * scale)

    pts = [to_px(p) for p in ab]
    path_d = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z"

    parts: list[str] = []
    parts.append(
        f'<polygon points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in pts)}" '
        'class="room" />'
    )

    walls = room.get("walls") or []
    lengths = doc["quality"].get("edge_lengths_m") or []
    unc = (doc.get("uncertainty") or {}).get("wall_position_1sigma_m") or []

    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" class="wall" />')

        length = lengths[i] if i < len(lengths) else float("nan")
        sigma = unc[i] if i < len(unc) else None
        label = f"{length:.3f} m" if length == length else "?"
        if sigma is not None:
            # ASCII only: the SVG is an assessor-facing artifact and glyphs like
            # U+00B1 render as tofu boxes in stricter SVG viewers.
            label += f"  +/-{sigma * 1000:.0f} mm"

        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1.0
        # Offset the label perpendicular to the wall, away from the room centre.
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        sgn = 1.0 if (-dy) * (mx - cx) + dx * (my - cy) >= 0 else -1.0
        ox, oy = (-dy / norm) * 16.0 * sgn, (dx / norm) * 16.0 * sgn
        anchor = "start" if abs(dx / norm) > 0.7 else "middle"
        parts.append(
            f'<text x="{mx + ox:.2f}" y="{my + oy + 4:.2f}" class="dim" '
            f'text-anchor="{anchor}">{label}</text>'
        )

    # Vertices, so a reader can trace the polygon back to the JSON.
    for i, (x, y) in enumerate(pts):
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" class="vert" />')
        parts.append(f'<text x="{x:.2f}" y="{y - 8:.2f}" class="vlabel" text-anchor="middle">V{i}</text>')

    parts.append(_svg_scale_bar(pts, lo, hi, scale, margin, h))
    parts.append(_svg_title_block(doc, w, h, margin))

    body = "\n    ".join(parts)
    return _svg_shell(w, h, body)


def _svg_scale_bar(pts, lo, hi, scale, margin, h) -> str:
    """A one-metre bar, so the drawing is measurable with a ruler."""
    x0 = margin + 60.0
    y0 = h - margin - 46.0
    bar = 1.0 * scale
    return (
        f'<g class="scale">'
        f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x0 + bar:.2f}" y2="{y0:.2f}" />'
        f'<line x1="{x0:.2f}" y1="{y0 - 5:.2f}" x2="{x0:.2f}" y2="{y0 + 5:.2f}" />'
        f'<line x1="{x0 + bar:.2f}" y1="{y0 - 5:.2f}" x2="{x0 + bar:.2f}" y2="{y0 + 5:.2f}" />'
        f'<text x="{x0 + bar / 2:.2f}" y="{y0 - 9:.2f}" text-anchor="middle">1 m</text>'
        f"</g>"
    )


def _svg_title_block(doc, w, h, margin) -> str:
    q = doc["quality"]
    u = doc.get("uncertainty") or {}
    i = doc["input"]
    lines = [
        f"scan {i['scan_id']}",
        f"floor area {q['floor_area_m2']:.3f} m2   perimeter {q['perimeter_m']:.3f} m",
    ]
    ch = q.get("ceiling_height_m")
    if ch is not None:
        sig = u.get("ceiling_height_1sigma_m")
        tail = f" +/-{sig * 1000:.0f} mm" if sig is not None else ""
        lines.append(f"ceiling height {ch:.3f} m{tail}")
    # A tape-assisted report has no depth frames to count, so the line reports what
    # the geometry actually is instead. Saying "frames used" for a document whose
    # dimensions came off a tape would be a fabrication in the title block.
    if doc.get("geometry_is_measured_not_reconstructed"):
        src = str(doc.get("geometry_source", "measured")).split(" (")[0]
        lines.append(f"walls {q.get('n_walls', len(doc['room'].get('walls') or []))}"
                     f"   geometry: {src}")
        lines.append("dimensions are MEASURED, not reconstructed from imagery")
    else:
        lines.append(
            f"walls {q.get('n_walls', len(doc['room'].get('walls') or []))}"
            f"   frames used {i.get('frames_used', 0)}/"
            f"{i.get('frames_in_archive', 0)}"
        )
        auc = u.get("floor_area_1sigma_pct")
        if auc is not None:
            lines.append(f"floor area +/-{auc:.2f}% (1 sigma)")
    lines.append("plan view in the gravity-aligned floor frame; no compass direction implied")

    ty = h - margin - 76
    out = [f'<g class="titleblock">']
    for k, line in enumerate(lines):
        out.append(
            f'<text x="{margin}" y="{ty + 14 * k:.2f}" class="title">{line}</text>'
        )
    out.append("</g>")
    return "\n    ".join(out)


def _svg_shell(w: int, h: int, body: str = "") -> str:
    w = max(int(w), 420)
    h = max(int(h), 320)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
     viewBox="0 0 {w} {h}" font-family="DejaVu Sans, Verdana, sans-serif">
  <style>
    .bg {{ fill: #ffffff; }}
    .room {{ fill: #f4f6f8; stroke: none; }}
    .wall {{ stroke: #1b2733; stroke-width: 2.5; stroke-linecap: round; }}
    .vert {{ fill: #1b2733; }}
    .vlabel {{ font-size: 9px; fill: #7a8894; }}
    .dim {{ font-size: 11px; fill: #1b2733; }}
    .scale line {{ stroke: #1b2733; stroke-width: 1.2; }}
    .scale text {{ font-size: 10px; fill: #1b2733; }}
    .title {{ font-size: 11px; fill: #1b2733; }}
    .titleblock {{ font-weight: 500; }}
    .warn {{ font-size: 13px; fill: #a4262c; }}
    .meta {{ font-size: 9px; fill: #7a8894; }}
  </style>
  <rect class="bg" x="0" y="0" width="{w}" height="{h}" />
  {body}
</svg>
"""