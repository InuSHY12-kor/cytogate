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
from ..io import FcsReadError, load_fcs
from ..plots import HistogramY, PlotType
from .gate_tools import TOOLS
from .plot_view import PlotView
from .transform_dialog import KINDS, NAMES, TransformDialog, starter_transform

HISTOGRAM_CHOICE = "Histogram"
ROLE = QtCore.Qt.ItemDataRole.UserRole
DEFAULT_GATE_NAMES = {"Rectangle": "Rectangle", "Polygon": "Polygon", "Ellipse": "Ellipse", "Range": "Range"}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CytoGate")
        self.resize(1200, 760)
        self.samples: dict[str, AnalyzedSample] = {}

        # workspace tree: samples with their populations (FlowJo-style columns)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Statistic", "#Cells"])
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
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
        self.gate_bar.addAction("Delete Gate", self.delete_selected_gate)

        self.view = PlotView()
        self.view.viewbox.shapeDrawn.connect(self.shape_drawn)
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addLayout(controls)
        rl.addWidget(self.gate_bar)
        rl.addWidget(self.view, 1)

        split = QtWidgets.QSplitter()
        split.addWidget(self.tree)
        split.addWidget(right)
        split.setSizes([420, 780])
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
                if a.name in self.samples:
                    continue
                self.samples[a.name] = a
                item = QtWidgets.QTreeWidgetItem([a.name, "", f"{a.ps.event_count:,}"])
                item.setTextAlignment(2, QtCore.Qt.AlignmentFlag.AlignRight)
                item.setData(0, ROLE, (a.name, ()))
                self.tree.addTopLevelItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        if errors:
            QtWidgets.QMessageBox.warning(self, "Some files could not be read", "\n".join(errors))
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
            item = next(item.child(i) for i in range(item.childCount()) if item.child(i).text(0) == part)
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

        def add(parent_item, pop):
            n = a.count(pop.path)
            it = QtWidgets.QTreeWidgetItem([pop.name, f"{a.freq_of_parent(pop.path):.3g}", f"{n:,}"])
            for col in (1, 2):
                it.setTextAlignment(col, QtCore.Qt.AlignmentFlag.AlignRight)
            it.setData(0, ROLE, (name, pop.path))
            parent_item.addChild(it)
            for c in pop.children:
                add(it, c)

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

    def delete_selected_gate(self):
        a, path = self.selection()
        if a is None or not path:
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
        self.view.show_gates(a.ps, x, y, [(c.name, c.gate, a.freq_of_parent(c.path)) for c in children if c.gate.dims])

    def export_graph_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Graph", "graph.png", "PNG image (*.png)")
        if path:
            self.view.export_png(path)
