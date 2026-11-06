"""A1 in the window: File > Save / Open, unsaved-changes marker, autosave, reconnect."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cytogate.core import RectangleGate  # noqa: E402
from cytogate.gui.main_window import MainWindow  # noqa: E402

E01 = "101_DEN084Y5_15_E01_008_clean.fcs"
E03 = "101_DEN084Y5_15_E03_009_clean.fcs"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make(app):
    w = MainWindow()
    w.show()
    return w


def close(w):
    import gc
    w.close_all_graphs()
    w.dirty = False
    w.close()
    w.ws.samples.clear()
    w.deleteLater()
    QtWidgets.QApplication.processEvents()
    gc.collect()


def test_save_and_open(app, data_dir, tmp_path):
    w = make(app)
    w.add_samples([data_dir / "8_color_data_set/fcs_files"])
    assert w.windowTitle().endswith("Untitled *")
    w.record()
    w.samples[E01].add_gate((), "Big", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    w.ws.create_group("Pair", [E01, E03])
    w.ws.apply_to_group(E01, ("Big",), "Pair")
    w.data_changed([E01, E03])
    path = w.save(tmp_path / "my analysis.wsp")
    assert path.is_file() and w.windowTitle() == "CytoGate — my analysis.wsp" and not w.dirty
    counts = {n: a.gates.counts(a.ps) for n, a in w.samples.items()}
    close(w)

    w2 = make(app)
    assert w2.open_workspace(path) == []
    assert {n: a.gates.counts(a.ps) for n, a in w2.samples.items()} == counts
    assert [w2.group_list.topLevelItem(i).text(0) for i in range(w2.group_list.topLevelItemCount())] == \
        ["All Samples", "Pair"]
    assert w2.find_item(E03, ("Big",)).font(0).bold()
    g = w2.open_graph(E03, ("Big",))
    assert g.view.last_render.sum() == counts[E03][("Big",)]  # a range gate opens as a histogram
    close(w2)


def test_open_flowjo_workspace_and_reconnect(app, data_dir, tmp_path):
    wsp = tmp_path / "fj.wsp"
    shutil.copy(data_dir / "8_color_data_set/8_color_ICS_simple.wsp", wsp)  # its FCS paths are on another PC
    w = make(app)
    asked = []
    w.ask_locate = lambda name: asked.append(name) or str(data_dir / "8_color_data_set/fcs_files" / name)
    assert w.open_workspace(wsp) == []
    assert len(asked) == 1  # the other two are found in the same folder automatically
    assert w.tree.topLevelItemCount() == 3
    assert ("Time", "Singlets", "aAmine-", "CD3+") in w.samples[E01].gates.paths()
    close(w)


def test_unsaved_changes_prompt_and_autosave(app, data_dir, tmp_path, monkeypatch):
    w = make(app)
    w.add_samples([data_dir / "8_color_data_set/fcs_files" / E01])
    w.path = tmp_path / "ws.wsp"
    target = w.autosave()
    assert target == tmp_path / "ws.autosave.wsp" and target.is_file()
    w.ask_save_changes = lambda: "cancel"
    assert not w.new_workspace() and w.samples  # cancelled: still there
    w.ask_save_changes = lambda: "discard"
    assert w.new_workspace() and not w.samples and w.windowTitle() == "CytoGate — Untitled"
    assert w.autosave() is None  # nothing to save
    close(w)
