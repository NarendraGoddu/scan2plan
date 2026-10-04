"""Monocular depth from photos and video frames, via Depth Anything V2 on ONNX.

Why ONNX Runtime and not PyTorch: the machine this runs on has integrated AMD
graphics and no CUDA device, and the CPU PyTorch wheel is a multi-gigabyte install
that would make the project awkward to set up. The exported fp32 model is 99 MB
and runs through `onnxruntime` in a fraction of a second per frame on CPU, which
is what makes it practical to run this over 87 photos and ~100 video frames.

The important caveat, stated up front because everything downstream depends on
it: **this is relative depth**. Depth Anything V2 predicts inverse depth with an
arbitrary, per-image scale and shift. Two photographs of the same room do not
share a scale, so nothing here is a measurement until a scale is anchored to
something of known size. `scale_to_metric` does that anchoring explicitly and
refuses to pretend otherwise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

# Depth Anything V2 (DPT) preprocessing, from the model's preprocessor_config.
# Aspect ratio is kept and both sides are rounded to a multiple of 14, because the
# ViT patch grid requires it. Forcing a square 518x518 instead would rescale the
# room non-uniformly and shear every wall angle in the output depth map.
TARGET_SHORT_SIDE = 518
PATCH_MULTIPLE = 14
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

def _model_candidates() -> list[str]:
    """Where to look for the ONNX weights, most explicit first.

    The repo checkout keeps them in `.models/` at the root, but the same code can
    run from an installed package where that directory does not exist, so an
    environment override and a package-local fallback are both honoured.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))
    paths = []
    env = os.environ.get("SCAN2PLAN_DEPTH_MODEL")
    if env:
        paths.append(env)
    paths.append(os.path.join(repo_root, ".models", "model.onnx"))
    paths.append(os.path.join(os.path.dirname(here), "models", "model.onnx"))
    return paths


def load_session(model_path: str | None = None, threads: int = 0):
    """Open the ONNX model, returning an inference session.

    Left to itself ONNX Runtime spawns a thread per core, which on this machine
    oversubscribes and slows the run down. `threads=0` means "decide for me";
    the caller can pass a positive number to pin it.
    """
    import onnxruntime as ort

    candidates = [model_path] if model_path else _model_candidates()
    path = next((p for p in candidates if p and os.path.isfile(p)), None)
    if path is None:
        raise FileNotFoundError(
            "Depth Anything V2 ONNX model not found. Looked in: "
            + ", ".join(str(p) for p in candidates)
            + ". Fetch it with scripts/fetch_depth_model.py"
        )
    opts = ort.SessionOptions()
    if threads > 0:
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
    return ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])



def preprocess(bgr: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """BGR uint8 image to the NCHW float tensor the model expects.

    Returns the tensor and the (height, width) it was resized to, so the depth map
    can be mapped back to the original image.
    """
    h, w = bgr.shape[:2]
    scale = TARGET_SHORT_SIDE / float(min(h, w))
    nh = max(PATCH_MULTIPLE, int(round(h * scale / PATCH_MULTIPLE)) * PATCH_MULTIPLE)
    nw = max(PATCH_MULTIPLE, int(round(w * scale / PATCH_MULTIPLE)) * PATCH_MULTIPLE)

    import cv2  # imported lazily so the package imports without OpenCV present

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_CUBIC)
    x = rgb.astype(np.float32) / 255.0
    x = (x - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None]), (nh, nw)


def infer_depth(session, bgr: np.ndarray) -> np.ndarray:
    """Predict inverse depth for one BGR image.

    The result is resized back to the input resolution and returned as float32,
    in arbitrary units, larger meaning closer. It is *not* metres.
    """
    import cv2

    tensor, (nh, nw) = preprocess(bgr)
    inp = session.get_inputs()[0].name
    out = session.run(None, {inp: tensor})[0]
    depth = np.asarray(out, dtype=np.float32)
    while depth.ndim > 2:
        depth = depth[0]
    if depth.shape[:2] != (nh, nw):
        depth = cv2.resize(depth, (nw, nh), interpolation=cv2.INTER_CUBIC)
    if depth.shape[:2] != bgr.shape[:2]:
        depth = cv2.resize(depth, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_CUBIC)
    return depth


@dataclass
class DepthStats:
    """Summary of one depth map, for triage across a whole capture."""

    path: str
    height: int
    width: int
    p01: float
    p50: float
    p99: float
    near_ratio: float  # fraction of pixels in the closest 10% of the range

    @classmethod
    def from_depth(cls, path: str, depth: np.ndarray, near_frac: float = 0.10) -> "DepthStats":
        lo, mid, hi = np.percentile(depth, [1, 50, 99])
        span = max(float(hi - lo), 1e-9)
        return cls(
            path=path,
            height=int(depth.shape[0]),
            width=int(depth.shape[1]),
            p01=float(lo),
            p50=float(mid),
            p99=float(hi),
            near_ratio=float(np.mean(depth > lo + near_frac * span)),
        )


def save_depth_png(depth: np.ndarray, path: str) -> None:
    """Write a depth map as 16-bit PNG, preserving the full float range.

    Normalising to 0-255 for an 8-bit PNG would quantise away exactly the
    wall-to-wall differences these maps are being used to measure, so the float
    range is mapped across the full 16 bits instead.
    """
    import cv2

    d = np.asarray(depth, dtype=np.float32)
    lo, hi = float(d.min()), float(d.max())
    if hi - lo < 1e-9:
        out = np.zeros_like(d, dtype=np.uint16)
    else:
        out = ((d - lo) / (hi - lo) * 65535.0).astype(np.uint16)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cv2.imwrite(path, out)


def scale_to_metric(
    depth: np.ndarray, near_m: float, far_m: float
) -> np.ndarray:
    """Convert one image's relative depth into metres using two known distances.

    Depth Anything V2 output is inverse depth, so depth in metres is affine in
    the reciprocal of the prediction. Anchoring the 1st and 99th percentile to
    `near_m` and `far_m` fixes both the scale and the arbitrary shift, which is
    what makes two independently-predicted images comparable.

    This is a *per-image* two-point fit. It is only as good as the two distances
    fed to it, and it assumes the extremes of the map really are those distances.
    Use it where a real measurement is available, not to manufacture one.
    """
    d = np.asarray(depth, dtype=np.float64)
    lo, hi = np.percentile(d, [1, 99])
    if hi - lo < 1e-9:
        return np.full_like(d, near_m)
    inv_near, inv_far = 1.0 / near_m, 1.0 / far_m
    # linear map from predicted value to 1/metres
    a = (inv_far - inv_near) / (hi - lo)
    b = inv_near - a * lo
    inv = np.clip(a * d + b, 1e-6, None)
    return 1.0 / inv
