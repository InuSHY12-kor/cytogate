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

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import ProcessedSample
from ..core.analysis import AnalyzedSample
from ..core.history import History
from ..core.workspace import ALL_SAMPLES, ApplyResult, Workspace
from ..io import FcsReadError, load_fcs
from .graph_window import GraphWindow
from .stat_dialog import StatDialog, format_stat

ROLE = QtCore.Qt.ItemDataRole.UserRole
STAT_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1  # StatSpec on statistic rows
POP_MIME = "application/x-cytogate-populations"


def encode_populations(items) -> QtCore.QMimeData:
    md = QtCore.QMimeData()
    md.setData(POP_MIME, QtCore.QByteArray(json.dumps([[s, list(p)] for s, p in items]).encode()))
    return md


def decode_populations(md: QtCore.QMimeData) -> list[tuple[str, tuple[str, ...]]]:
    if not md.hasFormat(POP_MIME):
        return []
    return [(s, tuple(p)) for s, p in json.loads(bytes(md.data(POP_MIME)).decode())]


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
        return encode_populations(self.main.populations_of(items))

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
                self.main.drop_on_sample(decode_populations(md), item.data(0, ROLE)[0])
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
            self.main.drop_on_group(decode_populations(e.mimeData()), item.data(0, ROLE))
            e.acceptProposedAction()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CytoGate — Workspace")
        self.resize(640, 760)
        self.ws = Workspace()
        self.samples: dict[str, AnalyzedSample] = self.ws.samples  # same dict
        self.graphs: list[GraphWindow] = []
        self.history = History(self.ws)

        bar = self.addToolBar("Workspace")
        bar.setMovable(False)
        self.undo_action = bar.addAction("↶ Undo", self.undo)
        self.undo_action.setShortcut(QtGui.QKeySequence("Ctrl+Z"))
        self.redo_action = bar.addAction("↷ Redo", self.redo)
        self.redo_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"))
        bar.addSeparator()
        bar.addAction("Add Samples…", self.add_samples_dialog)
        bar.addAction("Add Folder…", self.add_folder_dialog)
        bar.addSeparator()
        bar.addAction("New Group…", self.new_group_dialog)
        bar.addAction("Delete Group", self.delete_current_group)
        bar.addSeparator()
        bar.addAction("Add Statistic…", self.add_stat_dialog)
        bar.addAction("Apply to Group", self.apply_to_current_group)
        bar.addAction("Delete", self.delete_selected_gate)

        self.group_list = GroupTable(self)
        self.group_list.currentItemChanged.connect(lambda *_: self._group_changed())
        self.tree = SampleTree(self)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self.open_graph_for_item(item))
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Delete, self.tree, activated=self.delete_selected_gate)
        QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Return), self.tree,
                        activated=lambda: self.open_graph_for_item(self.tree.currentItem()))

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self.group_list)
        layout.addWidget(self.tree, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Add samples, then double-click a sample or gate to open a graph.")
        self.setAcceptDrops(True)
        self.refresh_groups()
        self._sync_undo()

    # --- samples ----------------------------------------------------------
    def add_samples(self, paths) -> list[str]:
        errors = []
        for path in paths:
            path = Path(path)
            files = sorted(p for p in path.iterdir() if p.suffix.lower() == ".fcs") if path.is_dir() else [path]
            for f in files:
                try:
                    a = AnalyzedSample(ProcessedSample(load_fcs(f)))
                except (FcsReadError, KeyError, ValueError) as exc:
                    errors.append(f"{f.name}: {exc}")
                    continue
                if not self.ws.add_sample(a):
                    continue
                item = QtWidgets.QTreeWidgetItem([a.name, "", f"{a.ps.event_count:,}"])
                item.setTextAlignment(2, QtCore.Qt.AlignmentFlag.AlignRight)
                item.setData(0, ROLE, (a.name, ()))
                self.tree.addTopLevelItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        if errors:
            QtWidgets.QMessageBox.warning(self, "Some files could not be read", "\n".join(errors))
        self.refresh_groups()
        self.statusBar().showMessage(f"{len(self.samples)} samples")
        return errors

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

    def _sync_undo(self) -> None:
        self.undo_action.setEnabled(self.history.can_undo)
        self.redo_action.setEnabled(self.history.can_redo)

    def _after_history(self, names) -> list[str]:
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
        members = set(self.ws.group(self.current_group()).samples)
        return [n for n in self.samples if n in members]

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
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(it.data(0, ROLE)[0] not in members)
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
    def ask_stat(self, a: AnalyzedSample):
        """Show the Add Statistic dialog; returns a StatSpec or None (replaced in tests)."""
        dlg = StatDialog([(p.name, p.label) for p in a.ps.parameters], self)
        return dlg.spec() if dlg.exec() else None

    def add_stat_dialog(self):
        a, path = self.selection()
        if a is None:
            return
        spec = self.ask_stat(a)
        if spec is None:
            return
        self.record()
        a.add_stat(path, spec)
        self.data_changed([a.name])

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
        for g in list(self.graphs):
            g.close()
        super().closeEvent(e)
