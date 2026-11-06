"""E1 in the window: Table Editor drag-in, options, result, export, saved with the workspace."""

import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")

from cytogate.core import RectangleGate  # noqa: E402
from cytogate.core.stats import Stat, StatSpec  # noqa: E402
from cytogate.gui.main_window import MainWindow, encode_populations  # noqa: E402

E01 = "101_DEN084Y5_15_E01_008_clean.fcs"
E03 = "101_DEN084Y5_15_E03_009_clean.fcs"


@pytest.fixture(scope="module")
def app():
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


def test_table_editor(app, data_dir, tmp_path):
    w = MainWindow()
    w.show()
    folder = data_dir / "8_color_data_set/fcs_files"
    w.add_samples([folder / E01, folder / E03])
    for n in (E01, E03):
        w.samples[n].add_gate((), "Big", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    w.data_changed([E01, E03])
    ed = w.open_table_editor()
    assert w.tables[0].name == "Table 1" and w.dirty
    spec = StatSpec(Stat.MEDIAN, "FSC-A")
    added = ed.drop(encode_populations([(E01, ("Big",))], [(E01, ("Big",), spec)]))
    assert [c.spec for c in added] == [StatSpec(Stat.FREQ_PARENT), spec]
    assert ed.drop(encode_populations([(E03, ("Big",))])) == []  # no duplicates
    assert ed.grid.rowCount() == 2
    # edit heading and heat map in the grid
    ed.grid.item(0, 2).setText("Big %")
    ed.grid.item(0, 5).setCheckState(QtCore.Qt.CheckState.Checked)
    assert w.tables[0].columns[0].heading == "Big %" and w.tables[0].columns[0].heatmap
    ed.summary.setChecked(False)
    df = ed.show_result()
    assert list(df.index) == [E01, E03] and ed.result.rowCount() == 2
    assert df.loc[E03, "Big %"] == pytest.approx(w.samples[E03].freq_of_parent(("Big",)))
    out = ed.export(tmp_path / "t.xlsx")
    assert pd.read_excel(out, index_col=0).shape == (2, 2)
    assert "Big %" in ed.copy_to_clipboard()
    ed.grid.selectRow(1)
    ed.move_selected(-1)
    assert w.tables[0].columns[0].spec == spec
    # saved and reopened with the workspace
    path = w.save(tmp_path / "ws.wsp")
    close(w)
    w2 = MainWindow()
    w2.open_workspace(path)
    assert [c.heading for c in w2.tables[0].columns] == ["", "Big %"]
    ed2 = w2.open_table_editor(w2.tables[0], show=False)
    assert ed2.grid.rowCount() == 2 and not ed2.summary.isChecked()
    close(w2)
