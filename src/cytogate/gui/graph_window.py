"""Graph window: one population of one sample, like FlowJo's graph windows.

Opened by double-clicking a sample or population in the workspace window.
Several can be open at once.

- ◀ ▶  previous / next sample in the current group, keeping the population,
        axes and plot type.
- ▲ ▼  parent population / first child population.
- The path at the top (sample ▸ Lymphocytes ▸ CD3+) jumps to any ancestor.
- "T" beside each axis opens the axis transform window.
- Gates drawn here become children of the population shown.
"""

from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import EllipseGate, PolygonGate, RectangleGate, quadrant_gates
from ..core.analysis import AnalyzedSample
from ..core.gating import translate_gate
from ..plots import HistogramY, PlotType
from .gate_tools import TOOLS
from .plot_view import PlotView
from .transform_editor import TransformEditor

HISTOGRAM_CHOICE = "Histogram"
DEFAULT_GATE_NAMES = {"Rectangle": "Rectangle", "Polygon": "Polygon", "Ellipse": "Ellipse", "Range": "Range"}


def default_axes(a: AnalyzedSample, path) -> tuple[str, str | None]:
    """Axes FlowJo would show for a population: those of its child gates, else
    those of its own gate, else FSC-A × SSC-A."""
    pop = a.gates.get(path)
    for g in [c.gate for c in pop.children if c.gate is not None and c.gate.dims] + \
             ([pop.gate] if pop.gate is not None and pop.gate.dims else []):
        dims = [a.ps.parameter(d).name for d in g.dims]
        return dims[0], (dims[1] if len(dims) > 1 else None)
    names = [p.name for p in a.ps.parameters]
    x = next((n for n in ("FSC-A", "FSC-H") if n in names), names[0])
    y = next((n for n in ("SSC-A", "SSC-H") if n in names), names[min(1, len(names) - 1)])
    return x, y


class GraphWindow(QtWidgets.QWidget):
    def __init__(self, workspace_window, sample: str, path=(), parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.main = workspace_window
        self.sample_name = sample
        path = tuple(path)
        known = self.analysis.gates.paths()
        while path and path not in known:  # nearest existing population
            path = path[:-1]
        self.path: tuple[str, ...] = path
        self.resize(560, 660)

        # navigation + path
        nav = QtWidgets.QHBoxLayout()
        self.prev_btn = QtWidgets.QToolButton(text="◀", toolTip="Previous sample (Ctrl+Left)")
        self.next_btn = QtWidgets.QToolButton(text="▶", toolTip="Next sample (Ctrl+Right)")
        self.up_btn = QtWidgets.QToolButton(text="▲", toolTip="Parent population (Ctrl+Up)")
        self.down_btn = QtWidgets.QToolButton(text="▼", toolTip="First child population (Ctrl+Down)")
        for b in (self.prev_btn, self.next_btn, self.up_btn, self.down_btn):
            nav.addWidget(b)
        self.prev_btn.clicked.connect(lambda: self.step_sample(-1))
        self.next_btn.clicked.connect(lambda: self.step_sample(+1))
        self.up_btn.clicked.connect(self.go_up)
        self.down_btn.clicked.connect(self.go_down)
        for key, fn in (("Ctrl+Left", lambda: self.step_sample(-1)), ("Ctrl+Right", lambda: self.step_sample(+1)),
                        ("Ctrl+Up", self.go_up), ("Ctrl+Down", self.go_down)):
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=fn)
        self.crumbs = QtWidgets.QHBoxLayout()
        self.crumbs.setSpacing(2)
        nav.addSpacing(8)
        nav.addLayout(self.crumbs)
        nav.addStretch(1)

        # gate tools + plot type
        self.tool_bar = QtWidgets.QToolBar()
        self.tool_group = QtGui.QActionGroup(self)
        self.tool_group.setExclusionPolicy(QtGui.QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.tool_actions: dict[str, QtGui.QAction] = {}
        for tool in TOOLS:
            act = self.tool_bar.addAction(tool)
            act.setCheckable(True)
            self.tool_group.addAction(act)
            self.tool_actions[tool] = act
        self.tool_group.triggered.connect(self._tool_changed)
        self.tool_bar.addSeparator()
        self.type_box = QtWidgets.QComboBox()
        self.type_box.addItems([t.value for t in PlotType if t != PlotType.HISTOGRAM])
        self.type_box.setCurrentText(PlotType.PSEUDOCOLOR.value)
        self.hist_y_box = QtWidgets.QComboBox()
        self.hist_y_box.addItems([h.value for h in HistogramY])
        self.tool_bar.addWidget(self.type_box)
        self.tool_bar.addWidget(self.hist_y_box)
        self.tool_bar.addSeparator()
        self.tool_bar.addAction("Export…", self.export_dialog)

        # axes: Y above-left, X below, each with its T button
        self.x_box, self.y_box = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.t_x = QtWidgets.QToolButton(text="T", toolTip="X axis transform")
        self.t_y = QtWidgets.QToolButton(text="T", toolTip="Y axis transform")
        for t in (self.t_x, self.t_y):
            f = t.font()
            f.setBold(True)
            t.setFont(f)
        self.t_x.clicked.connect(lambda: self.open_transform_editor("x"))
        self.t_y.clicked.connect(lambda: self.open_transform_editor("y"))
        yrow = QtWidgets.QHBoxLayout()
        yrow.addWidget(self.t_y)
        yrow.addWidget(QtWidgets.QLabel("Y"))
        yrow.addWidget(self.y_box, 1)
        xrow = QtWidgets.QHBoxLayout()
        xrow.addWidget(QtWidgets.QLabel("X"))
        xrow.addWidget(self.x_box, 1)
        xrow.addWidget(self.t_x)

        self.view = PlotView()
        self.view.viewbox.shapeDrawn.connect(self.shape_drawn)
        self.footer = QtWidgets.QLabel()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(nav)
        layout.addWidget(self.tool_bar)
        layout.addLayout(yrow)
        layout.addWidget(self.view, 1)
        layout.addLayout(xrow)
        layout.addWidget(self.footer)

        for box in (self.x_box, self.y_box, self.type_box, self.hist_y_box):
            box.currentIndexChanged.connect(self.redraw)
        self.y_box.currentIndexChanged.connect(self._sync_tools)
        self._fill_params(*default_axes(self.analysis, self.path))
        self.refresh()

    # --- state --------------------------------------------------------------------
    @property
    def analysis(self) -> AnalyzedSample:
        return self.main.samples[self.sample_name]

    @property
    def x(self) -> str:
        return self.x_box.currentData()

    @property
    def y(self) -> str | None:
        return self.y_box.currentData()

    def _fill_params(self, x: str, y: str | None) -> None:
        ps = self.analysis.ps
        for box in (self.x_box, self.y_box):
            box.blockSignals(True)
            box.clear()
            for p in ps.parameters:
                box.addItem(p.label, p.name)
        self.y_box.addItem(HISTOGRAM_CHOICE, None)
        self.x_box.setCurrentIndex(max(self.x_box.findData(x), 0))
        self.y_box.setCurrentIndex(self.y_box.count() - 1 if y is None else max(self.y_box.findData(y), 0))
        for box in (self.x_box, self.y_box):
            box.blockSignals(False)
        self._sync_tools()

    def set_axes(self, x: str, y: str | None) -> None:
        self._fill_params(x, y)
        self.redraw()

    def show_population(self, sample: str, path=(), keep_axes: bool = True) -> None:
        """Switch this window to another sample and/or population."""
        x, y = self.x, self.y
        self.sample_name = sample
        a = self.analysis
        path = tuple(path)
        while path and path not in a.gates.paths():  # nearest existing ancestor
            path = path[:-1]
        self.path = path
        names = {p.name for p in a.ps.parameters}
        if keep_axes and x in names and (y is None or y in names):
            self._fill_params(x, y)
        else:
            self._fill_params(*default_axes(a, path))
        self.refresh()

    def refresh(self) -> None:
        """Redraw after a change anywhere (gates, transforms, samples)."""
        a = self.analysis
        while self.path and self.path not in a.gates.paths():
            self.path = self.path[:-1]
        pop_text = "/".join(self.path) if self.path else "Ungated"
        self.setWindowTitle(f"{self.sample_name}: {pop_text}")
        self._build_crumbs()
        self.up_btn.setEnabled(bool(self.path))
        self.down_btn.setEnabled(bool(a.gates.get(self.path).children))
        self.redraw()

    def _build_crumbs(self) -> None:
        while self.crumbs.count():
            w = self.crumbs.takeAt(0).widget()
            if w:
                w.hide()  # deleteLater alone leaves it painted until the event loop runs
                w.setParent(None)
                w.deleteLater()
        parts = [(self.sample_name, ())] + [(name, self.path[: i + 1]) for i, name in enumerate(self.path)]
        for i, (text, p) in enumerate(parts):
            if i:
                self.crumbs.addWidget(QtWidgets.QLabel("›"))
            b = QtWidgets.QToolButton(text=text)
            b.setAutoRaise(True)
            b.setEnabled(p != self.path)
            b.clicked.connect(lambda _=False, p=p: self.show_population(self.sample_name, p))
            self.crumbs.addWidget(b)

    def crumb_texts(self) -> list[str]:
        return [self.crumbs.itemAt(i).widget().text() for i in range(self.crumbs.count())
                if isinstance(self.crumbs.itemAt(i).widget(), QtWidgets.QToolButton)]

    # --- navigation -------------------------------------------------------------------
    def step_sample(self, step: int) -> None:
        order = self.main.visible_samples()
        if self.sample_name not in order or len(order) < 2:
            return
        i = (order.index(self.sample_name) + step) % len(order)
        self.show_population(order[i], self.path)

    def go_up(self) -> None:
        if self.path:
            self.show_population(self.sample_name, self.path[:-1])

    def go_down(self) -> None:
        children = self.analysis.gates.get(self.path).children
        if children:
            self.show_population(self.sample_name, children[0].path)

    # --- drawing -----------------------------------------------------------------------
    def redraw(self, *_) -> None:
        a = self.analysis
        if self.x is None:
            return
        y = self.y
        kind = PlotType.HISTOGRAM if y is None else PlotType(self.type_box.currentText())
        self.type_box.setEnabled(y is not None)
        self.hist_y_box.setEnabled(y is None)
        population = self.path[-1] if self.path else "Ungated"
        self.view.show_sample(a.ps, self.x, y, kind, HistogramY(self.hist_y_box.currentText()),
                              population=population, mask=a.mask(self.path))
        children = a.gates.get(self.path).children
        self.view.show_gates(
            a.ps, self.x, y,
            [(c.name, c.gate, a.freq_of_parent(c.path), c.label_offset, (a.name, c.path))
             for c in children if c.gate.dims],
            on_label_moved=self.label_moved, on_gate_moved=self.gate_moved)
        n = a.count(self.path)
        if self.path:
            parent = a.count(self.path[:-1])
            self.footer.setText(f"Count: {n:,} / {parent:,}    {a.freq_of_parent(self.path):.6g} %")
        else:
            self.footer.setText(f"Count: {n:,}")

    def export_dialog(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Graph", "graph.png", "PNG image (*.png)")
        if path:
            self.view.export_png(path)

    # --- gating -----------------------------------------------------------------------
    def _tool_changed(self, *_) -> None:
        act = self.tool_group.checkedAction()
        self.view.viewbox.set_tool(act.text() if act else None)

    def _sync_tools(self, *_) -> None:
        hist = self.y is None
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
        """Turn a drawn shape (display units) into gate(s) under this population."""
        a, parent, x, y = self.analysis, self.path, self.x, self.y
        ps = a.ps
        to_x = lambda v: float(ps.transforms[x].inverse(np.array([v]))[0])  # noqa: E731
        to_y = (lambda v: float(ps.transforms[y].inverse(np.array([v]))[0])) if y else None  # noqa: E731

        if tool == "Quadrant":
            (qx, qy), = pts
            xs, ys = ps.parameter(x), ps.parameter(y)
            made = [a.add_gate(parent, a.unique_name(parent, qname), gate).path
                    for qname, gate in quadrant_gates(x, y, to_x(qx), to_y(qy), xs.stain or xs.name, ys.stain or ys.name)]
            self.main.data_changed([a.name])
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
            gate = EllipseGate((x, y), ((x0 + x1) / 2, (y0 + y1) / 2), max(w, h), min(w, h), 0.0 if w >= h else np.pi / 2)
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
        self.main.data_changed([a.name])
        return [pop.path]

    def gate_moved(self, key, dx: float, dy: float) -> list[str]:
        """A gate was dragged on the plot (display units)."""
        name, path = key
        a = self.main.samples[name]
        pop = a.gates.get(path)
        deltas = {a.ps.parameter(self.x).name: dx}
        if self.y is not None:
            deltas[a.ps.parameter(self.y).name] = dy
        new_gate = translate_gate(pop.gate, a.ps, deltas)
        linked = self.main.ws.linked_samples(name, path)
        whole = True
        if len(linked) > 1:
            scope = self.main.ask_scope(pop.owning_group, len(linked))
            if scope is None:
                self.redraw()
                return []
            whole = scope == "group"
        changed = self.main.ws.update_gate(name, path, new_gate, whole_group=whole)
        self.main.data_changed(changed)
        return changed

    def label_moved(self, key, offset) -> None:
        name, path = key
        self.main.samples[name].gates.get(path).label_offset = offset

    # --- axis transforms ---------------------------------------------------------------
    def open_transform_editor(self, axis: str, show: bool = True) -> TransformEditor | None:
        pname = self.x if axis == "x" else self.y
        if pname is None:
            return None
        a = self.analysis
        param = a.ps.parameter(pname)
        values = a.ps.values(pname)[a.mask(self.path)]
        pop_text = "/".join(self.path) if self.path else "Ungated"
        group = self.main.current_group()
        ed = TransformEditor(param, a.ps.transforms[pname], values, f"{self.sample_name}: {pop_text}",
                             group=group, parent=self)
        ed.applied.connect(lambda xf, to_group, p=pname: self.apply_transform(p, xf, to_group))
        if show:
            ed.show()
        return ed

    def apply_transform(self, pname: str, xf, to_group: bool) -> list[str]:
        targets = self.main.ws.group(self.main.current_group()).samples if to_group else [self.sample_name]
        changed = []
        for n in targets:
            ps = self.main.samples[n].ps
            if pname in {p.name for p in ps.parameters}:
                ps.set_transform(pname, xf)
                self.main.samples[n].invalidate()  # gates are evaluated in display space
                changed.append(n)
        self.main.data_changed(changed)
        return changed
