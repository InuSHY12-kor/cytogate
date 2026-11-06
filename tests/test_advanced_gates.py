"""C1: advanced gates (bisector, BifurGate split, spider, curly/offset quadrants, Boolean combinations)."""

import numpy as np
import pytest

from cytogate.core import Linear, ProcessedSample
from cytogate.core.advanced_gates import (boolean_combination_names, bisector_gates, curly_quadrant_gates,
                                          find_split, offset_quadrant_gates, spider_gates)
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.gating import GatingTree, RectangleGate
from cytogate.io import load_fcs

E01 = "101_DEN084Y5_15_E01_008_clean.fcs"


class Grid:
    """Synthetic sample: 10,000 events on a 100x100 grid, linear 0..100 axes."""

    def __init__(self):
        xs, ys = np.meshgrid(np.arange(0.5, 100), np.arange(0.5, 100))
        self._d = {"X": xs.ravel(), "Y": ys.ravel()}
        self.transforms = {"X": Linear(0, 100), "Y": Linear(0, 100)}
        self.event_count = 10000

    def parameter(self, name):
        return type("P", (), {"name": name})()

    def values(self, name):
        return self._d[name]


def partition(gates, s):
    masks = [g.contains(s) for _, g in gates]
    total = np.sum(masks, axis=0)
    return masks, total


def test_bisector():
    s = Grid()
    (lo_name, lo), (hi_name, hi) = bisector_gates("X", 30.0, "CD3")
    assert (lo_name, hi_name) == ("CD3-", "CD3+")
    assert lo.contains(s).sum() + hi.contains(s).sum() == 10000  # every event on exactly one side
    assert hi.contains(s).sum() == 70 * 100


def test_find_split():
    rng = np.random.default_rng(0)
    bimodal = np.concatenate([rng.normal(0.25, 0.05, 20000), rng.normal(0.7, 0.06, 30000)])
    split = find_split(bimodal)
    assert 0.38 < split < 0.55
    assert find_split(rng.normal(0.5, 0.08, 50000)) is None  # one peak
    assert find_split(np.array([0.5] * 10)) is None


def test_find_split_on_real_cd3(data_dir):
    ps = ProcessedSample(load_fcs(data_dir / "8_color_data_set/fcs_files" / E01))
    pos = ps.scaled("Comp-CD3 APC-H7 FLR-A")
    split = find_split(pos)
    xf = ps.transforms["Comp-CD3 APC-H7 FLR-A"]
    value = float(xf.inverse([split])[0])
    assert 200 < value < 1000  # between the CD3- (~10^2) and CD3+ (~2x10^3) peaks


@pytest.mark.parametrize("arms", [None, [(0.5, 1.0), (1.0, 0.7), (0.2, 0.0), (0.0, 0.3)]])
def test_spider_partitions_events(arms):
    s = Grid()
    gates = spider_gates("X", "Y", s.transforms["X"], s.transforms["Y"], (0.4, 0.5), arms)
    assert [n[:2] for n, _ in gates] == ["Q1", "Q2", "Q3", "Q4"]
    masks, total = partition(gates, s)
    assert total.max() <= 1  # sectors never overlap
    assert np.mean(total == 1) >= 0.999  # (grid points lying exactly on an arm can fall in neither)
    if arms is None:
        assert masks[0].sum() == 40 * 50  # same as a plain quadrant


def test_curly_quadrants_partition_and_curve():
    s = Grid()
    gates = curly_quadrant_gates("X", "Y", s.transforms["X"], s.transforms["Y"], (0.3, 0.3), curl=3.0)
    masks, total = partition(gates, s)
    assert np.mean(total == 1) > 0.995  # sampled curves: a few events right on a boundary
    q2 = masks[1].reshape(100, 100)  # rows = y
    # the upper arm bends right: at the top, Q2 starts further right than near the centre
    first_col = lambda row: int(np.argmax(row))  # noqa: E731
    assert first_col(q2[95]) > first_col(q2[35])
    q4 = masks[3].reshape(100, 100)
    assert q4[:30, :30].all() and not q4[30:, :].any()  # lower-left stays a straight rectangle


def test_offset_quadrants():
    s = Grid()
    gates = offset_quadrant_gates("X", "Y", x_upper=60, x_lower=30, y_left=50, y_right=50)
    masks, total = partition(gates, s)
    assert np.all(total == 1)
    assert masks[1].sum() == 40 * 50 and masks[2].sum() == 70 * 50
    with pytest.raises(ValueError):
        offset_quadrant_gates("X", "Y", 60, 30, 40, 50)


def test_boolean_combinations():
    names = boolean_combination_names(["A", "B"])
    assert names == [("A+ B+", (True, True)), ("A+ B-", (True, False)), ("A- B+", (False, True)),
                     ("A- B-", (False, False))]
