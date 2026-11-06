"""FlowJo's advanced gate tools, built from the basic gate types so they save
to .wsp files FlowJo can read.

- Bisector: one split point on a histogram -> two ranges (low "-", high "+").
- BifurGate: find the split automatically at the deepest dip between the two
  main peaks of a smoothed histogram.
- Spider: four polygons meeting at a centre, each pair separated by an arm.
- Curly quadrants: quadrants whose upper and right arms bend outward with the
  photon-counting spread, p2 = a * sqrt(p1) + b (FlowJo documentation).
- Offset quadrants: quadrants whose vertical (or horizontal) divider is at a
  different position above and below (or left and right of) the centre.
- Boolean combinations: every +/- combination of a set of populations.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter1d

from .gating import BooleanGate, PolygonGate, RectangleGate

__all__ = ["bisector_gates", "find_split", "spider_gates", "curly_quadrant_gates", "offset_quadrant_gates",
           "boolean_combination_names", "QUAD_ORDER"]

QUAD_ORDER = ("Q1", "Q2", "Q3", "Q4")  # top-left, top-right, bottom-right, bottom-left (FlowJo)


def _signs(xn, yn):
    return {"Q1": f"{xn}- , {yn}+", "Q2": f"{xn}+ , {yn}+", "Q3": f"{xn}+ , {yn}-", "Q4": f"{xn}- , {yn}-"}


def bisector_gates(x: str, split: float, name: str = ""):
    """Two ranges split at `split` (data value): [(name-, gate), (name+, gate)]."""
    n = name or x
    return [(f"{n}-", RectangleGate((x,), (None,), (split,))),
            (f"{n}+", RectangleGate((x,), (split,), (None,)))]


def find_split(pos: np.ndarray, bins: int = 256, smooth: float = 3.0, min_prominence: float = 0.25) -> float | None:
    """Display position (0..1) of the deepest dip between the two highest peaks
    of a histogram, or None when the data are not clearly bimodal."""
    pos = np.clip(np.asarray(pos, dtype=float), 0, 1)
    if pos.size < 50:
        return None
    h = np.bincount(np.minimum((pos * bins).astype(int), bins - 1), minlength=bins).astype(float)
    h[0] = h[-1] = 0  # events piled on the axis edges are not a peak
    h = gaussian_filter1d(h, smooth)
    peaks = [i for i in range(1, bins - 1) if h[i] >= h[i - 1] and h[i] > h[i + 1]]
    if len(peaks) < 2:
        return None
    best = None
    peaks_sorted = sorted(peaks, key=lambda i: -h[i])
    p1 = peaks_sorted[0]
    for p2 in peaks_sorted[1:]:
        lo, hi = sorted((p1, p2))
        valley = lo + int(np.argmin(h[lo:hi + 1]))
        depth = min(h[p1], h[p2]) - h[valley]
        if depth >= min_prominence * min(h[p1], h[p2]):
            if best is None or depth > best[0]:
                best = (depth, valley)
    return None if best is None else (best[1] + 0.5) / bins


def _to_data(xf, v):
    return float(xf.inverse(np.array([v]))[0])


def spider_gates(x: str, y: str, xfx, xfy, center, arms=None, x_name="", y_name=""):
    """Four polygons around `center` (display units). `arms` = display points
    where the up, right, down and left arms meet the plot edge (default:
    straight, like a quadrant)."""
    cx, cy = center
    up, right, down, left = arms or [(cx, 1.0), (1.0, cy), (cx, 0.0), (0.0, cy)]
    corners = {"tl": (0.0, 1.0), "tr": (1.0, 1.0), "br": (1.0, 0.0), "bl": (0.0, 0.0)}
    shapes = {
        "Q1": [center, left, corners["tl"], up],
        "Q2": [center, up, corners["tr"], right],
        "Q3": [center, right, corners["br"], down],
        "Q4": [center, down, corners["bl"], left],
    }
    names = _signs(x_name or x, y_name or y)
    out = []
    for q in QUAD_ORDER:
        verts = tuple((_to_data(xfx, px), _to_data(xfy, py)) for px, py in _dedupe(shapes[q]))
        out.append((f"{q}: {names[q]}", PolygonGate((x, y), verts)))
    return out


def _dedupe(points):
    out = []
    for p in points:
        if not out or (abs(out[-1][0] - p[0]) > 1e-9 or abs(out[-1][1] - p[1]) > 1e-9):
            out.append(p)
    return out


def curly_quadrant_gates(x: str, y: str, xfx, xfy, center, curl: float = 2.0, x_name="", y_name="", n: int = 40):
    """Quadrants with straight lower/left arms and curved upper/right arms:
    upper arm x = cx + curl*sqrt(y - cy), right arm y = cy + curl*sqrt(x - cx)
    (data units), sampled as polygons."""
    cxd, cyd = _to_data(xfx, center[0]), _to_data(xfy, center[1])
    top_x, top_y = _to_data(xfx, 1.0), _to_data(xfy, 1.0)
    ys = cyd + (top_y - cyd) * np.linspace(0, 1, n) ** 2  # denser near the centre
    xs = cxd + (top_x - cxd) * np.linspace(0, 1, n) ** 2
    up_arm = [(cxd + curl * math.sqrt(max(v - cyd, 0.0)), v) for v in ys]  # from centre to top
    right_arm = [(v, cyd + curl * math.sqrt(max(v - cxd, 0.0))) for v in xs]  # from centre to right
    lo_x, lo_y = _to_data(xfx, 0.0), _to_data(xfy, 0.0)
    huge_x, huge_y = top_x, top_y
    names = _signs(x_name or x, y_name or y)
    q1 = [(lo_x, cyd), (lo_x, huge_y), *reversed(up_arm)]
    q2 = [*up_arm, (huge_x, huge_y), *reversed(right_arm[1:])]
    q3 = [*right_arm, (huge_x, lo_y), (cxd, lo_y)]
    q4 = [(cxd, cyd), (cxd, lo_y), (lo_x, lo_y), (lo_x, cyd)]
    return [(f"{q}: {names[q]}", PolygonGate((x, y), tuple(_dedupe(v))))
            for q, v in zip(QUAD_ORDER, (q1, q2, q3, q4))]


def offset_quadrant_gates(x: str, y: str, x_upper: float, x_lower: float, y_left: float, y_right: float,
                          x_name="", y_name=""):
    """Quadrants (data units) whose vertical divider sits at x_upper above and
    x_lower below the centre, or whose horizontal divider sits at y_left and
    y_right on either side. Only one of the two may be offset (as in FlowJo),
    so the four always cover every event exactly once."""
    if x_upper != x_lower and y_left != y_right:
        raise ValueError("offset either the vertical or the horizontal divider, not both")
    names = _signs(x_name or x, y_name or y)
    if y_left == y_right:
        yc = y_left
        rects = {"Q1": ((None, yc), (x_upper, None)), "Q2": ((x_upper, yc), (None, None)),
                 "Q3": ((x_lower, None), (None, yc)), "Q4": ((None, None), (x_lower, yc))}
    else:
        xc = x_upper
        rects = {"Q1": ((None, y_left), (xc, None)), "Q2": ((xc, y_right), (None, None)),
                 "Q3": ((xc, None), (None, y_right)), "Q4": ((None, None), (xc, y_left))}
    return [(f"{q}: {names[q]}", RectangleGate((x, y), *rects[q])) for q in QUAD_ORDER]


def boolean_combination_names(names: list[str]) -> list[tuple[str, tuple[bool, ...]]]:
    """All 2^n +/- combinations: [("A+ B-", (True, False)), …], all-positive first."""
    out = []
    for k in range(2 ** len(names)):
        signs = tuple(not (k >> (len(names) - 1 - i)) & 1 for i in range(len(names)))
        out.append((" ".join(f"{n}{'+' if s else '-'}" for n, s in zip(names, signs)), signs))
    return out
