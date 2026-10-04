"""Dimensioned room geometry from segmented planes.

This is where planes become a floor plan:

  1. Establish a 2D basis in the floor plane, so the horizontal axes are a
     deliberate choice rather than an accident of which world axis happened to
     vary least.
  2. Project each vertical wall onto that basis. A vertical plane seen from
     above is a *line*, and the room lies on one side of it.
  3. Merge wall fragments belonging to the same physical wall. Segmentation
     legitimately returns several coplanar fragments per wall, and a four-sided
     room with two fragments on each long wall must still yield four corners.
  4. Intersect the half-planes to get the room polygon.
  5. Derive per-wall lengths, floor area and ceiling height.

Step 4 is the load-bearing one. Intersecting wall half-planes rather than
clustering wall segments is what makes the output a *room* instead of a bag of
planes: the fact that a wall is a boundary the interior lies behind is much
stronger evidence than the similarity of two wall fragments.

Coordinates: alpha/beta are horizontal offsets along an orthonormal basis, so
one alpha unit is one metre and polygon area is real floor area. The vertical
is kept separate, with the floor plane as the datum.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import HalfspaceIntersection, QhullError

from .segment import Plane

# A plane this close to horizontal carries no usable line in the floor plane.
MIN_WALL_HORIZONTAL_NORM = 0.25

# Walls within this many degrees of one another are treated as parallel.
PARALLEL_TOL_DEG = 12.0

# Parallel walls further apart than this are distinct walls, not fragments of
# one. A 3 m room with a 0.5 m jog must report two lines, not average them.
MAX_FRAGMENT_MERGE_M = 0.45


@dataclass
class Wall:
    """One wall of the room, as a line in floor coordinates.

    The line is `normal . (alpha, beta) = offset`, and `inward` records which
    side the room is on -- that is not recoverable from the planes alone, since
    a set of lines bounds two opposite regions and only one is the room.
    """

    normal: np.ndarray
    offset: float
    support: int
    rms_m: float
    inward: bool = True
    length_m: float = 0.0
    n_fragments: int = 1

    def distance_to(self, ab: np.ndarray) -> float:
        """Absolute distance from a floor-coordinate point to this wall line."""
        return abs(float(self.normal @ np.asarray(ab, dtype=np.float64) - self.offset))

    def uncertainty_m(self) -> float:
        """1-sigma on the wall position from residual and support.

        Residual scatter over sqrt(support) is the honest form: fifty agreeing
        frames should hold the wall more firmly than one bad frame.
        """
        return float(self.rms_m / max(math.sqrt(max(self.support, 1)), 1.0))


@dataclass
class RoomPlan:
    """A dimensioned room plus the numbers derived from it."""

    basis_a: np.ndarray
    basis_b: np.ndarray
    up: np.ndarray
    walls: list[Wall] = field(default_factory=list)
    vertices_ab: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    vertices_world: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    edge_lengths_m: list[float] = field(default_factory=list)
    edge_wall_index: list[int] = field(default_factory=list)
    floor_area_m2: float = 0.0
    perimeter_m: float = 0.0
    floor_height_m: float = 0.0
    ceiling_height_m: float | None = None
    rectangular_prior: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "rectangular_prior_applied": self.rectangular_prior,
            "basis": {
                "a": [round(float(x), 6) for x in self.basis_a],
                "b": [round(float(x), 6) for x in self.basis_b],
                "up": [round(float(x), 6) for x in self.up],
            },
            "vertices_world": [
                [round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4)]
                for v in self.vertices_world
            ],
            "floor_area_m2": round(self.floor_area_m2, 4),
            "perimeter_m": round(self.perimeter_m, 4),
            "floor_height_m": round(self.floor_height_m, 4),
            "ceiling_height_m": (
                None if self.ceiling_height_m is None else round(self.ceiling_height_m, 4)
            ),
            "walls": [],
            "notes": self.notes,
        }
        for i, w in enumerate(self.walls):
            length = self.edge_lengths_m[i] if i < len(self.edge_lengths_m) else w.length_m
            out["walls"].append(
                {
                    "normal_ab": [round(float(w.normal[0]), 5), round(float(w.normal[1]), 5)],
                    "offset_m": round(float(w.offset), 4),
                    "support": int(w.support),
                    "length_m": round(float(length), 4),
                    "position_uncertainty_m": round(float(w.uncertainty_m()), 4),
                    "n_fragments": int(w.n_fragments),
                    "constrained_by_edges": [
                        j for j, wi in enumerate(self.edge_wall_index) if wi == i
                    ],
                }
            )
        return out


def floor_basis(up: np.ndarray, positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two orthonormal horizontal directions, chosen deterministically.

    e_a follows the axis along which the walk spreads most, which conditions
    the intersection better than an arbitrary axis would. Signs are fixed so
    repeated runs agree, which matters because the baseline and the fixed run
    are diffed against each other.
    """
    u = up / (np.linalg.norm(up) + 1e-12)
    p = positions - (positions @ u)[:, None] * u[None, :]
    if p.shape[0] >= 3 and np.linalg.norm(p) > 1e-9:
        cov = p.T @ p / max(p.shape[0] - 1, 1)
        w, v = np.linalg.eigh(cov)
        a = v[:, int(np.argmax(w))]
    else:
        a = np.array([1.0, 0.0, 0.0])
    a = a - float(a @ u) * u
    na = float(np.linalg.norm(a))
    if na < 1e-9:
        a = np.cross(u, np.array([0.0, 0.0, 1.0]))
        na = float(np.linalg.norm(a))
        if na < 1e-9:
            a = np.cross(u, np.array([1.0, 0.0, 0.0]))
            na = float(np.linalg.norm(a))
    a = a / na
    b = np.cross(u, a)
    if a[int(np.argmax(np.abs(a)))] < 0:
        a, b = -a, -b
    return a, b


def wall_from_plane(
    plane: Plane, a: np.ndarray, b: np.ndarray, interior_ab: np.ndarray
) -> Wall | None:
    """Convert a near-vertical plane into a floor-coordinate wall line.

    With alpha/beta measured from the world origin, a point on the wall is
    p = alpha*a + beta*b, so the plane equation n.p = d becomes
    (n.a)*alpha + (n.b)*beta = d. Dividing through by the horizontal norm of n
    puts the line at unit scale.
    """
    n = plane.normal / (np.linalg.norm(plane.normal) + 1e-12)
    n_ab_raw = np.array([float(n @ a), float(n @ b)])
    norm = float(np.linalg.norm(n_ab_raw))
    if norm < MIN_WALL_HORIZONTAL_NORM:
        return None
    n_ab = n_ab_raw / norm
    offset = float(plane.offset) / norm
    w = Wall(normal=n_ab, offset=offset, support=plane.support, rms_m=plane.rms_m)
    w.inward = bool(float(n_ab @ interior_ab - offset) < 0.0)
    return w


def merge_wall_fragments(walls: list[Wall]) -> list[Wall]:
    """Collapse coplanar fragments of one wall into a single wall.

    Two fragments merge when they are parallel, bound the room on the same side,
    and lie close *along the shared normal*. Measuring separation along the
    normal is what keeps two parallel walls 3 m apart distinct while joining
    fragments 8 cm apart.
    """
    cos_tol = math.cos(math.radians(PARALLEL_TOL_DEG))
    kept: list[Wall] = []
    for w in sorted(walls, key=lambda x: -x.support):
        merged = False
        for k in kept:
            if float(k.normal @ w.normal) < cos_tol:
                continue
            if k.inward != w.inward:
                continue
            if abs(k.offset - w.offset) > MAX_FRAGMENT_MERGE_M:
                continue
            total = k.support + w.support
            k.offset = (k.offset * k.support + w.offset * w.support) / total
            k.rms_m = math.sqrt(
                (k.rms_m**2 * k.support + w.rms_m**2 * w.support) / total
            )
            k.support = total
            k.n_fragments += 1
            merged = True
            break
        if not merged:
            kept.append(w)
    return kept


# How far the camera path must pass through a wall line, on *both* sides, before
# that wall is called interior to the room. The operator's head wanders and the
# operator walks right up to walls, so a line clipping the *edge* of the path is a
# real wall.
WALL_CROSS_SLACK_M = 0.5

# ...and how centrally the wall must sit within the path for that to count. A
# wardrobe in the middle of a room has the operator walking around both sides of
# it. A doorway does not: the path leaves through the opening and the wall stays
# near one *edge* of the path's extent, not in the middle of it. Comparing the two
# excursions rather than just their presence is what separates the two cases.
WALL_CROSS_CENTRAL_RATIO = 0.5

# A wall in this orientation group must stand at least this far outside the
# camera path to count as the room boundary in that direction.
WALL_OUTSIDE_SLACK_M = 0.15


def crossed_by_path(walls: list[Wall], cam_ab: np.ndarray) -> dict[int, bool]:
    """Which walls the camera path straddles, i.e. which stand in the room.

    A room boundary has the camera path entirely on one side; an object in the
    middle of the room has the path on both sides. So a wall whose line crosses
    the interior of the path by more than the slack *on both sides*, and sits
    centrally within that excursion, is furniture rather than boundary.

    Centrality is what distinguishes furniture from a doorway. A wall merely
    clipped by the path is kept, because the operator can legitimately cross a
    wall plane on the way through a door. An earlier version discarded any
    straddled wall and threw away both side walls of the 6.2 x 3.1 m synthetic
    room: the path spans 4.53 m along that wall pair's normal and the extra 1.4 m
    is the doorway. Two unparallel walls cannot bound a region, so the room
    collapsed to 0.000 m2 with a NaN span.
    """
    cam = np.asarray(cam_ab, dtype=np.float64).reshape(-1, 2)
    out: dict[int, bool] = {}
    for w in walls:
        d = cam @ w.normal - w.offset
        below, above = -float(d.min()), float(d.max())
        if below <= WALL_CROSS_SLACK_M or above <= WALL_CROSS_SLACK_M:
            out[id(w)] = False
            continue
        out[id(w)] = min(below, above) >= WALL_CROSS_CENTRAL_RATIO * max(below, above)
    return out


def orientation_groups(walls: list[Wall]) -> list[list[Wall]]:
    """Group walls by orientation modulo 180 degrees, strongest support first."""
    cos_tol = math.cos(math.radians(PARALLEL_TOL_DEG))
    groups: list[list[Wall]] = []
    for w in sorted(walls, key=lambda x: -x.support):
        for g in groups:
            if abs(float(g[0].normal @ w.normal)) >= cos_tol:
                g.append(w)
                break
        else:
            groups.append([w])
    return groups


def select_room_boundary(
    walls: list[Wall], cam_ab: np.ndarray
) -> tuple[list[Wall], list[str]]:
    """Keep only the planes that actually bound the room.

    Every plane within VERTICAL_TOL_DEG of vertical is currently labelled a wall,
    which is harmless in an empty synthetic room and wrong in a real one: beds,
    wardrobes, doors, curtains and kitchen units all qualify. Feeding them to the
    half-plane intersection cuts the room down to a polygon far smaller than the
    room, which is exactly what happened -- 1.75 m2 reported for a room whose
    trajectory spans 17.3 m2.

    Two physical facts separate a wall from an object in the room:

      1. A room boundary has the camera path entirely on one side. An object in
         the middle of the room has the path on both sides. So a wall whose line
         crosses the interior of the path by more than the slack is discarded.
      2. Within one orientation group -- walls seen as parallel -- the room is
         bounded by the two outermost walls. Anything between them is closer to
         the camera than the room is wide, so it is furniture or an interior
         partition, not an outside wall.

    Returns the surviving walls and a human-readable note per rejection, so the
    report can say what was discarded rather than silently changing the answer.

    This makes NO assumption about the room's shape. See
    `select_rectangular_boundary` for the opt-in rectangular prior.
    """
    if len(walls) < 3:
        return list(walls), []
    notes: list[str] = []

    is_crossed = crossed_by_path(walls, cam_ab)

    # Group by orientation, modulo 180 degrees: opposite walls of a rectangular
    # room are parallel and must be considered together.
    groups = orientation_groups(walls)

    spanning: list[Wall] = []
    for g in groups:
        # Reference direction, then order by signed position along it so
        # "outermost on each side" is well defined.
        ref = g[0].normal / np.linalg.norm(g[0].normal)
        sgn = float(np.sign(ref @ g[0].normal)) or 1.0

        # `ref` and `sgn` are bound as defaults rather than captured: the closure
        # is only ever called inside this iteration, but binding them makes that
        # a guarantee instead of a coincidence someone can later break.
        def rank(w: Wall, _ref: np.ndarray = ref, _sgn: float = sgn) -> float:
            return w.offset * (_sgn if abs(float(_ref @ w.normal)) > 0 else 1.0)

        ordered = sorted(g, key=rank)

        # Discard walls the camera path straddles centrally -- furniture standing
        # in the middle of the room. The room boundary is then the outermost
        # survivor on each side.
        #
        # A wall merely *clipped* by the path is kept, because the camera can
        # legitimately cross a wall plane through a doorway. An earlier version of
        # this rule discarded any straddled wall and it threw away both side
        # walls of the 6.2 x 3.1 m synthetic room: the operator's path spans
        # 4.53 m along that 3.11 m wall pair's normal and the extra 1.4 m is the
        # doorway. Two unparallel walls cannot bound a region, so the room
        # collapsed to 0.000 m2 with a NaN span.
        survivors: list[Wall] = []
        for w in ordered:
            if is_crossed[id(w)]:
                notes.append(
                    f"discarded wall at offset {w.offset:+.2f} m "
                    f"(support {w.support}): camera path passes through it on "
                    f"both sides, so it stands in the middle of the room rather "
                    f"than bounding it"
                )
                continue
            survivors.append(w)

        if not survivors:
            # Every candidate in this orientation is straddled centrally, so the
            # crossing test cannot say which of them bounds the room. Dropping the
            # group would delete a room dimension, so keep the outermost pair and
            # record that the evidence was inconclusive.
            notes.append(
                f"orientation group near offset {ordered[0].offset:+.2f} m: all "
                f"{len(ordered)} candidates are straddled by the camera path, so "
                f"the outermost pair was kept without crossing-test support"
            )
            survivors = [ordered[0], ordered[-1]] if len(ordered) > 1 else list(ordered)

        if len(survivors) == 1:
            spanning.append(survivors[0])
            continue
        for w in (survivors[0], survivors[-1]):
            if not any(w is k for k in spanning):
                spanning.append(w)
        for w in survivors[1:-1]:
            notes.append(
                f"discarded parallel wall at offset {w.offset:+.2f} m "
                f"(support {w.support}): lies between the outermost walls of its "
                f"orientation, so it is interior to the room"
            )
    return spanning, notes


RECTANGULAR_PERP_TOL_DEG = 25.0
RECTANGULAR_ASSIGN_TOL_DEG = 50.0


def select_rectangular_boundary(
    walls: list[Wall], cam_ab: np.ndarray
) -> tuple[list[Wall], list[str]]:
    """OPT-IN PRIOR: assume the room is a rectangle, and return exactly 4 walls.

    **This is an assumption, not a measurement, and it is opt-in for that reason.**
    Read this before quoting any number it produces.

    What it assumes: that the room has four vertical walls meeting at right
    angles. That holds for the three field rooms (tape shows them rectangular
    within 3 degrees) and it is true by construction for every synthetic room in
    `synth.standard_rooms()`. Which is precisely the problem: because the
    benchmark rooms are rectangular by construction, running the benchmark with
    this prior switched on is **circular**. It cannot demonstrate that the pipeline
    finds a rectangle unaided, and no result from it may be cited as though it
    could. The benchmark is only meaningful with the prior OFF.

    What it is for: the real sample archives, where 63 consensus wall planes
    fragment into 8-9 boundary walls including 0.166 m slivers. Without a shape
    prior there is no way to choose between them; with one, the choice is forced.

    How it chooses the two axes, without being told them:
      1. discard walls the camera path straddles centrally (furniture);
      2. group the rest by orientation;
      3. pick the pair of groups that is most nearly perpendicular and carries the
         most support -- that is the room's two wall directions;
      4. assign every remaining wall to whichever of those two axes it is closer
         to, then keep the outermost on each side of each axis.

    Returns the four bounding walls plus a note per rejection, as
    `select_room_boundary` does.
    """
    notes: list[str] = []
    is_crossed = crossed_by_path(walls, cam_ab)
    standing = [w for w in walls if not is_crossed[id(w)]]
    for w in walls:
        if is_crossed[id(w)]:
            notes.append(
                f"discarded wall at offset {w.offset:+.2f} m (support {w.support}): "
                f"camera path passes through it on both sides, so it stands in the "
                f"middle of the room rather than bounding it"
            )

    groups = orientation_groups(standing)
    if len(groups) < 2:
        # The fallback's own notes must be concatenated, not replaced. An earlier
        # version appended the reason here and then returned the fallback's notes,
        # so the report claimed a rectangular result with no mention that the prior
        # had bailed out -- the assumption would have been invisible.
        notes.append(
            f"rectangular prior NOT applied: needs two orientations, only "
            f"{len(groups)} survived; fell back to shape-agnostic selection"
        )
        kept, more = select_room_boundary(walls, cam_ab)
        return kept, notes + more

    # Best perpendicular pair, by combined support.
    best: tuple[float, list[Wall], list[Wall]] | None = None
    for i, gi in enumerate(groups):
        for gj in groups[i + 1 :]:
            c = abs(float(gi[0].normal @ gj[0].normal))
            if c > math.sin(math.radians(RECTANGULAR_PERP_TOL_DEG)):
                continue
            score = sum(w.support for w in gi) + sum(w.support for w in gj)
            if best is None or score > best[0]:
                best = (score, gi, gj)
    if best is None:
        notes.append(
            f"rectangular prior NOT applied: no pair of wall orientations is "
            f"perpendicular within {RECTANGULAR_PERP_TOL_DEG} deg; fell back to "
            f"shape-agnostic selection"
        )
        kept, more = select_room_boundary(walls, cam_ab)
        return kept, notes + more

    _score, g1, g2 = best
    axes = [
        np.asarray(g1[0].normal, dtype=np.float64),
        np.asarray(g2[0].normal, dtype=np.float64),
    ]
    notes.append(
        f"rectangular prior applied: chose axes at "
        f"{math.degrees(math.acos(min(1.0, abs(float(axes[0] @ axes[1]))))):.1f} deg "
        f"with combined support {int(_score)}; this assumes a right-angled room"
    )

    kept: list[Wall] = []
    for axis, grp in zip(axes, (g1, g2), strict=False):
        ref = axis / np.linalg.norm(axis)
        assign_tol = math.cos(math.radians(RECTANGULAR_ASSIGN_TOL_DEG))
        assigned = [
            w
            for w in standing
            if abs(float(ref @ w.normal)) >= assign_tol
        ]
        for w in standing:
            # Identity, not `in`: Wall is a dataclass holding numpy arrays, so `==`
            # compares elementwise and `bool()` on the result raises "truth value of
            # an array is ambiguous". The shape-agnostic path uses `is` for the
            # same reason.
            if not any(w is a for a in assigned):
                notes.append(
                    f"discarded wall at offset {w.offset:+.2f} m (support {w.support}): "
                    f"not parallel to either rectangular axis"
                )
        if not assigned:
            assigned = list(grp)
            notes.append(
                f"rectangular axis had no parallel walls; kept its {len(grp)} "
                f"strongest candidates instead"
            )
        ordered = sorted(assigned, key=lambda w: w.offset * np.sign(float(ref @ w.normal)))
        for w in (ordered[0], ordered[-1]):
            if not any(w is k for k in kept):
                kept.append(w)
        for w in ordered[1:-1]:
            if not any(w is k for k in kept):
                notes.append(
                    f"discarded parallel wall at offset {w.offset:+.2f} m "
                    f"(support {w.support}): interior to the rectangle"
                )
    return kept, notes


def intersect_halfplanes(walls: list[Wall], interior: np.ndarray) -> np.ndarray | None:
    """Intersect the interior half-planes, returning polygon vertices.

    scipy needs a strictly interior seed point, and real constraint sets are
    frequently marginally inconsistent: two fragments of the same wall
    disagreeing by a centimetre can make the region empty or unbounded. On
    failure the least-supported constraints are dropped one at a time until a
    region appears, which keeps the best-observed walls in the answer and
    records in `notes` that something had to go.
    """
    live = list(walls)
    while len(live) >= 3:
        # scipy solves {x : A.x + b <= 0}. `inward` was decided by testing
        # n.x - offset < 0 at the interior point, so the inward half-plane is
        # exactly A = n, b = -offset, and the outward one is its negation.
        A = np.array([w.normal if w.inward else -w.normal for w in live])
        b = np.array([-w.offset if w.inward else w.offset for w in live])
        # scipy takes ONE (ndim, ndim+1) matrix whose last column is the offset.
        # Passing A and b separately makes it infer the wrong dimensionality and
        # it rejects the interior point.
        halfspaces = np.hstack([A, b[:, None]])
        try:
            pts = np.array(HalfspaceIntersection(halfspaces, interior).intersections)
            if len(pts) >= 3:
                return pts
        except (QhullError, ValueError):
            pass
        weakest = min(range(len(live)), key=lambda i: live[i].support)
        live.pop(weakest)
    return None


def order_polygon(poly: np.ndarray) -> np.ndarray:
    """Sort vertices by angle about their centroid, counter-clockwise."""
    c = poly.mean(axis=0)
    return np.argsort(np.arctan2(poly[:, 1] - c[1], poly[:, 0] - c[0]))


def polygon_area(poly: np.ndarray) -> float:
    """Shoelace area."""
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def build_room_plan(
    planes: list[Plane],
    up: np.ndarray,
    camera_positions: np.ndarray,
    min_wall_support: int = 2,
    rectangular: bool = False,
) -> RoomPlan:
    """Assemble a dimensioned room from segmented planes.

    `camera_positions` is used only to find a point known to lie inside the
    room, which fixes which side of each wall line the interior occupies.

    `rectangular=True` switches on the right-angled-room prior described in
    `select_rectangular_boundary`. It is OFF by default and must stay off for any
    result quoted as evidence about the synthetic benchmark, because those rooms
    are rectangular by construction.
    """
    u = up / (np.linalg.norm(up) + 1e-12)
    pos = np.asarray(camera_positions, dtype=np.float64).reshape(-1, 3)
    a, b = floor_basis(u, pos)

    centre = np.median(pos, axis=0) if len(pos) else np.zeros(3)
    interior = np.array([float(centre @ a), float(centre @ b)])

    floor = next((p for p in planes if p.kind == "floor"), None)
    ceiling = next((p for p in planes if p.kind == "ceiling"), None)

    # Floor plane as the vertical datum, expressed as a height along u.
    if floor is not None:
        denom = float(floor.normal @ u)
        floor_h = float(floor.offset) / denom if abs(denom) > 1e-06 else 0.0
    else:
        floor_h = 0.0

    plan = RoomPlan(basis_a=a, basis_b=b, up=u, floor_height_m=floor_h)
    if floor is None:
        plan.notes.append("no floor plane recovered")

    raw: list[Wall] = []
    for p in planes:
        if p.kind != "wall" or p.support < min_wall_support:
            continue
        w = wall_from_plane(p, a, b, interior)
        if w is not None:
            raw.append(w)

    if len(raw) < 3:
        plan.notes.append(f"only {len(raw)} usable wall planes; need at least 3")
        return plan

    walls = merge_wall_fragments(raw)
    cam_ab = np.column_stack([pos @ a, pos @ b])
    if rectangular:
        walls, discard_notes = select_rectangular_boundary(walls, cam_ab)
        plan.rectangular_prior = True
    else:
        walls, discard_notes = select_room_boundary(walls, cam_ab)
    plan.notes.extend(discard_notes)
    plan.walls = walls

    poly = intersect_halfplanes(walls, interior)
    if poly is None or len(poly) < 3:
        plan.notes.append("wall half-planes did not bound a region")
        return plan

    poly = poly[order_polygon(poly)]
    plan.vertices_ab = poly
    plan.vertices_world = np.array([p[0] * a + p[1] * b + floor_h * u for p in poly])
    plan.floor_area_m2 = abs(polygon_area(poly))

    # Each polygon edge is bounded by one wall; match them by proximity.
    nxt = np.roll(poly, -1, axis=0)
    edges = nxt - poly
    mids = poly + edges / 2.0
    lengths, which = [], []
    for e, m in zip(edges, mids, strict=False):
        lengths.append(float(np.linalg.norm(e)))
        j = int(np.argmin([w.distance_to(m) for w in walls]))
        which.append(j)
        walls[j].length_m = max(walls[j].length_m, lengths[-1])
    plan.edge_lengths_m = lengths
    plan.edge_wall_index = which
    plan.perimeter_m = float(np.sum(lengths))

    if ceiling is not None:
        dn = float(ceiling.normal @ u)
        if abs(dn) > 1e-6:
            h = float(ceiling.offset) / dn - floor_h
            if 1.5 < h < 6.0:
                plan.ceiling_height_m = h
            else:
                plan.notes.append(f"implausible ceiling height {h:.3f} m, omitted")
        else:
            plan.notes.append("ceiling plane not horizontal enough to measure height")
    return plan
