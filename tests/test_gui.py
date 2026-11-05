"""Phase 3: the desktop window, run headless (QT_QPA_PLATFORM=offscreen)."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyqtgraph")

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
    rows = [(win.tree.topLevelItem(i).text(0), win.tree.topLevelItem(i).text(1)) for i in range(win.tree.topLevelItemCount())]
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
    other = win.samples["101_DEN084Y5_15_E03_009_clean.fcs"]
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
