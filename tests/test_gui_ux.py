"""User feedback round: icon tool buttons, live drawing preview, larger vertex grab
area, several workspace windows, double-click a gate to open it."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore  # noqa: E402

from cytogate.core import RectangleGate  # noqa: E402
from cytogate.gui.gate_tools import TOOLS  # noqa: E402
from cytogate.gui.icons import ICON_NAMES, icon  # noqa: E402
from cytogate.gui.main_window import MainWindow  # noqa: E402
from cytogate.gui.plot_view import GateHandle, GateShape  # noqa: E402

E01 = "101_DEN084Y5_15_E01_008_clean.fcs"


@pytest.fixture(scope="module")
def app():
    import pyqtgraph as pg
    pg.setConfigOptions(mouseRateLimit=-1)
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def close(w):
    import gc
    w.close_all_graphs()
    w.dirty = False
    w.close()
    w.ws.samples.clear()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()
    gc.collect()


@pytest.fixture
def win(app, data_dir):
    w = MainWindow()
    w.show()
    w.add_samples([data_dir / "8_color_data_set/fcs_files" / E01])
    w.samples[E01].add_gate((), "Cells", RectangleGate(("FSC-A", "SSC-A"), (50000.0, 0.0), (None, 150000.0)))
    w.data_changed([E01])
    yield w
    close(w)


def test_icons_and_pointer(win):
    for name in ICON_NAMES:
        assert not icon(name).isNull()
    g = win.open_graph(E01, ())
    assert g.pointer_action.isChecked() and g.view.viewbox.tool is None
    for tool in TOOLS:
        assert not g.tool_actions[tool].icon().isNull() and g.tool_actions[tool].toolTip()
    assert g.tool_bar.toolButtonStyle() == QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly
    assert not g.up_btn.icon().isNull() and not g.prev_btn.icon().isNull()
    g.tool_actions["Polygon"].trigger()
    assert g.view.viewbox.tool == "Polygon" and not g.pointer_action.isChecked()
    g.tool_actions["Polygon"].trigger()  # off again -> pointer
    assert g.view.viewbox.tool is None and g.pointer_action.isChecked()
    g.tool_actions["Rectangle"].trigger()
    g.select_pointer()
    assert g.view.viewbox.tool is None and not g.tool_actions["Rectangle"].isChecked()


def test_live_preview(win):
    g = win.open_graph(E01, ())
    vb = g.view.viewbox
    g.tool_actions["Polygon"].trigger()
    vb.add_polygon_point((0.2, 0.2))
    vb.hover((0.5, 0.6))  # rubber band from the first point to the cursor
    xs, ys = vb.preview.getData()
    assert list(zip(xs, ys)) == [(0.2, 0.2), (0.5, 0.6)]
    vb.add_polygon_point((0.6, 0.2))
    vb.hover((0.4, 0.8))  # open polygon + cursor + dashed way back to the start
    xs, ys = vb.preview.getData()
    assert list(zip(xs, ys)) == [(0.2, 0.2), (0.6, 0.2), (0.4, 0.8), (0.2, 0.2)]
    assert vb.preview.opts["pen"].style() == QtCore.Qt.PenStyle.DashLine
    g.tool_actions["Quadrant"].trigger()
    vb.hover((0.3, 0.7))
    xs, ys = vb.guide.getData()
    assert xs[0] == xs[1] == 0.3 and ys[3] == ys[4] == 0.7  # crosshair
    g.select_pointer()
    assert vb.guide.getData()[0] is None or len(vb.guide.getData()[0]) == 0


def test_vertex_handles_are_easy_to_grab(win):
    g = win.open_graph(E01, ())
    g.select_gate((E01, ("Cells",)))
    handles = g.view.gate_handles()
    assert handles
    h = handles[0]
    assert h.cursor().shape() == QtCore.Qt.CursorShape.SizeAllCursor
    # grab area: 12 px around the vertex, not just the 7 px square
    assert h.shape().contains(QtCore.QPointF(10, 0)) and h.shape().contains(QtCore.QPointF(-7, 7))
    assert not h.shape().contains(QtCore.QPointF(14, 0))
    assert h.boundingRect().width() == 2 * GateHandle.HIT


class _Click:
    def __init__(self, double):
        self._double = double
        self.accepted = False

    def button(self):
        return QtCore.Qt.MouseButton.LeftButton

    def double(self):
        return self._double

    def accept(self):
        self.accepted = True

    def ignore(self):
        pass


def test_double_click_gate_opens_population(win):
    g = win.open_graph(E01, ())
    shape = next(s for s in g.view.gate_shapes())
    assert isinstance(shape, GateShape) and shape.on_open is not None
    n = len(win.graphs)
    shape.mouseClickEvent(_Click(double=True))
    assert len(win.graphs) == n + 1
    child = win.graphs[-1]
    assert child.path == ("Cells",) and child is not g
    assert child.geometry().left() >= g.geometry().right()
    # again: the existing window comes forward instead of a second copy
    assert g.open_gate_window((E01, ("Cells",))) is child and len(win.graphs) == n + 1
    # while drawing, a double-click belongs to the tool
    g.tool_actions["Polygon"].trigger()
    ev = _Click(double=True)
    g.view.gate_shapes()[0].mouseClickEvent(ev)
    assert not ev.accepted and len(win.graphs) == n + 1


def test_several_workspaces(win, tmp_path):
    assert win in MainWindow.open_windows()
    w2 = win.new_workspace_window()
    try:
        assert w2 in MainWindow.open_windows() and w2 is not win and win.samples  # the first one is untouched
        assert w2.windowTitle() == f"CytoGate — Untitled {w2.untitled_no}" and w2.untitled_no > 1 and w2.is_blank() and not win.is_blank()
        path = win.save(tmp_path / "first.wsp")
        # already open -> brought forward, not opened twice
        assert w2.open_in_window(path) is win
        # a blank window takes the workspace itself
        w2_path = tmp_path / "copy.wsp"
        import shutil
        shutil.copy(path, w2_path)
        assert w2.open_in_window(w2_path) is w2 and E01 in w2.samples
        # a busy window opens it in another new window
        shutil.copy(path, tmp_path / "third.wsp")
        w3 = win.open_in_window(tmp_path / "third.wsp")
        try:
            assert w3 is not win and w3 is not w2 and E01 in w3.samples and len(win.samples) == 1
        finally:
            close(w3)
        win._fill_window_menu()
        titles = [a.text() for a in win.window_menu.actions() if a.isCheckable()]
        assert "first.wsp" in titles and "copy.wsp" in titles
        # gates dragged from another window are ignored
        assert win.own([("not-here.fcs", ())]) == [] and win.own([(E01, ())]) == [(E01, ())]
    finally:
        close(w2)
    assert w2 not in MainWindow.open_windows()
