"""Plot rendering without any GUI dependency.

Input coordinates are display positions (0..1) from a transform. Events
outside the axis range are piled onto the edge bins, as FlowJo does, so they
stay visible and countable.

2D plot types follow FlowJo's: Dot, Pseudocolor, Contour, Density.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter

__all__ = [
    "PlotType", "HistogramY", "bin2d", "density", "probability_levels",
    "render_2d", "Rendered2D", "histogram", "PSEUDOCOLOR_LUT", "pixel_medians",
]


class PlotType(str, Enum):
    DOT = "Dot Plot"
    PSEUDOCOLOR = "Pseudocolor"
    CONTOUR = "Contour"
    DENSITY = "Density"
    HISTOGRAM = "Histogram"


class HistogramY(str, Enum):
    COUNT = "Count"
    MODE = "Normalized To Mode"  # tallest bin = 100


def _lut() -> np.ndarray:
    """Blue -> cyan -> green -> yellow -> red, the pseudocolor ramp FlowJo uses."""
    stops = np.array([
        [0.00, 0, 0, 255], [0.25, 0, 200, 255], [0.45, 0, 220, 0],
        [0.70, 255, 240, 0], [0.88, 255, 130, 0], [1.00, 230, 0, 0],
    ])
    pos = np.linspace(0, 1, 256)
    rgb = np.column_stack([np.interp(pos, stops[:, 0], stops[:, i]) for i in (1, 2, 3)])
    return np.column_stack([rgb, np.full(256, 255)]).astype(np.uint8)


PSEUDOCOLOR_LUT = _lut()


def _bin_index(v: np.ndarray, res: int) -> np.ndarray:
    v = np.nan_to_num(np.asarray(v, dtype=float), nan=0.0)
    return np.clip((v * res).astype(np.int64), 0, res - 1)


def bin2d(x: np.ndarray, y: np.ndarray, res: int = 256) -> np.ndarray:
    """Event counts per pixel, shape (res, res) indexed [x, y]. Off-scale events go to edge bins."""
    ix, iy = _bin_index(x, res), _bin_index(y, res)
    return np.bincount(ix * res + iy, minlength=res * res).reshape(res, res).astype(float)


def density(counts: np.ndarray, sigma: float | None = None) -> np.ndarray:
    """Smoothed density; sigma defaults to 1.5 pixels at 256 resolution."""
    if sigma is None:
        sigma = 1.5 * counts.shape[0] / 256
    return gaussian_filter(counts, sigma=sigma, mode="constant")


def probability_levels(dens: np.ndarray, counts: np.ndarray, step: float = 0.05) -> np.ndarray:
    """Density thresholds for equal-probability contours.

    Like FlowJo's "Level 5%": the region above the k-th threshold holds about
    (1 - k*step) of the events. Returned lowest first.
    """
    total = counts.sum()
    if total == 0:
        return np.array([])
    order = np.argsort(dens.ravel())
    d_sorted = dens.ravel()[order]
    cum = np.cumsum(counts.ravel()[order]) / total  # fraction of events at or below density
    fractions = np.arange(step, 1.0 - 1e-9, step)
    idx = np.searchsorted(cum, fractions)
    idx = idx[idx < d_sorted.size]
    levels = np.unique(d_sorted[idx])
    return levels[levels > 0]


@dataclass
class Rendered2D:
    image: np.ndarray  # RGBA uint8, shape (res, res, 4), indexed [x, y]
    counts: np.ndarray
    density: np.ndarray
    levels: np.ndarray  # contour levels (CONTOUR / DENSITY), else empty


def render_2d(
    x: np.ndarray,
    y: np.ndarray,
    kind: PlotType = PlotType.PSEUDOCOLOR,
    res: int = 512,
    level: float = 0.05,
    show_outliers: bool = True,
    dot_color=(0, 0, 0),
    large_dots: bool = False,
    color_by: np.ndarray | None = None,
) -> Rendered2D:
    """Render a 2D plot. `color_by` (display positions 0..1 of a third
    parameter, one per event) colours each occupied pixel by the median of that
    parameter there (FlowJo's colour-mapped axis) instead of by density."""
    counts = bin2d(x, y, res)
    dens = density(counts)
    image = np.zeros((res, res, 4), dtype=np.uint8)
    occupied = counts > 0
    levels = np.array([])

    if kind == PlotType.DOT:
        image[occupied] = (*dot_color, 255)
    elif kind == PlotType.PSEUDOCOLOR:
        # Colour each occupied pixel by local density (log-scaled, relative to the peak).
        d = np.log1p(dens / max(dens.max(), 1e-12) * 1000) / np.log1p(1000)
        image[occupied] = PSEUDOCOLOR_LUT[(np.clip(d[occupied], 0, 1) * 255).astype(int)]
    elif kind in (PlotType.CONTOUR, PlotType.DENSITY):
        levels = probability_levels(dens, counts, level)
        if levels.size:
            band = np.searchsorted(levels, dens, side="right")  # 0 = below lowest contour
            if kind == PlotType.DENSITY:
                inside = band > 0
                shade = (band[inside] - 1) / max(levels.size - 1, 1)
                image[inside] = PSEUDOCOLOR_LUT[(shade * 255).astype(int)]
            if show_outliers:
                outliers = occupied & (band == 0)
                image[outliers] = (*dot_color, 255)
        elif show_outliers:
            image[occupied] = (*dot_color, 255)
    else:
        raise ValueError(f"{kind} is not a 2D plot type")
    if color_by is not None and kind in (PlotType.DOT, PlotType.PSEUDOCOLOR):
        med = pixel_medians(x, y, color_by, res)
        image[occupied] = PSEUDOCOLOR_LUT[(np.clip(med[occupied], 0, 1) * 255).astype(int)]
    if large_dots:  # FlowJo "Draw Large Dots": grow single-colour dots by one pixel
        dots = np.all(image == (*dot_color, 255), axis=-1)
        grown = binary_dilation(dots) & (image[:, :, 3] == 0)
        image[grown] = (*dot_color, 255)
    return Rendered2D(image, counts, dens, levels)


def pixel_medians(x, y, c, res: int) -> np.ndarray:
    """Median of `c` over the events in each pixel (NaN where empty), shape (res, res)."""
    pix = _bin_index(x, res) * res + _bin_index(y, res)
    c = np.nan_to_num(np.asarray(c, dtype=float), nan=0.0)
    order = np.lexsort((c, pix))
    pix_s, c_s = pix[order], c[order]
    counts = np.bincount(pix_s, minlength=res * res)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    out = np.full(res * res, np.nan)
    has = counts > 0
    lo = starts[has] + (counts[has] - 1) // 2
    hi = starts[has] + counts[has] // 2
    out[has] = (c_s[lo] + c_s[hi]) / 2
    return out.reshape(res, res)


def histogram(
    x: np.ndarray,
    bins: int = 256,
    y_mode: HistogramY = HistogramY.COUNT,
    smooth: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """1D histogram over the display range. Returns (bin edges, heights)."""
    counts = np.bincount(_bin_index(x, bins), minlength=bins).astype(float)
    if smooth:
        counts = gaussian_filter(counts, sigma=1.5 * bins / 256, mode="constant")
    if y_mode == HistogramY.MODE and counts.max() > 0:
        counts = counts / counts.max() * 100
    return np.linspace(0, 1, bins + 1), counts
