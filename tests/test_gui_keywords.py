"""A3/A4 in the window: keyword columns, editing, sorting, filtering, renaming,
keyword groups, statistic drag & drop, sample properties."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtCore, QtGui  # noqa: E402

from cytogate.core import RectangleGate  # noqa: E402
from cytogate.core.stats import Stat, StatSpec  # noqa: E402
from cytogate.core.workspace import Criterion  # noqa: E402
from cytogate.gui.main_window import MainWindow, decode_statistics, encode_populations  # noqa: E402

E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


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


def names(w):
    return [w.tree.topLevelItem(i).text(0) for i in range(w.tree.topLevelItemCount())]


def test_keyword_columns_edit_and_sort(win):
    win.keyword_series([E01, E03, E05], "Dose", start=30, step=-10)
    win.set_keyword_columns(["$TOT", "Dose"])
    assert win.tree.headerItem().text(3) == "$TOT" and win.tree.headerItem().text(4) == "Dose"
    assert win.sample_item(E05).text(4) == "10"
    win.sort_samples(4)
    assert names(win) == [E05, E03, E01]
    assert win.visible_samples() == [E05, E03, E01]  # ◀ ▶ follow the sort order
    win.sort_samples(4)
    assert names(win) == [E01, E03, E05]
    win.sort_samples(2)  # #Cells
    assert names(win) == [E03, E05, E01]
    # edit a keyword cell in place
    win.sample_item(E01).setText(4, "99")
    assert win.samples[E01].keyword("Dose") == "99"
    win.undo()
    assert win.samples[E01].keyword("Dose") == "30" and win.sample_item(E01).text(4) == "30"


def test_filter(win):
    win.set_keyword([E03], "Stim", "PMA")
    win.filter_edit.setText("pma")
    assert win.visible_samples() == [E03]
    win.filter_edit.setText("E05")
    assert win.visible_samples() == [E05]
    win.filter_edit.clear()
    assert len(win.visible_samples()) == 3


def test_keyword_group_and_rename_group(win, monkeypatch):
    win.set_keyword([E01, E05], "Stim", "PMA")
    assert win.new_keyword_group("PMA", [Criterion("Stim", "PMA")]) == "PMA"
    assert win.visible_samples() == [E01, E05]
    win.set_keyword([E03], "Stim", "PMA")  # live group picks it up
    assert win.group_item("PMA").text(1) == "3"
    monkeypatch.setattr(win, "ask_text", lambda *a: "Stimulated")
    assert win.rename_group_dialog() == "Stimulated"
    assert win.current_group() == "Stimulated"


def test_rename_gate(win, monkeypatch):
    win.record()
    win.samples[E01].add_gate((), "Big", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    win.data_changed([E01])
    g = win.open_graph(E01, ("Big",))
    win.select(E01, ("Big",))
    monkeypatch.setattr(win, "ask_text", lambda *a: "Large cells")
    assert win.rename_selected() == [E01]
    assert win.find_item(E01, ("Large cells",)).text(0) == "Large cells"
    assert g.path == ("Large cells",) and g.windowTitle().endswith("Large cells")


def test_paste_keywords(win):
    keys = win.paste_keywords(f"Sample\tDonor\n{E05}\tD9\n")
    assert keys == ["Donor"] and "Donor" in win.keyword_columns
    assert win.sample_item(E05).text(3 + win.keyword_columns.index("Donor")) == "D9"


def test_drag_statistic_to_group_and_population(win):
    for n in (E01, E03, E05):
        win.samples[n].add_gate((), "Big", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    win.samples[E01].add_stat(("Big",), StatSpec(Stat.MEDIAN, "FSC-A"))
    win.data_changed([E01, E03, E05])
    stat_item = win.find_item(E01, ("Big",)).child(0)
    md = win.tree.mimeData([stat_item])
    assert decode_statistics(md) == [(E01, ("Big",), StatSpec(Stat.MEDIAN, "FSC-A"))]
    target = win.group_list.visualItemRect(win.group_item("All Samples")).center()
    win.group_list.dropEvent(QtGui.QDropEvent(QtCore.QPointF(target), QtCore.Qt.DropAction.CopyAction, md,
                                              QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier))
    for n in (E03, E05):
        assert win.samples[n].gates.get(("Big",)).stats == [StatSpec(Stat.MEDIAN, "FSC-A")]
    # dropped on another population of a sample: added there
    md2 = encode_populations([], [(E01, ("Big",), StatSpec(Stat.CV, "SSC-A"))])
    win.drop_stats_on_population(decode_statistics(md2), E05, ())
    assert StatSpec(Stat.CV, "SSC-A") in win.samples[E05].gates.root.stats


def test_sample_properties(win):
    dlg = win.sample_properties(E01)
    assert dlg.tabs.count() == 4
    rows = {dlg.kw_table.item(r, 0).text(): r for r in range(dlg.kw_table.rowCount())}
    dlg.kw_table.item(rows["$CYT"], 1).setText("MyCytometer")
    dlg.accept()
    assert win.samples[E01].keyword("$CYT") == "MyCytometer"
    assert dlg.comp_table.rowCount() == 8


def test_keyword_columns_saved(win, tmp_path):
    win.set_keyword_columns(["$CYT"])
    path = win.save(tmp_path / "k.wsp")
    win.dirty = False
    win.open_workspace(path)
    assert win.keyword_columns == ["$CYT"] and win.tree.headerItem().text(3) == "$CYT"
