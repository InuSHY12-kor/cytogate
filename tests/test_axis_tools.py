"""Axis adjustments behind the transform window's (−)(+) buttons and
"Calculate Width Basis"."""

import numpy as np
import pytest

from cytogate.core import Arcsinh, Biex, Linear, Log, Logicle
from cytogate.core.axis_tools import auto_width_basis, axis_max, axis_min, expand_high, expand_low


@pytest.mark.parametrize("xf", [Linear(0, 1000), Log(1, 5), Biex(), Logicle(262144, 0.5, 4.5, 0), Arcsinh(262144, 4, 0.5)])
def test_plus_shows_more_and_minus_undoes_it(xf):
    lo, hi = axis_min(xf), axis_max(xf)
    wider_low = expand_low(xf, +1)
    assert axis_min(wider_low) < lo
    assert axis_max(wider_low) == pytest.approx(hi, rel=1e-3)
    assert axis_min(expand_low(wider_low, -1)) == pytest.approx(lo, rel=1e-3, abs=1e-6)
    wider_high = expand_high(xf, +1)
    assert axis_max(wider_high) > hi
    assert axis_max(expand_high(wider_high, -1)) == pytest.approx(hi, rel=1e-3)


def test_biex_width_matches_flowjo_example():
    # FlowJo's transform window shows Min -1627.954 for width basis -100, no extra negative decades
    assert axis_min(Biex(width=-100, max_range=263037)) == pytest.approx(-1628, rel=0.01)


def test_biex_width_limits():
    xf = Biex(width=-1000)
    assert expand_low(xf, +1).width == -1000  # FlowJo's slider stops at 10^3
    assert expand_low(Biex(width=-1), -1).width == -1


def test_auto_width_basis():
    rng = np.random.default_rng(0)
    data = np.concatenate([rng.normal(0, 300, 20000), rng.normal(20000, 3000, 20000)])
    w = auto_width_basis(data, Biex())
    assert -1000 <= w <= -1
    assert axis_min(Biex(width=w)) <= np.percentile(data, 0.5)
    # a narrower width would cut off the lowest events
    assert axis_min(Biex(width=w / 10 ** 0.05)) > np.percentile(data, 0.5)
    assert auto_width_basis(np.abs(data) + 1, Biex()) == -10.0  # nothing negative
