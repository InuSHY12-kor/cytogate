"""Graph window features from FlowJo's Graph Window documentation:
menus (File/Edit/Graph/Display/Go), Y selector on the Y axis, breadcrumb menus,
undo/redo, duplicate view, active gate panel, T menu, edge warning, output."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyqtgraph")

import numpy as np  # noqa: E402
from PySide6 import QtCore, QtGui  # noqa: E402

from cytogate.core import Biex, EllipseGate, Linear, Log, PolygonGate, RectangleGate  # noqa: E402
from cytogate.core.stats import Stat, StatSpec  # noqa: E402
from cytogate.gui.graph_window import GraphWindow, ManualGateDialog  # noqa: E402
from cytogate.gui.main_window import MainWindow  # noqa: E402

E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def app():
    import pyqtgraph as pg
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
    w.add_samples([data_dir / "8_color_data_set/fcs_files"])
    yield w
    release(w)


@pytest.fixture
def g(win, monkeypatch):
    monkeypatch.setattr(GraphWindow, "ask_gate_name",
                        lambda self, default: self.analysis.unique_name(self.path, self.last_tool))
    gw = win.open_graph(E01)
    gw.resize(600, 760)
    QtWidgets.QApplication.processEvents()
    return gw


def act(gw, key):
    return gw.actions_by_name[key]


# --- layout ---------------------------------------------------------------------------------

def test_y_selector_sits_on_the_y_axis(g):
    QtWidgets.QApplication.processEvents()
    yb, plot = g.y_button.geometry(), g.view.geometry()
    assert yb.right() <= plot.left() + 2  # left of the plot
    assert yb.height() > 0.5 * plot.height()  # runs along it
    assert g.y_button.text() == "SSC-A"
    g.y_menu.aboutToShow.emit()
    items = [g.y_menu.list.item(i).text() for i in range(g.y_menu.list.count())]
    assert "Comp-CD8 PerCP-Cy55 FLR-A" in items and items[-1] == "Histogram"
    g.y_menu.search.setText("cd8")
    assert [g.y_menu.list.item(i).text() for i in range(g.y_menu.list.count())
            if not g.y_menu.list.item(i).isHidden()] == ["Comp-CD8 PerCP-Cy55 FLR-A"]
    g.y_menu.chosen.emit("Comp-CD8 PerCP-Cy55 FLR-A")
    assert g.y == "Comp-CD8 PerCP-Cy55 FLR-A" and g.y_button.text() == "Comp-CD8 PerCP-Cy55 FLR-A"


def test_menus(g):
    menus = {m.title(): [a.text() for a in m.actions() if a.text()] for m in g.menu_bar.findChildren(QtWidgets.QMenu)
             if m.title()}
    assert menus["File"] == ["Close", "Close All Graph Windows", "Reset This Graph Window's Size",
                             "Reset All Graph Windows Size", "Export Graphics As…", "Print…"]
    assert "Copy Histogram Function" in menus["Edit"] and "Undo" in menus["Edit"]
    assert menus["Graph"] == ["Duplicate View", "Transpose Axes", "Manually Enter Gate…"]
    assert menus["Display"] == ["Show Grid", "Draw Large Dots", "Show Gate Frequencies", "Show Gate Names",
                                "Show Medians", "Show Uncompensated Parameters"]
    assert menus["Go"] == ["Show Workspace", "Up to Parent", "Open Child Population", "Next Sample",
                           "Previous Sample", "Previous X", "Next X", "Previous Y", "Next Y"]


def test_suggested_gate_names(g):
    assert g.suggested_name() == "FSC-A, SSC-A subset"
    g.set_axes("Comp-CD3 APC-H7 FLR-A", None)
    assert g.suggested_name() == "Comp-CD3 APC-H7 FLR-A subset"


def test_uncompensated_parameters_hidden_by_default(g):
    names = [g.x_box.itemData(i) for i in range(g.x_box.count())]
    assert "Comp-CD4 PE-Cy7 FLR-A" in names and "CD4 PE-Cy7 FLR-A" not in names
    act(g, "uncomp").trigger()
    names = [g.x_box.itemData(i) for i in range(g.x_box.count())]
    assert "CD4 PE-Cy7 FLR-A" in names


# --- navigation -------------------------------------------------------------------------------

def test_breadcrumb_menus(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    g.shape_drawn("Ellipse", [(0.3, 0.4), (0.5, 0.6)])
    g.show_population(E01, ("Rectangle",))
    assert g.crumb_texts() == [E01, "Rectangle"]
    samples_menu, siblings_menu = g.crumb_menus()
    assert samples_menu == [E01, E03, E05]
    assert siblings_menu == ["Rectangle", "Ellipse"]


def test_next_previous_x_y_and_transpose(g):
    x0 = g.x
    act(g, "next_x").trigger()
    assert g.x != x0
    act(g, "prev_x").trigger()
    assert g.x == x0
    act(g, "next_y").trigger()
    assert g.y not in ("SSC-A", None)
    g.set_axes("FSC-A", "SSC-A")
    act(g, "transpose").trigger()
    assert (g.x, g.y) == ("SSC-A", "FSC-A")


def test_shift_arrow_moves_every_graph_of_the_sample(win, g):
    second = g.duplicate_view()
    other = win.open_graph(E05)
    g.step_all_graphs(+1)
    assert g.sample_name == E03 and second.sample_name == E03
    assert other.sample_name == E05


def test_duplicate_view(win, g):
    g.set_axes("Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A")
    g.type_box.setCurrentText("Contour")
    dup = g.duplicate_view()
    assert dup is not g and len(win.graphs) == 2
    assert (dup.x, dup.y, dup.type_box.currentText()) == (g.x, g.y, "Contour")
    dup.set_axes("FSC-A", "SSC-A")
    assert g.x == "Comp-CD4 PE-Cy7 FLR-A"  # independent views


def test_close_all(win, g):
    g.duplicate_view()
    act(g, "close_all").trigger()
    QtWidgets.QApplication.processEvents()
    assert win.graphs == []


# --- undo / redo -----------------------------------------------------------------------------

def test_undo_redo_gate_and_transform(win, g):
    a = win.samples[E01]
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    assert a.gates.paths() == [("Rectangle",)]
    g.set_axis_kind("x", "Log")
    assert isinstance(a.ps.transforms["FSC-A"], Log)
    act(g, "undo").trigger()
    assert isinstance(a.ps.transforms["FSC-A"], Linear)
    g.undo_btn.click()
    assert a.gates.paths() == [] and win.sample_item(E01).childCount() == 0
    act(g, "redo").trigger()
    assert a.gates.paths() == [("Rectangle",)]
    assert win.find_item(E01, ("Rectangle",)).text(2) == f"{a.count(('Rectangle',)):,}"


def test_undo_group_copy(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    win.drop_on_group([(E01, ("Rectangle",))], "All Samples")
    assert win.samples[E05].gates.paths() == [("Rectangle",)]
    win.undo()
    assert win.samples[E05].gates.paths() == []
    assert win.samples[E01].gates.get(("Rectangle",)).owning_group is None


# --- active gate --------------------------------------------------------------------------------

def test_select_gate_shows_handles_and_reshapes_polygon(win, g):
    g.tool_actions["Polygon"].trigger()
    vb = g.view.viewbox
    for p in [(0.2, 0.05), (0.6, 0.05), (0.6, 0.4), (0.2, 0.4)]:
        vb.add_polygon_point(p)
    vb.add_polygon_point((0.2, 0.05))
    g.tool_actions["Polygon"].trigger()  # tool off
    key = (E01, ("Polygon",))
    g.select_gate(key)
    assert g.active == key and len(g.view.gate_handles()) == 4
    assert g.view.gate_shapes()[0].active
    a = win.samples[E01]
    before = a.count(("Polygon",))
    g.vertex_moved(key, 2, (0.8, 0.6))
    xf = a.ps.transforms["FSC-A"]
    assert xf.apply([a.gates.get(("Polygon",)).gate.vertices[2][0]])[0] == pytest.approx(0.8)
    assert a.count(("Polygon",)) > before


def test_rectangle_corner_handle(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    key = (E01, ("Rectangle",))
    g.select_gate(key)
    assert len(g.view.gate_handles()) == 4
    g.vertex_moved(key, 2, (0.7, 0.5))  # top-right corner
    gate = win.samples[E01].gates.get(("Rectangle",)).gate
    ps = win.samples[E01].ps
    assert ps.transforms["FSC-A"].apply([gate.maxs[0]])[0] == pytest.approx(0.7)
    assert ps.transforms["SSC-A"].apply([gate.maxs[1]])[0] == pytest.approx(0.5)
    assert ps.transforms["FSC-A"].apply([gate.mins[0]])[0] == pytest.approx(0.2)  # opposite corner kept
    g.vertex_moved(key, 2, (1.0, 0.5))  # dragged onto the axis edge: open-ended
    assert win.samples[E01].gates.get(("Rectangle",)).gate.maxs[0] is None


def test_grow_shrink_inside_tint_delete(win, g):
    g.shape_drawn("Ellipse", [(0.3, 0.1), (0.5, 0.3)])
    key = (E01, ("Ellipse",))
    g.select_gate(key)
    a = win.samples[E01]
    n = a.count(("Ellipse",))
    g.grow_btn.click()
    assert a.gates.get(("Ellipse",)).gate.a == pytest.approx(0.1 * 1.05)
    assert a.count(("Ellipse",)) > n
    g.inside_box.setChecked(False)
    gate = a.gates.get(("Ellipse",)).gate
    assert gate.inside is False
    assert a.count(("Ellipse",)) == int((~EllipseGate(gate.dims, gate.center, gate.a, gate.b, gate.angle).contains(a.ps)).sum())
    g.tint_box.setChecked(True)
    assert a.gates.get(("Ellipse",)).tint == "#ff0000"
    assert g.view.gate_shapes()[0]._tint is not None
    QtWidgets.QApplication.sendEvent(g, QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Delete,
                                                        QtCore.Qt.KeyboardModifier.NoModifier))
    assert a.gates.paths() == []


def test_arrow_keys_nudge_active_gate(win, g):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    key = (E01, ("Rectangle",))
    g.select_gate(key)
    ps = win.samples[E01].ps
    before = ps.transforms["FSC-A"].apply([win.samples[E01].gates.get(("Rectangle",)).gate.mins[0]])[0]
    for _ in range(5):
        QtWidgets.QApplication.sendEvent(g, QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, QtCore.Qt.Key.Key_Right,
                                                            QtCore.Qt.KeyboardModifier.NoModifier))
    after = ps.transforms["FSC-A"].apply([win.samples[E01].gates.get(("Rectangle",)).gate.mins[0]])[0]
    px = g.view.viewbox.viewPixelSize()[0]
    assert after - before == pytest.approx(5 * px, rel=1e-6)


def test_manual_gate_entry(win, g):
    dlg = ManualGateDialog("FSC-A", "SSC-A")
    dlg.fields["xmin"].setText("50000")
    dlg.fields["xmax"].setText("150000")
    dlg.fields["ymin"].setText("")
    dlg.fields["ymax"].setText("80000")
    gate = dlg.gate("FSC-A", "SSC-A")
    assert gate == RectangleGate(("FSC-A", "SSC-A"), (50000.0, None), (150000.0, 80000.0))
    dlg.kind.setCurrentText("Polygon")
    dlg.vertices.setPlainText("1e4, 1e4\n9e4, 1e4\n9e4, 9e4")
    assert dlg.gate("FSC-A", "SSC-A") == PolygonGate(("FSC-A", "SSC-A"), ((1e4, 1e4), (9e4, 1e4), (9e4, 9e4)))
    dlg.vertices.setPlainText("1, 2")
    with pytest.raises(ValueError):
        dlg.gate("FSC-A", "SSC-A")
    assert g.add_gate(gate, "Manual") == ("Manual",)


# --- display, T menu, warning, statistics ------------------------------------------------------

def test_display_options(win, g):
    g.type_box.setCurrentText("Dot Plot")
    small = int((g.view.last_render.image[:, :, 3] > 0).sum())
    act(g, "large_dots").trigger()
    assert int((g.view.last_render.image[:, :, 3] > 0).sum()) > small
    act(g, "medians").trigger()
    assert len(g.view.median_items) == 2
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    assert "Rectangle" in g.view.gate_labels()[0].toPlainText()
    act(g, "names").trigger()
    assert "Rectangle" not in g.view.gate_labels()[0].toPlainText()
    act(g, "freqs").trigger()
    assert g.view.gate_labels() == []  # neither name nor frequency
    g.bg_btn.set_color((0, 0, 0))
    assert g.view.options.background == (0, 0, 0)


def test_t_button_menu_and_reset(win, g):
    menu = [a.text() for a in g.t_x.menu().actions() if a.text()]
    assert menu == ["Linear Axis", "Log Axis", "Biex Axis", "Reset", "Customize Axis…"]
    g.set_axes("Comp-CD4 PE-Cy7 FLR-A", "SSC-A")
    g.set_axis_kind("x", "Linear")
    assert isinstance(g.analysis.ps.transforms["Comp-CD4 PE-Cy7 FLR-A"], Linear)
    g.reset_axis("x")
    assert g.analysis.ps.transforms["Comp-CD4 PE-Cy7 FLR-A"] == Biex(max_range=262144)


def test_edge_warning(win, g):
    g.set_axes("Comp-CD4 PE-Cy7 FLR-A", "SSC-A")
    assert g.warning.isHidden()
    g.apply_transform("Comp-CD4 PE-Cy7 FLR-A", Linear(0, 1000), False)  # most events beyond 1000
    fx, fy = g.edge_fractions()
    assert fx > 0.08 and fy < 0.08
    assert not g.warning.isHidden() and "of events are on the chart edges" in g.warning.text()
    assert "ff8c00" in g.t_x.styleSheet() and g.t_y.styleSheet() == ""


def test_statistics_panel(win, g, monkeypatch):
    g.shape_drawn("Rectangle", [(0.2, 0.05), (0.6, 0.35)])
    sub = win.open_graph(E01, ("Rectangle",))
    monkeypatch.setattr(win, "ask_stat", lambda a: StatSpec(Stat.MEDIAN, "FSC-A"))
    sub.add_statistic_dialog()
    rows = [sub.stat_list.item(i).text() for i in range(sub.stat_list.count())]
    assert rows[0].startswith("Freq. of Total:") and rows[1].startswith("Median : FSC-A:")
    assert win.find_item(E01, ("Rectangle",)).child(0).text(0) == "Σ Median : FSC-A"


# --- output -----------------------------------------------------------------------------------------

def test_copy_histogram_function(win, g):
    g.set_axes("Comp-CD3 APC-H7 FLR-A", None)
    text = g.copy_histogram_function()
    lines = text.splitlines()
    assert len(lines) == 1 + 4096
    assert sum(int(line.split("\t")[1]) for line in lines[1:]) == 290172
    assert QtWidgets.QApplication.clipboard().text() == text


def test_copy_image_transparency(g):
    clear = g.copy_image(transparent=True)
    white = g.copy_image(transparent=False)
    assert QtGui.QColor.fromRgba(clear.pixel(1, 1)).alpha() == 0
    assert QtGui.QColor.fromRgba(white.pixel(1, 1)).alpha() == 255


@pytest.mark.parametrize("ext", ["png", "jpg", "svg", "pdf"])
def test_export_formats(g, tmp_path, ext):
    out = tmp_path / f"graph.{ext}"
    assert g.view.export_image(str(out))
    assert out.stat().st_size > 1000


def test_print_to_pdf(g, tmp_path):
    from PySide6 import QtPrintSupport
    printer = QtPrintSupport.QPrinter()
    printer.setOutputFormat(QtPrintSupport.QPrinter.OutputFormat.PdfFormat)
    out = tmp_path / "printed.pdf"
    printer.setOutputFileName(str(out))
    g.print_to(printer)
    assert out.stat().st_size > 1000
