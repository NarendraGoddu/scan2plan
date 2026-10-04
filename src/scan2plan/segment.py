"""Plane segmentation: per-frame fitting, then cross-frame consensus.

Architecture, and why it is this way
------------------------------------
The obvious approach is to concatenate every frame into one giant cloud and
run RANSAC on it. That is the wrong shape for this data and this hardware.

It is wrong because the depth maps are decimated 7.5x, so each frame carries
only a few thousand usable points. Fitting per frame keeps every RANSAC problem
small enough to be exact rather than lucky, and then agreeing across thousands
of independent fits is what produces a precise plane. A plane orientation
estimated from 40,000 views is far tighter than one estimated from a single
noisy frame, and this is where the sub-centimetre accuracy actually comes from
-- not from resolution.

It is also the only affordable option under numpy + Pillow, where a multi-million
point RANSAC would be hopeless.

So: fit planes within each frame, express each as (normal, offset) in world
coordinates, then cluster those equations across frames. A cluster with high
support is a real surface. Support count, residual scatter and the spread of
member fits all fall out of this for free, which is exactly the raw material the
confidence-interval gate needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .geometry import fit_plane_ransac, orthonormalize
from .ingest import Capture, poses_to_matrices
from .unproject import frame_points_world, gravity_axis

# A point subsample is retained per plane per frame so the merged plane can be
# refitted by total least squares and given real residuals, rather than
# inheriting the spread of dozens of independent small fits.
POINTS_PER_PLANE = 240


@dataclass
class PlaneObservation:
    """One plane seen in one frame."""

    normal: np.ndarray
    offset: float
    frame: int
    n_inliers: int
    rms_m: float
    points: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 3)))


@dataclass
class Plane:
    """A consensus plane merged across many frames."""

    normal: np.ndarray
    offset: float
    support: int  # number of frames that saw it
    n_frames_total: int
    rms_m: float
    kind: str = "unknown"  # floor | ceiling | wall | other
    inlier_points: np.ndarray = field(repr=False, default_factory=lambda: np.zeros((0, 3)))
    # Set by consolidate_horizontal: how far the planes that were folded
    # together disagreed, in metres. Carried as a first-class field because the
    # ceiling-height confidence interval depends on it.
    member_height_spread_m: float = 0.0
    n_consensus_members: int = 0

    @property
    def frame_fraction(self) -> float:
        return self.support / max(self.n_frames_total, 1)

    def signed_distance(self, p: np.ndarray) -> np.ndarray:
        return self.normal @ np.asarray(p) - self.offset

    def angle_to(self, axis: np.ndarray) -> float:
        c = float(np.clip(abs(self.normal @ axis) / (np.linalg.norm(axis) + 1e-12), 0.0, 1.0))
        return float(np.degrees(np.arccos(c)))


def canonical_plane(normal: np.ndarray, offset: float) -> tuple[np.ndarray, float]:
    """Put (n, d) in a deterministic hemisphere so n and -n compare equal.

    Without this, the same physical wall observed from two sides produces two
    equations that look 180 degrees apart and never cluster.

    The normal is scaled here rather than via `orthonormalize`, because that
    helper canonicalises the *sign* of the normal: passing it a normal and
    keeping the old offset silently describes a different plane. For
    (n=(0,-1,0), d=2.5) it returns (0,1,0) with d untouched, which is the
    mirror surface one floor-height away. So sign and offset must move together,
    and the scale must not touch the sign on its own.
    """
    n = np.asarray(normal, dtype=np.float64).reshape(3)
    norm = float(np.linalg.norm(n))
    if norm < 1e-12:
        raise ValueError("canonical_plane: degenerate normal")
    n = n / norm
    if n[int(np.argmax(np.abs(n)))] < 0:
        n, offset = -n, -float(offset)
    return n, float(offset)


def fit_frame_planes(
    points: np.ndarray,
    frame: int,
    max_planes: int = 4,
    inlier_thresh: float = 0.02,
    min_inliers: int = 400,
    max_iterations: int = 220,
) -> list[PlaneObservation]:
    """Extract up to `max_planes` planes from one frame's points.

    RANSAC is re-run on the remaining points each time, peeling planes off in
    order of support. Each retained plane keeps a point subsample for the
    later consensus refit.
    """
    remaining = points
    out: list[PlaneObservation] = []
    for _ in range(max_planes):
        if remaining.shape[0] < min_inliers:
            break
        n, d, inl = fit_plane_ransac(
            remaining, inlier_thresh=inlier_thresh, max_iterations=max_iterations
        )
        count = int(inl.sum())
        if count < min_inliers:
            break
        seg = remaining[inl]
        resid = np.abs(seg @ n - d)
        n, d = canonical_plane(n, d)
        # RANSAC may report the plane with either orientation; re-derive the
        # offset from the canonical normal so n and d stay consistent.
        d = float(np.median(seg @ n))
        if seg.shape[0] > POINTS_PER_PLANE:
            step = int(np.ceil(seg.shape[0] / POINTS_PER_PLANE))
            keep = seg[::step][:POINTS_PER_PLANE]
        else:
            keep = seg
        out.append(
            PlaneObservation(
                normal=n,
                offset=d,
                frame=frame,
                n_inliers=count,
                rms_m=float(np.sqrt(np.mean(resid**2))),
                points=keep,
            )
        )
        remaining = remaining[~inl]
    return out


def cluster_planes(
    observations: list[PlaneObservation],
    angle_tol_deg: float = 6.0,
    offset_tol_m: float = 0.10,
    n_frames_total: int = 1,
) -> list[Plane]:
    """Merge per-frame planes that describe the same physical surface.

    Two planes join the same cluster when their normals agree within
    `angle_tol_deg` and their offsets agree within `offset_tol_m`. The offset
    test is what separates two collinear-but-distinct surfaces, and it is why
    the merge can tell a far wall from a near one.
    """
    clusters: list[dict] = []
    cos_tol = float(np.cos(np.radians(angle_tol_deg)))

    # RANSAC and the SVD refit both return a normal with arbitrary sign, so the
    # same wall seen from two sides arrives as n and -n. Canonicalising (normal
    # and offset together, so the plane itself is preserved) before any
    # comparison is what makes cross-frame merging possible at all.
    observations = [
        PlaneObservation(
            normal=cn,
            offset=co,
            frame=o.frame,
            n_inliers=o.n_inliers,
            rms_m=o.rms_m,
            points=o.points,
        )
        for o, (cn, co) in (
            (o, canonical_plane(o.normal, o.offset)) for o in observations
        )
    ]

    def projected_offset(obs: PlaneObservation, along: np.ndarray) -> float:
        """Where this plane sits along `along`, comparable across members.

        A member's own offset is measured along its own normal, so for two
        nearly-parallel planes to be compared they must both be re-expressed
        along the same direction.
        """
        return float(obs.offset * (obs.normal @ along))

    for obs in observations:
        placed = False
        for c in clusters:
            mean_n = c["mean_normal"]
            if float(mean_n @ obs.normal) < cos_tol:
                continue
            # Offset test is what separates two collinear-but-distinct
            # surfaces, e.g. a near wall from the wall behind it.
            if abs(projected_offset(obs, mean_n) - c["mean_offset"]) > offset_tol_m:
                continue
            c["members"].append(obs)
            w = c["weights"]
            total = w.sum() + obs.n_inliers
            # Running inlier-weighted mean normal and offset.
            c["mean_normal"] = orthonormalize(
                (w[:, None] * c["normals"]).sum(0) + obs.normal * obs.n_inliers
            )
            c["normals"] = np.vstack([c["normals"], obs.normal])
            c["weights"] = np.append(w, obs.n_inliers)
            c["mean_offset"] = (
                float((w * c["offsets"]).sum() + projected_offset(obs, c["mean_normal"]) * obs.n_inliers)
                / total
            )
            c["offsets"] = np.append(
                c["offsets"], projected_offset(obs, c["mean_normal"])
            )
            placed = True
            break
        if not placed:
            clusters.append(
                {
                    "members": [obs],
                    "normals": obs.normal[None, :],
                    "offsets": np.array([obs.offset]),
                    "weights": np.array([float(obs.n_inliers)]),
                    "mean_normal": orthonormalize(obs.normal),
                    "mean_offset": float(obs.offset),
                }
            )

    planes: list[Plane] = []
    for c in clusters:
        members: list[PlaneObservation] = c["members"]
        w = np.array([m.n_inliers for m in members], dtype=np.float64)
        n = (w[:, None] * np.stack([m.normal for m in members])).sum(0)
        n = orthonormalize(n)
        # Project each member offset onto the mean normal so near-parallel
        # members combine instead of cancelling.
        dots = np.array([float(m.normal @ n) for m in members])
        offs = np.array([m.offset for m in members]) * dots
        d = float((w * offs).sum() / max((w * dots**2).sum(), 1e-9))

        pooled = np.concatenate([m.points for m in members if m.points.size], axis=0)
        if pooled.shape[0] >= 60:
            # Final total-least-squares refit on the pooled points, then
            # residuals for the confidence band. vt[2] has arbitrary sign, so
            # the plane is re-canonicalised before it leaves this function.
            centroid = pooled.mean(0)
            _, _, vt = np.linalg.svd(pooled - centroid, full_matrices=False)
            n2, d2 = canonical_plane(vt[2], float(vt[2] @ centroid))
            resid = np.abs(pooled @ n2 - d2)
            planes.append(
                Plane(
                    normal=n2,
                    offset=d2,
                    support=len(members),
                    n_frames_total=n_frames_total,
                    rms_m=float(np.sqrt(np.mean(resid**2))),
                    inlier_points=pooled,
                )
            )
        else:
            resid = np.array([m.rms_m for m in members])
            n, d = canonical_plane(n, d)
            planes.append(
                Plane(
                    normal=n,
                    offset=d,
                    support=len(members),
                    n_frames_total=n_frames_total,
                    rms_m=float(np.mean(resid)),
                )
            )
    planes.sort(key=lambda p: -p.support)
    return planes


# Angle bands for classifying a consensus plane. A room's surfaces are strongly
# anisotropic -- floors and ceilings are near-horizontal, walls near-vertical --
# so generous bands are safe and keep low-support planes from being discarded.
HORIZONTAL_TOL_DEG = 20.0
VERTICAL_TOL_DEG = 25.0


def classify_planes(planes: list[Plane], up: np.ndarray, camera_height: float) -> list[Plane]:
    """Label planes floor / ceiling / wall using angle and height.

    Angle alone is not enough. An upward-tilted scan makes the ceiling its most
    strongly horizontal plane, and a ceiling mislabelled as a floor inverts the
    ceiling-height gate. Height relative to the camera settles it, exactly as it
    did in the drift measurement.
    """
    u = up / (np.linalg.norm(up) + 1e-12)
    for p in planes:
        horiz = p.angle_to(u) <= HORIZONTAL_TOL_DEG
        vert = p.angle_to(u) >= 90.0 - VERTICAL_TOL_DEG
        height = float(p.offset * (p.normal @ u))
        if horiz and height < camera_height - 0.6:
            p.kind = "floor"
        elif horiz and height > camera_height + 0.6:
            p.kind = "ceiling"
        elif vert:
            p.kind = "wall"
        else:
            p.kind = "other"
    return planes


def orient_from_horizontal_planes(
    planes: list[Plane], v: np.ndarray, cam_h: float
) -> tuple[np.ndarray, dict]:
    """Choose the sign of the vertical axis from where horizontal planes sit.

    The vertical comes out of an SVD as an *axis*, so its sign is whatever the
    linear algebra produced -- and that is not consistent across captures. It
    came out -Y for two archives and +Y for a third, which silently relabelled
    floor_only's floor as a ceiling sitting 1.27 m overhead. Classifying with the
    wrong sign inverts the ceiling-height gate rather than crashing.

    The physical fact that settles it: a handheld sensor is roughly 0.6-2.2 m
    above the floor, and the floor is *below* the camera. Whichever sign of the
    axis puts the bulk of the strongly-horizontal planes below the camera is
    the one pointing up.
    """
    v = v / (np.linalg.norm(v) + 1e-12)
    horiz = [p for p in planes if p.angle_to(v) <= HORIZONTAL_TOL_DEG]
    deltas, weights = [], []
    for p in horiz:
        d = float(p.offset * (p.normal @ v)) - cam_h
        if 0.6 <= abs(d) <= 2.2:
            deltas.append(d)
            weights.append(float(p.support))
    info = {
        "n_horizontal": len(horiz),
        "n_in_height_band": len(deltas),
        "flipped": False,
        "confidence": 0.0,
    }
    if not deltas:
        info["note"] = "no horizontal plane in handheld-height band; sign undetermined"
        return v, info

    deltas_arr = np.array(deltas)
    w_arr = np.array(weights)
    # Total weight below the camera vs above it.
    below = float(w_arr[deltas_arr < 0].sum())
    above = float(w_arr[deltas_arr > 0].sum())
    total = below + above
    if above > below:
        v = -v
        info["flipped"] = True
    info["confidence"] = float(max(below, above) / total) if total > 0 else 0.0
    info["weight_below"] = below
    info["weight_above"] = above
    return v, info


def flip_plane(p: Plane, v_new: np.ndarray) -> Plane:
    """Re-express a plane's offset under a flipped vertical axis."""
    # The plane equation n.p = d is unchanged; only the sign convention on which
    # side is "up" changed, so the stored plane is still valid. Nothing to do
    # unless the normal was stored relative to the axis, which it was not.
    return p


# How far below the camera the floor must be, in metres.
#
# Every horizontal surface under the camera is classified "floor", which includes
# beds, tables, sofas and counters. Choosing among them by support alone is
# unstable: on with_ceiling the best-supported candidate moves from -1.488 m
# (support 301) over the whole walk to -0.908 m (support 147) over the second half
# alone, because the halves do not see the same surfaces equally well. That put the
# two halves 801 mm apart on the floor height.
#
# A person walking a room holds a phone roughly 1.0-1.8 m above the floor, so a
# surface 0.87 m below the lens is not the floor of a room anyone was standing in.
# This is the cheap version of the point HorizonNet (CVPR'19) makes properly: the
# floor and ceiling are parameters of one layout, constrained by where the camera is
# in it, not two independent plane fits competing on observation counts.
FLOOR_REACH_MIN_M = 1.00
FLOOR_REACH_MAX_M = 2.10

# How far above the camera the ceiling may be. Looser at the bottom end because a
# low ceiling over a doorway or alcove is legitimate, and the ceiling choice was
# never the unstable one.
CEILING_REACH_MIN_M = 0.30
CEILING_REACH_MAX_M = 2.20


def consolidate_horizontal(
    planes: list[Plane], kind: str, up: np.ndarray, outlier_tol_m: float = 0.45,
    camera_height_m: float | None = None,
) -> Plane | None:
    """Reduce all planes of one kind (floor or ceiling) to a single surface.

    A room has exactly one floor and one ceiling, but per-frame plane fits are
    noisy where the surface is far away, low in frame or occluded. Clustering
    with a tight offset tolerance therefore shatters one physical floor into
    several parallel planes -- on with_ceiling it produced four, spanning 0.63
    to 1.57 m below the camera, and any pairwise ceiling-height answer from
    those disagrees by a metre.

    So rather than trusting the clustering to keep a floor together, the
    structure is imposed: take the best-supported plane of this kind as the
    consensus and treat the rest as observations of it. Members within
    `outlier_tol_m` are folded in to refine the fit; ones beyond it are kept out
    and reported as disagreements, because that spread is honest uncertainty
    about the surface rather than a second floor.
    """
    u = up / (np.linalg.norm(up) + 1e-12)
    cands = [p for p in planes if p.kind == kind]
    if not cands:
        return None

    heights = np.array([float(p.offset * (p.normal @ u)) for p in cands])

    # Reach is only meaningful when the camera height is known. Computing it
    # unconditionally crashed the no-camera-height path, which the synthetic
    # unit tests use.
    plausible = None
    if camera_height_m is not None:
        reach = camera_height_m - heights  # + = surface below the camera
        if kind == "floor":
            lo, hi = FLOOR_REACH_MIN_M, FLOOR_REACH_MAX_M
            plausible = (reach >= lo) & (reach <= hi)
        else:
            lo, hi = CEILING_REACH_MIN_M, CEILING_REACH_MAX_M
            plausible = (reach <= -lo) & (reach >= -hi)

    rejected: list[str] = []
    if plausible is not None and plausible.any():
        pool = [p for p, ok in zip(cands, plausible, strict=False) if ok]
        for p, ok, r in zip(cands, plausible, reach, strict=False):
            if not ok:
                rejected.append(
                    f"{p.offset * (p.normal @ u):+.3f} m "
                    f"(support {p.support}, camera {abs(r):.2f} m "
                    f"{'below' if kind == 'floor' else 'above'} it)"
                )
        cands = pool
        heights = np.array([float(p.offset * (p.normal @ u)) for p in cands])
        primary = max(cands, key=lambda p: p.support)
    else:
        if plausible is not None:
            rejected.append("none")
        cands.sort(key=lambda p: -p.support)
        primary = cands[0]
    p_height = float(primary.offset * (primary.normal @ u))
    within = np.abs(heights - p_height) <= outlier_tol_m

    if within.sum() == 1:
        return primary

    # Inlier-weighted consensus over the members that agree with the primary.
    w = np.array([p.support for p, keep in zip(cands, within, strict=False) if keep], dtype=np.float64)
    n = (w[:, None] * np.stack([p.normal for p, keep in zip(cands, within, strict=False) if keep])).sum(0)
    n = orthonormalize(n)
    offs = np.array(
        [
            float(p.offset * (p.normal @ n))
            for p, keep in zip(cands, within, strict=False)
            if keep
        ]
    )
    offset = float((w * offs).sum() / max(w.sum(), 1e-9))

    pooled = [
        p.inlier_points
        for p, keep in zip(cands, within, strict=False)
        if keep and p.inlier_points.size
    ]
    pooled_arr = np.concatenate(pooled, axis=0) if pooled else np.zeros((0, 3))
    if pooled_arr.shape[0] >= 60:
        centroid = pooled_arr.mean(0)
        _, _, vt = np.linalg.svd(pooled_arr - centroid, full_matrices=False)
        n2 = orthonormalize(vt[2])
        d2 = float(n2 @ centroid)
        resid = np.abs(pooled_arr @ n2 - d2)
        offset, n, rms = d2, n2, float(np.sqrt(np.mean(resid**2)))
    else:
        rms = float(np.mean([p.rms_m for p, keep in zip(cands, within, strict=False) if keep]))

    merged = Plane(
        normal=n,
        offset=offset,
        support=int(w.sum()),
        n_frames_total=primary.n_frames_total,
        rms_m=rms,
        kind=kind,
        inlier_points=pooled_arr,
    )
    # Record how much the rejected planes disagreed. This is a real signal for
    # the ceiling-height confidence interval, not bookkeeping.
    merged.member_height_spread_m = float(
        np.ptp(heights[~within]) if (~within).any() else 0.0
    )
    merged.n_consensus_members = int(within.sum())
    return merged


def segment_capture(
    capture: Capture,
    scale: float,
    frame_stride: int = 8,
    point_stride: int = 4,
    up: np.ndarray | None = None,
    max_planes_per_frame: int = 4,
    min_support: int = 3,
    verbose: bool = True,
) -> dict:
    """Full segmentation of one capture.

    Returns consensus planes plus the fitted vertical axis, so callers do not
    have to recompute (and potentially disagree about) which way is up.
    """
    T_cw = poses_to_matrices(capture.positions, capture.quats)
    u = gravity_axis(capture.positions) if up is None else up
    u = u / (np.linalg.norm(u) + 1e-12)
    cam_h = float(np.median(capture.positions @ u))

    n_frames = 0
    observations: list[PlaneObservation] = []
    for f in range(0, capture.n_frames, frame_stride):
        pts = frame_points_world(capture, f, scale, T_cw, stride=point_stride)
        if pts.shape[0] < 800:
            continue
        n_frames += 1
        observations.extend(
            fit_frame_planes(pts, f, max_planes=max_planes_per_frame)
        )

    if not observations:
        return {"planes": [], "up": u, "n_frames_used": 0, "camera_height_m": cam_h}

    planes = cluster_planes(observations, n_frames_total=max(n_frames, 1))
    planes = [p for p in planes if p.support >= min_support]

    # Settle the sign of the vertical axis from the geometry before classifying,
    # since classifying with an inverted axis swaps floor and ceiling.
    u, orient = orient_from_horizontal_planes(planes, u, cam_h)

    planes = classify_planes(planes, u, cam_h)

    # Impose the room structure: one floor, one ceiling. See consolidate_horizontal.
    # cam_h is passed so the floor can be required to sit a plausible distance below
    # the camera, which is what stops furniture tops winning on observation count.
    for kind in ("floor", "ceiling"):
        merged = consolidate_horizontal(planes, kind, u, camera_height_m=cam_h)
        if merged is None:
            continue
        planes = [p for p in planes if p.kind != kind]
        planes.append(merged)
    planes.sort(key=lambda p: (p.kind != "floor", p.kind != "ceiling", -p.support))

    if verbose:
        counts: dict[str, int] = {}
        for p in planes:
            counts[p.kind] = counts.get(p.kind, 0) + 1
        print(f"    frames used {n_frames}, plane observations {len(observations)}, "
              f"consensus planes {len(planes)} {counts}")

    return {
        "planes": planes,
        "up": u,
        "orientation": orient,
        "n_frames_used": n_frames,
        "camera_height_m": cam_h,
    }
