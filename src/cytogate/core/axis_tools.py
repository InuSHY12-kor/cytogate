"""Adjusting an axis transform, as in FlowJo's transform window.

- `expand_low` / `expand_high`: the (−)(+) buttons at each end of the axis.
  step=+1 shows more range at that end, step=-1 shows less.
- `auto_width_basis`: "Calculate Width Basis" for Biex — the smallest width
  basis whose axis still reaches the lowest data (0.5th percentile).
- `axis_min`: data value at the left/bottom edge (FlowJo's read-only "Min").
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from .transforms import Arcsinh, Biex, Linear, Log, Logicle, Transform

__all__ = ["expand_low", "expand_high", "auto_width_basis", "axis_min", "axis_max",
           "WIDTH_MIN", "WIDTH_MAX"]

WIDTH_MIN, WIDTH_MAX = 1.0, 1000.0  # |width basis|, FlowJo's slider spans 10^0 .. 10^3
_WIDTH_STEP = 10 ** 0.2  # one (+)/(−) click on a Biex low end
_HIGH_FACTOR = 2.0  # one click on a log-like high end
_LINEAR_FACTOR = 1.1  # one click on a linear axis end


def axis_min(xf: Transform) -> float:
    return float(xf.inverse(np.array([0.0]))[0])


def axis_max(xf: Transform) -> float:
    return float(xf.inverse(np.array([1.0]))[0])


def expand_low(xf: Transform, step: int = 1) -> Transform:
    if isinstance(xf, Linear):  # range grows/shrinks by 10%, so + then − is exact
        span = (xf.max_range - xf.min_range) * _LINEAR_FACTOR ** step
        return dataclasses.replace(xf, min_range=xf.max_range - span)
    if isinstance(xf, Log):
        top = xf.offset * 10 ** xf.decades
        offset = max(xf.offset / 10 ** step, 1e-3)
        return dataclasses.replace(xf, offset=offset, decades=math.log10(top / offset))
    if isinstance(xf, Biex):
        w = float(np.clip(abs(xf.width) * _WIDTH_STEP ** step, WIDTH_MIN, WIDTH_MAX))
        return dataclasses.replace(xf, width=-w)
    if isinstance(xf, Logicle):
        w = float(np.clip(xf.W + 0.1 * step, 0.0, xf.M / 2 - 0.01))
        return dataclasses.replace(xf, W=w)
    if isinstance(xf, Arcsinh):
        return dataclasses.replace(xf, A=max(0.0, xf.A + 0.1 * step))
    raise TypeError(type(xf).__name__)


def expand_high(xf: Transform, step: int = 1) -> Transform:
    if isinstance(xf, Linear):
        span = (xf.max_range - xf.min_range) * _LINEAR_FACTOR ** step
        return dataclasses.replace(xf, max_range=xf.min_range + span)
    f = _HIGH_FACTOR ** step
    if isinstance(xf, Log):
        return dataclasses.replace(xf, decades=max(0.5, xf.decades + math.log10(f)))
    if isinstance(xf, Biex):
        return dataclasses.replace(xf, max_range=xf.max_range * f)
    if isinstance(xf, (Logicle, Arcsinh)):
        return dataclasses.replace(xf, T=xf.T * f)
    raise TypeError(type(xf).__name__)


def auto_width_basis(values: np.ndarray, xf: Biex) -> float:
    """Width basis (negative) that keeps the lowest 0.5% of events on scale."""
    values = np.asarray(values, dtype=float)
    low = float(np.percentile(values, 0.5)) if values.size else 0.0
    if low >= 0:
        return -10.0  # FlowJo's default; nothing negative to show
    for exp10 in np.arange(0.0, 3.0001, 0.05):
        w = -float(10 ** exp10)
        if axis_min(dataclasses.replace(xf, width=w)) <= low:
            return round(w, 2)
    return -WIDTH_MAX
