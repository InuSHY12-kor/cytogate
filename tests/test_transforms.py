"""Phase 2: axis transforms.

Ground truth:
- Biex: a lookup table exported from FlowJo (display channel -> data value).
- Every transform element in FlowJo 10 .wsp files parses and writes back unchanged.
- Linear / Log / ArcSinh: closed-form definitions, cross-checked with FlowKit.
- Logicle: defining properties from Parks et al. (2006).
"""

import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from flowkit import transforms as fkt

from cytogate.core import Arcsinh, Biex, Linear, Log, Logicle, axis_ticks, from_xml
from cytogate.core.transforms import NS

LUT = "flowjo_xforms/tr_biex_l256_w-7.943282_n1.000000_m4.418540_r262144.000029.csv"
WSP_FILES = [
    "8_color_data_set/8_color_ICS_simple.wsp",  # linear, logicle
    "8_color_data_set/reused_quad_gate_with_child.wsp",  # biex width -100
    "simple_diamond_example/test_data_diamond_biex_rect.wsp",  # biex width -10
    "simple_diamond_example/test_data_diamond_asinh_rect.wsp",  # fasinh
    "simple_diamond_example/test_data_diamond_asinh_rect2.wsp",  # fasinh M=1.3
]
X = np.array([-5000, -500, -50, 0, 10, 100, 1000, 10000, 100000, 262144], dtype=float)


def wsp_transform_elements(path):
    root = ET.parse(path).getroot()
    for block in root.iter("Transformations"):
        yield from block


# --- Biex against FlowJo ----------------------------------------------------

def test_biex_matches_flowjo_lookup_table(data_dir):
    channel, value = np.loadtxt(data_dir / LUT, delimiter=",", skiprows=1, unpack=True)
    xf = Biex(width=-7.943282, negative=1.0, positive=4.418540, max_range=262144.000029)
    # FlowJo's table has 4096 display channels.
    np.testing.assert_allclose(xf.apply(value) * 4096, channel, atol=0.01)
    np.testing.assert_allclose(xf.inverse(channel / 4096), value, rtol=1e-4, atol=0.01)


def test_biex_defaults_are_flowjo_defaults():
    xf = Biex()
    assert (xf.width, xf.negative, xf.positive, xf.max_range) == (-10, 0, 4.418539922, 262144)
    assert xf.apply([262144])[0] == pytest.approx(1.0)


# --- closed forms -------------------------------------------------------------

def test_linear():
    xf = Linear(-100, 900)
    np.testing.assert_allclose(xf.apply([-100, 400, 900]), [0, 0.5, 1])
    np.testing.assert_allclose(xf.inverse(xf.apply(X)), X)


def test_log_matches_flowkit():
    xf = Log(offset=1, decades=5.4185)
    np.testing.assert_allclose(xf.apply(X), fkt.WSPLogTransform(offset=1, decades=5.4185).apply(X))
    assert xf.apply([1000])[0] == pytest.approx(3 / 5.4185)
    pos = X[X >= 1]
    np.testing.assert_allclose(xf.inverse(xf.apply(pos)), pos)


@pytest.mark.parametrize("T, M, A", [(262144, 4, 0), (262144, 1.3, 0), (10000, 4.5, 1)])
def test_arcsinh(T, M, A):
    xf = Arcsinh(T, M, A)
    expected = (np.arcsinh(X * math.sinh(M * math.log(10)) / T) + A * math.log(10)) / ((M + A) * math.log(10))
    np.testing.assert_allclose(xf.apply(X), expected)
    np.testing.assert_allclose(xf.apply(X), fkt.AsinhTransform(T, M, A).apply(X), rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(xf.inverse(xf.apply(X)), X, rtol=1e-9, atol=1e-6)
    assert xf.apply([T])[0] == pytest.approx(1.0)


def logicle_inverse_reference(y, T, W, M, A):
    """Logicle inverse S(y) written out from Moore & Parks, Cytometry A 81A (2012)."""
    w, x2 = W / (M + A), A / (M + A)
    x1, x0 = x2 + w, x2 + 2 * w
    b = (M + A) * math.log(10)
    # d solves w = 2 (ln d - ln b) / (b + d), 0 < d <= b
    lo, hi = 1e-12, b
    for _ in range(200):
        d = (lo + hi) / 2
        if 2 * (math.log(d) - math.log(b)) / (b + d) + w > 0:
            hi = d
        else:
            lo = d
    ca = math.exp(x0 * (b + d))
    fa = math.exp(b * x1) - ca / math.exp(d * x1)
    a = T / ((math.exp(b) - fa) - ca / math.exp(d))
    c, f = ca * a, -fa * a

    def s(v):
        return a * np.exp(b * v) - c * np.exp(-d * v) + f

    y = np.asarray(y, dtype=float)
    return np.where(y >= x1, s(y), -s(2 * x1 - y))


@pytest.mark.parametrize("T, W, M, A", [(262144, 0.5, 4.5, 0), (262144, 1, 4.418539922, 0), (10000, 0.5, 4.5, 1)])
def test_logicle_properties(T, W, M, A):
    xf = Logicle(T, W, M, A)
    assert xf.apply([T])[0] == pytest.approx(1.0)
    zero = (W + A) / (M + A)  # display position of 0
    assert xf.apply([0])[0] == pytest.approx(zero)
    # odd symmetry around the zero point
    pos = np.array([10.0, 100, 1000, 5000])
    np.testing.assert_allclose(xf.apply(pos) - zero, zero - xf.apply(-pos), rtol=1e-9)
    # inverse equals the closed-form biexponential of Moore & Parks (2012)
    y = np.linspace(0, 1, 101)
    np.testing.assert_allclose(xf.inverse(y), logicle_inverse_reference(y, T, W, M, A), rtol=1e-6, atol=1e-6)
    assert np.all(np.diff(xf.apply(np.sort(X))) > 0)
    np.testing.assert_allclose(xf.inverse(xf.apply(X)), X, rtol=1e-6, atol=1e-6)


# --- FlowJo .wsp elements -----------------------------------------------------

@pytest.mark.parametrize("wsp", WSP_FILES)
def test_parse_and_write_flowjo_transform_xml(data_dir, wsp):
    els = list(wsp_transform_elements(data_dir / wsp))
    assert els
    for el in els:
        param, xf = from_xml(el)
        assert el.tag.endswith("}" + xf.kind)
        assert param
        out = xf.to_xml(param)
        assert from_xml(out) == (param, xf)
        for key, value in el.attrib.items():
            assert float(out.get(key)) == pytest.approx(float(value)), key
        assert xf.apply([0.0, 1000.0]).shape == (2,)


def test_flowjo_transform_kinds_seen(data_dir):
    kinds = {from_xml(el)[1].kind for w in WSP_FILES for el in wsp_transform_elements(data_dir / w)}
    assert kinds == {"linear", "logicle", "biex", "fasinh"}


def test_log_xml_round_trip():
    el = Log(offset=1, decades=4.5).to_xml("FL1-H")
    assert el.tag == "{%s}log" % NS["transforms"]
    assert from_xml(el) == ("FL1-H", Log(1, 4.5))
    assert "transforms:log" in ET.tostring(el).decode()


def test_unknown_transform_rejected():
    with pytest.raises(ValueError):
        from_xml(ET.Element("{%s}hyperbolic" % NS["transforms"]))


# --- axis ticks -----------------------------------------------------------------

def test_linear_ticks_flowjo_style():
    labels = [t.label for t in axis_ticks(Linear(0, 262144)) if t.label]
    assert labels == ["0", "50K", "100K", "150K", "200K", "250K"]


@pytest.mark.parametrize("xf", [Biex(), Logicle(262144, 1, 4.418539922, 0), Arcsinh(262144, 4, 0)])
def test_log_like_ticks(xf):
    ticks = axis_ticks(xf)
    labels = [t.label for t in ticks if t.label]
    assert {"0", "10⁴", "10⁵"} <= set(labels)
    positions = [t.position for t in ticks]
    assert positions == sorted(positions) and 0 <= positions[0] and positions[-1] <= 1
    shown = [t for t in ticks if t.label]
    assert all(b.position - a.position >= 0.04 - 1e-12 for a, b in zip(shown, shown[1:]))
    assert any(not t.major for t in ticks)  # 2..9 minor ticks


def test_log_ticks():
    labels = [t.label for t in axis_ticks(Log(1, 5)) if t.label]
    assert labels == ["10⁰", "10¹", "10²", "10³", "10⁴", "10⁵"]
