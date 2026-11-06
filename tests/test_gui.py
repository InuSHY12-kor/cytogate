"""Desktop windows, run headless (QT_QPA_PLATFORM=offscreen).

Structure (like FlowJo): a workspace window with groups and the sample /
population tree, and separate graph windows opened per population.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyqtgraph")

import numpy as np  # noqa: E402
from PySide6 import QtCore, QtGui  # noqa: E402

from cytogate.core import Biex, Linear, Logicle, axis_ticks  # noqa: E402
from cytogate.core.axis_tools import axis_min  # noqa: E402
from cytogate.core.stats import Stat, StatSpec  # noqa: E402
from cytogate.gui.graph_window import HISTOGRAM_CHOICE  # noqa: E402
from cytogate.gui.main_window import POP_MIME, MainWindow, decode_populations, encode_populations  # noqa: E402
from cytogate.plots import PlotType  # noqa: E402

FOLDER = "8_color_data_set/fcs_files"
E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def app():
    import pyqtgraph as pg

    # Tests send mouse moves faster than a hand can; don't let pyqtgraph drop them.
    pg.setConfigOptions(mouseRateLimit=-1)
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def release(w):
    """Close a workspace window and free its sample data (each holds ~160 MB)."""
    import gc
    w.close_all_graphs()
    w.close()
    w.ws.samples.clear()
    w.history._undo.clear()
    w.history._redo.clear()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()
    gc.collect()


@pytest.fixture
def win(app, data_dir):
    w = MainWindow()
    w.show()
    w.add_samples([data_dir / FOLDER])
    yield w
    release(w)


@pytest.fixture
def g(win, monkeypatch):
    """Graph window for E01 (ungated), FSC-A x SSC-A, gate names auto-accepted."""
    from cytogate.gui.graph_window import GraphWindow

    # name new gates after their tool ("Rectangle", "Rectangle 2", …) to keep tests readable
    monkeypatch.setattr(GraphWindow, "ask_gate_name",
                        lambda self, default: self.analysis.unique_name(self.path, self.last_tool))
    gw = win.open_graph(E01)
    gw.resize(560, 660)
    QtWidgets.QApplication.processEvents()
    return gw


def rows(item):
    return [(item.child(i).text(0), item.child(i).text(1), item.child(i).text(2)) for i in range(item.childCount())]


def set_axes(gw, x, y):
    gw.x_box.setCurrentIndex(gw.x_box.findData(x))
    if y is None:
        gw.y_box.setCurrentIndex(gw.y_box.findText(HISTOGRAM_CHOICE))
    else:
        gw.y_box.setCurrentIndex(gw.y_box.findData(y))


# --- mouse helpers -----------------------------------------------------------------------

def _send(vp, kind, pt, button, buttons):
    pos = QtCore.QPointF(pt)
    ev = QtGui.QMouseEvent(kind, pos, pos, vp.mapToGlobal(pos), button, buttons, QtCore.Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(vp, ev)


def drag(gw, start, end):
    """Left-drag on a graph from `start` to `end` (display units)."""
    view, vb = gw.view.plot, gw.view.viewbox
    to_w = lambda x, y: QtCore.QPointF(view.mapFromScene(vb.mapViewToScene(QtCore.QPointF(x, y))))  # noqa: E731
    p0, p1 = to_w(*start), to_w(*end)
    vp = view.viewport()
    L, N, T = QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton, QtCore.QEvent.Type
    _send(vp, T.MouseButtonPress, p0, L, L)
    for t in np.linspace(0, 1, 10)[1:]:
        _send(vp, T.MouseMove, p0 + (p1 - p0) * float(t), N, L)
    _send(vp, T.MouseButtonRelease, p1, L, N)
    QtWidgets.QApplication.processEvents()


def drop(widget, item_rect_center, md):
    ev = QtGui.QDropEvent(QtCore.QPointF(item_rect_center), QtCore.Qt.DropAction.CopyAction, md,
                          QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
    widget.dropEvent(ev)


# --- workspace window ----------------------------------------------------------------------

def test_samples_and_groups_listed(win):
    tops = [(win.tree.topLevelItem(i).text(0), win.tree.topLevelItem(i).text(2)) for i in range(win.tree.topLevelItemCount())]
    assert tops == [(E01, "290,172"), (E03, "283,969"), (E05, "285,290")]
    top = win.group_list.topLevelItem(0)
    assert (top.text(0), top.text(1)) == ("All Samples", "3")


def test_bad_file_reported(app, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a: shown.append(a[2]))
    bad = tmp_path / "bad.fcs"
    bad.write_bytes(b"nope")
    w = MainWindow()
    assert w.add_samples([bad])
    assert shown and "bad.fcs" in shown[0] and w.tree.topLevelItemCount() == 0


def test_double_click_opens_graph_windows(win):
    win.tree.itemDoubleClicked.emit(win.sample_item(E01), 0)
    assert len(win.graphs) == 1
    gw = win.graphs[0]
    assert gw.isWindow() and gw.windowTitle() == f"{E01}: Ungated"
    assert (gw.x, gw.y, gw.type_box.currentText()) == ("FSC-A", "SSC-A", "Pseudocolor")
    assert gw.view.last_render.counts.sum() == 290172
    assert gw.footer.text() == "Count: 290,172"
    assert win.open_graph(E01) is gw  # same population: brought forward, not duplicated
    other = win.open_graph(E03)
    assert len(win.graphs) == 2 and other is not gw
    other.close()
    QtWidgets.QApplication.processEvents()
    assert win.graphs == [gw]


def test_plot_types_and_histogram(g):
    for kind in (PlotType.DOT, PlotType.CONTOUR, PlotType.DENSITY):
        g.type_box.setCurrentText(kind.value)
        assert g.view.last_render.counts.sum() == 290172
    set_axes(g, "Comp-CD3 APC-H7 FLR-A", None)
    assert g.view.last_render.sum() == 290172
    assert not g.type_box.isEnabled() and g.tool_actions["Range"].isEnabled()
    g.hist_y_box.setCurrentText("Normalized To Mode")
    assert g.view.last_render.max() == pytest.approx(100)
    set_axes(g, "Comp-CD4 PE-Cy7 FLR-A", "SSC-A")
    expected = [(t.position, t.label) for t in axis_ticks(Biex()) if t.major]
    assert g.view.plot.getAxis("bottom")._tickLevels[0] == expected


# --- gating in a graph window ------------------------------------------------------------------

def test_rectangle_gate_updates_tree_and_plot(win, g):
    a = win.samples[E01]
    [path] = g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    n = a.count(path)
    xs, ys = a.ps.scaled("FSC-A"), a.ps.scaled("SSC-A")
    assert n == int(((xs >= 0.2) & (xs <= 0.6) & (ys >= 0.05) & (ys <= 0.35)).sum())
    assert rows(win.sample_item(E01)) == [("Rectangle", f"{100 * n / 290172:.3g}", f"{n:,}")]
    assert len(g.view.gate_items) == 2  # outline + label


def test_population_window_and_nested_gate(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    sub = win.open_graph(E01, ("Rectangle",))
    a = win.samples[E01]
    n = a.count(("Rectangle",))
    assert sub.windowTitle() == f"{E01}: Rectangle"
    assert sub.view.last_render.counts.sum() == n
    assert sub.footer.text().startswith(f"Count: {n:,} / 290,172")
    # axes default to those of the gate that made the population
    assert (sub.x, sub.y) == ("FSC-A", "SSC-A")
    [path] = sub.shape_drawn("Ellipse", [(0.3, 0.1), (0.5, 0.3)])
    assert path == ("Rectangle", "Ellipse")
    assert 0 < a.count(path) < n


def test_polygon_through_viewbox(win, g):
    g.tool_actions["Polygon"].trigger()
    vb = g.view.viewbox
    for p in [(0.2, 0.05), (0.6, 0.05), (0.6, 0.4), (0.2, 0.4)]:
        vb.add_polygon_point(p)
    vb.add_polygon_point((0.2, 0.05))  # clicking the first vertex closes it
    assert ("Polygon",) in win.samples[E01].gates.paths()


def test_quadrants_and_range(win, g):
    set_axes(g, "Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A")
    made = g.shape_drawn("Quadrant", [(0.45, 0.45)])
    a = win.samples[E01]
    assert [p[0][:2] for p in made] == ["Q1", "Q2", "Q3", "Q4"]
    assert sum(a.count(p) for p in made) == 290172
    assert all(s.on_moved is None for s in g.view.gate_shapes())  # quadrants are not dragged
    set_axes(g, "Comp-CD3 APC-H7 FLR-A", None)
    [path] = g.shape_drawn("Range", [(0.55, 0.0), (1.0, 0.0)])
    assert a.count(path) == int((a.ps.scaled("Comp-CD3 APC-H7 FLR-A") >= 0.55).sum())


def test_mouse_drag_draws_rectangle(win, g):
    g.tool_actions["Rectangle"].trigger()
    drag(g, (0.2, 0.1), (0.6, 0.4))
    gate = win.samples[E01].gates.get(("Rectangle",)).gate
    assert win.samples[E01].ps.transforms["FSC-A"].apply([gate.mins[0]])[0] == pytest.approx(0.2, abs=0.02)


def test_delete_gate_moves_open_window_to_parent(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    sub = win.open_graph(E01, ("Rectangle",))
    win.select(E01, ("Rectangle",))
    win.delete_selected_gate()
    assert win.samples[E01].gates.paths() == []
    assert sub.path == () and sub.windowTitle() == f"{E01}: Ungated"


def test_missing_population_falls_back(win, g):
    sub = win.open_graph(E03, ("Nope",))
    assert sub.path == ()


# --- labels and moving gates -----------------------------------------------------------------

def test_drag_label(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    [label] = g.view.gate_labels()
    start = (label.pos().x(), label.pos().y())
    drag(g, start, (start[0] + 0.25, start[1] + 0.4))
    pop = win.samples[E01].gates.get(("Rectangle",))
    assert pop.label_offset == pytest.approx((0.25, 0.4), abs=0.02)
    g.redraw()
    assert g.view.gate_labels()[0].pos().x() == pytest.approx(start[0] + 0.25, abs=0.02)


def test_drag_histogram_label(win, g):
    set_axes(g, "Comp-CD3 APC-H7 FLR-A", None)
    g.shape_drawn("Range", [(0.55, 0.0), (0.9, 0.0)])
    [label] = g.view.gate_labels()
    top = g.view.viewbox.viewRange()[1][1]
    start = (label.pos().x(), label.pos().y())
    drag(g, start, (start[0] - 0.2, start[1] + 0.2 * top))
    assert win.samples[E01].gates.get(("Range",)).label_offset == pytest.approx((-0.2, 0.2), abs=0.03)


def test_label_double_click_resets(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    pop = win.samples[E01].gates.get(("Rectangle",))
    pop.label_offset = (0.3, 0.3)
    g.redraw()

    class Ev:
        def double(self):
            return True

        def accept(self):
            pass

        def ignore(self):
            pass

    g.view.gate_labels()[0].mouseClickEvent(Ev())
    assert pop.label_offset == (0.0, 0.0)


def test_drawing_over_a_label_draws_a_gate(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    g.tool_actions["Rectangle"].trigger()
    [label] = g.view.gate_labels()
    start = (label.pos().x(), label.pos().y())
    drag(g, start, (start[0] + 0.2, start[1] + 0.3))
    a = win.samples[E01]
    assert a.gates.get(("Rectangle",)).label_offset == (0.0, 0.0)
    assert ("Rectangle 2",) in a.gates.paths()


def test_move_group_gate(win, g, monkeypatch):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    win.select(E01, ("Rectangle",))
    assert win.apply_to_current_group().added == [E03, E05]
    asked = []
    monkeypatch.setattr(win, "ask_scope", lambda grp, n: asked.append((grp, n)) or "group")
    drag(g, (0.3, 0.1), (0.4, 0.15))  # inside the gate, away from its label
    assert asked == [("All Samples", 3)]
    xf = win.samples[E01].ps.transforms["FSC-A"]
    for n in (E01, E03, E05):
        gate = win.samples[n].gates.get(("Rectangle",)).gate
        assert xf.apply([gate.mins[0]])[0] == pytest.approx(0.3, abs=0.02)


def test_move_gate_for_one_sample(win, g, monkeypatch):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    win.select(E01, ("Rectangle",))
    win.apply_to_current_group()
    monkeypatch.setattr(win, "ask_scope", lambda grp, n: "sample")
    drag(g, (0.3, 0.1), (0.3, 0.2))
    a1, a3 = win.samples[E01], win.samples[E03]
    assert a1.gates.get(("Rectangle",)).gate != a3.gates.get(("Rectangle",)).gate
    assert a1.gates.get(("Rectangle",)).owning_group is None
    assert not win.find_item(E01, ("Rectangle",)).font(0).bold()
    assert win.find_item(E03, ("Rectangle",)).font(0).bold()


def test_move_own_gate_without_asking(win, g, monkeypatch):
    g.shape_drawn("Ellipse", [(0.2, 0.05), (0.6, 0.35)])
    monkeypatch.setattr(win, "ask_scope", lambda grp, n: pytest.fail("should not ask"))
    drag(g, (0.4, 0.12), (0.5, 0.12))
    assert win.samples[E01].gates.get(("Ellipse",)).gate.center == pytest.approx((0.5, 0.2), abs=0.02)


# --- navigation -------------------------------------------------------------------------------

def test_next_previous_sample_keep_plot(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    win.select(E01, ("Rectangle",))
    win.apply_to_current_group()
    sub = win.open_graph(E01, ("Rectangle",))
    set_axes(sub, "Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A")
    sub.type_box.setCurrentText("Contour")
    sub.next_btn.click()
    assert (sub.sample_name, sub.path) == (E03, ("Rectangle",))
    assert (sub.x, sub.y, sub.type_box.currentText()) == ("Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A", "Contour")
    assert sub.view.last_render.counts.sum() == win.samples[E03].count(("Rectangle",))
    sub.next_btn.click()
    assert sub.sample_name == E05
    sub.next_btn.click()
    assert sub.sample_name == E01  # wraps around
    sub.prev_btn.click()
    assert sub.sample_name == E05


def test_next_sample_without_the_population(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    sub = win.open_graph(E01, ("Rectangle",))
    sub.step_sample(+1)  # E03 has no such gate: show its nearest existing population
    assert (sub.sample_name, sub.path) == (E03, ())


def test_navigation_follows_group(win, g, monkeypatch):
    monkeypatch.setattr(win, "ask_group_name", lambda: "Pair")
    win.tree.clearSelection()
    win.sample_item(E01).setSelected(True)
    win.sample_item(E05).setSelected(True)
    win.new_group_dialog()
    assert win.visible_samples() == [E01, E05]
    g.step_sample(+1)
    assert g.sample_name == E05


def test_up_down_and_path(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    sub = win.open_graph(E01, ("Rectangle",))
    sub.shape_drawn("Ellipse", [(0.3, 0.1), (0.5, 0.3)])
    sub.down_btn.click()
    assert sub.path == ("Rectangle", "Ellipse")
    assert sub.crumb_texts() == [E01, "Rectangle", "Ellipse"]
    assert not sub.down_btn.isEnabled()
    sub.up_btn.click()
    sub.up_btn.click()
    assert sub.path == () and not sub.up_btn.isEnabled()


# --- transform window (T buttons) ---------------------------------------------------------------

def test_transform_window_biex(win, g):
    set_axes(g, "Comp-CD4 PE-Cy7 FLR-A", "SSC-A")
    ed = g.open_transform_editor("x", show=False)
    assert ed.scale_box.currentText() == "Biex"
    assert ed.width_edit.text() == "-10"
    assert float(ed.min_edit.text()) == pytest.approx(axis_min(Biex()), rel=1e-3) and ed.min_edit.isReadOnly()
    ed.width.slider.setValue(ed.width.slider.maximum() * 2 // 3)  # 10^2 -> width basis -100
    assert ed.xf.width == pytest.approx(-100, rel=0.01)
    assert float(ed.min_edit.text()) < -1000
    ed.neg.box.setValue(0.5)
    assert ed.xf.negative == 0.5
    before = ed.xf
    ed.adjust_high(+1)
    assert ed.xf.max_range == pytest.approx(2 * before.max_range)
    ed.adjust_high(-1)
    ed.calculate_width_basis()
    assert -1000 <= ed.xf.width <= -1
    # nothing changes until Apply
    assert g.analysis.ps.transforms["Comp-CD4 PE-Cy7 FLR-A"] == Biex(max_range=262144)
    chosen = ed.xf
    ed.apply()
    assert g.analysis.ps.transforms["Comp-CD4 PE-Cy7 FLR-A"] == chosen
    assert win.samples[E03].ps.transforms["Comp-CD4 PE-Cy7 FLR-A"] == Biex(max_range=262144)  # this sample only
    assert g.view.plot.getAxis("bottom")._tickLevels[0] == [(t.position, t.label) for t in axis_ticks(chosen) if t.major]


def test_transform_window_scale_change_and_group(win, g):
    ed = g.open_transform_editor("y", show=False)  # SSC-A, linear
    assert ed.scale_box.currentText() == "Linear" and not ed.min_edit.isReadOnly()
    ed.max_edit.setText("200000")
    ed.max_edit.editingFinished.emit()
    assert ed.xf == Linear(0, 200000)
    ed.adjust_low(+1)
    assert ed.xf.min_range < 0
    ed.scale_box.setCurrentText("Logicle")
    ed.scale_box.activated.emit(ed.scale_box.currentIndex())
    assert isinstance(ed.xf, Logicle)
    ed.group_check.setChecked(True)
    ed.apply()
    for n in (E01, E03, E05):
        assert isinstance(win.samples[n].ps.transforms["SSC-A"], Logicle)


def test_transform_window_cancel(win, g):
    ed = g.open_transform_editor("x", show=False)
    ed.adjust_high(+1)
    ed.reject()
    assert g.analysis.ps.transforms["FSC-A"] == Linear(0, 262144)


def test_transform_change_recounts_gates(win, g):
    set_axes(g, "Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A")
    g.shape_drawn("Ellipse", [(0.4, 0.4), (0.7, 0.6)])  # ellipses live in display space
    a = win.samples[E01]
    before = a.count(("Ellipse",))
    ed = g.open_transform_editor("x", show=False)
    ed.adjust_low(+1)
    ed.apply()
    assert a.count(("Ellipse",)) != before
    assert win.find_item(E01, ("Ellipse",)).text(2) == f"{a.count(('Ellipse',)):,}"


# --- drag & drop gates onto groups / samples ---------------------------------------------------

def test_drag_gates_to_all_samples(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    g.shape_drawn("Ellipse", [(0.3, 0.4), (0.5, 0.6)])
    win.tree.clearSelection()
    for p in (("Rectangle",), ("Ellipse",)):
        win.find_item(E01, p).setSelected(True)
    md = win.tree.mimeData(win.tree.selectedItems())
    assert md.hasFormat(POP_MIME)
    assert decode_populations(md) == [(E01, ("Rectangle",)), (E01, ("Ellipse",))]
    drop(win.group_list, win.group_list.visualItemRect(win.group_item("All Samples")).center(), md)
    for n in (E03, E05):
        assert win.samples[n].gates.paths() == [("Rectangle",), ("Ellipse",)]
        assert win.find_item(n, ("Rectangle",)).font(0).bold()
        assert win.samples[n].gates.get(("Ellipse",)).owning_group == "All Samples"


def test_drop_gate_on_one_sample(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    drop(win.tree, win.tree.visualItemRect(win.sample_item(E05)).center(), encode_populations([(E01, ("Rectangle",))]))
    assert win.samples[E05].gates.paths() == [("Rectangle",)]
    assert win.samples[E05].gates.get(("Rectangle",)).owning_group is None
    assert win.samples[E03].gates.paths() == []


def test_drop_child_without_parent_is_reported(win, g, monkeypatch):
    warned = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a: warned.append(a[2]))
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    sub = win.open_graph(E01, ("Rectangle",))
    sub.shape_drawn("Ellipse", [(0.3, 0.1), (0.5, 0.3)])
    result = win.drop_on_group([(E01, ("Rectangle", "Ellipse"))], "All Samples")
    assert set(result.skipped) == {E03, E05} and warned


# --- groups and statistics ------------------------------------------------------------------------

def test_new_group_filters_samples(win, monkeypatch):
    monkeypatch.setattr(win, "ask_group_name", lambda: "Stim")
    win.tree.clearSelection()
    win.sample_item(E03).setSelected(True)
    win.sample_item(E05).setSelected(True)
    assert win.new_group_dialog() == "Stim"
    groups = [(win.group_list.topLevelItem(i).text(0), win.group_list.topLevelItem(i).text(1))
              for i in range(win.group_list.topLevelItemCount())]
    assert groups == [("All Samples", "3"), ("Stim", "2")]
    assert win.sample_item(E01).isHidden() and not win.sample_item(E03).isHidden()
    win.group_list.setCurrentItem(win.group_item("All Samples"))
    assert not win.sample_item(E01).isHidden()
    win.group_list.setCurrentItem(win.group_item("Stim"))
    win.delete_current_group()
    assert win.group_list.topLevelItemCount() == 1


def test_statistics(win, g, monkeypatch):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    win.select(E01, ("Rectangle",))
    spec = StatSpec(Stat.MEDIAN, "Comp-CD3 APC-H7 FLR-A")
    monkeypatch.setattr(win, "ask_stat", lambda a: spec)
    win.add_stat_dialog()
    item = win.find_item(E01, ("Rectangle",))
    stat_rows = [item.child(i) for i in range(item.childCount()) if item.child(i).text(0).startswith("Σ")]
    assert [r.text(0) for r in stat_rows] == ["Σ Median : Comp-CD3 APC-H7 FLR-A"]
    assert stat_rows[0].text(1) == f"{win.samples[E01].stat(('Rectangle',), spec):.0f}"
    win.tree.setCurrentItem(stat_rows[0])
    win.delete_selected_gate()
    assert win.samples[E01].gates.get(("Rectangle",)).stats == []
    assert ("Rectangle",) in win.samples[E01].gates.paths()


def test_stat_dialog(app):
    from cytogate.gui.stat_dialog import StatDialog

    dlg = StatDialog([("FSC-A", "FSC-A"), ("Comp-R780-A", "Comp-R780-A :: CD3")])
    dlg.stat_box.setCurrentIndex(dlg.stat_box.findData(Stat.FREQ_PARENT))
    assert not dlg.param_box.isEnabled() and dlg.spec().parameter is None
    dlg.stat_box.setCurrentIndex(dlg.stat_box.findData(Stat.PERCENTILE))
    dlg.param_box.setCurrentIndex(1)
    dlg.pct_box.setValue(95)
    assert dlg.spec() == StatSpec(Stat.PERCENTILE, "Comp-R780-A", 95)
