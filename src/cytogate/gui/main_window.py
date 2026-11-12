"""Workspace window, like FlowJo's: groups on top, samples and their
populations below. Double-click a sample or population to open a graph window
for it (several can be open at once).

Drag & drop:
- select gates (Ctrl/Shift for several) and drop them on a group, e.g.
  "All Samples", to copy them to every sample in it as group gates;
- drop them on another sample to copy them to that sample only;
- drop FCS files or folders anywhere to add samples.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import ProcessedSample
from ..core.analysis import AnalyzedSample
from ..core.history import History
from ..core.presets import apply_presets
from ..core.stats import Stat, StatSpec
from ..core.workspace import ALL_SAMPLES, ApplyResult, Criterion, Workspace
from ..io import FcsReadError, load_fcs
from ..io.wsp_load import load_workspace
from ..io.wsp_write import write_wsp
from .graph_window import GraphWindow
from .icons import icon
from .widgets import Ribbon
from .stat_dialog import StatDialog, format_stat

ROLE = QtCore.Qt.ItemDataRole.UserRole
STAT_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1  # StatSpec on statistic rows
POP_MIME = "application/x-cytogate-populations"
AUTOSAVE_MINUTES = 5


def _spec_to_json(spec: StatSpec) -> dict:
    return {"stat": spec.stat.value, "parameter": spec.parameter, "percentile": spec.percentile,
            "ancestor": list(spec.ancestor) if spec.ancestor else None}


def _spec_from_json(d: dict) -> StatSpec:
    return StatSpec(Stat(d["stat"]), d.get("parameter"), d.get("percentile"),
                    tuple(d["ancestor"]) if d.get("ancestor") else None)


def encode_populations(items, stats=()) -> QtCore.QMimeData:
    """Drag data: populations [(sample, path)] and statistic nodes [(sample, path, StatSpec)]."""
    payload = [[s, list(p), None] for s, p in items] + [[s, list(p), _spec_to_json(sp)] for s, p, sp in stats]
    md = QtCore.QMimeData()
    md.setData(POP_MIME, QtCore.QByteArray(json.dumps(payload).encode()))
    return md


def _decode(md: QtCore.QMimeData) -> list:
    if not md.hasFormat(POP_MIME):
        return []
    return json.loads(bytes(md.data(POP_MIME)).decode())


def decode_populations(md: QtCore.QMimeData) -> list[tuple[str, tuple[str, ...]]]:
    return [(e[0], tuple(e[1])) for e in _decode(md) if len(e) < 3 or e[2] is None]


def decode_statistics(md: QtCore.QMimeData) -> list[tuple[str, tuple[str, ...], StatSpec]]:
    return [(e[0], tuple(e[1]), _spec_from_json(e[2])) for e in _decode(md) if len(e) >= 3 and e[2] is not None]


class SampleTree(QtWidgets.QTreeWidget):
    """Samples and populations. Gates can be dragged out; gates or files dropped in."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setHeaderLabels(["Name", "Statistic", "#Cells"])
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragDrop)

    def mimeTypes(self):
        return [POP_MIME]

    def mimeData(self, items):
        return encode_populations(self.main.populations_of(items), self.main.statistics_of(items))

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(POP_MIME) or e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasUrls():
            self.main.add_samples([u.toLocalFile() for u in md.urls()])
        elif md.hasFormat(POP_MIME):
            item = self.itemAt(e.position().toPoint())
            if item is not None:
                name, path = item.data(0, ROLE)
                pops, stats = self.main.own(decode_populations(md)), self.main.own(decode_statistics(md))
                if pops:
                    self.main.drop_on_sample(pops, name)
                if stats:  # FlowJo: a statistic dropped on a population is added there
                    self.main.drop_stats_on_population(stats, name, tuple(path))
        e.acceptProposedAction()


class GroupTable(QtWidgets.QTreeWidget):
    """Groups with their sizes. Dropping gates on a group applies them to it."""

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setHeaderLabels(["Group", "Size"])
        self.setRootIsDecorated(False)
        self.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.setMaximumHeight(140)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(POP_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self.itemAt(e.position().toPoint()) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        item = self.itemAt(e.position().toPoint())
        if item is not None:
            group = item.data(0, ROLE)
            pops = self.main.own(decode_populations(e.mimeData()))
            stats = self.main.own(decode_statistics(e.mimeData()))
            if pops:
                self.main.drop_on_group(pops, group)
            if stats:
                self.main.drop_stats_on_group(stats, group)
            e.acceptProposedAction()


class MainWindow(QtWidgets.QMainWindow):
    windows: list["MainWindow"] = []  # every open workspace window

    def __init__(self):
        super().__init__()
        used = {w.untitled_no for w in MainWindow.open_windows()}
        self.untitled_no = next(n for n in range(1, len(used) + 2) if n not in used)
        MainWindow.windows.append(self)
        self.resize(640, 760)
        self.ws = Workspace()
        self.samples: dict[str, AnalyzedSample] = self.ws.samples  # same dict
        self.graphs: list[GraphWindow] = []
        self.history = History(self.ws)
        self.path: Path | None = None  # where the workspace is saved
        self.dirty = False
        self.extras: dict = {}  # CytoGate-only data saved in the side file (tables, layouts, …)
        self.tables: list = []  # Table Editor definitions (core.table.TableDef)
        self.table_editors: list = []
        self.layouts: list = []  # Layout Editor documents (core.layout.Layout)
        self.layout_editors: list = []
        self._build_menu()

        self.undo_action = QtGui.QAction(icon("undo"), "Undo", self, triggered=self.undo)
        self.undo_action.setShortcut(QtGui.QKeySequence("Ctrl+Z"))
        self.redo_action = QtGui.QAction(icon("redo"), "Redo", self, triggered=self.redo)
        self.redo_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"))
        self.addAction(self.undo_action)
        self.addAction(self.redo_action)
        self.ribbon = self._build_ribbon()

        self.group_list = GroupTable(self)
        self.group_list.currentItemChanged.connect(lambda *_: self._group_changed())
        self.tree = SampleTree(self)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.header().sectionClicked.connect(self.sort_samples)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_F2), self.tree, activated=self.rename_selected)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+D"), self.tree, activated=self.duplicate_selected)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_F2), self.group_list, activated=self.rename_group_dialog)
        self.filter_edit = QtWidgets.QLineEdit(placeholderText="Filter samples (name or keyword value)")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda *_: self._group_changed())
        self.keyword_columns: list[str] = []
        self._filling = False
        self._sort = (None, True)
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Delete, self.tree, activated=self.delete_selected_gate)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Return), self.tree,
                        activated=lambda: self.open_graph_for_item(self.tree.currentItem()))

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.addWidget(self.ribbon)
        layout.addWidget(self.group_list)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.tree, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Add samples, then double-click a sample or gate to open a graph.")
        self.setAcceptDrops(True)
        self.refresh_groups()
        self._sync_undo()
        self._update_title()
        self.autosave_timer = QtCore.QTimer(self, interval=AUTOSAVE_MINUTES * 60 * 1000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()

    def _build_ribbon(self) -> Ribbon:
        """Tabs of related commands (2nd line) over icon+label buttons (3rd line)."""
        r = Ribbon()
        w = r.page("Workspace")
        w.add("New", "new", self.new_workspace_window, "New workspace window (Ctrl+N)")
        w.add("Open", "open", self.open_workspace_dialog, "Open a FlowJo workspace (Ctrl+O)")
        w.add("Save", "save", self.save, "Save (Ctrl+S)")
        w.add("Save As", "save_as", self.save_as_dialog, "Save As…")
        w.separator()
        w.add("Undo", action=self.undo_action).setIcon(icon("undo"))
        w.add("Redo", action=self.redo_action).setIcon(icon("redo"))
        w.separator()
        w.add("Add Samples", "add_samples", self.add_samples_dialog, "Add FCS files…")
        w.add("Add Folder", "add_folder", self.add_folder_dialog, "Add every FCS file in a folder…")
        w.add("Export", "export", self.export_dialog, "Export / Concatenate populations (FCS, CSV)…")
        g = r.page("Groups")
        g.add("New Group", "group_new", self.new_group_dialog, "New group from the selected samples…")
        g.add("By Keyword", "group_keyword", self.new_keyword_group_dialog, "New group of samples with a keyword value…")
        g.add("Rename", "group_rename", self.rename_group_dialog, "Rename the current group…")
        g.add("Delete", "group_delete", self.delete_current_group, "Delete the current group")
        g.separator()
        g.add("Apply Gates", "apply_group", self.apply_to_current_group,
              "Copy the selected gates to every sample of the current group")
        q = r.page("Gates")
        q.add("Add Statistic", "statistic", self.add_stat_dialog, "Add a statistic to the selected population…")
        q.add("Rename", "rename", self.rename_selected, "Rename the selected gate (F2)")
        q.add("Duplicate", "gate_duplicate", self.duplicate_selected, "Duplicate the selected gate (Ctrl+D)")
        q.add("Delete", "delete", self.delete_selected_gate, "Delete the selected gate or statistic (Del)")
        q.separator()
        q.add("AND", "and", lambda: self.make_boolean("and"), "AND gate of the selected gates")
        q.add("OR", "or", lambda: self.make_boolean("or"), "OR gate of the selected gates")
        q.add("NOT", "not", lambda: self.make_boolean("not"), "NOT gate of the selected gate")
        q.add("Combinations", "combos", self.make_combinations, "Every Boolean combination of the selected gates")
        q.add("BifurGate", "bifur", self.bifurgate, "Split the selected histogram population automatically")
        k = r.page("Keywords")
        k.add("Add / Edit", "keyword", self.add_keyword_dialog, "Add or edit a keyword…")
        k.add("Series", "series", self.keyword_series_dialog, "Keyword value series…")
        k.add("Find / Replace", "find", self.find_replace_dialog, "Find and replace keyword values…")
        k.add("Paste", "paste", lambda: self.paste_keywords(QtWidgets.QApplication.clipboard().text()),
              "Paste keyword columns from a spreadsheet")
        k.add("Columns", "columns", self.keyword_columns_dialog, "Show keyword columns…")
        k.add("Properties", "properties", self.sample_properties, "Sample properties…")
        c = r.page("Compensation")
        c.add("Matrix Editor", "comp", self.compensation_dialog, "Compensation matrix editor…")
        c.add("Remove", "comp_remove", lambda: self.apply_compensation(None, self.selected_sample_names()),
              "Remove compensation from the selected samples")
        c.add("Copy Transforms", "transforms", self.copy_transforms_to_group,
              "Copy axis transforms to the current group")
        t = r.page("Reports")
        t.add("New Table", "new_table", lambda: self.open_table_editor(None), "Table Editor: new table")
        t.add("Open Table", "table", lambda: self._popup(self._fill_tables_menu, self.tables_menu, t, "Open Table"),
              "Open a table")
        t.add("New Layout", "new_layout", lambda: self.open_layout_editor(None), "Layout Editor: new layout")
        t.add("Open Layout", "layout", lambda: self._popup(self._fill_layouts_menu, self.layouts_menu, t, "Open Layout"),
              "Open a layout")
        return r

    def _popup(self, fill, menu, row, text) -> None:
        fill()
        b = row.button(text)
        menu.popup(b.mapToGlobal(QtCore.QPoint(0, b.height())))

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("File")
        for text, slot, key in (("New Workspace", self.new_workspace_window, "Ctrl+N"),
                                ("Open Workspace…", self.open_workspace_dialog, "Ctrl+O"),
                                ("Save", self.save, "Ctrl+S"),
                                ("Save As…", self.save_as_dialog, "Ctrl+Shift+S")):
            a = m.addAction(text, slot)
            a.setShortcut(QtGui.QKeySequence(key))
        m.addAction("Close Workspace", self.close)
        m.addSeparator()
        m.addAction("Add Samples…", self.add_samples_dialog)
        m.addAction("Add Folder…", self.add_folder_dialog)
        m.addSeparator()
        m.addAction("Export / Concatenate Populations…", self.export_dialog)
        g = self.menuBar().addMenu("Groups")
        g.addAction("New Group from Selection…", self.new_group_dialog)
        g.addAction("New Group by Keyword…", self.new_keyword_group_dialog)
        g.addAction("Rename Group…", self.rename_group_dialog)
        g.addAction("Delete Group", self.delete_current_group)
        k = self.menuBar().addMenu("Keywords")
        k.addAction("Add / Edit Keyword…", self.add_keyword_dialog)
        k.addAction("Keyword Value Series…", self.keyword_series_dialog)
        k.addAction("Find / Replace…", self.find_replace_dialog)
        k.addAction("Paste Keywords from Spreadsheet", lambda: self.paste_keywords(QtWidgets.QApplication.clipboard().text()))
        k.addSeparator()
        k.addAction("Show Keyword Columns…", self.keyword_columns_dialog)
        b = self.menuBar().addMenu("Gates")
        b.addAction("Make AND Gate", lambda: self.make_boolean("and"))
        b.addAction("Make OR Gate", lambda: self.make_boolean("or"))
        b.addAction("Make NOT Gate", lambda: self.make_boolean("not"))
        b.addAction("Boolean Combinations", self.make_combinations)
        b.addSeparator()
        b.addAction("BifurGate (auto bisector gates)", self.bifurgate)
        c = self.menuBar().addMenu("Compensation")
        c.addAction("Compensation Matrix Editor…", self.compensation_dialog)
        c.addAction("Remove Compensation (selected samples)",
                    lambda: self.apply_compensation(None, self.selected_sample_names()))
        c.addSeparator()
        c.addAction("Copy Transforms to Current Group", self.copy_transforms_to_group)
        self.window_menu = self.menuBar().addMenu("Window")
        self.window_menu.aboutToShow.connect(self._fill_window_menu)
        t = self.menuBar().addMenu("Reports")
        t.addAction("New Table…", lambda: self.open_table_editor(None))
        self.tables_menu = t.addMenu("Open Table")
        self.tables_menu.aboutToShow.connect(self._fill_tables_menu)
        t.addSeparator()
        t.addAction("New Layout…", lambda: self.open_layout_editor(None))
        self.layouts_menu = t.addMenu("Open Layout")
        self.layouts_menu.aboutToShow.connect(self._fill_layouts_menu)
        k.addAction("Sample Properties…", self.sample_properties)

    # --- saving / opening ----------------------------------------------------------------
    def _update_title(self) -> None:
        name = self.path.name if self.path else ("Untitled" if self.untitled_no == 1 else f"Untitled {self.untitled_no}")
        self.setWindowTitle(f"CytoGate — {name}{' *' if self.dirty else ''}")

    def mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def collect_extras(self) -> dict:
        """CytoGate-only data to keep in the side file (filled in by the editors)."""
        out = dict(self.extras)
        out["keyword_columns"] = list(self.keyword_columns)
        from .table_editor import tables_to_extras
        out["tables"] = tables_to_extras(self.tables)
        from ..core.layout import layout_to_json
        out["layouts"] = [layout_to_json(lay) for lay in self.layouts]
        return out

    # --- layouts -----------------------------------------------------------------------
    def store_layouts(self) -> None:
        """Called by the Layout Editor whenever a layout changes."""
        self.mark_dirty()

    def unique_layout_name(self, base: str = "Layout") -> str:
        import re as _re
        names = {lay.name for lay in self.layouts}
        if base not in names:
            return base
        stem = _re.sub(r"\s+\d+$", "", base) or "Layout"
        n = 2
        while f"{stem} {n}" in names:
            n += 1
        return f"{stem} {n}"

    def open_layout_editor(self, layout=None, show: bool = True):
        from ..core.layout import Layout
        from .layout_editor import LayoutEditor
        if layout is None:
            layout = Layout(name=self.unique_layout_name("Layout 1"))
        if all(lay is not layout for lay in self.layouts):
            self.layouts.append(layout)
            self.mark_dirty()
        for ed in self.layout_editors:
            if ed.layout is layout:
                ed.rebuild()
                if show:
                    ed.show()
                    ed.raise_()
                return ed
        ed = LayoutEditor(self, layout)
        self.layout_editors.append(ed)
        if show:
            ed.show()
            ed.fit_page()
        return ed

    def add_graph_to_layout(self, sample, path, x, y, plot_type=None, hist_y=None, show: bool = True):
        """Graph window > File > Add to Layout: into the last layout opened (a new one if none)."""
        ed = next((e for e in reversed(self.layout_editors) if e.isVisible()), None) or \
            (self.layout_editors[-1] if self.layout_editors else self.open_layout_editor(None, show=show))
        it = ed.add_graph(sample, path, x, y, plot_type, hist_y)
        if show:
            ed.show()
            ed.raise_()
        return ed, it

    def _fill_layouts_menu(self) -> None:
        self.layouts_menu.clear()
        for lay in self.layouts:
            self.layouts_menu.addAction(lay.name, lambda lay=lay: self.open_layout_editor(lay))

    def close_layout_editors(self) -> None:
        for ed in self.layout_editors:
            ed.close()
            ed.deleteLater()
        self.layout_editors.clear()

    # --- tables ------------------------------------------------------------------------
    def store_tables(self) -> None:
        """Called by the Table Editor whenever a definition changes."""
        self.mark_dirty()

    def open_table_editor(self, table=None, show: bool = True):
        from ..core.table import TableDef
        from .table_editor import TableEditor
        if table is None:
            names = {t.name for t in self.tables}
            n = 1
            while f"Table {n}" in names:
                n += 1
            table = TableDef(name=f"Table {n}")
            self.tables.append(table)
            self.mark_dirty()
        for ed in self.table_editors:
            if ed.table is table:
                ed.reload_choices()
                if show:
                    ed.show()
                    ed.raise_()
                return ed
        ed = TableEditor(self, table)
        self.table_editors.append(ed)
        if show:
            ed.show()
        return ed

    def _fill_tables_menu(self) -> None:
        self.tables_menu.clear()
        for t in self.tables:
            self.tables_menu.addAction(t.name, lambda t=t: self.open_table_editor(t))

    def close_table_editors(self) -> None:
        for ed in self.table_editors:
            ed.close()
            ed.deleteLater()
        self.table_editors.clear()

    def save(self, path=None) -> Path | None:
        path = Path(path) if path else self.path
        if path is None:
            return self.save_as_dialog()
        if path.suffix.lower() != ".wsp":
            path = path.with_suffix(".wsp")
        write_wsp(path, self.ws, extras=self.collect_extras())
        self.path = path
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}")
        return path

    def save_as_dialog(self) -> Path | None:
        start = str(self.path or "workspace.wsp")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Workspace", start, "FlowJo workspace (*.wsp)")
        return self.save(path) if path else None

    def autosave(self) -> Path | None:
        """Write a backup next to the workspace (or in the home folder if never saved)."""
        if not self.dirty or not self.samples:
            return None
        target = (self.path.with_name(self.path.stem + ".autosave.wsp") if self.path
                  else Path.home() / ("CytoGate-autosave.wsp" if self.untitled_no == 1
                                      else f"CytoGate-autosave-{self.untitled_no}.wsp"))
        try:
            write_wsp(target, self.ws, extras=self.collect_extras())
        except OSError:
            return None
        self.statusBar().showMessage(f"Auto-saved to {target}")
        return target

    def ask_locate(self, file_name: str) -> str | None:
        """Ask where a missing FCS file is now (replaced in tests)."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, f"Locate {file_name}", file_name,
                                                        "FCS files (*.fcs *.FCS);;All files (*)")
        return path or None

    def ask_save_changes(self) -> str:
        """"save" / "discard" / "cancel" before closing or replacing the workspace (replaced in tests)."""
        r = QtWidgets.QMessageBox.question(
            self, "CytoGate", "Save changes to this workspace?",
            QtWidgets.QMessageBox.StandardButton.Save | QtWidgets.QMessageBox.StandardButton.Discard
            | QtWidgets.QMessageBox.StandardButton.Cancel)
        return {QtWidgets.QMessageBox.StandardButton.Save: "save",
                QtWidgets.QMessageBox.StandardButton.Discard: "discard"}.get(r, "cancel")

    def _ok_to_replace(self) -> bool:
        if not self.dirty or not self.samples:
            return True
        choice = self.ask_save_changes()
        if choice == "save":
            return self.save() is not None
        return choice == "discard"

    def _install(self, ws: Workspace, path: Path | None, extras: dict) -> None:
        self.close_all_graphs()
        self.ws = ws
        self.samples = ws.samples
        self.history = History(ws)
        self.path = path
        self.extras = dict(extras)
        self.tree.clear()
        self.keyword_columns = list(self.extras.pop("keyword_columns", []))
        from .table_editor import tables_from_extras
        self.close_table_editors()
        self.tables = tables_from_extras(self.extras.pop("tables", []))
        from ..core.layout import layout_from_json
        self.close_layout_editors()
        self.layouts = []
        for d in self.extras.pop("layouts", []) or []:
            try:
                self.layouts.append(layout_from_json(d))
            except (KeyError, TypeError, ValueError):
                continue
        self.tree.setColumnCount(3 + len(self.keyword_columns))
        self.tree.setHeaderLabels(["Name", "Statistic", "#Cells", *self.keyword_columns])
        for a in ws.samples.values():
            self._add_sample_item(a)
            self.refresh_tree(a.name)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.refresh_groups(select=ALL_SAMPLES)
        self.dirty = False
        self._sync_undo()
        self._update_title()
        self.workspace_replaced()

    def workspace_replaced(self) -> None:
        """Hook for editors that keep state per workspace."""

    def new_workspace(self) -> bool:
        if not self._ok_to_replace():
            return False
        self._install(Workspace(), None, {})
        return True

    def open_workspace(self, path) -> list[str]:
        """Open a .wsp; returns the FCS files that could not be found."""
        if not self._ok_to_replace():
            return []
        opened = load_workspace(path, locate=self.ask_locate)
        self._install(opened.workspace, Path(path), opened.extras)
        if opened.missing:
            QtWidgets.QMessageBox.warning(self, "Missing samples",
                                          "These FCS files were not found:\n" + "\n".join(opened.missing))
        self.statusBar().showMessage(f"Opened {path} ({len(self.samples)} samples)")
        return opened.missing

    def open_workspace_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open Workspace", "", "FlowJo workspace (*.wsp)")
        if path:
            self.open_in_window(path)

    # --- several workspaces at once ------------------------------------------------------
    def own(self, items) -> list:
        """Dragged items that belong to this workspace (gates dragged in from another
        workspace window are ignored)."""
        out = [it for it in items if it[0] in self.samples]
        if len(out) < len(items):
            self.statusBar().showMessage("Gates from another workspace window can't be dropped here")
        return out

    @classmethod
    def open_windows(cls) -> list["MainWindow"]:
        """Workspace windows that are still open (drops closed or deleted ones)."""
        import shiboken6
        cls.windows[:] = [w for w in cls.windows if shiboken6.isValid(w)]
        return [w for w in cls.windows if w.isVisible()]

    def is_blank(self) -> bool:
        """Nothing loaded and nothing to save: a workspace can be opened here."""
        return not self.samples and self.path is None and not self.dirty

    def new_workspace_window(self) -> "MainWindow":
        """File > New Workspace: another workspace window (the current one stays open)."""
        w = MainWindow()
        w.resize(self.size())
        w.move(self.pos() + QtCore.QPoint(30, 30))
        w.show()
        return w

    def open_in_window(self, path) -> "MainWindow":
        """Open a .wsp here if this window is empty, else in a new window; a workspace
        that is already open is just brought forward."""
        path = Path(path)
        for w in MainWindow.open_windows():
            if w.path is not None and w.path.resolve() == path.resolve():
                w.show()
                w.raise_()
                w.activateWindow()
                return w
        target = self if self.is_blank() else self.new_workspace_window()
        target.open_workspace(path)
        return target

    def _fill_window_menu(self) -> None:
        self.window_menu.clear()
        for w in MainWindow.open_windows():
            a = self.window_menu.addAction(w.windowTitle().removeprefix("CytoGate — "),
                                           lambda w=w: (w.show(), w.raise_(), w.activateWindow()))
            a.setCheckable(True)
            a.setChecked(w is self)
        self.window_menu.addSeparator()
        self.window_menu.addAction("New Workspace", self.new_workspace_window)

    # --- samples ----------------------------------------------------------
    def add_samples(self, paths) -> list[str]:
        errors = []
        for path in paths:
            path = Path(path)
            files = sorted(p for p in path.iterdir() if p.suffix.lower() == ".fcs") if path.is_dir() else [path]
            for f in files:
                try:
                    a = AnalyzedSample(ProcessedSample(load_fcs(f)))
                    apply_presets(a.ps)  # saved transform presets
                except (FcsReadError, KeyError, ValueError) as exc:
                    errors.append(f"{f.name}: {exc}")
                    continue
                if not self.ws.add_sample(a):
                    continue
                self._add_sample_item(a)
                self.mark_dirty()
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        if errors:
            QtWidgets.QMessageBox.warning(self, "Some files could not be read", "\n".join(errors))
        self.refresh_groups()
        self.statusBar().showMessage(f"{len(self.samples)} samples")
        return errors

    def _add_sample_item(self, a: AnalyzedSample) -> QtWidgets.QTreeWidgetItem:
        item = QtWidgets.QTreeWidgetItem([a.name, "", f"{a.ps.event_count:,}"])
        item.setTextAlignment(2, QtCore.Qt.AlignmentFlag.AlignRight)
        item.setData(0, ROLE, (a.name, ()))
        self.tree.addTopLevelItem(item)
        self._fill_keyword_cells(item, a)
        return item

    # --- keyword columns -----------------------------------------------------------
    def _fill_keyword_cells(self, item, a) -> None:
        self._filling = True
        for i, k in enumerate(self.keyword_columns):
            item.setText(3 + i, a.keyword(k))
        item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsEditable)
        self._filling = False

    def set_keyword_columns(self, keywords) -> None:
        """Show these keywords as columns of the sample list (FlowJo's keyword columns)."""
        self.keyword_columns = list(keywords)
        self.tree.setColumnCount(3 + len(self.keyword_columns))
        self.tree.setHeaderLabels(["Name", "Statistic", "#Cells", *self.keyword_columns])
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            self._fill_keyword_cells(it, self.samples[it.data(0, ROLE)[0]])

    def refresh_keywords(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            self._fill_keyword_cells(it, self.samples[it.data(0, ROLE)[0]])
        self.refresh_groups()

    def sort_samples(self, column: int) -> None:
        """Header click: order samples by that column (repeat to reverse). The
        order is what ◀ ▶ and batches follow."""
        col, asc = self._sort
        asc = not asc if col == column else True
        self._sort = (column, asc)
        items = [self.tree.takeTopLevelItem(0) for _ in range(self.tree.topLevelItemCount())]

        def key(it):
            name = it.data(0, ROLE)[0]
            if column == 2:
                return (0, self.samples[name].ps.event_count, "")
            text = it.text(column)
            try:
                return (0, float(text), "")
            except ValueError:
                return (1, 0.0, text.lower())
        items.sort(key=key, reverse=not asc)
        for it in items:
            self.tree.addTopLevelItem(it)
            it.setExpanded(True)
        self.tree.expandAll()
        self._group_changed()

    def _double_clicked(self, item, column: int) -> None:
        if item.parent() is None and column >= 3:
            self.tree.editItem(item, column)  # edit a keyword value in place
        else:
            self.open_graph_for_item(item)

    def _item_changed(self, item, column: int) -> None:
        if self._filling or item.parent() is not None or column < 3:
            return
        key = self.keyword_columns[column - 3]
        name = item.data(0, ROLE)[0]
        if self.samples[name].keyword(key) == item.text(column):
            return
        self.set_keyword([name], key, item.text(column))

    # --- keyword editing ---------------------------------------------------------------
    def target_samples(self) -> list[str]:
        """Selected samples, else every visible one."""
        return self.selected_sample_names() or self.visible_samples()

    def set_keyword(self, samples, key: str, value: str) -> None:
        self.record()
        self.ws.set_keyword(samples, key, value)
        self.refresh_keywords()

    def keyword_series(self, samples, key: str, **kw) -> list[str]:
        self.record()
        out = self.ws.keyword_series(samples, key, **kw)
        self.refresh_keywords()
        return out

    def find_replace(self, find: str, replace: str, keyword: str | None = None) -> int:
        self.record()
        n = self.ws.find_replace(find, replace, keyword)
        self.refresh_keywords()
        self.statusBar().showMessage(f"Replaced in {n} keyword value(s)")
        return n

    def paste_keywords(self, text: str) -> list[str]:
        self.record()
        keys = self.ws.paste_keywords(text)
        for k in keys:
            if k not in self.keyword_columns:
                self.keyword_columns.append(k)
        self.set_keyword_columns(self.keyword_columns)
        self.refresh_keywords()
        return keys

    def _form(self, title: str, fields: list[tuple[str, QtWidgets.QWidget]]) -> bool:
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(title)
        form = QtWidgets.QFormLayout(dlg)
        for label, w in fields:
            form.addRow(label, w)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        return bool(dlg.exec())

    def _keyword_combo(self, editable=True) -> QtWidgets.QComboBox:
        c = QtWidgets.QComboBox(editable=editable)
        c.addItems(sorted(self.ws.keyword_names(), key=lambda k: (k.startswith("$"), k.lower())))
        c.setCurrentText("")
        return c

    def add_keyword_dialog(self) -> None:
        key, value = self._keyword_combo(), QtWidgets.QLineEdit()
        if self._form("Add / Edit Keyword", [("Keyword", key), ("Value", value)]) and key.currentText().strip():
            self.set_keyword(self.target_samples(), key.currentText().strip(), value.text())

    def keyword_series_dialog(self) -> None:
        key = self._keyword_combo()
        start = QtWidgets.QDoubleSpinBox(minimum=-1e9, maximum=1e9, value=1, decimals=3)
        step = QtWidgets.QDoubleSpinBox(minimum=-1e9, maximum=1e9, value=1, decimals=3)
        prefix, suffix = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        digits = QtWidgets.QSpinBox(minimum=0, maximum=8)
        if self._form("Keyword Value Series", [("Keyword", key), ("Start", start), ("Step", step),
                                               ("Prefix", prefix), ("Suffix", suffix), ("Digits", digits)]):
            if key.currentText().strip():
                self.keyword_series(self.target_samples(), key.currentText().strip(), start=start.value(),
                                    step=step.value(), prefix=prefix.text(), suffix=suffix.text(), digits=digits.value())

    def find_replace_dialog(self) -> None:
        find, repl = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        key = self._keyword_combo(editable=False)
        key.insertItem(0, "(all keywords)")
        key.setCurrentIndex(0)
        if self._form("Find / Replace", [("Find", find), ("Replace with", repl), ("In", key)]) and find.text():
            self.find_replace(find.text(), repl.text(), None if key.currentIndex() == 0 else key.currentText())

    def keyword_columns_dialog(self) -> None:
        lst = QtWidgets.QListWidget()
        for k in sorted(self.ws.keyword_names(), key=lambda k: (k.startswith("$"), k.lower())):
            it = QtWidgets.QListWidgetItem(k)
            it.setCheckState(QtCore.Qt.CheckState.Checked if k in self.keyword_columns else QtCore.Qt.CheckState.Unchecked)
            lst.addItem(it)
        if self._form("Keyword Columns", [("Show", lst)]):
            self.set_keyword_columns([lst.item(i).text() for i in range(lst.count())
                                      if lst.item(i).checkState() == QtCore.Qt.CheckState.Checked])

    def sample_properties(self, name: str | None = None) -> QtWidgets.QDialog | None:
        """Keywords (editable), parameters, compensation matrix and file details of a sample."""
        name = name or (self.selection()[0].name if self.selection()[0] else None)
        if name is None:
            return None
        from .sample_properties import SamplePropertiesDialog
        dlg = SamplePropertiesDialog(self.samples[name], self)
        dlg.keywords_changed.connect(lambda changes: [self.set_keyword([name], k, v) for k, v in changes.items()])
        dlg.show()
        return dlg

    # --- data export -----------------------------------------------------------------------
    def export_items(self) -> list[tuple[str, tuple[str, ...]]]:
        """Selected populations/samples; with nothing selected, the samples of the current group."""
        items = self.populations_of(self.tree.selectedItems())
        return items or [(n, ()) for n in self.ws.group(self.current_group()).samples]

    def export_dialog(self, show: bool = True):
        from .export_dialog import ExportDialog
        items = self.export_items()
        if not items:
            return None
        dlg = ExportDialog(self, items, self)
        if show:
            dlg.exec()
        return dlg

    # --- Boolean gates and BifurGate -----------------------------------------------------------
    def _selected_paths(self) -> tuple[str, list[tuple[str, ...]]] | None:
        pops = [(n, p) for n, p in self.populations_of(self.tree.selectedItems()) if p]
        if not pops or len({n for n, _ in pops}) != 1:
            self.statusBar().showMessage("Select populations of one sample.")
            return None
        return pops[0][0], [p for _, p in pops]

    @staticmethod
    def _common_parent(paths) -> tuple[str, ...]:
        parents = [p[:-1] for p in paths]
        common = parents[0]
        for p in parents[1:]:
            i = 0
            while i < min(len(common), len(p)) and common[i] == p[i]:
                i += 1
            common = common[:i]
        return common

    def make_boolean(self, op: str, sample: str | None = None, paths=None) -> tuple[str, ...] | None:
        """AND / OR of the selected populations, or NOT of one, placed under their
        common parent (FlowJo names: "A & B", "A or B", "NOT A")."""
        from ..core.gating import BooleanGate
        if paths is None:
            sel = self._selected_paths()
            if sel is None:
                return None
            sample, paths = sel
        paths = [tuple(p) for p in paths]
        if (op == "not") != (len(paths) == 1) or not paths:
            self.statusBar().showMessage("AND/OR need two or more populations; NOT needs one.")
            return None
        a = self.samples[sample]
        names = [p[-1] for p in paths]
        name = {"and": " & ".join(names), "or": " or ".join(names), "not": f"NOT {names[0]}"}[op]
        parent = self._common_parent(paths)
        self.record()
        pop = a.add_gate(parent, a.unique_name(parent, name), BooleanGate(op, tuple(paths)))
        self.data_changed([sample])
        return pop.path

    def make_combinations(self, sample: str | None = None, paths=None) -> list[tuple[str, ...]]:
        """Every +/- combination of the selected populations (FlowJo's Boolean
        combination gates), as AND gates of the populations and NOT gates of them."""
        from ..core.advanced_gates import boolean_combination_names
        from ..core.gating import BooleanGate
        if paths is None:
            sel = self._selected_paths()
            if sel is None:
                return []
            sample, paths = sel
        paths = [tuple(p) for p in paths]
        if len(paths) < 2:
            return []
        a = self.samples[sample]
        parent = self._common_parent(paths)
        self.record()
        negs = []
        for p in paths:
            neg = p[:-1] + (f"NOT {p[-1]}",)
            if neg not in a.gates.paths():
                a.add_gate(p[:-1], neg[-1], BooleanGate("not", (p,)))
            negs.append(neg)
        made = []
        for name, signs in boolean_combination_names([p[-1] for p in paths]):
            refs = tuple(p if s else n for p, n, s in zip(paths, negs, signs))
            made.append(a.add_gate(parent, a.unique_name(parent, name), BooleanGate("and", refs)).path)
        self.data_changed([sample])
        return made

    def bifurgate(self, sample: str | None = None, path=None) -> list[tuple[str, ...]]:
        """Bisector gates on every fluorescence parameter (compensated when
        available) not already gated above this population, split at the dip
        between its two main peaks."""
        from ..core.advanced_gates import bisector_gates, find_split
        if sample is None:
            a, path = self.selection()
            if a is None:
                return []
            sample = a.name
        a = self.samples[sample]
        path = tuple(path or ())
        used = set()
        node = a.gates.get(path)
        while node is not None and node.gate is not None:
            used.update(a.ps.parameter(d).name for d in node.gate.dims)
            node = node.parent
        comp = a.ps.compensation
        params = [p for p in a.ps.parameters if p.kind == "fluorescence"
                  and (p.compensated or comp is None or p.name not in comp.detectors) and p.name not in used]
        mask = a.mask(path)
        plans = []
        for p in params:
            split = find_split(np.clip(a.ps.scaled(p.name)[mask], 0, 1))
            if split is not None:
                value = float(a.ps.transforms[p.name].inverse([split])[0])
                plans.append(bisector_gates(p.name, value, p.stain or p.name))
        if not plans:
            self.statusBar().showMessage("BifurGate: no bimodal parameters found")
            return []
        self.record()
        made = []
        for gates in plans:
            for name, g in gates:
                made.append(a.add_gate(path, a.unique_name(path, name), g).path)
        self.data_changed([sample])
        self.statusBar().showMessage(f"BifurGate: split {len(plans)} parameter(s)")
        return made

    # --- compensation and transforms -----------------------------------------------------
    def compensation_dialog(self, show: bool = True):
        a, _ = self.selection()
        if a is None:
            return None
        from .compensation_dialog import CompensationDialog
        dlg = CompensationDialog(self, a.name, self)
        if show:
            dlg.show()
        return dlg

    def apply_compensation(self, comp, targets) -> list[str]:
        """Apply a matrix (None = remove compensation) to samples."""
        targets = list(targets)
        if not targets:
            return []
        self.record()
        done, skipped = self.ws.apply_compensation(comp, targets)
        if skipped:
            QtWidgets.QMessageBox.warning(self, "Compensation", "Not applied to:\n" +
                                          "\n".join(f"{k}: {v}" for k, v in skipped.items()))
        self.data_changed(done)
        for g in self.graphs:  # parameter lists change with the matrix
            if g.sample_name in done:
                g.show_population(g.sample_name, g.path)
        self.statusBar().showMessage(
            f"{'Removed compensation from' if comp is None else 'Applied ' + comp.name + ' to'} {len(done)} sample(s)")
        return done

    def copy_transforms_to_group(self, source: str | None = None) -> list[str]:
        source = source or (self.selection()[0].name if self.selection()[0] else None)
        if source is None:
            return []
        self.record()
        done = self.ws.copy_transforms(source, self.ws.group(self.current_group()).samples)
        self.data_changed(done)
        self.statusBar().showMessage(f"Copied transforms from {source} to {len(done)} sample(s)")
        return done

    # --- renaming ------------------------------------------------------------------------------
    def ask_text(self, title: str, label: str, default: str = "") -> str | None:
        """Ask for a line of text (replaced in tests)."""
        text, ok = QtWidgets.QInputDialog.getText(self, title, label, text=default)
        return text.strip() if ok and text.strip() else None

    def rename_selected(self) -> list[str]:
        a, path = self.selection()
        if a is None or not path or self.selected_stat() is not None:
            return []
        new = self.ask_text("Rename", "New name:", path[-1])
        if not new or new == path[-1]:
            return []
        pop = a.gates.get(path)
        linked = self.ws.linked_samples(a.name, path)
        whole = True
        if len(linked) > 1:
            scope = self.ask_scope(pop.owning_group, len(linked))
            if scope is None:
                return []
            whole = scope == "group"
        self.record()
        try:
            changed = self.ws.rename_population(a.name, path, new, whole_group=whole)
        except ValueError as exc:
            self.history.undo()
            QtWidgets.QMessageBox.warning(self, "Rename", str(exc))
            return []
        for g in self.graphs:  # windows showing the old path follow the new name
            if g.sample_name in changed and g.path[: len(path)] == path:
                g.path = path[:-1] + (new,) + g.path[len(path):]
        self.data_changed(changed)
        self.select(a.name, path[:-1] + (new,))
        return changed

    def rename_group_dialog(self) -> str | None:
        old = self.current_group()
        if old == ALL_SAMPLES:
            return None
        new = self.ask_text("Rename Group", "New name:", old)
        if not new or new == old:
            return None
        self.record()
        try:
            self.ws.rename_group(old, new)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Rename Group", str(exc))
            return None
        self.refresh_groups(select=new)
        self.data_changed(list(self.samples))
        return new

    def new_keyword_group(self, name: str, criteria) -> str | None:
        self.record()
        try:
            self.ws.create_group(name, criteria=criteria)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Group", str(exc))
            return None
        self.refresh_groups(select=name)
        return name

    def new_keyword_group_dialog(self) -> None:
        name, key = QtWidgets.QLineEdit(), self._keyword_combo()
        op = QtWidgets.QComboBox()
        op.addItems(["equals", "contains", "starts with"])
        value = QtWidgets.QLineEdit()
        if self._form("New Group by Keyword", [("Group name", name), ("Keyword", key), ("", op), ("Value", value)]):
            if name.text().strip() and key.currentText().strip():
                self.new_keyword_group(name.text().strip(),
                                       [Criterion(key.currentText().strip(), value.text(), op.currentText())])

    def _tree_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        m = QtWidgets.QMenu(self)
        m.addAction("Open Graph", lambda: self.open_graph_for_item(item))
        m.addAction("Add Statistic…", self.add_stat_dialog)
        if item.parent() is not None and item.data(0, STAT_ROLE) is None:
            m.addAction("Rename…", self.rename_selected)
            m.addAction(icon("gate_duplicate"), "Duplicate", self.duplicate_selected)
        m.addAction("Sample Properties…", lambda: self.sample_properties(item.data(0, ROLE)[0]))
        m.addSeparator()
        m.addAction("Delete", self.delete_selected_gate)
        m.popup(self.tree.viewport().mapToGlobal(pos))

    def add_samples_dialog(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Add Samples", "", "FCS files (*.fcs *.FCS);;All files (*)")
        if files:
            self.add_samples(files)

    def add_folder_dialog(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Add Folder")
        if folder:
            self.add_samples([folder])

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QtGui.QDropEvent):
        self.add_samples([u.toLocalFile() for u in e.mimeData().urls()])

    # --- graph windows ----------------------------------------------------------
    def open_graph(self, sample: str, path=(), new: bool = False) -> GraphWindow:
        """Open (or bring forward) a graph window for a population.
        new=True always opens another one (Duplicate View)."""
        path = tuple(path)
        known = self.samples[sample].gates.paths()
        while path and path not in known:
            path = path[:-1]
        for g in self.graphs if not new else []:
            if g.sample_name == sample and g.path == path:
                g.show()
                g.raise_()
                g.activateWindow()
                return g
        g = GraphWindow(self, sample, path)
        g.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        g.destroyed.connect(lambda *_, g=g: self.graphs.remove(g) if g in self.graphs else None)
        self.graphs.append(g)
        n = len(self.graphs) - 1  # cascade new windows beside the workspace
        g.move(self.geometry().right() + 10 + 24 * n, self.geometry().top() + 24 * n)
        g.show()
        return g

    def close_all_graphs(self) -> None:
        for g in list(self.graphs):
            g.close()

    # --- undo / redo ----------------------------------------------------------------------
    def record(self) -> None:
        """Remember the state before a change, for Undo."""
        self.history.record()
        self._sync_undo()
        self.mark_dirty()

    def _sync_undo(self) -> None:
        self.undo_action.setEnabled(self.history.can_undo)
        self.redo_action.setEnabled(self.history.can_redo)

    def _after_history(self, names) -> list[str]:
        self.mark_dirty()
        self.refresh_keywords()
        self.refresh_groups()
        self.data_changed(names)
        self._sync_undo()
        return names

    def undo(self) -> list[str]:
        return self._after_history(self.history.undo())

    def redo(self) -> list[str]:
        return self._after_history(self.history.redo())

    def open_graph_for_item(self, item) -> GraphWindow | None:
        if item is None:
            return None
        name, path = item.data(0, ROLE)
        return self.open_graph(name, path)

    def data_changed(self, names) -> None:
        """Gates, statistics or transforms of these samples changed."""
        names = list(dict.fromkeys(names))
        for n in names:
            if n in self.samples:
                self.refresh_tree(n)
        for g in list(self.graphs):
            if g.sample_name in names:
                g.refresh()
        self._sync_undo()

    def visible_samples(self) -> list[str]:
        """Samples of the current group, in workspace order (graph window ◀ ▶)."""
        out = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if not it.isHidden():
                out.append(it.data(0, ROLE)[0])
        return out

    # --- selection ----------------------------------------------------------
    def selection(self) -> tuple[AnalyzedSample, tuple[str, ...]] | tuple[None, None]:
        item = self.tree.currentItem()
        if item is None:
            return None, None
        name, path = item.data(0, ROLE)
        return self.samples[name], tuple(path)

    def selected_stat(self):
        item = self.tree.currentItem()
        return item.data(0, STAT_ROLE) if item is not None else None

    def sample_item(self, name: str) -> QtWidgets.QTreeWidgetItem:
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, ROLE)[0] == name:
                return it
        raise KeyError(name)

    def find_item(self, name: str, path) -> QtWidgets.QTreeWidgetItem:
        item = self.sample_item(name)
        for part in path:
            item = next(item.child(i) for i in range(item.childCount())
                        if item.child(i).text(0) == part and item.child(i).data(0, STAT_ROLE) is None)
        return item

    def select(self, name: str, path=()) -> None:
        self.tree.setCurrentItem(self.find_item(name, tuple(path)))

    def selected_sample_names(self) -> list[str]:
        names = []
        for it in self.tree.selectedItems():
            name = it.data(0, ROLE)[0]
            if name not in names:
                names.append(name)
        return names

    def statistics_of(self, items) -> list[tuple[str, tuple[str, ...], StatSpec]]:
        out = []
        for it in items:
            if it is not None and it.data(0, STAT_ROLE) is not None:
                name, path = it.data(0, ROLE)
                out.append((name, tuple(path), it.data(0, STAT_ROLE)))
        return out

    def drop_stats_on_group(self, stats, group: str) -> list[str]:
        """Statistic nodes dropped on a group: add them to the same population in every sample."""
        self.record()
        done = []
        for _, path, spec in stats:
            done += self.ws.copy_statistic(spec, path, self.ws.group(group).samples)
        self.data_changed(done)
        return done

    def drop_stats_on_population(self, stats, sample: str, path) -> list[str]:
        """Statistic nodes dropped on a population: add them there."""
        self.record()
        done = []
        for _, _, spec in stats:
            done += self.ws.copy_statistic(spec, tuple(path), [sample])
        self.data_changed(done)
        return done

    def populations_of(self, items) -> list[tuple[str, tuple[str, ...]]]:
        """(sample, path) of tree items, skipping statistic rows; a sample row means its whole tree."""
        out = []
        for it in items:
            if it is None or it.data(0, STAT_ROLE) is not None:
                continue
            name, path = it.data(0, ROLE)
            if (name, tuple(path)) not in out:
                out.append((name, tuple(path)))
        return out

    # --- population tree ----------------------------------------------------
    def refresh_tree(self, name: str) -> None:
        """Rebuild the population rows under one sample, keeping the selection."""
        a = self.samples[name]
        cur_item = self.tree.currentItem()
        cur = cur_item.data(0, ROLE) if cur_item is not None else None
        top = self.sample_item(name)
        top.takeChildren()

        def add_stats(parent_item, pop):
            for spec in pop.stats:
                label = a.ps.parameter(spec.parameter).label if spec.parameter else None
                it = QtWidgets.QTreeWidgetItem(["Σ " + spec.label(label), format_stat(spec.stat, a.stat(pop.path, spec)), ""])
                it.setTextAlignment(1, QtCore.Qt.AlignmentFlag.AlignRight)
                it.setData(0, ROLE, (name, pop.path))
                it.setData(0, STAT_ROLE, spec)
                parent_item.addChild(it)

        def add(parent_item, pop):
            it = QtWidgets.QTreeWidgetItem([pop.name, f"{a.freq_of_parent(pop.path):.3g}", f"{a.count(pop.path):,}"])
            for col in (1, 2):
                it.setTextAlignment(col, QtCore.Qt.AlignmentFlag.AlignRight)
            it.setData(0, ROLE, (name, pop.path))
            if pop.owning_group:
                font = it.font(0)
                font.setBold(True)
                it.setFont(0, font)
                it.setToolTip(0, f"Group gate: {pop.owning_group}")
            parent_item.addChild(it)
            add_stats(it, pop)
            for c in pop.children:
                add(it, c)

        add_stats(top, a.gates.root)
        for pop in a.gates.root.children:
            add(top, pop)
        top.setExpanded(True)
        self.tree.expandAll()
        if cur is not None and cur[0] == name:
            path = tuple(cur[1])
            while True:
                try:
                    self.tree.setCurrentItem(self.find_item(name, path))
                    break
                except StopIteration:
                    path = path[:-1]

    # --- groups -----------------------------------------------------------
    def current_group(self) -> str:
        item = self.group_list.currentItem()
        return item.data(0, ROLE) if item else ALL_SAMPLES

    def group_item(self, name: str) -> QtWidgets.QTreeWidgetItem:
        for i in range(self.group_list.topLevelItemCount()):
            if self.group_list.topLevelItem(i).data(0, ROLE) == name:
                return self.group_list.topLevelItem(i)
        raise KeyError(name)

    def refresh_groups(self, select: str | None = None) -> None:
        keep = select or self.current_group()
        self.group_list.blockSignals(True)
        self.group_list.clear()
        for name, g in self.ws.groups.items():
            it = QtWidgets.QTreeWidgetItem([name, str(len(g.samples))])
            it.setData(0, ROLE, name)
            it.setTextAlignment(1, QtCore.Qt.AlignmentFlag.AlignRight)
            self.group_list.addTopLevelItem(it)
            if name == keep:
                self.group_list.setCurrentItem(it)
        if self.group_list.currentItem() is None and self.group_list.topLevelItemCount():
            self.group_list.setCurrentItem(self.group_list.topLevelItem(0))
        self.group_list.blockSignals(False)
        self._group_changed()

    def _group_changed(self) -> None:
        """Show only the samples of the selected group."""
        members = set(self.ws.group(self.current_group()).samples)
        text = self.filter_edit.text().strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            name = it.data(0, ROLE)[0]
            match = not text or text in name.lower() or any(
                text in v.lower() for v in self.samples[name].keywords.values())
            it.setHidden(name not in members or not match)
        cur = self.tree.currentItem()
        if cur is None or cur.isHidden():
            visible = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                       if not self.tree.topLevelItem(i).isHidden()]
            if visible:
                self.tree.setCurrentItem(visible[0])

    def ask_group_name(self) -> str | None:
        """Prompt for a new group name (replaced in tests)."""
        name, ok = QtWidgets.QInputDialog.getText(self, "New Group", "Group name:")
        return name.strip() if ok and name.strip() else None

    def new_group_dialog(self) -> str | None:
        """Make a group from the selected samples (or the current one)."""
        members = self.selected_sample_names() or ([self.selection()[0].name] if self.selection()[0] else [])
        name = self.ask_group_name()
        if not name:
            return None
        self.record()
        try:
            self.ws.create_group(name, members)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Group", str(exc))
            return None
        self.refresh_groups(select=name)
        return name

    def delete_current_group(self) -> None:
        name = self.current_group()
        if name == ALL_SAMPLES:
            return
        self.record()
        self.ws.delete_group(name)
        self.refresh_groups(select=ALL_SAMPLES)
        self.data_changed(list(self.samples))

    # --- copying gates ------------------------------------------------------
    def _report(self, result: ApplyResult, where: str) -> ApplyResult:
        n = len(result.added) + len(result.updated)
        msg = f"Copied to {n} sample(s) in {where}"
        if result.skipped:
            msg += f"; skipped {len(result.skipped)}"
            QtWidgets.QMessageBox.warning(
                self, "Copy gates",
                "Not copied to:\n" + "\n".join(f"{k}: {v}" for k, v in result.skipped.items()))
        self.statusBar().showMessage(msg)
        return result

    def drop_on_group(self, items, group: str) -> ApplyResult | None:
        """Gates dropped on a group: copy them to every sample in it as group gates."""
        items = [(s, p) for s, p in items if p or self.samples[s].gates.root.children]
        if not items:
            return None
        self.record()
        result = self.ws.copy_populations(items, self.ws.group(group).samples, owner=group)
        self.data_changed([s for s, _ in items] + result.added + result.updated)
        return self._report(result, group)

    def drop_on_sample(self, items, sample: str) -> ApplyResult | None:
        """Gates dropped on a sample: copy them to that sample only."""
        items = [(s, p) for s, p in items if s != sample and (p or self.samples[s].gates.root.children)]
        if not items:
            return None
        self.record()
        result = self.ws.copy_populations(items, [sample], owner=None)
        self.data_changed([sample])
        return self._report(result, sample)

    def apply_to_current_group(self) -> ApplyResult | None:
        """Copy the selected gates (or a sample's whole tree) to the current group."""
        items = self.tree.selectedItems() or [self.tree.currentItem()]
        return self.drop_on_group(self.populations_of(items), self.current_group())

    # --- statistics ----------------------------------------------------------
    def ask_stat(self, a: AnalyzedSample, path=()):
        """Show the Add Statistic dialog; returns a StatSpec or None (replaced in tests)."""
        path = tuple(path)
        dlg = StatDialog([(p.name, p.label) for p in a.ps.parameters], self,
                         ancestors=[path[:i] for i in range(len(path) - 1)])
        return dlg.spec() if dlg.exec() else None

    def add_stat_dialog(self):
        a, path = self.selection()
        if a is None:
            return
        spec = self.ask_stat(a, path)
        if spec is None:
            return
        self.record()
        a.add_stat(path, spec)
        self.data_changed([a.name])

    def duplicate_selected(self) -> list[tuple[str, tuple[str, ...]]]:
        """Duplicate the selected gate(s) with everything below them (FlowJo: Duplicate)."""
        items = [(n, p) for n, p in self.populations_of(self.tree.selectedItems() or [self.tree.currentItem()]) if p]
        if not items:
            self.statusBar().showMessage("Select a gate to duplicate")
            return []
        self.record()
        made = [(n, self.samples[n].duplicate_gate(p).path) for n, p in items]
        self.data_changed([n for n, _ in made])
        self.select(*made[-1])
        self.mark_dirty()
        return made

    def delete_selected_gate(self):
        """Delete the selected gate (with its children) or statistic."""
        a, path = self.selection()
        if a is None:
            return
        spec = self.selected_stat()
        if spec is not None:
            self.record()
            a.remove_stat(path, spec)
            self.data_changed([a.name])
            self.select(a.name, path)
            return
        if not path:
            return
        self.record()
        a.remove_gate(path)
        self.data_changed([a.name])
        self.select(a.name, path[:-1])

    def ask_scope(self, group: str, n: int) -> str | None:
        """Ask whether a change to a group gate applies to the whole group.
        Returns "group", "sample" or None (cancel). Replaced in tests."""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Group gate")
        box.setText(f"This gate is shared by {n} samples in group \"{group}\".")
        whole = box.addButton("All samples in group", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        one = box.addButton("This sample only", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.exec()
        return {whole: "group", one: "sample"}.get(box.clickedButton())

    def closeEvent(self, e):
        if not self._ok_to_replace():
            e.ignore()
            return
        for g in list(self.graphs):
            g.close()
        self.close_table_editors()
        self.close_layout_editors()
        if self in MainWindow.windows:
            MainWindow.windows.remove(self)
        super().closeEvent(e)
