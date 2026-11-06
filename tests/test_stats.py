"""Phase 5: statistics.

FlowJo's definitions, checked on data with known answers. (Agreement with
FlowJo's own tables to the last digit is for the validation phase: the example
workspaces only store Count, which is covered in test_gating.py.)
"""

import math

import numpy as np
import pytest

from cytogate.core import Biex, Linear, Log, ProcessedSample, RectangleGate
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.stats import GeoMean, Stat, StatSpec, column_name, parameter_stat
from cytogate.io import load_fcs

rng = np.random.default_rng(1)
NORMAL = rng.normal(1000.0, 100.0, 400_000)


def ps_(stat, x, xf=None, **kw):
    return parameter_stat(x, StatSpec(stat, "P", kw.get("pct")), xf, kw.get("geomean", GeoMean.GRAPH))


@pytest.mark.parametrize("stat, expected, tol", [
    (Stat.MEAN, 1000.0, 1.0),
    (Stat.MEDIAN, 1000.0, 1.0),
    (Stat.SD, 100.0, 0.5),
    (Stat.CV, 10.0, 0.05),
    (Stat.ROBUST_SD, 100.0, 0.7),  # = SD for a normal distribution
    (Stat.ROBUST_CV, 10.0, 0.07),
    (Stat.MAD, 100.0 * 0.6745, 0.5),  # MAD of a normal = 0.6745 SD
    (Stat.MADP, 10.0, 0.07),  # 1.4826 x MAD = SD
])
def test_normal_distribution(stat, expected, tol):
    assert ps_(stat, NORMAL) == pytest.approx(expected, abs=tol)


def test_exact_small_values():
    x = np.array([1.0, 2, 3, 4, 100])
    assert ps_(Stat.MEDIAN, x) == 3
    assert ps_(Stat.MEAN, x) == 22
    assert ps_(Stat.SD, x) == pytest.approx(np.std(x, ddof=1))
    assert ps_(Stat.MAD, x) == 1  # |x - 3| = 2,1,0,1,97
    assert ps_(Stat.PERCENTILE, x, pct=25) == 2
    assert ps_(Stat.ROBUST_CV, x) == pytest.approx(100 * 0.5 * (np.percentile(x, 84.13) - np.percentile(x, 15.87)) / 3)


def test_geometric_mean():
    x = np.array([10.0, 100.0, 1000.0])
    assert ps_(Stat.GEOMEAN, x, geomean=GeoMean.CLASSIC) == pytest.approx(100.0)
    # in the space of a Log axis it is the same thing
    assert ps_(Stat.GEOMEAN, x, Log(1, 5)) == pytest.approx(100.0)
    # on a Biex axis it is defined even with negative values, and stays between min and max
    y = np.array([-200.0, 50.0, 3000.0, 20000.0])
    g = ps_(Stat.GEOMEAN, y, Biex())
    assert y.min() < g < y.max() and math.isfinite(g)
    # classic ignores non-positive values
    assert ps_(Stat.GEOMEAN, y, geomean=GeoMean.CLASSIC) == pytest.approx(np.exp(np.mean(np.log([50, 3000, 20000]))))


def test_mode_finds_the_peak():
    x = np.concatenate([rng.normal(500, 30, 5000), rng.normal(20000, 2000, 50000)])
    m = ps_(Stat.MODE, x, Biex())
    assert 15000 < m < 25000
    assert ps_(Stat.MODE, NORMAL, Linear(0, 2000)) == pytest.approx(1000, abs=10)


def test_empty_and_degenerate():
    assert math.isnan(ps_(Stat.CV, np.array([0.0, 0.0])))
    assert math.isnan(ps_(Stat.SD, np.array([5.0])))
    assert math.isnan(ps_(Stat.ROBUST_CV, np.zeros(10)))


def test_spec_validation_and_labels():
    with pytest.raises(ValueError):
        StatSpec(Stat.MEDIAN)
    with pytest.raises(ValueError):
        StatSpec(Stat.PERCENTILE, "P")
    assert StatSpec(Stat.FREQ_PARENT).label() == "Freq. of Parent"
    assert StatSpec(Stat.MEDIAN, "Comp-R780-A").label("Comp-R780-A :: CD3") == "Median : Comp-R780-A :: CD3"
    assert StatSpec(Stat.PERCENTILE, "X", 95).label() == "95th Percentile : X"
    assert column_name(("Lymphocytes", "CD3+"), StatSpec(Stat.MEDIAN, "Comp-R780-A"), "Comp-R780-A :: CD3") == \
        "Lymphocytes/CD3+ | Median (Comp-R780-A :: CD3)"
    assert column_name(("Lymphocytes",), StatSpec(Stat.FREQ_PARENT)) == "Lymphocytes | Freq. of Parent"


@pytest.fixture(scope="module")
def analysis(data_dir):
    a = AnalyzedSample(ProcessedSample(load_fcs(data_dir / "8_color_data_set/fcs_files/101_DEN084Y5_15_E01_008_clean.fcs")))
    a.add_gate((), "Big", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    a.add_gate("Big", "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
    return a


def test_frequencies(analysis):
    total = 290172
    big, cd3 = analysis.count(("Big",)), analysis.count(("Big", "CD3+"))
    assert analysis.stat(("Big", "CD3+"), StatSpec(Stat.COUNT)) == cd3
    assert analysis.stat(("Big", "CD3+"), StatSpec(Stat.FREQ_PARENT)) == pytest.approx(100 * cd3 / big)
    assert analysis.stat(("Big", "CD3+"), StatSpec(Stat.FREQ_GRANDPARENT)) == pytest.approx(100 * cd3 / total)
    assert analysis.stat(("Big", "CD3+"), StatSpec(Stat.FREQ_TOTAL)) == pytest.approx(100 * cd3 / total)
    assert math.isnan(analysis.stat(("Big",), StatSpec(Stat.FREQ_GRANDPARENT)))
    assert analysis.stat((), StatSpec(Stat.FREQ_PARENT)) == 100.0


def test_parameter_stats_use_population_events(analysis):
    mask = analysis.mask(("Big", "CD3+"))
    v = analysis.ps.values("Comp-CD3 APC-H7 FLR-A")[mask]
    spec = StatSpec(Stat.MEDIAN, "Comp-CD3 APC-H7 FLR-A")
    assert analysis.stat(("Big", "CD3+"), spec) == pytest.approx(np.median(v))
    assert analysis.stat(("Big", "CD3+"), spec) >= 600
    # lookup by stain/label works too
    assert analysis.stat(("Big",), StatSpec(Stat.MEAN, "FSC-A")) == pytest.approx(analysis.ps.values("FSC-A")[analysis.mask(("Big",))].mean())


def test_stats_follow_gate_changes(analysis):
    spec = StatSpec(Stat.COUNT)
    analysis.add_stat(("Big",), spec)
    analysis.add_stat(("Big",), spec)  # no duplicates
    assert analysis.gates.get(("Big",)).stats == [spec]
    before = analysis.stat(("Big", "CD3+"), spec)
    analysis.remove_gate(("Big", "CD3+"))
    analysis.add_gate("Big", "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (2000.0,), (None,)))
    assert analysis.stat(("Big", "CD3+"), spec) < before
