"""Main window: workspace tree (samples and populations) on the left, graph on the right.

Select a sample or population to plot its events; gates drawn on the graph
become children of the selected population.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import EllipseGate, PolygonGate, ProcessedSample, RectangleGate, quadrant_gates
from ..core.analysis import AnalyzedSample
from ..core.gating import translate_gate
from ..core.workspace import ALL_SAMPLES, Workspace
from ..io import FcsReadError, load_fcs
from ..plots import HistogramY, PlotType
from .gate_tools import TOOLS
from .plot_view import PlotView
from .stat_dialog import StatDialog, format_stat
from .transform_dialog import KINDS, NAMES, TransformDialog, starter_transform

HISTOGRAM_CHOICE = "Histogram"
ROLE = QtCore.Qt.ItemDataRole.UserRole
STAT_ROLE = QtCore.Qt.ItemDataRole.UserRole + 1  # StatSpec on statistic rows
DEFAULT_GATE_NAMES = {"Rectangle": "Rectangle", "Polygon": "Polygon", "Ellipse": "Ellipse", "Range": "Range"}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CytoGate")
        self.resize(1200, 760)
        self.ws = Workspace()
        self.samples: dict[str, AnalyzedSample] = self.ws.samples  # same dict

        # groups pane (FlowJo's group list above the samples)
        self.group_list = QtWidgets.QListWidget()
        self.group_list.setMaximumHeight(130)
        self.group_list.currentItemChanged.connect(lambda *_: self._group_changed())
        group_bar = QtWidgets.QToolBar("Groups")
        group_bar.addAction("New Group…", self.new_group_dialog)
        group_bar.addAction("Delete Group", self.delete_current_group)

        # workspace tree: samples with their populations (FlowJo-style columns)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Statistic", "#Cells"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.currentItemChanged.connect(lambda *_: self._selection_changed())
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Delete, self.tree, activated=self.delete_selected_gate)

        # graph controls
        self.x_box, self.y_box = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.type_box = QtWidgets.QComboBox()
        self.type_box.addItems([t.value for t in PlotType if t != PlotType.HISTOGRAM])
        self.type_box.setCurrentText(PlotType.PSEUDOCOLOR.value)  # FlowJo's default
        self.hist_y_box = QtWidgets.QComboBox()
        self.hist_y_box.addItems([h.value for h in HistogramY])
        self.x_xf, self.y_xf = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        for box in (self.x_xf, self.y_xf):
            box.addItems(KINDS)
        self.x_custom = QtWidgets.QToolButton(text="…", toolTip="Customize X axis")
        self.y_custom = QtWidgets.QToolButton(text="…", toolTip="Customize Y axis")

        controls = QtWidgets.QGridLayout()
        controls.addWidget(QtWidgets.QLabel("X"), 0, 0)
        controls.addWidget(self.x_box, 0, 1)
        controls.addWidget(self.x_xf, 0, 2)
        controls.addWidget(self.x_custom, 0, 3)
        controls.addWidget(QtWidgets.QLabel("Y"), 1, 0)
        controls.addWidget(self.y_box, 1, 1)
        controls.addWidget(self.y_xf, 1, 2)
        controls.addWidget(self.y_custom, 1, 3)
        controls.addWidget(QtWidgets.QLabel("Plot"), 0, 4)
        controls.addWidget(self.type_box, 0, 5)
        controls.addWidget(QtWidgets.QLabel("Histogram Y"), 1, 4)
        controls.addWidget(self.hist_y_box, 1, 5)
        controls.setColumnStretch(1, 1)

        # gate tools
        self.gate_bar = QtWidgets.QToolBar("Gates")
        self.tool_group = QtGui.QActionGroup(self)
        self.tool_group.setExclusionPolicy(QtGui.QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.tool_actions: dict[str, QtGui.QAction] = {}
        for tool in TOOLS:
            act = self.gate_bar.addAction(tool)
            act.setCheckable(True)
            self.tool_group.addAction(act)
            self.tool_actions[tool] = act
        self.tool_group.triggered.connect(self._tool_changed)
        self.gate_bar.addSeparator()
        self.gate_bar.addAction("Delete", self.delete_selected_gate)
        self.gate_bar.addSeparator()
        self.gate_bar.addAction("Add Statistic…", self.add_stat_dialog)
        self.gate_bar.addSeparator()
        self.gate_bar.addAction("Apply to Group", self.apply_to_current_group)

        self.view = PlotView()
        self.view.viewbox.shapeDrawn.connect(self.shape_drawn)
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addLayout(controls)
        rl.addWidget(self.gate_bar)
        rl.addWidget(self.view, 1)

        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QtWidgets.QLabel("Groups"))
        ll.addWidget(group_bar)
        ll.addWidget(self.group_list)
        ll.addWidget(self.tree, 1)

        split = QtWidgets.QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([480, 720])
        self.setCentralWidget(split)

        bar = self.addToolBar("Main")
        bar.setMovable(False)
        bar.addAction("Add Samples…", self.add_samples_dialog)
        bar.addAction("Add Folder…", self.add_folder_dialog)
        bar.addSeparator()
        bar.addAction("Export Graph…", self.export_graph_dialog)
        self.statusBar()
        self.setAcceptDrops(True)

        for box in (self.x_box, self.y_box, self.type_box, self.hist_y_box):
            box.currentIndexChanged.connect(self.redraw)
        self.x_box.currentIndexChanged.connect(self._sync_xf_boxes)
        self.y_box.currentIndexChanged.connect(self._sync_xf_boxes)
        self.y_box.currentIndexChanged.connect(self._sync_tools)
        self.x_xf.activated.connect(lambda: self._xf_kind_chosen(self.x_box, self.x_xf))
        self.y_xf.activated.connect(lambda: self._xf_kind_chosen(self.y_box, self.y_xf))
        self.x_custom.clicked.connect(lambda: self._customize(self.x_box))
        self.y_custom.clicked.connect(lambda: self._customize(self.y_box))

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

    # --- groups -----------------------------------------------------------
    def current_group(self) -> str:
        item = self.group_list.currentItem()
        return item.data(ROLE) if item else ALL_SAMPLES

    def refresh_groups(self, select: str | None = None) -> None:
        keep = select or self.current_group()
        self.group_list.blockSignals(True)
        self.group_list.clear()
        for name, g in self.ws.groups.items():
            it = QtWidgets.QListWidgetItem(f"{name} ({len(g.samples)})")
            it.setData(ROLE, name)
            self.group_list.addItem(it)
            if name == keep:
                self.group_list.setCurrentItem(it)
        if self.group_list.currentItem() is None and self.group_list.count():
            self.group_list.setCurrentRow(0)
        self.group_list.blockSignals(False)
        self._group_changed()

    def _group_changed(self) -> None:
        """Show only the samples of the selected group."""
        members = set(self.ws.group(self.current_group()).samples) if self.samples else set()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setHidden(it.data(0, ROLE)[0] not in members)
        cur = self.tree.currentItem()
        if cur is not None and cur.isHidden() or cur is None:
            visible = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                       if not self.tree.topLevelItem(i).isHidden()]
            if visible:
                self.tree.setCurrentItem(visible[0])

    def selected_sample_names(self) -> list[str]:
        names = []
        for it in self.tree.selectedItems():
            name = it.data(0, ROLE)[0]
            if name not in names:
                names.append(name)
        return names

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
        self.ws.delete_group(name)
        self.refresh_groups(select=ALL_SAMPLES)
        for n in self.samples:
            self.refresh_tree(n)

    def apply_to_current_group(self):
        """Copy the selected population (or a sample's whole gate tree) to every
        sample in the current group."""
        a, path = self.selection()
        if a is None:
            return None
        group = self.current_group()
        if not path and not a.gates.root.children:
            return None
        result = self.ws.apply_to_group(a.name, path, group)
        for n in self.ws.group(group).samples:
            self.refresh_tree(n)
        self.select(a.name, path)
        msg = f"Applied to {len(result.added) + len(result.updated)} sample(s) in {group}"
        if result.skipped:
            msg += f"; skipped {len(result.skipped)}"
            QtWidgets.QMessageBox.warning(
                self, "Apply to Group",
                "Not applied to:\n" + "\n".join(f"{k}: {v}" for k, v in result.skipped.items()))
        self.statusBar().showMessage(msg)
        return result

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

    # --- selection ----------------------------------------------------------
    def selection(self) -> tuple[AnalyzedSample, tuple[str, ...]] | tuple[None, None]:
        item = self.tree.currentItem()
        if item is None:
            return None, None
        name, path = item.data(0, ROLE)
        return self.samples[name], tuple(path)

    def current(self) -> ProcessedSample | None:
        a, _ = self.selection()
        return a.ps if a else None

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

    def _selection_changed(self):
        a, path = self.selection()
        if a is None:
            return
        ps = a.ps
        old_x, old_y = self.x_box.currentData(), self.y_box.currentData()
        for box in (self.x_box, self.y_box):
            box.blockSignals(True)
            box.clear()
        for p in ps.parameters:
            self.x_box.addItem(p.label, p.name)
            self.y_box.addItem(p.label, p.name)
        self.y_box.addItem(HISTOGRAM_CHOICE, None)
        names = [p.name for p in ps.parameters]
        default_x = next((n for n in ("FSC-A", "FSC-H") if n in names), names[0])
        default_y = next((n for n in ("SSC-A", "SSC-H") if n in names), names[min(1, len(names) - 1)])
        self.x_box.setCurrentIndex(names.index(old_x) if old_x in names else names.index(default_x))
        if old_y is None and old_x is not None:
            self.y_box.setCurrentIndex(self.y_box.count() - 1)
        else:
            self.y_box.setCurrentIndex(names.index(old_y) if old_y in names else names.index(default_y))
        for box in (self.x_box, self.y_box):
            box.blockSignals(False)
        self._sync_xf_boxes()
        self._sync_tools()
        self.redraw()

    # --- population tree ----------------------------------------------------
    def refresh_tree(self, name: str) -> None:
        """Rebuild the population items under one sample, keeping the selection."""
        a = self.samples[name]
        cur = self.selection()
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
            n = a.count(pop.path)
            it = QtWidgets.QTreeWidgetItem([pop.name, f"{a.freq_of_parent(pop.path):.3g}", f"{n:,}"])
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
        if cur[0] is a:
            try:
                self.tree.setCurrentItem(self.find_item(name, cur[1]))
            except StopIteration:
                self.tree.setCurrentItem(top)

    def select(self, name: str, path=()) -> None:
        self.tree.setCurrentItem(self.find_item(name, tuple(path)))

    def selected_stat(self):
        item = self.tree.currentItem()
        return item.data(0, STAT_ROLE) if item is not None else None

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
        a.add_stat(path, spec)
        self.refresh_tree(a.name)

    def delete_selected_gate(self):
        """Delete the selected gate (with its children) or statistic."""
        a, path = self.selection()
        if a is None:
            return
        spec = self.selected_stat()
        if spec is not None:
            a.remove_stat(path, spec)
            self.tree.setCurrentItem(self.sample_item(a.name))
            self.refresh_tree(a.name)
            self.select(a.name, path)
            return
        if not path:
            return
        a.remove_gate(path)
        self.tree.setCurrentItem(self.sample_item(a.name))
        self.refresh_tree(a.name)
        self.select(a.name, path[:-1])
        self.redraw()

    # --- gating ---------------------------------------------------------------
    def _tool_changed(self, *_):
        act = self.tool_group.checkedAction()
        self.view.viewbox.set_tool(act.text() if act else None)

    def _sync_tools(self, *_):
        hist = self.y_box.currentData() is None
        for tool, act in self.tool_actions.items():
            ok = (tool == "Range") == hist
            act.setEnabled(ok)
            if not ok and act.isChecked():
                act.setChecked(False)
                self.view.viewbox.set_tool(None)

    def ask_gate_name(self, default: str) -> str | None:
        """Prompt for a gate name (replaced in tests)."""
        name, ok = QtWidgets.QInputDialog.getText(self, "Gate Name", "Name:", text=default)
        return name.strip() if ok and name.strip() else None

    def shape_drawn(self, tool: str, pts) -> list[tuple[str, ...]]:
        """Turn a drawn shape (display units) into gate(s) under the selected population."""
        a, parent = self.selection()
        if a is None:
            return []
        ps = a.ps
        x = self.x_box.currentData()
        y = self.y_box.currentData()
        xfx = ps.transforms[x]
        to_x = lambda v: float(xfx.inverse(np.array([v]))[0])  # noqa: E731
        if y is not None:
            xfy = ps.transforms[y]
            to_y = lambda v: float(xfy.inverse(np.array([v]))[0])  # noqa: E731

        if tool == "Quadrant":
            (qx, qy), = pts
            xs, ys = ps.parameter(x), ps.parameter(y)
            made = []
            for qname, gate in quadrant_gates(x, y, to_x(qx), to_y(qy), xs.stain or xs.name, ys.stain or ys.name):
                made.append(a.add_gate(parent, a.unique_name(parent, qname), gate).path)
            self._after_gating(a, parent)
            return made

        if tool == "Range":
            (x0, _), (x1, _) = pts
            gate = RectangleGate((x,), (to_x(x0),), (to_x(x1),))
        elif tool == "Rectangle":
            (x0, y0), (x1, y1) = pts
            gate = RectangleGate((x, y), (to_x(min(x0, x1)), to_y(min(y0, y1))), (to_x(max(x0, x1)), to_y(max(y0, y1))))
        elif tool == "Ellipse":
            (x0, y0), (x1, y1) = pts
            w, h = abs(x1 - x0) / 2, abs(y1 - y0) / 2
            center = ((x0 + x1) / 2, (y0 + y1) / 2)
            gate = EllipseGate((x, y), center, max(w, h), min(w, h), 0.0 if w >= h else np.pi / 2)
        elif tool == "Polygon":
            gate = PolygonGate((x, y), tuple((to_x(px), to_y(py)) for px, py in pts))
        else:
            return []
        name = self.ask_gate_name(a.unique_name(parent, DEFAULT_GATE_NAMES[tool]))
        if not name:
            return []
        try:
            pop = a.add_gate(parent, name, gate)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Gate", str(exc))
            return []
        self._after_gating(a, parent)
        return [pop.path]

    def _after_gating(self, a: AnalyzedSample, parent) -> None:
        self.refresh_tree(a.name)
        self.select(a.name, parent)
        self.redraw()

    # --- transforms -------------------------------------------------------
    def _sync_xf_boxes(self):
        ps = self.current()
        if ps is None:
            return
        for pbox, xbox in ((self.x_box, self.x_xf), (self.y_box, self.y_xf)):
            name = pbox.currentData()
            xbox.setEnabled(name is not None)
            if name is not None:
                xbox.setCurrentText(NAMES[type(ps.transforms[name])])

    def _transform_changed(self):
        a, _ = self.selection()
        a.invalidate()  # gates are evaluated in display space
        self.refresh_tree(a.name)
        self.redraw()

    def _xf_kind_chosen(self, pbox, xbox):
        ps, name = self.current(), pbox.currentData()
        if ps is None or name is None:
            return
        ps.set_transform(name, starter_transform(xbox.currentText(), ps.parameter(name), ps.transforms[name]))
        self._transform_changed()

    def _customize(self, pbox):
        ps, name = self.current(), pbox.currentData()
        if ps is None or name is None:
            return
        dlg = TransformDialog(ps.parameter(name), ps.transforms[name], self)
        if dlg.exec():
            ps.set_transform(name, dlg.transform())
            self._sync_xf_boxes()
            self._transform_changed()

    # --- drawing ----------------------------------------------------------
    def redraw(self, *_):
        a, path = self.selection()
        if a is None or self.x_box.currentData() is None:
            self.view.clear()
            return
        x, y = self.x_box.currentData(), self.y_box.currentData()
        kind = PlotType.HISTOGRAM if y is None else PlotType(self.type_box.currentText())
        self.type_box.setEnabled(y is not None)
        self.hist_y_box.setEnabled(y is None)
        population = path[-1] if path else "Ungated"
        self.view.show_sample(a.ps, x, y, kind, HistogramY(self.hist_y_box.currentText()),
                              population=population, mask=a.mask(path))
        children = a.gates.get(path).children
        self.view.show_gates(
            a.ps, x, y,
            [(c.name, c.gate, a.freq_of_parent(c.path), c.label_offset, (a.name, c.path)) for c in children if c.gate.dims],
            on_label_moved=self.label_moved,
            on_gate_moved=self.gate_moved,
        )

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

    def gate_moved(self, key, dx: float, dy: float) -> list[str]:
        """A gate was dragged on the plot: move it (display units)."""
        name, path = key
        a = self.samples[name]
        pop = a.gates.get(path)
        x, y = self.x_box.currentData(), self.y_box.currentData()
        deltas = {a.ps.parameter(x).name: dx}
        if y is not None:
            deltas[a.ps.parameter(y).name] = dy
        new_gate = translate_gate(pop.gate, a.ps, deltas)
        linked = self.ws.linked_samples(name, path)
        whole = True
        if len(linked) > 1:
            scope = self.ask_scope(pop.owning_group, len(linked))
            if scope is None:
                self.redraw()
                return []
            whole = scope == "group"
        changed = self.ws.update_gate(name, path, new_gate, whole_group=whole)
        for n in changed:
            self.refresh_tree(n)
        self.redraw()
        return changed

    def label_moved(self, key, offset) -> None:
        """A gate label was dragged on the plot; remember where."""
        name, path = key
        self.samples[name].gates.get(path).label_offset = offset

    def export_graph_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Graph", "graph.png", "PNG image (*.png)")
        if path:
            self.view.export_png(path)
