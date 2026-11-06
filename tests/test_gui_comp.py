"""D1/D2 in the window: matrix editor, controls wizard, remove, copy transforms, presets."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cytogate.core import Linear, Logicle  # noqa: E402
from cytogate.gui.compensation_dialog import ControlsWizard, guess_control  # noqa: E402
from cytogate.gui.main_window import MainWindow  # noqa: E402

E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, data_dir, tmp_path, monkeypatch):
    import gc
    monkeypatch.setenv("CYTOGATE_HOME", str(tmp_path))
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


def test_edit_and_apply_matrix(win):
    win.select(E01)
    dlg = win.compensation_dialog(show=False)
    assert dlg.table.rowCount() == 8 and dlg.name.text() == "Acquisition-defined"
    assert not (dlg.table.item(0, 0).flags() & dlg.table.item(0, 0).flags().ItemIsEditable)  # diagonal fixed
    dlg.table.item(1, 7).setText("0.2")
    dlg.name.setText("Edited")
    dlg.target.setCurrentIndex(1)  # current group = All Samples
    assert dlg.apply() == [E01, E03, E05]
    for n in (E01, E03, E05):
        comp = win.samples[n].ps.compensation
        assert comp.name == "Edited" and comp.spillover.values[1, 7] == pytest.approx(0.2)
    win.undo()
    assert win.samples[E03].ps.compensation.name == "Acquisition-defined"


def test_remove_compensation_updates_graphs(win):
    g = win.open_graph(E01)
    win.tree.clearSelection()
    win.sample_item(E01).setSelected(True)
    assert win.apply_compensation(None, [E01]) == [E01]
    names = [g.x_box.itemData(i) for i in range(g.x_box.count())]
    assert "CD4 PE-Cy7 FLR-A" in names and "Comp-CD4 PE-Cy7 FLR-A" not in names
    assert win.samples[E01].ps.compensation is None


def test_controls_wizard(win):
    dets = list(win.samples[E01].ps.compensation.detectors)
    wiz = ControlsWizard(win, dets)
    for d, box in wiz.boxes.items():
        box.setCurrentText(E01)  # not real single-stain tubes; checks the plumbing only
    wiz.method.setCurrentIndex(0)
    comp = wiz.matrix()
    assert comp.detectors == dets and np.allclose(np.diag(comp.spillover.values), 1)
    for box in wiz.boxes.values():
        box.setCurrentIndex(0)
    with pytest.raises(ValueError):
        wiz.matrix()
    assert guess_control("FITC-A", "CD3", {"Tube_FITC_ctrl.fcs": 1, "x.fcs": 2}) == "Tube_FITC_ctrl.fcs"


def test_copy_transforms_and_presets(win, tmp_path):
    win.samples[E01].ps.set_transform("FSC-A", Linear(0, 150000))
    win.select(E01)
    assert win.copy_transforms_to_group() == [E03, E05]
    assert win.samples[E05].ps.transforms["FSC-A"] == Linear(0, 150000)
    g = win.open_graph(E01)
    g.set_axes("Comp-CD4 PE-Cy7 FLR-A", "SSC-A")
    ed = g.open_transform_editor("x", show=False)
    ed.set_transform(Logicle(262144, 0.9, 4.5, 0))
    ed.save_preset()
    assert (tmp_path / "transform_presets.json").is_file()
    w2 = MainWindow()
    w2.add_samples([win.samples[E03].ps.sample.path])
    assert w2.samples[E03].ps.transforms["Comp-CD4 PE-Cy7 FLR-A"] == Logicle(262144, 0.9, 4.5, 0)
    w2.dirty = False
    w2.close()
