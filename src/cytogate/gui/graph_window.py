"""Graph window: one population of one sample, laid out like FlowJo's.

Opened by double-clicking a sample or population in the workspace window;
several can be open at once.

Top:     menus (File, Edit, Graph, Display, Go); Undo/Redo; ◀ ▶ previous/next
         sample (Shift: every open graph of this sample); ▲ ▼ parent / first
         child; the breadcrumb (▸ other samples, › sibling populations);
         Duplicate View; gate tools.
Middle:  the plot with the Y parameter selector along the Y axis and the X
         selector under the X axis, each with its "T" (transform) button.
         A warning appears when more than 8% of events sit on the axis edges.
Bottom:  Options (plot type, colours, smoothing, outliers), Active Gate (events
         inside, tint, grow/shrink) and Statistics panels.
"""

from __future__ import annotations

import dataclasses

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import EllipseGate, PolygonGate, RectangleGate, default_transform, quadrant_gates
from ..core.advanced_gates import (QUAD_ORDER, bisector_gates, curly_quadrant_gates, offset_quadrant_gates,
                                   spider_gates)
from ..core.analysis import AnalyzedSample
from ..core.gating import move_vertex, scale_gate, translate_gate
from ..plots import HistogramY, PlotType
from .gate_tools import HISTOGRAM_TOOLS, TOOLS
from .plot_view import IMAGE_FORMATS, PlotView
from .stat_dialog import format_stat
from .transform_dialog import starter_transform
from .transform_editor import TransformEditor

HISTOGRAM_CHOICE = "Histogram"
DEFAULT_SIZE = QtCore.QSize(600, 760)
EDGE_WARNING = 0.08  # FlowJo warns when more than 8% of events pile up on an axis edge
HIST_BINS = 4096  # FlowJo's "Copy Histogram Function" resolution


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


class YAxisButton(QtWidgets.QToolButton):
    """The Y parameter selector, drawn vertically along the Y axis."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolTip("Y axis parameter")

    def sizeHint(self):
        h = super().sizeHint()
        return QtCore.QSize(h.height() + 4, max(h.width(), 120))

    def minimumSizeHint(self):
        return QtCore.QSize(self.sizeHint().width(), 60)

    def paintEvent(self, e):
        p = QtWidgets.QStylePainter(self)
        opt = QtWidgets.QStyleOptionToolButton()
        self.initStyleOption(opt)
        opt.text = ""
        opt.features &= ~QtWidgets.QStyleOptionToolButton.ToolButtonFeature.HasMenu
        p.drawComplexControl(QtWidgets.QStyle.ComplexControl.CC_ToolButton, opt)
        p.rotate(-90)
        rect = QtCore.QRect(-self.height(), 0, self.height(), self.width())
        text = self.fontMetrics().elidedText(self.text() + "  ▾", QtCore.Qt.TextElideMode.ElideMiddle, rect.width() - 8)
        p.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, text)


class ParamMenu(QtWidgets.QMenu):
    """Parameter list with a search box (FlowJo 10.4.1+), used by the Y selector."""

    chosen = QtCore.Signal(object)  # parameter name, or None for Histogram

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        self.search = QtWidgets.QLineEdit(placeholderText="Search")
        self.list = QtWidgets.QListWidget()
        self.list.setMinimumSize(260, 260)
        lay.addWidget(self.search)
        lay.addWidget(self.list)
        act = QtWidgets.QWidgetAction(self)
        act.setDefaultWidget(box)
        self.addAction(act)
        self.search.textChanged.connect(self._filter)
        self.list.itemClicked.connect(self._pick)
        self.list.itemActivated.connect(self._pick)

    def set_items(self, items, current) -> None:
        self.list.clear()
        for label, data in items:
            it = QtWidgets.QListWidgetItem(label)
            it.setData(QtCore.Qt.ItemDataRole.UserRole, data)
            self.list.addItem(it)
            if data == current:
                self.list.setCurrentItem(it)
        self.search.clear()

    def _filter(self, text: str) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(text.lower() not in it.text().lower())

    def _pick(self, item) -> None:
        self.chosen.emit(item.data(QtCore.Qt.ItemDataRole.UserRole))
        self.close()


class Collapsible(QtWidgets.QWidget):
    """A titled panel that expands and collapses (FlowJo's bottom drop-downs)."""

    def __init__(self, title: str, content: QtWidgets.QWidget, summary: QtWidgets.QLabel | None = None,
                 expanded: bool = False):
        super().__init__()
        self.button = QtWidgets.QToolButton(text=title, checkable=True, checked=expanded)
        self.button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setAutoRaise(True)
        self.content = content
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.button)
        if summary is not None:
            head.addWidget(summary, 1)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addWidget(content)
        self.button.toggled.connect(self._toggle)
        self._toggle(expanded)

    def _toggle(self, on: bool) -> None:
        self.button.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)
        self.content.setVisible(on)


class ColorButton(QtWidgets.QToolButton):
    changed = QtCore.Signal(tuple)

    def __init__(self, color, tip: str):
        super().__init__(toolTip=tip)
        self.color = tuple(color)
        self.setFixedSize(26, 20)
        self.clicked.connect(self._pick)
        self._paint()

    def set_color(self, color) -> None:
        self.color = tuple(color)
        self._paint()
        self.changed.emit(self.color)

    def _paint(self) -> None:
        self.setStyleSheet("background-color: rgb(%d,%d,%d); border: 1px solid #666;" % self.color[:3])

    def _pick(self) -> None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(*self.color[:3]), self)
        if c.isValid():
            self.set_color((c.red(), c.green(), c.blue()))


class ManualGateDialog(QtWidgets.QDialog):
    """Graph ▸ Manually Enter Gate: type the gate's coordinates (data values)."""

    def __init__(self, x_label: str, y_label: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual Gate Entry")
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(["Range"] if y_label is None else ["Rectangle", "Polygon"])
        self.fields = {k: QtWidgets.QLineEdit(placeholderText="none") for k in ("xmin", "xmax", "ymin", "ymax")}
        self.vertices = QtWidgets.QPlainTextEdit(placeholderText="one vertex per line:  x, y")
        form = QtWidgets.QFormLayout()
        form.addRow("Gate type", self.kind)
        form.addRow(f"{x_label} min", self.fields["xmin"])
        form.addRow(f"{x_label} max", self.fields["xmax"])
        if y_label is not None:
            form.addRow(f"{y_label} min", self.fields["ymin"])
            form.addRow(f"{y_label} max", self.fields["ymax"])
            form.addRow("Polygon vertices", self.vertices)
        self.name = QtWidgets.QLineEdit()
        form.addRow("Name", self.name)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                             | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _num(self, key):
        text = self.fields[key].text().strip()
        return float(text) if text else None

    def gate(self, x: str, y: str | None):
        kind = self.kind.currentText()
        if kind == "Range":
            return RectangleGate((x,), (self._num("xmin"),), (self._num("xmax"),))
        if kind == "Rectangle":
            return RectangleGate((x, y), (self._num("xmin"), self._num("ymin")), (self._num("xmax"), self._num("ymax")))
        pts = [tuple(float(v) for v in line.replace(";", ",").split(",")[:2])
               for line in self.vertices.toPlainText().splitlines() if line.strip()]
        if len(pts) < 3:
            raise ValueError("a polygon needs at least 3 vertices")
        return PolygonGate((x, y), tuple(pts))


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
        self.active: tuple | None = None  # (sample, path) of the selected (active) gate
        self.show_uncompensated = False
        self.resize(DEFAULT_SIZE)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        self.view = PlotView()
        self.view.viewbox.shapeDrawn.connect(self.shape_drawn)
        self._build_menus()
        self._build_top()
        self._build_axes()
        self._build_panels()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setMenuBar(self.menu_bar)
        layout.addLayout(self.nav)
        layout.addWidget(self.tool_bar)
        layout.addWidget(self.warning)
        layout.addLayout(self.plot_grid, 1)
        layout.addWidget(self.options_panel)
        layout.addWidget(self.gate_panel)
        layout.addWidget(self.stats_panel)

        for box in (self.x_box, self.y_box, self.type_box, self.hist_y_box):
            box.currentIndexChanged.connect(self.redraw)
        self.y_box.currentIndexChanged.connect(self._sync_tools)
        self.y_box.currentIndexChanged.connect(self._sync_y_button)
        self._fill_params(*default_axes(self.analysis, self.path))
        self.refresh()

    # --- construction ------------------------------------------------------------------
    def _act(self, menu, text, slot, shortcut=None, checkable=False, checked=False):
        a = menu.addAction(text)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QtGui.QKeySequence(shortcut))
            a.setShortcutContext(QtCore.Qt.ShortcutContext.WindowShortcut)
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
        return a

    def _build_menus(self) -> None:
        mb = self.menu_bar = QtWidgets.QMenuBar(self)
        self.actions_by_name: dict[str, QtGui.QAction] = {}
        A = self.actions_by_name
        f = mb.addMenu("File")
        A["close"] = self._act(f, "Close", self.close, "Ctrl+W")
        A["close_all"] = self._act(f, "Close All Graph Windows", lambda: self.main.close_all_graphs())
        A["reset_size"] = self._act(f, "Reset This Graph Window's Size", lambda: self.resize(DEFAULT_SIZE))
        A["reset_all"] = self._act(f, "Reset All Graph Windows Size",
                                   lambda: [g.resize(DEFAULT_SIZE) for g in self.main.graphs])
        f.addSeparator()
        A["export"] = self._act(f, "Export Graphics As…", self.export_dialog, "Ctrl+S")
        A["print"] = self._act(f, "Print…", self.print_dialog, "Ctrl+P")
        A["to_layout"] = self._act(f, "Add to Layout", self.add_to_layout, "Ctrl+L")
        e = mb.addMenu("Edit")
        A["undo"] = self._act(e, "Undo", lambda: self.main.undo(), "Ctrl+Z")
        A["redo"] = self._act(e, "Redo", lambda: self.main.redo(), "Ctrl+Shift+Z")
        e.addSeparator()
        A["clear"] = self._act(e, "Clear (delete active gate)", self.clear_active_gate)
        A["copy"] = self._act(e, "Copy Image", lambda: self.copy_image(transparent=True), "Ctrl+C")
        A["copy_white"] = self._act(e, "Copy Image without Transparency", lambda: self.copy_image(transparent=False))
        A["copy_hist"] = self._act(e, "Copy Histogram Function", self.copy_histogram_function, "Ctrl+F")
        e.addSeparator()
        A["add_stat"] = self._act(e, "Add Statistic…", self.add_statistic_dialog)
        g = mb.addMenu("Graph")
        A["duplicate"] = self._act(g, "Duplicate View", self.duplicate_view, "Ctrl+D")
        A["transpose"] = self._act(g, "Transpose Axes", self.transpose_axes)
        A["manual"] = self._act(g, "Manually Enter Gate…", self.manual_gate_dialog)
        d = mb.addMenu("Display")
        o = self.view.options
        A["grid"] = self._act(d, "Show Grid", lambda on: self._set_opt("show_grid", on), checkable=True, checked=o.show_grid)
        A["large_dots"] = self._act(d, "Draw Large Dots", lambda on: self._set_opt("large_dots", on), checkable=True)
        A["freqs"] = self._act(d, "Show Gate Frequencies", lambda on: self._set_opt("show_gate_freqs", on),
                               checkable=True, checked=o.show_gate_freqs)
        A["names"] = self._act(d, "Show Gate Names", lambda on: self._set_opt("show_gate_names", on),
                               checkable=True, checked=o.show_gate_names)
        A["medians"] = self._act(d, "Show Medians", lambda on: self._set_opt("show_medians", on), checkable=True)
        A["uncomp"] = self._act(d, "Show Uncompensated Parameters", self._set_uncompensated, checkable=True)
        go = mb.addMenu("Go")
        A["workspace"] = self._act(go, "Show Workspace", self._show_workspace)
        A["up"] = self._act(go, "Up to Parent", self.go_up, "Ctrl+Up")
        A["down"] = self._act(go, "Open Child Population", self.go_down, "Ctrl+Down")
        A["next"] = self._act(go, "Next Sample", lambda: self.step_sample(+1), "Ctrl+Right")
        A["prev"] = self._act(go, "Previous Sample", lambda: self.step_sample(-1), "Ctrl+Left")
        go.addSeparator()
        A["prev_x"] = self._act(go, "Previous X", lambda: self.step_param("x", -1))
        A["next_x"] = self._act(go, "Next X", lambda: self.step_param("x", +1))
        A["prev_y"] = self._act(go, "Previous Y", lambda: self.step_param("y", -1))
        A["next_y"] = self._act(go, "Next Y", lambda: self.step_param("y", +1))

    def _build_top(self) -> None:
        nav = self.nav = QtWidgets.QHBoxLayout()
        self.undo_btn = QtWidgets.QToolButton(text="↶", toolTip="Undo (Ctrl+Z)")
        self.redo_btn = QtWidgets.QToolButton(text="↷", toolTip="Redo (Ctrl+Shift+Z)")
        self.prev_btn = QtWidgets.QToolButton(text="◀", toolTip="Previous sample (Shift: all graphs of this sample)")
        self.next_btn = QtWidgets.QToolButton(text="▶", toolTip="Next sample (Shift: all graphs of this sample)")
        self.up_btn = QtWidgets.QToolButton(text="▲", toolTip="Up to parent population")
        self.down_btn = QtWidgets.QToolButton(text="▼", toolTip="Open child population")
        self.undo_btn.clicked.connect(lambda: self.main.undo())
        self.redo_btn.clicked.connect(lambda: self.main.redo())
        self.prev_btn.clicked.connect(lambda: self._arrow(-1))
        self.next_btn.clicked.connect(lambda: self._arrow(+1))
        self.up_btn.clicked.connect(self.go_up)
        self.down_btn.clicked.connect(self.go_down)
        for b in (self.undo_btn, self.redo_btn):
            nav.addWidget(b)
        nav.addSpacing(6)
        for b in (self.prev_btn, self.next_btn, self.up_btn, self.down_btn):
            nav.addWidget(b)
        self.crumbs = QtWidgets.QHBoxLayout()
        self.crumbs.setSpacing(0)
        nav.addSpacing(8)
        nav.addLayout(self.crumbs)
        nav.addStretch(1)
        self.dup_btn = QtWidgets.QToolButton(text="⧉", toolTip="Duplicate View")
        self.dup_btn.clicked.connect(self.duplicate_view)
        nav.addWidget(self.dup_btn)

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

        self.warning = QtWidgets.QLabel()
        self.warning.setStyleSheet("color: #c00000; font-weight: bold;")
        self.warning.hide()

    def _t_button(self, axis: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(text="T", toolTip=f"{axis.upper()} axis transform")
        f = b.font()
        f.setBold(True)
        b.setFont(f)
        b.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QtWidgets.QMenu(b)
        m.addAction("Linear Axis", lambda: self.set_axis_kind(axis, "Linear"))
        m.addAction("Log Axis", lambda: self.set_axis_kind(axis, "Log"))
        m.addAction("Biex Axis", lambda: self.set_axis_kind(axis, "Biex"))
        m.addAction("Reset", lambda: self.reset_axis(axis))
        m.addSeparator()
        m.addAction("Customize Axis…", lambda: self.open_transform_editor(axis))
        b.setMenu(m)
        return b

    def _build_axes(self) -> None:
        self.x_box, self.y_box = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.y_box.hide()  # model behind the vertical Y selector
        self.y_button = YAxisButton()
        self.y_menu = ParamMenu(self.y_button)
        self.y_button.setMenu(self.y_menu)
        self.y_menu.aboutToShow.connect(self._load_y_menu)
        self.y_menu.chosen.connect(lambda name: self.y_box.setCurrentIndex(self.y_box.findData(name)))
        self.t_x, self.t_y = self._t_button("x"), self._t_button("y")
        g = self.plot_grid = QtWidgets.QGridLayout()
        g.setSpacing(2)
        g.addWidget(self.t_y, 0, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        g.addWidget(self.y_button, 1, 0)
        g.addWidget(self.view, 0, 1, 2, 1)
        xrow = QtWidgets.QHBoxLayout()
        xrow.addStretch(1)
        xrow.addWidget(self.x_box, 3)
        xrow.addWidget(self.t_x)
        xrow.addStretch(1)
        g.addLayout(xrow, 2, 1)
        g.setRowStretch(1, 1)
        g.setColumnStretch(1, 1)

    def _build_panels(self) -> None:
        # Options
        w = QtWidgets.QWidget()
        f = QtWidgets.QGridLayout(w)
        self.type_box = QtWidgets.QComboBox()
        self.type_box.addItems([t.value for t in PlotType if t != PlotType.HISTOGRAM])
        self.type_box.setCurrentText(PlotType.PSEUDOCOLOR.value)
        self.hist_y_box = QtWidgets.QComboBox()
        self.hist_y_box.addItems([h.value for h in HistogramY])
        o = self.view.options
        self.fg_btn = ColorButton(o.foreground, "Foreground colour")
        self.bg_btn = ColorButton(o.background, "Background colour")
        self.fg_btn.changed.connect(lambda c: self._set_opt("foreground", c))
        self.bg_btn.changed.connect(lambda c: self._set_opt("background", c))
        self.smooth_box = QtWidgets.QCheckBox("Smooth")
        self.outliers_box = QtWidgets.QCheckBox("Show Outliers", checked=o.show_outliers)
        self.smooth_box.toggled.connect(lambda on: self._set_opt("smooth", on))
        self.outliers_box.toggled.connect(lambda on: self._set_opt("show_outliers", on))
        f.addWidget(QtWidgets.QLabel("Type"), 0, 0)
        f.addWidget(self.type_box, 0, 1)
        f.addWidget(QtWidgets.QLabel("Histogram Y"), 0, 2)
        f.addWidget(self.hist_y_box, 0, 3)
        f.addWidget(QtWidgets.QLabel("Color"), 1, 0)
        colors = QtWidgets.QHBoxLayout()
        colors.addWidget(self.fg_btn)
        colors.addWidget(self.bg_btn)
        colors.addStretch(1)
        f.addLayout(colors, 1, 1)
        f.addWidget(self.smooth_box, 1, 2)
        f.addWidget(self.outliers_box, 1, 3)
        self.color_map_box = QtWidgets.QCheckBox("Color Map Axis")
        self.color_param_box = QtWidgets.QComboBox()
        self.color_map_box.toggled.connect(self._color_map_changed)
        self.color_param_box.currentIndexChanged.connect(self._color_map_changed)
        self.level_box = QtWidgets.QComboBox()
        for v in (2, 5, 10, 20):
            self.level_box.addItem(f"{v}%", v / 100)
        self.level_box.setCurrentIndex(1)
        self.level_box.currentIndexChanged.connect(lambda *_: self._set_opt("contour_level", self.level_box.currentData()))
        self.res_box = QtWidgets.QComboBox()
        for v in (128, 256, 512, 1024):
            self.res_box.addItem(str(v), v)
        self.res_box.setCurrentText("512")
        self.res_box.currentIndexChanged.connect(lambda *_: self._set_opt("resolution", self.res_box.currentData()))
        f.addWidget(self.color_map_box, 2, 0)
        f.addWidget(self.color_param_box, 2, 1)
        f.addWidget(QtWidgets.QLabel("Contour level"), 2, 2)
        f.addWidget(self.level_box, 2, 3)
        f.addWidget(QtWidgets.QLabel("Resolution"), 3, 2)
        f.addWidget(self.res_box, 3, 3)
        self.options_panel = Collapsible("Options", w)

        # Active Gate
        w = QtWidgets.QWidget()
        f = QtWidgets.QHBoxLayout(w)
        self.inside_box = QtWidgets.QCheckBox("Events Inside", checked=True)
        self.inside_box.toggled.connect(self.set_events_inside)
        self.tint_box = QtWidgets.QCheckBox("Tint")
        self.tint_color = ColorButton((255, 0, 0), "Tint colour")
        self.tint_box.toggled.connect(lambda on: self.set_tint(on))
        self.tint_color.changed.connect(lambda c: self.set_tint(self.tint_box.isChecked()))
        self.shrink_btn = QtWidgets.QToolButton(text="−", toolTip="Shrink gate")
        self.grow_btn = QtWidgets.QToolButton(text="+", toolTip="Expand gate")
        self.shrink_btn.clicked.connect(lambda: self.scale_active(1 / 1.05))
        self.grow_btn.clicked.connect(lambda: self.scale_active(1.05))
        for wid in (self.inside_box, self.tint_box, self.tint_color):
            f.addWidget(wid)
        f.addStretch(1)
        f.addWidget(QtWidgets.QLabel("Size"))
        f.addWidget(self.shrink_btn)
        f.addWidget(self.grow_btn)
        self.gate_summary = QtWidgets.QLabel()
        self.gate_panel = Collapsible("Active Gate", w, self.gate_summary)

        # Statistics
        w = QtWidgets.QWidget()
        f = QtWidgets.QVBoxLayout(w)
        self.stat_list = QtWidgets.QListWidget()
        self.stat_list.setMaximumHeight(90)
        add = QtWidgets.QPushButton("Add Statistics…")
        add.clicked.connect(self.add_statistic_dialog)
        f.addWidget(self.stat_list)
        f.addWidget(add, 0, QtCore.Qt.AlignmentFlag.AlignLeft)
        self.footer = QtWidgets.QLabel()
        self.stats_panel = Collapsible("Statistics", w, self.footer)

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

    def _visible_params(self, keep=()):
        """Parameters offered on the axes. As in FlowJo, uncompensated copies of
        compensated detectors are hidden unless Display ▸ Show Uncompensated."""
        ps = self.analysis.ps
        hide_raw = ps.compensation is not None and not self.show_uncompensated
        comp = set(ps.compensation.detectors) if ps.compensation is not None else set()
        return [p for p in ps.parameters if not (hide_raw and p.name in comp) or p.name in keep]

    def _fill_params(self, x: str, y: str | None) -> None:
        params = self._visible_params(keep=(x, y))
        for box in (self.x_box, self.y_box):
            box.blockSignals(True)
            box.clear()
            for p in params:
                box.addItem(p.label, p.name)
        self.y_box.addItem(HISTOGRAM_CHOICE, None)
        keep_color = self.color_param_box.currentData()
        self.color_param_box.blockSignals(True)
        self.color_param_box.clear()
        for p in params:
            self.color_param_box.addItem(p.label, p.name)
        if keep_color is not None and self.color_param_box.findData(keep_color) >= 0:
            self.color_param_box.setCurrentIndex(self.color_param_box.findData(keep_color))
        self.color_param_box.blockSignals(False)
        self.x_box.setCurrentIndex(max(self.x_box.findData(x), 0))
        self.y_box.setCurrentIndex(self.y_box.count() - 1 if y is None else max(self.y_box.findData(y), 0))
        for box in (self.x_box, self.y_box):
            box.blockSignals(False)
        self._sync_tools()
        self._sync_y_button()

    def _sync_y_button(self, *_) -> None:
        self.y_button.setText(self.y_box.currentText())

    def _load_y_menu(self) -> None:
        self.y_menu.set_items([(self.y_box.itemText(i), self.y_box.itemData(i)) for i in range(self.y_box.count())], self.y)

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
        self.active = None
        names = {p.name for p in a.ps.parameters}
        if keep_axes and x in names and (y is None or y in names):
            self._fill_params(x, y)
        else:
            self._fill_params(*default_axes(a, path))
        self.refresh()

    def refresh(self) -> None:
        """Redraw after a change anywhere (gates, transforms, samples)."""
        a = self.analysis
        known = a.gates.paths()
        while self.path and self.path not in known:
            self.path = self.path[:-1]
        if self.active is not None and (self.active[0] != self.sample_name or self.active[1] not in known):
            self.active = None
        pop_text = "/".join(self.path) if self.path else "Ungated"
        self.setWindowTitle(f"{self.sample_name}: {pop_text}")
        self._build_crumbs()
        self.up_btn.setEnabled(bool(self.path))
        self.down_btn.setEnabled(bool(a.gates.get(self.path).children))
        self.undo_btn.setEnabled(self.main.history.can_undo)
        self.redo_btn.setEnabled(self.main.history.can_redo)
        self.redraw()

    def _build_crumbs(self) -> None:
        """▸ (other samples) sample › (siblings) gate › (siblings) gate …"""
        while self.crumbs.count():
            w = self.crumbs.takeAt(0).widget()
            if w:
                w.hide()  # deleteLater alone leaves it painted until the event loop runs
                w.setParent(None)
                w.deleteLater()
        a = self.analysis

        def menu_button(text, entries):
            b = QtWidgets.QToolButton(text=text)
            b.setAutoRaise(True)
            b.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
            m = QtWidgets.QMenu(b)
            for label, fn in entries:
                m.addAction(label, fn)
            b.setMenu(m)
            b.setObjectName("crumb-menu")
            self.crumbs.addWidget(b)

        def crumb(text, path):
            b = QtWidgets.QToolButton(text=text)
            b.setAutoRaise(True)
            b.setObjectName("crumb")
            b.setEnabled(path != self.path)
            b.clicked.connect(lambda _=False, p=path: self.show_population(self.sample_name, p))
            self.crumbs.addWidget(b)

        menu_button("▸", [(n, lambda n=n: self.show_population(n, ())) for n in self.main.visible_samples()])
        crumb(self.sample_name, ())
        for i, name in enumerate(self.path):
            parent = a.gates.get(self.path[:i])
            menu_button("›", [(c.name, lambda p=c.path: self.show_population(self.sample_name, p)) for c in parent.children])
            crumb(name, self.path[: i + 1])

    def crumb_texts(self) -> list[str]:
        return [self.crumbs.itemAt(i).widget().text() for i in range(self.crumbs.count())
                if self.crumbs.itemAt(i).widget().objectName() == "crumb"]

    def crumb_menus(self) -> list[list[str]]:
        out = []
        for i in range(self.crumbs.count()):
            w = self.crumbs.itemAt(i).widget()
            if w.objectName() == "crumb-menu":
                out.append([a.text() for a in w.menu().actions()])
        return out

    # --- navigation -------------------------------------------------------------------
    def _arrow(self, step: int) -> None:
        if QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            self.step_all_graphs(step)
        else:
            self.step_sample(step)

    def step_sample(self, step: int) -> None:
        order = self.main.visible_samples()
        if self.sample_name not in order or len(order) < 2:
            return
        i = (order.index(self.sample_name) + step) % len(order)
        self.show_population(order[i], self.path)

    def step_all_graphs(self, step: int) -> None:
        """Shift-click: every open graph of this sample moves to the next sample."""
        current = self.sample_name
        for g in list(self.main.graphs):
            if g.sample_name == current:
                g.step_sample(step)

    def go_up(self) -> None:
        if self.path:
            self.show_population(self.sample_name, self.path[:-1])

    def go_down(self) -> None:
        children = self.analysis.gates.get(self.path).children
        if children:
            self.show_population(self.sample_name, children[0].path)

    def step_param(self, axis: str, step: int) -> None:
        box = self.x_box if axis == "x" else self.y_box
        n = box.count() - (1 if axis == "y" else 0)  # skip "Histogram" when stepping Y
        if n <= 0:
            return
        if axis == "y" and box.currentData() is None:
            box.setCurrentIndex(0)
        else:
            box.setCurrentIndex((box.currentIndex() + step) % n)

    def transpose_axes(self) -> None:
        if self.y is not None:
            self.set_axes(self.y, self.x)

    def duplicate_view(self) -> "GraphWindow":
        g = self.main.open_graph(self.sample_name, self.path, new=True)
        g.view.options = dataclasses.replace(self.view.options)
        g.type_box.setCurrentText(self.type_box.currentText())
        g.hist_y_box.setCurrentText(self.hist_y_box.currentText())
        g.set_axes(self.x, self.y)
        return g

    def _show_workspace(self) -> None:
        self.main.show()
        self.main.raise_()
        self.main.activateWindow()

    # --- drawing -----------------------------------------------------------------------
    def _set_opt(self, name: str, value) -> None:
        setattr(self.view.options, name, value)
        self.redraw()

    def _color_map_changed(self, *_) -> None:
        on = self.color_map_box.isChecked()
        self.view.options.color_param = self.color_param_box.currentData() if on else None
        self.redraw()

    def set_color_map(self, param: str | None) -> None:
        """Colour dots by a third parameter (None turns it off)."""
        if param is not None:
            self.color_param_box.setCurrentIndex(self.color_param_box.findData(param))
        self.color_map_box.setChecked(param is not None)
        self._color_map_changed()

    def _set_uncompensated(self, on: bool) -> None:
        self.show_uncompensated = on
        self._fill_params(self.x, self.y)
        self.redraw()

    def add_to_layout(self, show: bool = True):
        """This graph (sample, population, axes, plot type) into the Layout Editor."""
        kind = "Histogram" if self.y is None else self.type_box.currentText()
        return self.main.add_graph_to_layout(self.sample_name, self.path, self.x, self.y, kind,
                                             self.hist_y_box.currentText(), show=show)

    def redraw(self, *_) -> None:
        a = self.analysis
        if self.x is None:
            return
        y = self.y
        kind = PlotType.HISTOGRAM if y is None else PlotType(self.type_box.currentText())
        self.type_box.setEnabled(y is not None)
        self.hist_y_box.setEnabled(y is None)
        population = self.path[-1] if self.path else "Ungated"
        mask = a.mask(self.path)
        self.view.show_sample(a.ps, self.x, y, kind, HistogramY(self.hist_y_box.currentText()),
                              population=population, mask=mask)
        children = a.gates.get(self.path).children
        self.view.show_gates(
            a.ps, self.x, y,
            [(c.name, c.gate, a.freq_of_parent(c.path), c.label_offset, (a.name, c.path), c.tint)
             for c in children if c.gate.dims],
            on_label_moved=self.label_moved, on_gate_moved=self.gate_moved,
            active=self.active, on_select=self.select_gate, on_vertex_moved=self.vertex_moved)
        self._quad_handles()
        self._edge_warning(mask)
        n = a.count(self.path)
        if self.path:
            parent = a.count(self.path[:-1])
            self.footer.setText(f"Count: {n:,} / {parent:,}    {a.freq_of_parent(self.path):.6g} %")
        else:
            self.footer.setText(f"Count: {n:,}")
        self._sync_gate_panel()
        self._sync_stats_panel()

    def edge_fractions(self, mask=None) -> tuple[float, float | None]:
        """Fraction of the population's events on the edges of the X and Y axes."""
        a = self.analysis
        mask = a.mask(self.path) if mask is None else mask
        xs = a.ps.scaled(self.x)[mask]
        fx = float(np.mean((xs <= 0) | (xs >= 1))) if xs.size else 0.0
        if self.y is None:
            return fx, None
        ys = a.ps.scaled(self.y)[mask]
        return fx, (float(np.mean((ys <= 0) | (ys >= 1))) if ys.size else 0.0)

    def _edge_warning(self, mask) -> None:
        fx, fy = self.edge_fractions(mask)
        a = self.analysis
        xs = a.ps.scaled(self.x)[mask]
        on_edge = (xs <= 0) | (xs >= 1)
        if self.y is not None:
            ys = a.ps.scaled(self.y)[mask]
            on_edge |= (ys <= 0) | (ys >= 1)
        total = float(np.mean(on_edge)) if on_edge.size else 0.0
        warn = "QToolButton { border: 2px solid #ff8c00; border-radius: 3px; }"
        self.t_x.setStyleSheet(warn if fx > EDGE_WARNING else "")
        self.t_y.setStyleSheet(warn if fy is not None and fy > EDGE_WARNING else "")
        if total > EDGE_WARNING:
            self.warning.setText(f"Warning: {total * 100:.0f}% of events are on the chart edges!")
            self.warning.show()
        else:
            self.warning.hide()

    def _sync_stats_panel(self) -> None:
        a = self.analysis
        pop = a.gates.get(self.path)
        self.stat_list.clear()
        if self.path:
            self.stat_list.addItem(f"Freq. of Total: {a.count(self.path) / a.ps.event_count * 100:.4g} %")
        for spec in pop.stats:
            label = a.ps.parameter(spec.parameter).label if spec.parameter else None
            self.stat_list.addItem(f"{spec.label(label)}: {format_stat(spec.stat, a.stat(self.path, spec))}")

    def _sync_gate_panel(self) -> None:
        pop = self.active_population()
        for w in (self.inside_box, self.tint_box, self.tint_color, self.shrink_btn, self.grow_btn):
            w.setEnabled(pop is not None)
        if pop is None:
            self.gate_summary.setText("(click a gate to select it)")
            return
        a = self.analysis
        self.gate_summary.setText(f"{pop.name}: {a.count(pop.path):,} events, {a.freq_of_parent(pop.path):.4g} %")
        for w, v in ((self.inside_box, pop.gate.inside), (self.tint_box, pop.tint is not None)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        resizable = isinstance(pop.gate, (PolygonGate, EllipseGate))
        self.shrink_btn.setEnabled(resizable)
        self.grow_btn.setEnabled(resizable)

    # --- output -------------------------------------------------------------------------
    def export_dialog(self) -> None:
        filters = ";;".join(IMAGE_FORMATS.values())
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Graphics As", f"{self.sample_name}.png", filters)
        if path:
            if "." not in path.replace("\\", "/").rsplit("/", 1)[-1]:
                path += ".png"
            self.view.export_image(path)

    def print_to(self, printer) -> None:
        painter = QtGui.QPainter(printer)
        page = printer.pageLayout().paintRectPixels(printer.resolution())
        img = self.view.to_image(scale=2.0)
        target = QtCore.QRectF(page)
        ratio = min(target.width() / img.width(), target.height() / img.height())
        painter.drawImage(QtCore.QRectF(target.topLeft(), QtCore.QSizeF(img.width() * ratio, img.height() * ratio)), img)
        painter.end()

    def print_dialog(self) -> None:
        from PySide6 import QtPrintSupport
        printer = QtPrintSupport.QPrinter()
        dlg = QtPrintSupport.QPrintDialog(printer, self)
        if dlg.exec():
            self.print_to(printer)

    def copy_image(self, transparent: bool = True) -> QtGui.QImage:
        img = self.view.to_image(scale=2.0, transparent=transparent)
        QtWidgets.QApplication.clipboard().setImage(img)
        return img

    def histogram_function(self) -> str:
        """Binned X values of the population: bin start value and event count, 4096 rows."""
        a = self.analysis
        xf = a.ps.transforms[a.ps.parameter(self.x).name]
        pos = np.clip(a.ps.scaled(self.x)[a.mask(self.path)], 0, 1)
        counts = np.bincount(np.minimum((pos * HIST_BINS).astype(int), HIST_BINS - 1), minlength=HIST_BINS)
        starts = xf.inverse(np.arange(HIST_BINS) / HIST_BINS)
        rows = [f"{a.ps.parameter(self.x).label}\tCount"] + [f"{v:.6g}\t{c}" for v, c in zip(starts, counts)]
        return "\n".join(rows)

    def copy_histogram_function(self) -> str:
        text = self.histogram_function()
        QtWidgets.QApplication.clipboard().setText(text)
        return text

    # --- gating -----------------------------------------------------------------------
    def _tool_changed(self, *_) -> None:
        act = self.tool_group.checkedAction()
        self.view.viewbox.set_tool(act.text() if act else None)

    def _sync_tools(self, *_) -> None:
        hist = self.y is None
        for tool, act in self.tool_actions.items():
            ok = (tool in HISTOGRAM_TOOLS) == hist
            act.setEnabled(ok)
            if not ok and act.isChecked():
                act.setChecked(False)
                self.view.viewbox.set_tool(None)

    def suggested_name(self) -> str:
        """FlowJo suggests "<X>, <Y> subset" (stain names where there are any)."""
        ps = self.analysis.ps
        px = ps.parameter(self.x)
        parts = [px.stain or px.name]
        if self.y is not None:
            py = ps.parameter(self.y)
            parts.append(py.stain or py.name)
        return ", ".join(parts) + " subset"

    def ask_gate_name(self, default: str) -> str | None:
        """Prompt for a gate name (replaced in tests)."""
        name, ok = QtWidgets.QInputDialog.getText(self, "Gate Name", "Name:", text=default)
        return name.strip() if ok and name.strip() else None

    def shape_drawn(self, tool: str, pts) -> list[tuple[str, ...]]:
        """Turn a drawn shape (display units) into gate(s) under this population."""
        a, parent, x, y = self.analysis, self.path, self.x, self.y
        ps = a.ps
        self.last_tool = tool  # the tool behind the gate being named
        to_x = lambda v: float(ps.transforms[x].inverse(np.array([v]))[0])  # noqa: E731
        to_y = (lambda v: float(ps.transforms[y].inverse(np.array([v]))[0])) if y else None  # noqa: E731

        if tool == "Quadrant":
            (qx, qy), = pts
            xs, ys = ps.parameter(x), ps.parameter(y)
            self.main.record()
            made = [a.add_gate(parent, a.unique_name(parent, qname), gate).path
                    for qname, gate in quadrant_gates(x, y, to_x(qx), to_y(qy), xs.stain or xs.name, ys.stain or ys.name)]
            self.main.data_changed([a.name])
            return made
        if tool in ("Spider", "Curly Quad", "Bisector"):
            (px, py), = pts
            xs = ps.parameter(x)
            if tool == "Bisector":
                made_gates = bisector_gates(x, to_x(px), xs.stain or xs.name)
            else:
                ys = ps.parameter(y)
                maker = spider_gates if tool == "Spider" else curly_quadrant_gates
                made_gates = maker(x, y, ps.transforms[x], ps.transforms[y], (px, py),
                                   x_name=xs.stain or xs.name, y_name=ys.stain or ys.name)
            self.main.record()
            made = [a.add_gate(parent, a.unique_name(parent, n), g).path for n, g in made_gates]
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
        name = self.ask_gate_name(a.unique_name(parent, self.suggested_name()))
        if not name:
            return []
        path = self.add_gate(gate, name)
        return [path] if path else []

    def add_gate(self, gate, name: str):
        a = self.analysis
        if any(c.name == name for c in a.gates.get(self.path).children):
            QtWidgets.QMessageBox.warning(self, "Gate", f"{name} already exists here")
            return None
        self.main.record()
        pop = a.add_gate(self.path, name, gate)
        self.main.data_changed([a.name])
        return pop.path

    def manual_gate_dialog(self) -> None:
        ps = self.analysis.ps
        dlg = ManualGateDialog(ps.parameter(self.x).label, ps.parameter(self.y).label if self.y else None, self)
        dlg.name.setText(self.analysis.unique_name(self.path, self.suggested_name()))
        if dlg.exec():
            try:
                gate = dlg.gate(self.x, self.y)
            except ValueError as exc:
                QtWidgets.QMessageBox.warning(self, "Manual Gate Entry", str(exc))
                return
            self.add_gate(gate, dlg.name.text().strip() or self.suggested_name())

    # active gate -------------------------------------------------------------------------
    def active_population(self):
        if self.active is None:
            return None
        try:
            return self.main.samples[self.active[0]].gates.get(self.active[1])
        except KeyError:
            return None

    def select_gate(self, key) -> None:
        self.active = key
        self.setFocus()
        self.redraw()

    def _change_gate(self, key, new_gate) -> list[str]:
        """Replace a gate, asking about the group for group-owned gates."""
        name, path = key
        pop = self.main.samples[name].gates.get(path)
        linked = self.main.ws.linked_samples(name, path)
        whole = True
        if len(linked) > 1:
            scope = self.main.ask_scope(pop.owning_group, len(linked))
            if scope is None:
                self.redraw()
                return []
            whole = scope == "group"
        self.main.record()
        changed = self.main.ws.update_gate(name, path, new_gate, whole_group=whole)
        self.main.data_changed(changed)
        return changed

    def gate_moved(self, key, dx: float, dy: float) -> list[str]:
        """A gate was dragged on the plot (display units)."""
        name, path = key
        a = self.main.samples[name]
        deltas = {a.ps.parameter(self.x).name: dx}
        if self.y is not None:
            deltas[a.ps.parameter(self.y).name] = dy
        self.active = key
        return self._change_gate(key, translate_gate(a.gates.get(path).gate, a.ps, deltas))

    # quadrant arms (offset quadrants) ------------------------------------------------------
    def quad_set(self):
        """If the active gate belongs to a rectangle quadrant on these axes:
        ({"Q1": path, …}, splits dict in data units)."""
        pop = self.active_population()
        if pop is None or not isinstance(pop.gate, RectangleGate) or pop.name[:3] not in {f"{q}:" for q in QUAD_ORDER}:
            return None
        sibs = {c.name[:2]: c for c in pop.parent.children if c.name[:3] in {f"{q}:" for q in QUAD_ORDER}
                and isinstance(c.gate, RectangleGate) and len(c.gate.dims) == 2}
        if set(sibs) != set(QUAD_ORDER) or self.y is None:
            return None
        ps = self.analysis.ps
        if tuple(ps.parameter(d).name for d in sibs["Q2"].gate.dims) != (ps.parameter(self.x).name, ps.parameter(self.y).name):
            return None
        q1, q2, q3, q4 = (sibs[q].gate for q in QUAD_ORDER)
        splits = {"x_upper": q2.mins[0], "x_lower": q3.mins[0], "y_left": q1.mins[1], "y_right": q2.mins[1]}
        if None in splits.values():
            return None
        return {q: sibs[q].path for q in QUAD_ORDER}, splits

    def _quad_handles(self) -> None:
        found = self.quad_set()
        if found is None:
            return
        _, sp = found
        ps = self.analysis.ps
        fx = lambda v: float(ps.transforms[self.x].apply([v])[0])  # noqa: E731
        fy = lambda v: float(ps.transforms[self.y].apply([v])[0])  # noqa: E731
        pts = [(fx(sp["x_upper"]), fy(sp["y_left"])),  # 0 centre
               (fx(sp["x_upper"]), 0.97), (fx(sp["x_lower"]), 0.03),  # 1 top arm, 2 bottom arm
               (0.03, fy(sp["y_left"])), (0.97, fy(sp["y_right"]))]  # 3 left arm, 4 right arm
        self.view.add_handles(pts, self.quad_arm_moved)

    def quad_arm_moved(self, index: int, pos) -> list[str]:
        """Drag the centre (moves the quadrant) or one arm (offsets that half of
        the divider; the other direction is straightened, as in FlowJo)."""
        found = self.quad_set()
        if found is None:
            return []
        paths, sp = found
        ps = self.analysis.ps
        vx = float(ps.transforms[self.x].inverse([pos[0]])[0])
        vy = float(ps.transforms[self.y].inverse([pos[1]])[0])
        if index == 0:
            sp = {"x_upper": vx, "x_lower": vx, "y_left": vy, "y_right": vy}
        elif index in (1, 2):
            sp["x_upper" if index == 1 else "x_lower"] = vx
            sp["y_right"] = sp["y_left"]
        else:
            sp["y_left" if index == 3 else "y_right"] = vy
            sp["x_lower"] = sp["x_upper"]
        new = dict(zip(QUAD_ORDER, (g for _, g in offset_quadrant_gates(self.x, self.y, **sp))))
        first = paths["Q1"]
        linked = self.main.ws.linked_samples(self.sample_name, first)
        whole = True
        if len(linked) > 1:
            scope = self.main.ask_scope(self.active_population().owning_group, len(linked))
            if scope is None:
                self.redraw()
                return []
            whole = scope == "group"
        self.main.record()
        changed = set()
        for q in QUAD_ORDER:
            changed.update(self.main.ws.update_gate(self.sample_name, paths[q], new[q], whole_group=whole))
        self.main.data_changed(sorted(changed))
        return sorted(changed)

    def vertex_moved(self, key, index: int, pos) -> list[str]:
        name, path = key
        a = self.main.samples[name]
        return self._change_gate(key, move_vertex(a.gates.get(path).gate, a.ps, self.x, self.y, index, pos))

    def scale_active(self, factor: float) -> list[str]:
        pop = self.active_population()
        if pop is None:
            return []
        return self._change_gate(self.active, scale_gate(pop.gate, self.analysis.ps, factor))

    def set_events_inside(self, inside: bool) -> list[str]:
        pop = self.active_population()
        if pop is None or pop.gate.inside == inside:
            return []
        return self._change_gate(self.active, dataclasses.replace(pop.gate, inside=inside))

    def set_tint(self, on: bool) -> None:
        pop = self.active_population()
        if pop is None:
            return
        self.main.record()
        pop.tint = ("#%02x%02x%02x" % self.tint_color.color[:3]) if on else None
        self.redraw()

    def clear_active_gate(self) -> None:
        pop = self.active_population()
        if pop is None:
            return
        name, path = self.active
        self.main.record()
        self.main.samples[name].remove_gate(path)
        self.active = None
        self.main.data_changed([name])

    def nudge_active(self, dx_px: int, dy_px: int) -> list[str]:
        """Arrow keys move the active gate one pixel."""
        if self.active is None:
            return []
        px = self.view.viewbox.viewPixelSize()  # view units per screen pixel (x, y)
        top = self.view.y_top if self.y is None else 1.0
        return self.gate_moved(self.active, dx_px * px[0], dy_px * px[1] / top)

    def keyPressEvent(self, e: QtGui.QKeyEvent) -> None:
        k = e.key()
        if e.modifiers() == QtCore.Qt.KeyboardModifier.NoModifier and self.active is not None:
            moves = {QtCore.Qt.Key.Key_Left: (-1, 0), QtCore.Qt.Key.Key_Right: (1, 0),
                     QtCore.Qt.Key.Key_Up: (0, 1), QtCore.Qt.Key.Key_Down: (0, -1)}
            if k in moves:
                self.nudge_active(*moves[k])
                return
            if k in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
                self.clear_active_gate()
                return
        if k == QtCore.Qt.Key.Key_Escape and self.active is not None:
            self.active = None
            self.redraw()
            return
        super().keyPressEvent(e)

    def label_moved(self, key, offset) -> None:
        name, path = key
        self.main.record()
        self.main.samples[name].gates.get(path).label_offset = offset

    def add_statistic_dialog(self) -> None:
        a = self.analysis
        spec = self.main.ask_stat(a, self.path)
        if spec is None:
            return
        self.main.record()
        a.add_stat(self.path, spec)
        self.main.data_changed([a.name])

    # --- axis transforms ---------------------------------------------------------------
    def _axis_param(self, axis: str):
        return self.x if axis == "x" else self.y

    def set_axis_kind(self, axis: str, kind: str) -> None:
        pname = self._axis_param(axis)
        if pname is None:
            return
        ps = self.analysis.ps
        self.apply_transform(pname, starter_transform(kind, ps.parameter(pname), ps.transforms[pname]), False)

    def reset_axis(self, axis: str) -> None:
        pname = self._axis_param(axis)
        if pname is None:
            return
        ps = self.analysis.ps
        self.apply_transform(pname, default_transform(ps.parameter(pname), ps.values(pname)), False)

    def open_transform_editor(self, axis: str, show: bool = True) -> TransformEditor | None:
        pname = self._axis_param(axis)
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
        self.main.record()
        changed = []
        for n in targets:
            ps = self.main.samples[n].ps
            if pname in {p.name for p in ps.parameters}:
                ps.set_transform(pname, xf)
                self.main.samples[n].invalidate()  # gates are evaluated in display space
                changed.append(n)
        self.main.data_changed(changed)
        return changed
