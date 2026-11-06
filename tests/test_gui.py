"""Phase 3: the desktop window, run headless (QT_QPA_PLATFORM=offscreen)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyqtgraph")

import numpy as np  # noqa: E402

from cytogate.core import Biex, Linear, Logicle, axis_ticks  # noqa: E402
from cytogate.gui.main_window import HISTOGRAM_CHOICE, MainWindow  # noqa: E402
from cytogate.gui.transform_dialog import TransformDialog  # noqa: E402
from cytogate.plots import PlotType  # noqa: E402

FOLDER = "8_color_data_set/fcs_files"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, data_dir):
    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    w.add_samples([data_dir / FOLDER])
    yield w
    w.close()


def test_samples_listed_with_counts(win):
    rows = [(win.tree.topLevelItem(i).text(0), win.tree.topLevelItem(i).text(2)) for i in range(win.tree.topLevelItemCount())]
    assert rows == [
        ("101_DEN084Y5_15_E01_008_clean.fcs", "290,172"),
        ("101_DEN084Y5_15_E03_009_clean.fcs", "283,969"),
        ("101_DEN084Y5_15_E05_010_clean.fcs", "285,290"),
    ]


def test_default_graph_is_fsc_ssc_pseudocolor(win):
    assert win.x_box.currentData() == "FSC-A"
    assert win.y_box.currentData() == "SSC-A"
    assert win.type_box.currentText() == "Pseudocolor"
    assert win.view.last_render.counts.sum() == 290172
    assert "290,172" in win.view.title.text()


@pytest.mark.parametrize("kind", [PlotType.DOT, PlotType.CONTOUR, PlotType.DENSITY])
def test_switch_plot_type(win, kind):
    win.type_box.setCurrentText(kind.value)
    assert win.view.last_render.counts.sum() == 290172


def test_histogram_choice(win):
    win.x_box.setCurrentIndex(win.x_box.findData("Comp-CD3 APC-H7 FLR-A"))
    win.y_box.setCurrentIndex(win.y_box.findText(HISTOGRAM_CHOICE))
    assert win.view.last_render.sum() == 290172
    assert not win.type_box.isEnabled() and win.hist_y_box.isEnabled()
    win.hist_y_box.setCurrentText("Normalized To Mode")
    assert win.view.last_render.max() == pytest.approx(100)


def test_axis_ticks_follow_transform(win):
    win.x_box.setCurrentIndex(win.x_box.findData("Comp-CD4 PE-Cy7 FLR-A"))
    axis = win.view.plot.getAxis("bottom")
    expected = [(t.position, t.label) for t in axis_ticks(Biex()) if t.major]
    assert axis._tickLevels[0] == expected


def test_change_transform_kind(win):
    win.x_box.setCurrentIndex(win.x_box.findData("Comp-CD4 PE-Cy7 FLR-A"))
    win.x_xf.setCurrentText("Logicle")
    win._xf_kind_chosen(win.x_box, win.x_xf)
    assert isinstance(win.current().transforms["Comp-CD4 PE-Cy7 FLR-A"], Logicle)
    # each sample keeps its own transforms
    other = win.samples["101_DEN084Y5_15_E03_009_clean.fcs"].ps
    assert isinstance(other.transforms["Comp-CD4 PE-Cy7 FLR-A"], Biex)


def test_transform_dialog(app, win):
    ps = win.current()
    dlg = TransformDialog(ps.parameter("FSC-A"), ps.transforms["FSC-A"])
    assert set(dlg.fields) == {"min_range", "max_range"}
    dlg.fields["max_range"].setValue(200000)
    assert dlg.transform() == Linear(0, 200000)
    dlg.kind.setCurrentText("Biex")
    dlg.fields["width"].setValue(-100)
    assert dlg.transform() == Biex(width=-100, max_range=262144)


def test_export_png(win, tmp_path):
    out = tmp_path / "g.png"
    assert win.view.export_png(str(out))
    assert out.stat().st_size > 5000


def test_bad_file_reported(app, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    bad = tmp_path / "bad.fcs"
    bad.write_bytes(b"nope")
    w = MainWindow()
    assert w.add_samples([bad])
    assert shown and "bad.fcs" in shown[0]
    assert w.tree.topLevelItemCount() == 0


# --- gating (phase 4) ------------------------------------------------------------

E01 = "101_DEN084Y5_15_E01_008_clean.fcs"


@pytest.fixture
def gw(win, monkeypatch):
    """Window with E01 selected, FSC-A x SSC-A, gate names auto-accepted."""
    monkeypatch.setattr(win, "ask_gate_name", lambda default: default)
    win.select(E01)
    win.x_box.setCurrentIndex(win.x_box.findData("FSC-A"))
    win.y_box.setCurrentIndex(win.y_box.findData("SSC-A"))
    return win


def item_rows(item):
    return [(item.child(i).text(0), item.child(i).text(1), item.child(i).text(2)) for i in range(item.childCount())]


def test_rectangle_gate_from_shape(gw):
    a = gw.samples[E01]
    [path] = gw.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    assert path == ("Rectangle",)
    n = a.count(path)
    xs, ys = a.ps.scaled("FSC-A"), a.ps.scaled("SSC-A")
    assert n == int(((xs >= 0.2) & (xs <= 0.6) & (ys >= 0.05) & (ys <= 0.35)).sum())
    rows = item_rows(gw.sample_item(E01))
    assert rows == [("Rectangle", f"{100 * n / 290172:.3g}", f"{n:,}")]
    assert len(gw.view.gate_items) == 2  # outline + label on the plot


def test_nested_gate_and_population_plot(gw):
    gw.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    gw.select(E01, ("Rectangle",))
    a = gw.samples[E01]
    assert f"{a.count(('Rectangle',)):,}" in gw.view.title.text()
    [path] = gw.shape_drawn("Ellipse", [(0.3, 0.1), (0.5, 0.3)])
    assert path == ("Rectangle", "Ellipse")
    assert 0 < a.count(path) < a.count(("Rectangle",))


def test_polygon_through_viewbox_signal(gw):
    vb = gw.view.viewbox
    gw.tool_actions["Polygon"].trigger()
    assert vb.tool == "Polygon"
    for p in [(0.2, 0.05), (0.6, 0.05), (0.6, 0.4), (0.2, 0.4)]:
        vb.add_polygon_point(p)
    vb.add_polygon_point((0.2, 0.05))  # click on the first vertex closes it
    a = gw.samples[E01]
    assert ("Polygon",) in a.gates.paths()
    assert isinstance(a.gates.get(("Polygon",)).gate.vertices, tuple)


def test_quadrants_sum_to_parent(gw):
    gw.x_box.setCurrentIndex(gw.x_box.findData("Comp-CD4 PE-Cy7 FLR-A"))
    gw.y_box.setCurrentIndex(gw.y_box.findData("Comp-CD8 PerCP-Cy55 FLR-A"))
    made = gw.shape_drawn("Quadrant", [(0.45, 0.45)])
    assert [p[0][:2] for p in made] == ["Q1", "Q2", "Q3", "Q4"]
    a = gw.samples[E01]
    assert sum(a.count(p) for p in made) == 290172


def test_range_gate_on_histogram(gw):
    gw.x_box.setCurrentIndex(gw.x_box.findData("Comp-CD3 APC-H7 FLR-A"))
    gw.y_box.setCurrentIndex(gw.y_box.findText(HISTOGRAM_CHOICE))
    assert gw.tool_actions["Range"].isEnabled() and not gw.tool_actions["Rectangle"].isEnabled()
    [path] = gw.shape_drawn("Range", [(0.55, 0.0), (1.0, 0.0)])
    a = gw.samples[E01]
    cd3 = a.ps.scaled("Comp-CD3 APC-H7 FLR-A")
    assert a.count(path) == int((cd3 >= 0.55).sum())  # drawn to the axis edge: open-ended


def test_delete_gate(gw):
    gw.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    gw.select(E01, ("Rectangle",))
    gw.delete_selected_gate()
    assert gw.samples[E01].gates.paths() == []
    assert gw.sample_item(E01).childCount() == 0


def test_mouse_drag_draws_rectangle(gw, app):
    from PySide6 import QtCore, QtGui

    gw.tool_actions["Rectangle"].trigger()
    app.processEvents()
    vb = gw.view.viewbox
    view = gw.view.plot

    def to_widget(x, y):
        scene = vb.mapViewToScene(QtCore.QPointF(x, y))
        return view.mapFromScene(scene)

    p0, p1 = to_widget(0.2, 0.1), to_widget(0.6, 0.4)
    vp = view.viewport()
    left, none = QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton
    mods = QtCore.Qt.KeyboardModifier.NoModifier

    def send(kind, pt, button, buttons):
        pos = QtCore.QPointF(pt)
        ev = QtGui.QMouseEvent(kind, pos, pos, vp.mapToGlobal(pos), button, buttons, mods)
        QtWidgets.QApplication.sendEvent(vp, ev)

    send(QtCore.QEvent.Type.MouseButtonPress, p0, left, left)
    for t in np.linspace(0, 1, 10)[1:]:
        pt = QtCore.QPointF(p0.x() + (p1.x() - p0.x()) * t, p0.y() + (p1.y() - p0.y()) * t)
        send(QtCore.QEvent.Type.MouseMove, pt, none, left)
    send(QtCore.QEvent.Type.MouseButtonRelease, p1, left, none)
    app.processEvents()
    gate = gw.samples[E01].gates.get(("Rectangle",)).gate
    lo = gw.samples[E01].ps.transforms["FSC-A"].apply(np.array([gate.mins[0]]))[0]
    assert lo == pytest.approx(0.2, abs=0.02)


# --- draggable gate labels ---------------------------------------------------------

def _mouse(vp, kind, pt, button, buttons):
    from PySide6 import QtCore, QtGui
    pos = QtCore.QPointF(pt)
    ev = QtGui.QMouseEvent(kind, pos, pos, vp.mapToGlobal(pos), button, buttons, QtCore.Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(vp, ev)


def _drag(view, vb, start_view, end_view, app):
    from PySide6 import QtCore
    to_w = lambda x, y: QtCore.QPointF(view.mapFromScene(vb.mapViewToScene(QtCore.QPointF(x, y))))  # noqa: E731
    p0, p1 = to_w(*start_view), to_w(*end_view)
    vp = view.viewport()
    L, N, T = QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton, QtCore.QEvent.Type
    _mouse(vp, T.MouseButtonPress, p0, L, L)
    for t in np.linspace(0, 1, 10)[1:]:
        _mouse(vp, T.MouseMove, p0 + (p1 - p0) * float(t), N, L)
    _mouse(vp, T.MouseButtonRelease, p1, L, N)
    app.processEvents()


def test_drag_gate_label(gw, app):
    gw.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    app.processEvents()
    [label] = gw.view.gate_labels()
    start = (label.pos().x(), label.pos().y())
    _drag(gw.view.plot, gw.view.viewbox, start, (start[0] + 0.25, start[1] + 0.4), app)
    pop = gw.samples[E01].gates.get(("Rectangle",))
    assert pop.label_offset == pytest.approx((0.25, 0.4), abs=0.02)
    # the gate itself did not move
    assert pop.gate.mins[0] == pytest.approx(float(gw.samples[E01].ps.transforms["FSC-A"].inverse([0.2])[0]))
    # position is kept when the plot is redrawn
    gw.redraw()
    [label] = gw.view.gate_labels()
    assert label.pos().x() == pytest.approx(start[0] + 0.25, abs=0.02)


def test_label_drag_ignored_while_drawing(gw, app):
    gw.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    gw.tool_actions["Rectangle"].trigger()
    app.processEvents()
    [label] = gw.view.gate_labels()
    start = (label.pos().x(), label.pos().y())
    _drag(gw.view.plot, gw.view.viewbox, start, (start[0] + 0.2, start[1] + 0.3), app)
    a = gw.samples[E01]
    assert a.gates.get(("Rectangle",)).label_offset == (0.0, 0.0)
    assert ("Rectangle 2",) in a.gates.paths()  # the drag drew a new gate instead


def test_label_double_click_resets(gw, app):
    gw.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    pop = gw.samples[E01].gates.get(("Rectangle",))
    pop.label_offset = (0.3, 0.3)
    gw.redraw()
    [label] = gw.view.gate_labels()

    class Ev:
        def double(self):
            return True

        def accept(self):
            pass

        def ignore(self):
            pass

    label.mouseClickEvent(Ev())
    assert pop.label_offset == (0.0, 0.0)


def test_drag_histogram_label(gw, app):
    gw.x_box.setCurrentIndex(gw.x_box.findData("Comp-CD3 APC-H7 FLR-A"))
    gw.y_box.setCurrentIndex(gw.y_box.findText(HISTOGRAM_CHOICE))
    gw.shape_drawn("Range", [(0.55, 0.0), (0.9, 0.0)])
    app.processEvents()
    [label] = gw.view.gate_labels()
    top = gw.view.viewbox.viewRange()[1][1]
    start = (label.pos().x(), label.pos().y())
    _drag(gw.view.plot, gw.view.viewbox, start, (start[0] - 0.2, start[1] + 0.2 * top), app)
    off = gw.samples[E01].gates.get(("Range",)).label_offset
    assert off == pytest.approx((-0.2, 0.2), abs=0.03)


# --- statistics (phase 5) --------------------------------------------------------

def test_add_and_delete_statistic(gw, monkeypatch):
    from cytogate.core.stats import Stat, StatSpec

    gw.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    gw.select(E01, ("Rectangle",))
    spec = StatSpec(Stat.MEDIAN, "Comp-CD3 APC-H7 FLR-A")
    monkeypatch.setattr(gw, "ask_stat", lambda a: spec)
    gw.add_stat_dialog()
    pop_item = gw.find_item(E01, ("Rectangle",))
    stat_rows = [pop_item.child(i) for i in range(pop_item.childCount()) if pop_item.child(i).text(0).startswith("Σ")]
    assert [r.text(0) for r in stat_rows] == ["Σ Median : Comp-CD3 APC-H7 FLR-A"]
    a = gw.samples[E01]
    assert stat_rows[0].text(1) == f"{a.stat(('Rectangle',), spec):.0f}"
    # selecting the statistic plots its population; Delete removes only the statistic
    gw.tree.setCurrentItem(stat_rows[0])
    assert gw.selection()[1] == ("Rectangle",)
    gw.delete_selected_gate()
    assert a.gates.get(("Rectangle",)).stats == []
    assert ("Rectangle",) in a.gates.paths()


def test_statistic_on_sample_root(gw, monkeypatch):
    from cytogate.core.stats import Stat, StatSpec

    monkeypatch.setattr(gw, "ask_stat", lambda a: StatSpec(Stat.COUNT))
    gw.select(E01)
    gw.add_stat_dialog()
    top = gw.sample_item(E01)
    assert top.child(0).text(0) == "Σ Count" and top.child(0).text(1) == "290,172"


def test_stat_dialog(app):
    from cytogate.core.stats import Stat
    from cytogate.gui.stat_dialog import StatDialog

    dlg = StatDialog([("FSC-A", "FSC-A"), ("Comp-R780-A", "Comp-R780-A :: CD3")])
    dlg.stat_box.setCurrentIndex(dlg.stat_box.findData(Stat.FREQ_PARENT))
    assert not dlg.param_box.isEnabled() and dlg.spec().parameter is None
    dlg.stat_box.setCurrentIndex(dlg.stat_box.findData(Stat.PERCENTILE))
    dlg.param_box.setCurrentIndex(1)
    dlg.pct_box.setValue(95)
    s = dlg.spec()
    assert (s.stat, s.parameter, s.percentile) == (Stat.PERCENTILE, "Comp-R780-A", 95)
