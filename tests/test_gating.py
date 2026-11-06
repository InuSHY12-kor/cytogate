"""Phase 4: gating.

Ground truth: event counts FlowJo 10 saved in .wsp files.

- Workspaces made in FlowJo 10 match FlowJo exactly.
- The "DEN" workspaces (converted from FlowJo 9) differ from the Singlets gate
  down: up to ~0.3% for polygons/rectangles and ~3.2% for the ellipse on
  Logicle axes. Cause unresolved (FlowKit shows the same gaps); a different
  Logicle display mapping in FlowJo is the leading suspect. The Time gate
  there matches exactly. Tests bound the differences so they cannot grow.
"""

import math

import numpy as np
import pytest

from cytogate.core import ProcessedSample
from cytogate.core.gating import (BooleanGate, EllipseGate, GatingTree, PolygonGate, RectangleGate,
                                  quadrant_gates)
from cytogate.core.transforms import Linear
from cytogate.io import load_fcs
from cytogate.io.wsp import read_wsp

EXACT = [  # (workspace, folder with its FCS files)
    ("8_color_data_set/reused_quad_gate_with_child.wsp", "8_color_data_set/fcs_files"),  # comp + biex + quads + child
    ("simple_diamond_example/simple_diamond_example_quad_gate.wsp", "simple_diamond_example"),
    ("simple_diamond_example/test_data_diamond_biex_rect.wsp", "simple_diamond_example"),
    ("simple_diamond_example/test_data_diamond_asinh_rect.wsp", "simple_diamond_example"),
    ("simple_diamond_example/test_data_diamond_asinh_rect2.wsp", "simple_diamond_example"),
    ("simple_line_example/single_ellipse_51_events.wsp", "simple_line_example"),
]
DEN = [
    "8_color_data_set/8_color_ICS_simple.wsp",
    "8_color_data_set/8_color_ICS_with_ellipse.wsp",
    "8_color_data_set/8_color_ICS_boolean_gate_testing.wsp",
]


def analyse(data_dir, wsp_rel, fcs_dir):
    ws = read_wsp(data_dir / wsp_rel)
    out = []
    for s in ws.samples:
        ps = ProcessedSample(load_fcs(data_dir / fcs_dir / s.file_name), compensation=s.compensation, transforms=s.transforms)
        out.append((s, ps, s.gates.counts(ps)))
    return out


def computed(s):
    """Populations FlowJo actually computed (-1 / 0 mean not computed in these files)."""
    return {k: v for k, v in s.flowjo_counts.items() if k and v > 0}


@pytest.mark.parametrize("wsp, folder", EXACT)
def test_flowjo10_workspaces_match_exactly(data_dir, wsp, folder):
    n = 0
    for s, _, counts in analyse(data_dir, wsp, folder):
        for path, fj in computed(s).items():
            assert counts[path] == fj, "/".join(path)
            n += 1
    assert n > 0


@pytest.fixture(scope="module")
def den(data_dir):
    return {w: analyse(data_dir, w, "8_color_data_set/fcs_files") for w in DEN}


def test_time_gate_matches_exactly(den):
    for s, _, counts in den[DEN[0]]:
        assert counts[("Time",)] == s.flowjo_counts[("Time",)]


def test_time_gain_matches_flowjo(den):
    for s, ps, _ in den[DEN[0]]:
        # FlowJo writes 10 significant digits
        assert ps.sample.time_gain == pytest.approx(s.transforms["Time"].gain, rel=1e-7)


@pytest.mark.parametrize("wsp", DEN)
def test_den_workspaces_within_known_difference(den, wsp):
    for s, _, counts in den[wsp]:
        for path, fj in computed(s).items():
            if fj >= 1000:
                bound = 0.035 if path[-1] == "ellipse1" else 0.005
                assert abs(counts[path] - fj) / fj < bound, "/".join(path)


def test_boolean_gates_follow_flowjo_identities(den):
    for s, _, c in den["8_color_data_set/8_color_ICS_boolean_gate_testing.wsp"]:
        cd4 = ("Time", "Singlets", "aAmine-", "CD3+", "CD4+")
        cd8 = ("Time", "Singlets", "aAmine-", "CD3+", "CD8+")
        assert c[cd4 + ("IL2+-",)] == c[cd4] - c[cd4 + ("IL2+",)]
        assert c[cd4 + ("TNFa+-",)] == c[cd4] - c[cd4 + ("TNFa+",)]
        assert c[cd8 + ("TNFa+-",)] == c[cd8] - c[cd8 + ("TNFa+",)]
        a, b = c[cd4 + ("CD107a+",)], c[cd4 + ("IFNg+",)]
        both = c[cd4 + ("CD107a+ & IFNg+",)]
        assert c[cd4 + ("CD107a+ or IFNg+",)] == a + b - both
        # the same identities hold in FlowJo's own numbers
        f = s.flowjo_counts
        assert f[cd4 + ("CD107a+ or IFNg+",)] == f[cd4 + ("CD107a+",)] + f[cd4 + ("IFNg+",)] - f[cd4 + ("CD107a+ & IFNg+",)]


# --- unit tests on a synthetic sample -------------------------------------------

class FakeSample:
    """Minimal stand-in for ProcessedSample with linear 0..100 axes."""

    def __init__(self, data: dict[str, np.ndarray]):
        self._d = data
        self.transforms = {k: Linear(0, 100) for k in data}
        self.event_count = len(next(iter(data.values())))

    def parameter(self, name):
        class P:
            pass
        p = P()
        p.name = name
        return p

    def values(self, name):
        return self._d[name]


@pytest.fixture
def grid():
    xs, ys = np.meshgrid(np.arange(0.5, 100), np.arange(0.5, 100))  # 10,000 events, one per unit cell
    return FakeSample({"X": xs.ravel(), "Y": ys.ravel()})


def test_rectangle_and_range(grid):
    assert RectangleGate(("X", "Y"), (10, 20), (30, 60)).contains(grid).sum() == 20 * 40
    assert RectangleGate(("X",), (25,), (None,)).contains(grid).sum() == 75 * 100
    assert RectangleGate(("X",), (25,), (None,), inside=False).contains(grid).sum() == 25 * 100


def test_gate_on_axis_edge_includes_offscale_events():
    s = FakeSample({"X": np.array([-50.0, 0.0, 10, 50, 100, 150]), "Y": np.zeros(6)})
    # drawn from the axis minimum: events below the axis count too
    assert RectangleGate(("X",), (0.0,), (60.0,)).contains(s).tolist() == [True, True, True, True, False, False]
    # drawn to the axis maximum: events beyond it count too
    assert RectangleGate(("X",), (40.0,), (100.0,)).contains(s).tolist() == [False, False, False, True, True, True]


def test_polygon(grid):
    tri = PolygonGate(("X", "Y"), ((0, 0), (100, 0), (0, 100)))
    assert tri.contains(grid).sum() == pytest.approx(5000, abs=100)
    square = PolygonGate(("X", "Y"), ((10, 10), (20, 10), (20, 20), (10, 20)))
    assert square.contains(grid).sum() == 100


def test_ellipse(grid):
    e = EllipseGate(("X", "Y"), (0.5, 0.5), 0.3, 0.1, math.radians(30))
    assert e.contains(grid).sum() == pytest.approx(math.pi * 30 * 10, rel=0.02)
    foci, edges = e.flowjo_points()
    again = EllipseGate.from_flowjo(("X", "Y"), foci, edges)
    assert again.center == pytest.approx(e.center)
    assert (again.a, again.b, again.angle) == pytest.approx((e.a, e.b, e.angle))


def test_quadrants_partition_parent(grid):
    tree = GatingTree()
    for name, g in quadrant_gates("X", "Y", 30, 70):
        tree.add((), name, g)
    counts = tree.counts(grid)
    assert sum(counts[(n,)] for n, _ in quadrant_gates("X", "Y", 30, 70)) == grid.event_count
    assert counts[("Q1: X- , Y+",)] == 30 * 30


def test_tree_and_booleans(grid):
    t = GatingTree()
    t.add((), "Left", RectangleGate(("X",), (None,), (50,)))
    t.add("Left", "Low", RectangleGate(("Y",), (None,), (20,)))
    t.add("Left", "Mid", RectangleGate(("Y",), (10,), (30,)))
    t.add("Left", "Low&Mid", BooleanGate("and", (("Left", "Low"), ("Left", "Mid"))))
    t.add("Left", "Low|Mid", BooleanGate("or", (("Left", "Low"), ("Left", "Mid"))))
    t.add("Left", "notLow", BooleanGate("not", (("Left", "Low"),)))
    c = t.counts(grid)
    assert c[()] == 10000 and c[("Left",)] == 5000
    assert c[("Left", "Low&Mid")] == 50 * 10
    assert c[("Left", "Low|Mid")] == 50 * 30
    assert c[("Left", "notLow")] == 5000 - 50 * 20
    assert [p.path for p in t][:2] == [("Left",), ("Left", "Low")]
    with pytest.raises(ValueError):
        t.add("Left", "Low", RectangleGate(("Y",), (0,), (1,)))
    t.remove(("Left", "Mid"))
    assert ("Left", "Mid") not in t.paths()


def test_circular_boolean_rejected(grid):
    t = GatingTree()
    t.add((), "A", BooleanGate("not", (("B",),)))
    t.add((), "B", BooleanGate("not", (("A",),)))
    with pytest.raises(ValueError):
        t.compute(grid)
