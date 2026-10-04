"""Dimensioned floor plans for the real site, from tape measurements.

This is the production fallback for a capture with no usable depth sensor. The
geometry here is *measured*, not reconstructed: the room outline comes from the
steel tape readings in data/field_ground_truth.json, and the photographs are
attached as evidence for each surface. Every output says so in its `geometry_source`
field. Nothing in this script infers a dimension from an image, and it must not be
mistaken for a reconstruction result.

Why this exists rather than a better depth model. Monocular depth on this capture
was measured, not assumed, and it lands 30-75% out on room area: the plane fits
are good to 5-49 mm but the extents are not, because no single frame contains both
corners of a wall and there is no scale reference in any frame. A tape is a
better instrument than that, and on a real site a surveyor has one. So the honest
production path for a phone-only capture is assisted, and this is it.

What it produces per room:

  <room>.json   dimensions, per-wall evidence, openings, damage, confidence
  <room>.svg    dimensioned plan, reusing the automatic tier's renderer
  index.html    all rooms side by side, linking evidence

Confidence is computed from named reasons rather than asserted, so a reader can see
which condition is missing. A plan built from tape with no photograph evidence is
not the same artifact as one with a wall photo per side, and the score says so.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from scan2plan.capture_manifest import scan_capture  # noqa: E402
from scan2plan.report import render_svg  # noqa: E402

# A 5 m steel tape read at eye level is good to roughly this, and the tape is the
# reference here rather than the thing being tested, so it is quoted as a
# measurement uncertainty rather than a pipeline error.
TAPE_SIGMA_M = 0.005

WALL_ORDER_NOTE = (
    "Wall numbers follow the photographer's filenames. That they run clockwise "
    "around the room was never confirmed, so wall 1 may not be physically adjacent "
    "to wall 2 in the drawing. Lengths are unaffected."
)


def room_polygon(length_m: float, width_m: float) -> np.ndarray:
    """Four corners of a rectangle, counter-clockwise from the origin."""
    return np.array(
        [[0.0, 0.0], [length_m, 0.0], [length_m, width_m], [0.0, width_m]],
        dtype=np.float64,
    )


def build_document(
    room_truth: dict,
    evidence: dict,
    photos_by_wall: dict[int, list[str]],
    photos_by_surface: dict[str, list[str]],
) -> dict:
    """Assemble a report document in the schema render_svg already consumes."""
    length = float(room_truth["length_m"])
    width = float(room_truth["width_m"])
    height = float(room_truth["height_m"])
    poly = room_polygon(length, width)

    edges = np.roll(poly, -1, axis=0) - poly
    edge_lengths = [float(np.linalg.norm(e)) for e in edges]

    # Per-edge outward normal, to place openings on the right wall.
    centre = poly.mean(axis=0)
    normals = []
    for i, e in enumerate(edges):
        n = np.array([e[1], -e[0]], dtype=np.float64)
        n /= max(np.linalg.norm(n), 1e-12)
        mid = poly[i] + e / 2.0
        if np.dot(n, mid - centre) < 0:
            n = -n
        normals.append(n)

    area = length * width
    perimeter = 2.0 * (length + width)

    # The renderer works in the shared floor frame and expects 3D vertices, so the
    # 2D layout is lifted with z = 0 (the floor is the datum).
    poly3 = np.column_stack([poly, np.zeros(len(poly))])

    # Diagonal is a tape reading too, and a rectangle's diagonal is a consistency
    # check on the two sides. Reported because it can contradict them.
    diag = float(room_truth.get("diagonal_m") or 0.0)
    diag_pred = math.hypot(length, width)
    diag_check = None
    if diag > 0:
        diag_check = {
            "measured_m": diag,
            "predicted_from_sides_m": round(diag_pred, 4),
            "difference_mm": round(1000.0 * (diag - diag_pred), 1),
        }

    doc = {
        "schema": "scan2plan/field-tape/v1",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "geometry_source": "steel tape measurement (5 m tape, corner to corner at "
        "floor level between masking-tape corner marks)",
        "geometry_is_measured_not_reconstructed": True,
        "topology_corroboration": {
            "source": "capture filenames, not the tape",
            "labelled_wall_indices": sorted(int(k) for k in photos_by_wall),
            "labelled_wall_count": len(photos_by_wall),
            "independent_support_for_four_walls": sorted(photos_by_wall) == [1, 2, 3, 4],
            "note": "The photographer labelled each wall and numbered the viewpoints "
                    "of that wall (m_wall_2.5 is the fifth view of wall 2). Each room "
                    "folder contains walls 1-4 and nothing else, so the four-sided "
                    "room model is supported by the capture itself and does not rest "
                    "on the tape alone. The labels say nothing about dimensions.",
        },
        "input": {
            "scan_id": f"field-{room_truth['id']}",
            "room_id": room_truth["id"],
            "captured": "2026-10-04",
            "media": {
                "photos_labelled": int(room_truth.get("photos") or 0),
                "videos": 0,
                "exif_present": False,
                "gps_exif_present": False,
                "scale_reference_in_frame": False,
            },
        },
        "room": {
            "vertices_world": poly3.tolist(),
            "vertex_labels": ["V0", "V1", "V2", "V3"],
            "walls": [
                {
                    "index": i + 1,
                    "length_m": round(edge_lengths[i], 4),
                    "from_vertex": f"V{i}",
                    "to_vertex": f"V{(i + 1) % 4}",
                    "outward_normal_ab": [round(float(c), 4) for c in normals[i]],
                    "evidence_photos": photos_by_wall.get(i + 1, []),
                    "n_evidence_photos": len(photos_by_wall.get(i + 1, [])),
                }
                for i in range(4)
            ],
        },
        "coordinate_frame": {
            "basis_a_world": [1.0, 0.0, 0.0],
            "basis_b_world": [0.0, 1.0, 0.0],
            "up_world": [0.0, 0.0, 1.0],
            "origin": "room corner V0",
            "note": "Defined by the tape layout, not by a sensor frame, so there is "
            "no compass direction and no north arrow is drawn.",
        },
        "quality": {
            "floor_area_m2": round(area, 4),
            "perimeter_m": round(perimeter, 4),
            "edge_lengths_m": [round(v, 4) for v in edge_lengths],
            "ceiling_height_m": height,
            "room_aspect": round(length / width, 4),
        },
        "uncertainty": {
            "wall_position_1sigma_m": [TAPE_SIGMA_M] * 4,
            "source": "tape reading uncertainty, not a pipeline residual",
            "floor_area_1sigma_m2": round(
                TAPE_SIGMA_M * (2.0 * (length + width)), 4
            ),
        },
        "openings": room_truth.get("openings") or [],
        "opening_evidence": {
            "door_photos": photos_by_surface.get("door", []),
            "note": "Opening sizes are tape readings. Photographs are attached as "
            "evidence but were not used to derive the dimensions.",
        },
        "damage": evidence.get("damage", []),
        "diagonal_consistency": diag_check,
        "notes": [WALL_ORDER_NOTE],
    }

    doc["confidence"] = compute_confidence(doc, photos_by_wall, photos_by_surface)
    return doc


def compute_confidence(
    doc: dict, photos_by_wall: dict[int, list[str]], photos_by_surface: dict[str, list[str]]
) -> dict:
    """Score the plan from named conditions, so the score is auditable.

    Two things are deliberately kept apart. Dimensional confidence comes from the
    tape and is high. Photographic corroboration is a separate axis and is low for
    this capture, because no wall-by-wall comparison was performed. Averaging them
    into one number would hide the thing a reader most needs to know.
    """
    dims = []
    corroboration = []

    dims.append(("dimensions from steel tape", True,
                 f"+/-{TAPE_SIGMA_M * 1000:.0f} mm on each side"))
    diag = doc.get("diagonal_consistency")
    if diag:
        ok = abs(diag["difference_mm"]) <= 40.0
        dims.append((f"diagonal self-consistent ({diag['difference_mm']:+.0f} mm)", ok,
                     "measured vs predicted from sides"))

    walls_with = sum(1 for i in range(1, 5) if photos_by_wall.get(i))
    corroboration.append((
        f"every wall has a photograph ({walls_with}/4)",
        walls_with == 4,
        "wall photos labelled in the filename",
    ))
    corroboration.append((
        "ceiling photographed", bool(photos_by_surface.get("ceiling")),
        "enables an independent height check",
    ))
    corroboration.append((
        "door photographed", bool(photos_by_surface.get("door")),
        "enables an opening cross-check",
    ))
    corroboration.append((
        "no scale reference in any frame", False,
        "photo tier cannot be metric on its own",
    ))
    corroboration.append((
        "wall numbering order unconfirmed", False,
        "affects adjacency, not lengths",
    ))

    def summarise(items):
        met = sum(1 for _n, ok, _d in items if ok)
        return {
            "score_pct": round(100.0 * met / len(items), 1) if items else 0.0,
            "met": met,
            "total": len(items),
            "conditions": [
                {"condition": n, "met": ok, "detail": d} for n, ok, d in items
            ],
        }

    dim = summarise(dims)
    corr = summarise(corroboration)
    return {
        "dimensional": dim,
        "photographic_corroboration": corr,
        "overall_note": (
            "Dimensional confidence is high and photographic corroboration is low. "
            "These are not averaged: the plan's dimensions come from the tape, and "
            "the photographs corroborate surfaces without having been measured "
            "against them."
        ),
    }


def render_index(docs: list[dict], out_dir: str) -> str:
    """One page linking every room plan, its evidence and its confidence."""
    rows = []
    for d in docs:
        rid = d["input"]["room_id"]
        q = d["quality"]
        c = d["confidence"]
        walls = d["room"]["walls"]
        ev = sum(w["n_evidence_photos"] for w in walls)
        photos = []
        for w in walls:
            for p in w["evidence_photos"][:3]:
                photos.append(f'<li>{html.escape(os.path.basename(p))} '
                              f'<span class="w">wall {w["index"]}</span></li>')
        rows.append(f"""
    <section>
      <h2>{html.escape(rid.replace('_', ' '))}</h2>
      <p class="dims">{q['edge_lengths_m'][0]:.3f} x {q['edge_lengths_m'][1]:.3f} m
         &middot; height {q['ceiling_height_m']:.2f} m
         &middot; area {q['floor_area_m2']:.2f} m&sup2;</p>
      <p class="conf">dimensional {c['dimensional']['score_pct']:.0f}%
         &middot; photographic corroboration {c['photographic_corroboration']['score_pct']:.0f}%
         &middot; {ev} wall photographs attached</p>
      <img src="{html.escape(rid)}.svg" alt="{html.escape(rid)} floor plan">
      <details><summary>wall evidence ({len(photos)} shown)</summary><ul>{''.join(photos)}</ul></details>
      <p><a href="{html.escape(rid)}.json">JSON</a> &middot;
         <a href="{html.escape(rid)}.svg">SVG</a></p>
    </section>""")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Field site report - scan2plan</title>
<style>
 body{{font:15px/1.55 system-ui,sans-serif;margin:0 auto;max-width:1000px;padding:32px;color:#1a1a1a}}
 header{{border-bottom:3px solid #1a1a1a;padding-bottom:14px;margin-bottom:8px}}
 h1{{margin:0 0 6px;font-size:26px}} h2{{margin:0 0 4px;font-size:19px;text-transform:capitalize}}
 .sub{{color:#555;margin:0 0 18px}}
 .banner{{background:#fff4e5;border-left:5px solid #d97706;padding:12px 16px;margin:16px 0}}
 section{{border-top:1px solid #ddd;padding:20px 0}}
 .dims{{font-size:17px;margin:0 0 4px;font-variant-numeric:tabular-nums}}
 .conf{{color:#555;margin:0 0 12px;font-size:13px}}
 img{{max-width:460px;border:1px solid #ccc;background:#fff}}
 ul{{font-size:13px;color:#333}} .w{{color:#777}}
 details{{margin:8px 0}} a{{color:#0b5fff}}
</style></head><body>
<header>
  <h1>Field site report</h1>
  <p class="sub">Three rooms of a real 3BHK, captured 2026-10-04.
     Generated by scan2plan.</p>
</header>
<div class="banner">
  <b>Geometry source: steel tape measurement.</b> These dimensions were measured,
  not reconstructed from imagery. Monocular depth was run over all 238 photographs
  and is reported separately; on this capture it is not metric-grade because no
  frame contains a scale reference. Wall photographs are attached as evidence for
  each surface and were not used to derive any dimension.
</div>
{''.join(rows)}
</body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", default=os.path.join(ROOT, os.pardir, "capture"))
    ap.add_argument("--truth", default=os.path.join(ROOT, "data", "field_ground_truth.json"))
    ap.add_argument("--damage", default=os.path.join(ROOT, "runs", "openings_and_damage.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "field"))
    args = ap.parse_args()

    capture_dir = os.path.abspath(args.capture)
    with open(args.truth, encoding="utf-8") as fh:
        truth = json.load(fh)

    damage_by_room: dict[str, list] = {}
    if os.path.isfile(args.damage):
        with open(args.damage, encoding="utf-8") as fh:
            for entry in json.load(fh).get("damage", []):
                damage_by_room.setdefault(entry["room_id"], []).append(entry)

    items = scan_capture(capture_dir)

    os.makedirs(args.out, exist_ok=True)
    docs = []
    for room_truth in truth["rooms"]:
        rid = room_truth["id"]
        mine = [i for i in items if i.room_id == rid]

        photos_by_wall: dict[int, list[str]] = {}
        photos_by_surface: dict[str, list[str]] = {}
        for it in mine:
            rel = os.path.join(it.folder, it.filename)
            if it.surface == "wall" and it.wall_index:
                photos_by_wall.setdefault(it.wall_index, []).append(rel)
            elif it.surface:
                photos_by_surface.setdefault(it.surface, []).append(rel)

        evidence = {"damage": damage_by_room.get(rid, [])}
        doc = build_document(room_truth, evidence, photos_by_wall, photos_by_surface)
        docs.append(doc)

        base = os.path.join(args.out, rid)
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        with open(base + ".svg", "w", encoding="utf-8") as fh:
            fh.write(render_svg(doc))
        print(f"{rid}: {doc['quality']['edge_lengths_m'][:2]} m, "
              f"area {doc['quality']['floor_area_m2']:.2f} m2, "
              f"{sum(len(v) for v in photos_by_wall.values())} wall photos")

    index = render_index(docs, args.out)
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(index)
    print(f"\nwrote {len(docs)} room plans + index.html to {args.out}")


if __name__ == "__main__":
    main()
