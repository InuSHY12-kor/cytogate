"""C1 in the windows: Boolean gates/combinations, BifurGate, bisector, spider,
curly quad tools and offset quadrant arms."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cytogate.core import RectangleGate  # noqa: E402
from cytogate.core.gating import BooleanGate, PolygonGate  # noqa: E402
from cytogate.gui.main_window import MainWindow  # noqa: E402

E01, E03 = "101_DEN084Y5_15_E01_008_clean.fcs", "101_DEN084Y5_15_E03_009_clean.fcs"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, data_dir):
    import gc
    w = MainWindow()
    w.show()
    w.add_samples([data_dir / "8_color_data_set/fcs_files"])
    yield w
    w.close_all_graphs()
    w.dirty = False
    w.close()
    w.ws.samples.clear()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()
    gc.collect()


@pytest.fixture
def two_gates(win):
    a = win.samples[E01]
    a.add_gate((), "Big", RectangleGate(("FSC-A",), (80000.0,), (None,)))
    a.add_gate((), "LowSSC", RectangleGate(("SSC-A",), (None,), (60000.0,)))
    win.data_changed([E01])
    return a


def test_boolean_gates_from_selection(win, two_gates):
    a = two_gates
    def select(*paths):
        win.tree.clearSelection()
        for p in paths:
            win.find_item(E01, p).setSelected(True)

    select(("Big",), ("LowSSC",))
    assert win.make_boolean("and") == ("Big & LowSSC",)
    select(("Big",), ("LowSSC",))
    assert win.make_boolean("or") == ("Big or LowSSC",)
    select(("Big",), ("LowSSC",))
    assert win.make_boolean("not") is None  # NOT needs exactly one
    win.tree.clearSelection()
    win.find_item(E01, ("Big",)).setSelected(True)
    assert win.make_boolean("not") == ("NOT Big",)
    big, low = a.count(("Big",)), a.count(("LowSSC",))
    both = a.count(("Big & LowSSC",))
    assert a.count(("Big or LowSSC",)) == big + low - both
    assert a.count(("NOT Big",)) == 290172 - big
    win.undo()
    assert ("NOT Big",) not in a.gates.paths()


def test_boolean_combinations(win, two_gates):
    a = two_gates
    made = win.make_combinations(E01, [("Big",), ("LowSSC",)])
    assert [p[-1] for p in made] == ["Big+ LowSSC+", "Big+ LowSSC-", "Big- LowSSC+", "Big- LowSSC-"]
    assert sum(a.count(p) for p in made) == 290172  # the combinations partition the parent
    assert a.count(("Big+ LowSSC+",)) == a.count(("Big",)) - a.count(("Big+ LowSSC-",))
    assert isinstance(a.gates.get(("NOT Big",)).gate, BooleanGate)


def test_bifurgate(win):
    a = win.samples[E01]
    a.add_gate((), "Cells", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    made = win.bifurgate(E01, ("Cells",))
    names = [p[-1] for p in made]
    assert "Comp-CD3 APC-H7 FLR-A+" in names and "Comp-CD3 APC-H7 FLR-A-" in names
    assert not any(n.startswith("FSC") for n in names)  # scatter and already-gated parameters skipped
    plus, minus = ("Cells", "Comp-CD3 APC-H7 FLR-A+"), ("Cells", "Comp-CD3 APC-H7 FLR-A-")
    assert a.count(plus) + a.count(minus) >= a.count(("Cells",))


def test_bisector_spider_curly_tools(win, monkeypatch):
    g = win.open_graph(E01)
    a = win.samples[E01]
    g.set_axes("Comp-CD3 APC-H7 FLR-A", None)
    assert g.tool_actions["Bisector"].isEnabled() and not g.tool_actions["Spider"].isEnabled()
    made = g.shape_drawn("Bisector", [(0.6, 0.5)])
    assert [p[-1] for p in made] == ["Comp-CD3 APC-H7 FLR-A-", "Comp-CD3 APC-H7 FLR-A+"]
    assert sum(a.count(p) for p in made) == 290172
    g.set_axes("Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A")
    spider = g.shape_drawn("Spider", [(0.5, 0.5)])
    assert len(spider) == 4 and all(isinstance(a.gates.get(p).gate, PolygonGate) for p in spider)
    assert abs(sum(a.count(p) for p in spider) - 290172) <= 2
    curly = g.shape_drawn("Curly Quad", [(0.5, 0.5)])
    assert len(curly) == 4 and abs(sum(a.count(p) for p in curly) - 290172) < 0.002 * 290172


def test_offset_quadrant_arms(win):
    g = win.open_graph(E01)
    a = win.samples[E01]
    g.set_axes("Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A")
    quads = g.shape_drawn("Quadrant", [(0.5, 0.5)])
    g.select_gate((E01, quads[1]))
    assert g.quad_set() is not None and len(g.view.gate_handles()) == 5
    g.quad_arm_moved(1, (0.6, 0.97))  # top arm only
    _, sp = g.quad_set()
    xf = a.ps.transforms["Comp-CD4 PE-Cy7 FLR-A"]
    assert xf.apply([sp["x_upper"]])[0] == pytest.approx(0.6)
    assert xf.apply([sp["x_lower"]])[0] == pytest.approx(0.5)
    assert sum(a.count(p) for p in quads) == 290172  # still covers every event once
    g.quad_arm_moved(0, (0.4, 0.4))  # centre: straightens and moves everything
    _, sp = g.quad_set()
    assert sp["x_upper"] == sp["x_lower"] and sp["y_left"] == sp["y_right"]
