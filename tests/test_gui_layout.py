"""F1–F4 in the window: Layout Editor editing, overlays, backgating, presets, export, persistence."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")

from cytogate.core import RectangleGate  # noqa: E402
from cytogate.core.layout import LayoutItem  # noqa: E402
from cytogate.gui.layout_editor import ItemGraphic  # noqa: E402
from cytogate.gui.main_window import MainWindow, encode_populations  # noqa: E402

E01 = "101_DEN084Y5_15_E01_008_clean.fcs"
E03 = "101_DEN084Y5_15_E03_009_clean.fcs"


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
    folder = data_dir / "8_color_data_set/fcs_files"
    w.add_samples([folder / E01, folder / E03])
    for n in (E01, E03):
        w.samples[n].add_gate((), "Cells", RectangleGate(("FSC-A", "SSC-A"), (50000.0, 0.0), (None, 150000.0)))
        w.samples[n].add_gate("Cells", "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
    w.data_changed([E01, E03])
    yield w
    close(w)


def test_editing(win, tmp_path, monkeypatch):
    monkeypatch.setenv("CYTOGATE_HOME", str(tmp_path / "home"))
    ed = win.open_layout_editor()
    lay = ed.layout
    assert win.layouts == [lay] and lay.name == "Layout 1"
    made = ed.drop(encode_populations([(E01, ("Cells",))]), QtCore.QPointF(40, 50))
    g = made[0]
    assert (g.kind, g.path, g.x_param, g.y_param, g.sample) == ("graph", ("Cells",), "Comp-CD3 APC-H7 FLR-A", None, None)
    assert (g.x, g.y) == (36.0, 54.0)  # snapped to the 18 pt grid
    # dropping on the graph overlays
    ed.drop(encode_populations([(E01, ("Cells", "CD3+")), (E03, ("Cells", "CD3+"))]), QtCore.QPointF(g.x + 50, g.y + 50))
    assert [(l.path, l.sample) for l in g.layers] == [(("Cells", "CD3+"), None), (("Cells", "CD3+"), E03)]
    assert len(lay.items) == 1
    gr = ed.graphic(g)
    gr.setPos(100, 101)
    assert (g.x, g.y) == (108.0, 108.0)
    gr.setSelected(True)
    gr.resize_to(250, 200)
    assert (g.w, g.h) == (252.0, 198.0)
    from PySide6 import QtGui
    img = QtGui.QImage(300, 300, QtGui.QImage.Format.Format_ARGB32)
    painter = QtGui.QPainter(img)
    ed.scene.render(painter)  # paints through layout_render
    painter.end()
    assert gr._cache is not None
    # properties panel: single selection gives a form; remove an overlay
    assert ed.props_form.rowCount() > 8
    ed.remove_layer(g, g.layers[1])
    assert len(g.layers) == 1
    # backgating the gated population onto Ungated FSC/SSC
    ed.scene.clearSelection()
    h = ed.add_graph(E01, ("Cells", "CD3+"), "FSC-A", "SSC-A")
    h_g = ed.graphic(h)
    ed.scene.clearSelection()
    h_g.setSelected(True)
    monkeypatch.setattr(ed, "ask_ancestor", lambda it: ())
    b = ed.backgate_selected()
    assert b.path == () and b.layers[0].path == ("Cells", "CD3+") and (b.x_param, b.y_param) == ("FSC-A", "SSC-A")
    # text, shapes, table and grid arrangement
    ed.add_item(LayoutItem(kind="text", text="{Name}", w=200, h=20))
    ed.add_item(LayoutItem(kind="shape", shape="ellipse", w=60, h=40))
    win.open_table_editor(show=False).table.add_population(("Cells", "CD3+"))
    monkeypatch.setattr(ed, "ask_table", lambda: "Table 1")
    t = ed.add_table_dialog()
    assert t.kind == "table" and t.table == "Table 1"
    ed.scene.clearSelection()
    monkeypatch.setattr(ed, "ask_columns", lambda: 2)
    ed.arrange_dialog()
    xs = sorted({round(i.x) for i in lay.items})
    assert len(xs) == 2
    assert all(ed.graphic(i).pos().x() == i.x for i in lay.items)
    n = len(lay.items)
    ed.graphic(lay.items[-1]).setSelected(True)
    ed.restack(front=False)
    assert ed.graphic(lay.items[0]).zValue() == 0 and len(lay.items) == n
    ed.delete_selected()
    assert len(lay.items) == n - 1
    # batch settings and export
    ed.iterate_box.setCurrentIndex(ed.iterate_box.findData("sample"))
    assert lay.iterate == "sample" and "2 copies" in ed.page_info.text()
    out = ed.export(tmp_path / "fig.pdf", dpi=60)
    assert out[0].read_bytes()[:4] == b"%PDF"
    assert len(ed.export(tmp_path / "fig.png", dpi=30)) == 2
    # presets
    monkeypatch.setattr(ed, "ask_text", lambda *a: "Two-panel")
    assert ed.save_preset_dialog().is_file()
    lay2 = ed.from_preset("Two-panel")
    assert lay2.name == "Layout 2" and len(lay2.items) == len(lay.items)


def test_graph_window_and_persistence(win, tmp_path):
    gw = win.open_graph(E01, ("Cells",))
    ed, it = gw.add_to_layout(show=False)
    assert (it.path, it.x_param, it.y_param) == (("Cells",), gw.x, gw.y)
    ed2, it2 = gw.add_to_layout(show=False)
    assert ed2 is ed and len(ed.layout.items) == 2 and it2.x > it.x
    ed.header_edit.setText("{Layout}")
    path = win.save(tmp_path / "ws.wsp")
    close(win)
    w2 = MainWindow()
    w2.open_workspace(path)
    assert len(w2.layouts) == 1 and len(w2.layouts[0].items) == 2 and w2.layouts[0].header == "{Layout}"
    e = w2.open_layout_editor(w2.layouts[0], show=False)
    assert len(e.graphics) == 2 and isinstance(e.graphics[0], ItemGraphic)
    close(w2)
