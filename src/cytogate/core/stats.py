"""Population statistics with FlowJo's names and definitions.

Frequencies (percent):
    Freq. of Parent / Grandparent / Total = 100 x count / count of that population.

Per-parameter statistics, on data values (compensated for Comp- parameters):
    Mean, Median, SD, CV = 100 x SD / Mean
    Geometric Mean  computed in graph space: inverse_transform(mean(transform(x))),
                    so it works with zero and negative values. With a Log axis
                    this is the classical geometric mean. (GeoMean.CLASSIC gives
                    exp(mean(ln x)) over positive values instead.)
    Robust SD       = ((P84.13 - Median) + (Median - P15.87)) / 2
    Robust CV       = 100 x 1/2 (P84.13 - P15.87) / Median
    Median Abs Dev  = median(|x - Median|)            (FlowJo 10.8+ definition)
    MADP            = 100 x 1.4826 x Median Abs Dev / Median
    Mode            = value at the tallest bin of a 256-channel histogram on the axis
    Percentile      = value at the given percentile (linear interpolation)

FlowJo notes that it computes statistics on binned data, so its values can
differ in the last digits; exact agreement is checked against FlowJo tables
in the validation phase.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

__all__ = ["Stat", "GeoMean", "StatSpec", "compute", "PARAMETER_STATS", "FREQUENCY_STATS", "column_name"]


class Stat(str, Enum):
    COUNT = "Count"
    FREQ_PARENT = "Freq. of Parent"
    FREQ_GRANDPARENT = "Freq. of Grandparent"
    FREQ_TOTAL = "Freq. of Total"
    MEAN = "Mean"
    MEDIAN = "Median"
    GEOMEAN = "Geometric Mean"
    MODE = "Mode"
    SD = "SD"
    CV = "CV"
    ROBUST_SD = "Robust SD"
    ROBUST_CV = "Robust CV"
    MAD = "Median Abs Dev"
    MADP = "MADP"
    PERCENTILE = "Percentile"


class GeoMean(str, Enum):
    GRAPH = "graph"  # FlowJo 10: in the axis transform's space
    CLASSIC = "classic"  # exp(mean(ln x)), positive values only


FREQUENCY_STATS = (Stat.COUNT, Stat.FREQ_PARENT, Stat.FREQ_GRANDPARENT, Stat.FREQ_TOTAL)
PARAMETER_STATS = tuple(s for s in Stat if s not in FREQUENCY_STATS)
LO_PCT, HI_PCT = 15.87, 84.13  # +-1 SD of a normal distribution


@dataclass(frozen=True)
class StatSpec:
    """A statistic attached to a population, like FlowJo's Σ nodes."""

    stat: Stat
    parameter: str | None = None  # needed for PARAMETER_STATS
    percentile: float | None = None  # for Stat.PERCENTILE

    def __post_init__(self):
        if self.stat in PARAMETER_STATS and not self.parameter:
            raise ValueError(f"{self.stat.value} needs a parameter")
        if self.stat == Stat.PERCENTILE and self.percentile is None:
            raise ValueError("Percentile needs a percentile value")

    def label(self, parameter_label: str | None = None) -> str:
        """FlowJo node name, e.g. "Median : Comp-APC-A" or "Freq. of Parent"."""
        name = self.stat.value
        if self.stat == Stat.PERCENTILE:
            name = f"{self.percentile:g}th Percentile"
        if self.parameter:
            return f"{name} : {parameter_label or self.parameter}"
        return name


def column_name(path: tuple[str, ...], spec: StatSpec, parameter_label: str | None = None) -> str:
    """FlowJo Table Editor column header: "Lymphocytes/CD3+ | Median (Comp-APC-A :: CD3)"."""
    pop = "/".join(path) if path else "Ungated"
    name = spec.stat.value if spec.stat != Stat.PERCENTILE else f"{spec.percentile:g}th Percentile"
    if spec.parameter:
        return f"{pop} | {name} ({parameter_label or spec.parameter})"
    return f"{pop} | {name}"


def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else math.nan


def compute(analysis, path, spec: StatSpec, geomean: GeoMean = GeoMean.GRAPH) -> float:
    """Value of `spec` for population `path` of an AnalyzedSample."""
    path = tuple(path)
    n = analysis.count(path)
    if spec.stat == Stat.COUNT:
        return float(n)
    if spec.stat == Stat.FREQ_PARENT:
        return _pct(n, analysis.count(path[:-1])) if path else 100.0
    if spec.stat == Stat.FREQ_GRANDPARENT:
        return _pct(n, analysis.count(path[:-2])) if len(path) >= 2 else math.nan
    if spec.stat == Stat.FREQ_TOTAL:
        return _pct(n, analysis.ps.event_count)

    ps = analysis.ps
    pname = ps.parameter(spec.parameter).name
    x = ps.values(pname)[analysis.mask(path)]
    if x.size == 0:
        return math.nan
    return parameter_stat(x, spec, ps.transforms[pname], geomean)


def parameter_stat(x: np.ndarray, spec: StatSpec, xf=None, geomean: GeoMean = GeoMean.GRAPH) -> float:
    s = spec.stat
    if s == Stat.MEAN:
        return float(np.mean(x))
    if s == Stat.MEDIAN:
        return float(np.median(x))
    if s == Stat.SD:
        return float(np.std(x, ddof=1)) if x.size > 1 else math.nan
    if s == Stat.CV:
        m = float(np.mean(x))
        return 100.0 * float(np.std(x, ddof=1)) / m if x.size > 1 and m != 0 else math.nan
    if s in (Stat.ROBUST_SD, Stat.ROBUST_CV):
        lo, med, hi = np.percentile(x, [LO_PCT, 50.0, HI_PCT])
        rsd = ((hi - med) + (med - lo)) / 2.0
        if s == Stat.ROBUST_SD:
            return float(rsd)
        return 100.0 * float(rsd) / float(med) if med != 0 else math.nan
    if s in (Stat.MAD, Stat.MADP):
        med = float(np.median(x))
        mad = float(np.median(np.abs(x - med)))
        if s == Stat.MAD:
            return mad
        return 100.0 * 1.4826 * mad / med if med != 0 else math.nan
    if s == Stat.PERCENTILE:
        return float(np.percentile(x, spec.percentile))
    if s == Stat.GEOMEAN:
        if geomean == GeoMean.CLASSIC or xf is None:
            pos = x[x > 0]
            return float(np.exp(np.mean(np.log(pos)))) if pos.size else math.nan
        return float(xf.inverse(np.array([np.mean(xf.apply(x))]))[0])
    if s == Stat.MODE:
        if xf is None:
            raise ValueError("Mode needs the parameter's transform")
        pos = np.clip(xf.apply(x), 0.0, 1.0)
        counts = np.bincount(np.minimum((pos * 256).astype(int), 255), minlength=256)
        peak = int(np.argmax(counts))
        return float(xf.inverse(np.array([(peak + 0.5) / 256]))[0])
    raise ValueError(f"not a parameter statistic: {s}")
