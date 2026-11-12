"""Layout Editor (FlowJo's Layout Editor): arrange graphs, text, shapes and tables
on pages, overlay and backgate populations, batch over a group and export.

- Drag populations from the workspace onto the page to add graphs; drop them on
  an existing graph to overlay them.
- Items move with the mouse (snapping to the grid) and resize from their
  bottom-right corner; the Properties panel edits the selected item.
- Batch: choose a group and how copies are arranged (pages / columns / rows);
  graphs whose sample is "(batch)" show each sample in turn.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.layout import (OVERLAY_COLORS, PAGE_SIZES, Layer, Layout, LayoutItem, arrange_grid, backgate,
                           layout_from_json, layout_to_json, list_presets, load_preset, paginate, save_preset, snap)
from ..core.workspace import ALL_SAMPLES
from ..plots.render import HistogramY, PlotType
from .layout_output import FORMATS, export_layout
from .layout_render import paint_item
from .widgets import ButtonRow

BATCH = "(batch)"
HANDLE = 8.0


class ItemGraphic(QtWidgets.QGraphicsObject):
    """A layout item on the page; paints through layout_render."""

    def __init__(self, editor, item: LayoutItem):
        super().__init__()
        self.editor = editor
        self.item = item
        self._cache: QtGui.QPixmap | None = None
        self._resizing = False
        self.setFlags(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setPos(item.x, item.y)

    def boundingRect(self):
        return QtCore.QRectF(0, 0, self.item.w, self.item.h).adjusted(-2, -2, 2, 2)

    def invalidate(self) -> None:
        self._cache = None
        self.prepareGeometryChange()
        self.update()

    def paint(self, p, option, widget=None):
        it = self.item
        if self._cache is None:
            k = 2.0
            pm = QtGui.QPixmap(max(int(it.w * k), 1), max(int(it.h * k), 1))
            pm.fill(QtCore.Qt.GlobalColor.transparent)
            pp = QtGui.QPainter(pm)
            pp.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            pp.scale(k, k)
            paint_item(pp, it, self.editor.main.ws, self.editor.sample_for(it), -it.x, -it.y,
                       self.editor.main.tables, 1, 1, self.editor.layout.name)
            pp.end()
            self._cache = pm
        p.drawPixmap(QtCore.QRectF(0, 0, it.w, it.h), self._cache, QtCore.QRectF(self._cache.rect()))
        if self.isSelected():
            p.setPen(QtGui.QPen(QtGui.QColor(30, 120, 255), 0, QtCore.Qt.PenStyle.DashLine))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRect(QtCore.QRectF(0, 0, it.w, it.h))
            p.setBrush(QtGui.QColor(30, 120, 255))
            p.drawRect(QtCore.QRectF(it.w - HANDLE, it.h - HANDLE, HANDLE, HANDLE))

    def _in_handle(self, pos) -> bool:
        return pos.x() >= self.item.w - HANDLE and pos.y() >= self.item.h - HANDLE

    def hoverMoveEvent(self, e):
        self.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor if self.isSelected() and self._in_handle(e.pos())
                       else QtCore.Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, e):
        if self.isSelected() and self._in_handle(e.pos()):
            self._resizing = True
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            self.resize_to(e.pos().x(), e.pos().y())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._resizing:
            self._resizing = False
            self.invalidate()
            self.editor.changed(self.item)
            return
        super().mouseReleaseEvent(e)
        self.editor.changed(self.item)

    def resize_to(self, w: float, h: float) -> None:
        lay = self.editor.layout
        g = lay.grid if lay.snap else 0
        self.prepareGeometryChange()
        self.item.w = max(snap(w, g) if g else w, 12.0)
        self.item.h = max(snap(h, g) if g else h, 12.0)
        self._cache = None
        self.update()

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            lay = self.editor.layout
            if lay.snap and lay.grid > 0:
                value = QtCore.QPointF(snap(value.x(), lay.grid), snap(value.y(), lay.grid))
            return value
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.item.x, self.item.y = float(self.pos().x()), float(self.pos().y())
        if change == QtWidgets.QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)


class PageView(QtWidgets.QGraphicsView):
    def __init__(self, editor):
        super().__init__(editor.scene)
        self.editor = editor
        self.setAcceptDrops(True)
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.setBackgroundBrush(QtGui.QColor(200, 200, 200))

    def drawBackground(self, p, rect):
        super().drawBackground(p, rect)
        lay = self.editor.layout
        w, h = lay.size
        page = QtCore.QRectF(0, 0, w, h)
        p.fillRect(page, QtGui.QColor("white"))
        if lay.show_grid and lay.grid > 0:
            p.setPen(QtGui.QPen(QtGui.QColor(225, 230, 240), 0))
            x = lay.grid
            while x < w:
                p.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, h))
                x += lay.grid
            y = lay.grid
            while y < h:
                p.drawLine(QtCore.QPointF(0, y), QtCore.QPointF(w, y))
                y += lay.grid
        p.setPen(QtGui.QPen(QtGui.QColor(170, 190, 220), 0, QtCore.Qt.PenStyle.DotLine))
        m = lay.margin
        p.drawRect(QtCore.QRectF(m, m, w - 2 * m, h - 2 * m))

    def dragEnterEvent(self, e):
        from .main_window import POP_MIME
        if e.mimeData().hasFormat(POP_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        self.editor.drop(e.mimeData(), self.mapToScene(e.position().toPoint()))
        e.acceptProposedAction()

    def wheelEvent(self, e):
        if e.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
        else:
            super().wheelEvent(e)


class LayoutEditor(QtWidgets.QMainWindow):
    def __init__(self, main, layout: Layout | None = None):
        super().__init__(main)
        self.main = main
        self.layout = layout or Layout()
        self.resize(1100, 820)
        self.scene = QtWidgets.QGraphicsScene(self)
        self.view = PageView(self)
        central = QtWidgets.QWidget()
        self._central_layout = QtWidgets.QVBoxLayout(central)
        self._central_layout.setContentsMargins(0, 0, 0, 0)
        self._central_layout.setSpacing(0)
        self.setCentralWidget(central)
        self.graphics: list[ItemGraphic] = []
        self._building = False
        self._build_toolbar()
        self._central_layout.addWidget(self.buttons)
        self._central_layout.addWidget(self.view, 1)
        self._build_docks()
        self.scene.selectionChanged.connect(self._selection_changed)
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Delete, self, activated=self.delete_selected)
        self.rebuild()

    # --- construction ---------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = self.buttons = ButtonRow(size=22)
        bar.add("Text", "text", lambda: self.add_item(LayoutItem(kind="text", text="Text", w=200, h=24, font_size=12)),
                "Add text ({Name}, {Count}, keywords … are filled in)")
        bar.add("Rectangle", "rect", lambda: self.add_item(LayoutItem(kind="shape", shape="rect", w=120, h=80)))
        bar.add("Ellipse", "ellipse", lambda: self.add_item(LayoutItem(kind="shape", shape="ellipse", w=120, h=80)))
        bar.add("Line", "line", lambda: self.add_item(LayoutItem(kind="shape", shape="line", w=120, h=0.1)))
        bar.add("Table", "table", self.add_table_dialog, "Add a Table Editor table…")
        bar.separator()
        bar.add("Backgate", "backgate", self.backgate_selected, "Backgate the selected graph…")
        bar.add("Arrange Grid", "grid", self.arrange_dialog, "Arrange items in a grid…")
        bar.add("Front", "front", lambda: self.restack(front=True), "Bring to front")
        bar.add("Back", "back", lambda: self.restack(front=False), "Send to back")
        bar.add("Delete", "delete", self.delete_selected, "Delete the selected items (Del)")
        bar.separator()
        bar.add("Batch / Export", "export", self.export_dialog, "Batch and export: PDF, PNG, SVG, PowerPoint, web page…")
        m = self.menuBar().addMenu("Figure Presets")
        m.addAction("Save Layout as Preset…", self.save_preset_dialog)
        self.preset_menu = m.addMenu("New from Preset")
        self.preset_menu.aboutToShow.connect(self._fill_presets)
        z = self.menuBar().addMenu("View")
        z.addAction("Zoom In", lambda: self.view.scale(1.2, 1.2), "Ctrl++")
        z.addAction("Zoom Out", lambda: self.view.scale(1 / 1.2, 1 / 1.2), "Ctrl+-")
        z.addAction("Fit Page", self.fit_page)

    def _build_docks(self) -> None:
        # page and batch settings
        lay = self.layout
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        self.name_edit = QtWidgets.QLineEdit()
        self.page_box = QtWidgets.QComboBox()
        self.page_box.addItems(list(PAGE_SIZES))
        self.landscape = QtWidgets.QCheckBox("Landscape")
        self.header_edit = QtWidgets.QLineEdit(placeholderText="e.g. {Name} — {Date}")
        self.footer_edit = QtWidgets.QLineEdit()
        self.grid_spin = QtWidgets.QDoubleSpinBox(minimum=0, maximum=144, value=lay.grid)
        self.show_grid = QtWidgets.QCheckBox("Show grid")
        self.snap_box = QtWidgets.QCheckBox("Snap to grid")
        self.group_box = QtWidgets.QComboBox()
        self.iterate_box = QtWidgets.QComboBox()
        for text, data in (("No batch", "none"), ("Each sample", "sample"), ("Each keyword value", "keyword")):
            self.iterate_box.addItem(text, data)
        self.iter_kw = QtWidgets.QComboBox(editable=True)
        self.disc_kw = QtWidgets.QComboBox(editable=True)
        self.direction_box = QtWidgets.QComboBox()
        for text, data in (("One copy per page", "pages"), ("Across (columns)", "columns"), ("Down (rows)", "rows")):
            self.direction_box.addItem(text, data)
        for label, widget in (("Name", self.name_edit), ("Page", self.page_box), ("", self.landscape),
                              ("Header", self.header_edit), ("Footer", self.footer_edit), ("Grid (pt)", self.grid_spin),
                              ("", self.show_grid), ("", self.snap_box), ("Batch group", self.group_box),
                              ("Iterate", self.iterate_box), ("Keyword", self.iter_kw),
                              ("Tube keyword", self.disc_kw), ("Arrange", self.direction_box)):
            f.addRow(label, widget)
        self.page_info = QtWidgets.QLabel()
        f.addRow("", self.page_info)
        for widget, sig in ((self.name_edit, "textChanged"), (self.page_box, "currentIndexChanged"),
                            (self.landscape, "toggled"), (self.header_edit, "textChanged"),
                            (self.footer_edit, "textChanged"), (self.grid_spin, "valueChanged"),
                            (self.show_grid, "toggled"), (self.snap_box, "toggled"),
                            (self.group_box, "currentIndexChanged"), (self.iterate_box, "currentIndexChanged"),
                            (self.iter_kw, "currentTextChanged"), (self.disc_kw, "currentTextChanged"),
                            (self.direction_box, "currentIndexChanged")):
            getattr(widget, sig).connect(self._page_changed)
        dock = self.page_dock = QtWidgets.QDockWidget("Page && Batch", self)
        dock.setWidget(w)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        # selected item
        self.props = QtWidgets.QWidget()
        self.props_form = QtWidgets.QFormLayout(self.props)
        dock2 = self.props_dock = QtWidgets.QDockWidget("Properties", self)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.props)
        dock2.setWidget(scroll)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock2)
        self.tabifyDockWidget(dock, dock2)
        dock.raise_()

    # --- model <-> view ------------------------------------------------------------------------
    def rebuild(self) -> None:
        """Recreate the scene from the layout model."""
        self._building = True
        lay = self.layout
        self.setWindowTitle(f"Layout Editor — {lay.name}")
        self.name_edit.setText(lay.name)
        self.page_box.setCurrentText(lay.page)
        self.landscape.setChecked(lay.landscape)
        self.header_edit.setText(lay.header)
        self.footer_edit.setText(lay.footer)
        self.grid_spin.setValue(lay.grid)
        self.show_grid.setChecked(lay.show_grid)
        self.snap_box.setChecked(lay.snap)
        self.group_box.clear()
        self.group_box.addItems(list(self.main.ws.groups))
        self.group_box.setCurrentText(lay.group if lay.group in self.main.ws.groups else ALL_SAMPLES)
        kws = sorted(self.main.ws.keyword_names())
        for box, value in ((self.iter_kw, lay.iterate_keyword), (self.disc_kw, lay.discriminator_keyword)):
            box.clear()
            box.addItems(kws)
            box.setCurrentText(value)
        self.iterate_box.setCurrentIndex(max(self.iterate_box.findData(lay.iterate), 0))
        self.direction_box.setCurrentIndex(max(self.direction_box.findData(lay.direction), 0))
        self.scene.clear()
        self.graphics = []
        for it in lay.items:
            self._add_graphic(it)
        w, h = lay.size
        self.scene.setSceneRect(QtCore.QRectF(-40, -40, w + 80, h + 80))
        self._building = False
        self._update_page_info()
        self._selection_changed()

    def _add_graphic(self, it: LayoutItem) -> ItemGraphic:
        g = ItemGraphic(self, it)
        g.setZValue(len(self.graphics))
        self.scene.addItem(g)
        self.graphics.append(g)
        return g

    def graphic(self, it: LayoutItem) -> ItemGraphic:
        return next(g for g in self.graphics if g.item is it)

    def _page_changed(self, *_) -> None:
        if self._building:
            return
        lay = self.layout
        lay.name = self.name_edit.text() or "Layout"
        size_changed = (lay.page, lay.landscape) != (self.page_box.currentText(), self.landscape.isChecked())
        lay.page = self.page_box.currentText()
        lay.landscape = self.landscape.isChecked()
        lay.header, lay.footer = self.header_edit.text(), self.footer_edit.text()
        lay.grid = self.grid_spin.value()
        lay.show_grid, lay.snap = self.show_grid.isChecked(), self.snap_box.isChecked()
        lay.group = self.group_box.currentText() or ALL_SAMPLES
        lay.iterate = self.iterate_box.currentData()
        lay.iterate_keyword, lay.discriminator_keyword = self.iter_kw.currentText(), self.disc_kw.currentText()
        lay.direction = self.direction_box.currentData()
        self.setWindowTitle(f"Layout Editor — {lay.name}")
        if size_changed:
            w, h = lay.size
            self.scene.setSceneRect(QtCore.QRectF(-40, -40, w + 80, h + 80))
        self.refresh_all()
        self.view.viewport().update()
        self.view.resetCachedContent()
        self.changed()

    def _update_page_info(self) -> None:
        copies = paginate(self.main.ws, self.layout)
        pages = (max(c.page for c in copies) + 1) if copies else 1
        self.page_info.setText(f"{len(copies)} cop{'y' if len(copies) == 1 else 'ies'} on {pages} page(s)")

    def changed(self, item: LayoutItem | None = None) -> None:
        self._update_page_info()
        self.main.store_layouts()

    def refresh_all(self) -> None:
        for g in self.graphics:
            g.invalidate()

    def sample_for(self, it: LayoutItem) -> str | None:
        """What the editor shows for an item: its own sample, or the first copy's."""
        if it.sample:
            return it.sample
        copies = paginate(self.main.ws, self.layout)
        if copies:
            idx = next((i for i, x in enumerate(self.layout.items) if x is it), None)
            if idx is not None and idx in copies[0].samples:
                return copies[0].samples[idx]
        return next(iter(self.main.ws.samples), None)

    def fit_page(self) -> None:
        w, h = self.layout.size
        self.view.fitInView(QtCore.QRectF(0, 0, w, h), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    # --- adding / removing ------------------------------------------------------------------------
    def _free_spot(self, it: LayoutItem) -> None:
        lay = self.layout
        if lay.items:
            last = lay.items[-1]
            it.x, it.y = last.x + last.w + lay.grid, last.y
            if it.x + it.w > lay.size[0] - lay.margin:
                it.x, it.y = lay.margin, max(i.y + i.h for i in lay.items) + lay.grid
        else:
            it.x, it.y = lay.margin, lay.margin

    def add_item(self, it: LayoutItem, place: bool = True) -> LayoutItem:
        if place:
            self._free_spot(it)
        self.layout.items.append(it)
        g = self._add_graphic(it)
        self.scene.clearSelection()
        g.setSelected(True)
        self.changed(it)
        return it

    def graph_item(self, sample, path, x=None, y=None, plot_type=None, hist_y=None) -> LayoutItem:
        from .graph_window import default_axes
        a = self.main.ws.samples[sample]
        dx, dy = default_axes(a, tuple(path))
        if x is None:
            x, y = dx, dy
        kind = plot_type or (PlotType.HISTOGRAM.value if y is None else PlotType.PSEUDOCOLOR.value)
        return LayoutItem(kind="graph", w=180, h=180, sample=None,
                          path=tuple(path), x_param=x, y_param=y, plot_type=kind,
                          hist_y=hist_y or HistogramY.MODE.value)

    def add_graph(self, sample, path, x=None, y=None, plot_type=None, hist_y=None, pos=None) -> LayoutItem:
        it = self.graph_item(sample, path, x, y, plot_type, hist_y)
        if sample != next(iter(self.main.ws.samples), None) and self.layout.iterate == "none":
            it.sample = sample  # keep showing the sample that was dragged in
        if pos is not None:
            it.x, it.y = snap(pos.x(), self.layout.grid), snap(pos.y(), self.layout.grid)
        return self.add_item(it, place=pos is None)

    def drop(self, md, pos: QtCore.QPointF) -> list[LayoutItem]:
        """Populations dropped on the page become graphs; on a graph, overlays."""
        from .main_window import decode_populations
        items = decode_populations(md)
        target = next((g for g in self.scene.items(pos) if isinstance(g, ItemGraphic) and g.item.kind == "graph"), None)
        if target is not None and items:
            self.add_overlays(target.item, items)
            return [target.item]
        out = []
        for k, (s, p) in enumerate(items):
            q = QtCore.QPointF(pos.x() + k * (180 + self.layout.grid), pos.y())
            out.append(self.add_graph(s, p, pos=q))
        return out

    def add_overlays(self, it: LayoutItem, items) -> list[Layer]:
        base = self.sample_for(it)
        added = []
        for s, p in items:
            color = OVERLAY_COLORS[(len(it.layers)) % len(OVERLAY_COLORS)]
            layer = Layer(tuple(p), None if s == base else s, color)
            it.layers.append(layer)
            added.append(layer)
        self.graphic(it).invalidate()
        self.changed(it)
        self._selection_changed()
        return added

    def selected_items(self) -> list[LayoutItem]:
        return [g.item for g in self.graphics if g.isSelected()]

    def delete_selected(self) -> None:
        for it in self.selected_items():
            g = self.graphic(it)
            self.scene.removeItem(g)
            self.graphics.remove(g)
            self.layout.items.remove(it)
        self.changed()

    def restack(self, front: bool) -> None:
        sel = self.selected_items()
        rest = [i for i in self.layout.items if i not in sel]
        self.layout.items[:] = rest + sel if front else sel + rest
        for z, it in enumerate(self.layout.items):
            self.graphic(it).setZValue(z)
        self.changed()

    def ask_table(self) -> str | None:
        names = [t.name for t in self.main.tables]
        if not names:
            self.statusBar().showMessage("Make a table in the Table Editor first (Reports > New Table)")
            return None
        name, ok = QtWidgets.QInputDialog.getItem(self, "Add Table", "Table", names, 0, False)
        return name if ok else None

    def add_table_dialog(self) -> LayoutItem | None:
        name = self.ask_table()
        if not name:
            return None
        return self.add_item(LayoutItem(kind="table", table=name, w=self.layout.size[0] - 2 * self.layout.margin,
                                        h=140, font_size=8))

    def ask_ancestor(self, it: LayoutItem) -> tuple[str, ...] | None:
        path = tuple(it.path)
        choices = [path[:i] for i in range(len(path))]
        names = ["/".join(c) if c else "Ungated" for c in choices]
        name, ok = QtWidgets.QInputDialog.getItem(self, "Backgate", "Show the population on", names, 0, False)
        return choices[names.index(name)] if ok else None

    def backgate_selected(self) -> LayoutItem | None:
        graphs = [i for i in self.selected_items() if i.kind == "graph" and i.path]
        if not graphs:
            self.statusBar().showMessage("Select a graph of a gated population to backgate")
            return None
        anc = self.ask_ancestor(graphs[0])
        if anc is None:
            return None
        b = backgate(graphs[0], anc)
        a = self.main.ws.samples.get(self.sample_for(graphs[0]))
        if a is not None:
            from .graph_window import default_axes
            b.x_param, b.y_param = default_axes(a, anc)
            if b.y_param is None:
                b.plot_type = PlotType.HISTOGRAM.value
            elif b.plot_type == PlotType.HISTOGRAM.value:
                b.plot_type = PlotType.DOT.value
        return self.add_item(b, place=False)

    def ask_columns(self) -> int | None:
        n, ok = QtWidgets.QInputDialog.getInt(self, "Arrange in Grid", "Columns", 3, 1, 20)
        return n if ok else None

    def arrange_dialog(self) -> None:
        items = self.selected_items() or list(self.layout.items)
        n = self.ask_columns()
        if not n:
            return
        x0 = min(i.x for i in items) if items else self.layout.margin
        y0 = min(i.y for i in items) if items else self.layout.margin
        arrange_grid(items, n, x0, y0, self.layout.grid)
        for it in items:
            self.graphic(it).setPos(it.x, it.y)
        self.changed()

    # --- properties of the selected item -----------------------------------------------------
    def _clear_props(self) -> None:
        while self.props_form.rowCount():
            self.props_form.removeRow(0)

    def _selection_changed(self) -> None:
        try:
            sel = self.selected_items()
        except RuntimeError:
            return
        self._clear_props()
        if len(sel) != 1:
            self.props_form.addRow(QtWidgets.QLabel("Select one item to edit it." if not sel else f"{len(sel)} items"))
            return
        it = sel[0]
        f = self.props_form
        self.props_dock.raise_()

        def apply(attr, conv=lambda v: v):
            def go(v):
                setattr(it, attr, conv(v))
                self.graphic(it).invalidate()
                self.changed(it)
            return go

        geo = QtWidgets.QLabel(f"{it.kind} at {it.x:.0f}, {it.y:.0f} — {it.w:.0f} × {it.h:.0f} pt")
        f.addRow(geo)
        for attr in ("w", "h"):
            s = QtWidgets.QDoubleSpinBox(minimum=1, maximum=3000, value=getattr(it, attr))
            s.valueChanged.connect(apply(attr, float))
            f.addRow("Width" if attr == "w" else "Height", s)
        if it.kind == "graph":
            ws = self.main.ws
            sb = QtWidgets.QComboBox()
            sb.addItems([BATCH, *ws.samples])
            sb.setCurrentText(it.sample or BATCH)
            sb.currentTextChanged.connect(apply("sample", lambda v: None if v == BATCH else v))
            f.addRow("Sample", sb)
            a = ws.samples.get(self.sample_for(it))
            pb = QtWidgets.QComboBox()
            if a is not None:
                paths = [()] + [tuple(p) for p in a.gates.paths()]
                for p in paths:
                    pb.addItem("/".join(p) if p else "Ungated", p)
                pb.setCurrentIndex(max(paths.index(tuple(it.path)) if tuple(it.path) in paths else 0, 0))
            pb.currentIndexChanged.connect(lambda i: apply("path", tuple)(pb.itemData(i)))
            f.addRow("Population", pb)
            xb, yb = QtWidgets.QComboBox(), QtWidgets.QComboBox()
            yb.addItem("Histogram", None)
            if a is not None:
                for p in a.ps.parameters:
                    xb.addItem(p.label, p.name)
                    yb.addItem(p.label, p.name)
            xb.setCurrentIndex(max(xb.findData(it.x_param), 0))
            yb.setCurrentIndex(max(yb.findData(it.y_param), 0))
            xb.currentIndexChanged.connect(lambda i: apply("x_param")(xb.itemData(i)))

            def set_y(i):
                it.y_param = yb.itemData(i)
                if it.y_param is None:
                    it.plot_type = PlotType.HISTOGRAM.value
                elif it.plot_type == PlotType.HISTOGRAM.value:
                    it.plot_type = PlotType.PSEUDOCOLOR.value
                self.graphic(it).invalidate()
                self.changed(it)
            yb.currentIndexChanged.connect(set_y)
            f.addRow("X", xb)
            f.addRow("Y", yb)
            tb = QtWidgets.QComboBox()
            tb.addItems([k.value for k in PlotType if k != PlotType.HISTOGRAM])
            tb.setCurrentText(it.plot_type)
            tb.currentTextChanged.connect(apply("plot_type"))
            tb.setEnabled(it.y_param is not None)
            f.addRow("Plot type", tb)
            hb = QtWidgets.QComboBox()
            hb.addItems([h.value for h in HistogramY])
            hb.setCurrentText(it.hist_y)
            hb.currentTextChanged.connect(apply("hist_y"))
            f.addRow("Histogram Y", hb)
            for attr, text in (("show_gates", "Show gates"), ("show_title", "Show title"),
                               ("stagger", "Offset histogram overlays")):
                c = QtWidgets.QCheckBox(text, checked=getattr(it, attr))
                c.toggled.connect(apply(attr, bool))
                f.addRow("", c)
            title = QtWidgets.QLineEdit(it.text, placeholderText="{Name}\\n{Population}\\n{Count}")
            title.editingFinished.connect(lambda: apply("text")(title.text().replace("\\n", "\n")))
            f.addRow("Title", title)
            disc = QtWidgets.QLineEdit(it.discriminator, placeholderText="tube keyword value (keyword batch)")
            disc.editingFinished.connect(lambda: apply("discriminator")(disc.text().strip()))
            f.addRow("Tube", disc)
            for k, layer in enumerate(it.layers):
                row = QtWidgets.QHBoxLayout()
                name = ("/".join(layer.path) or "Ungated") + (f" ({layer.sample})" if layer.sample else "")
                lab = QtWidgets.QLabel(name)
                lab.setStyleSheet("color: rgb(%d,%d,%d)" % tuple(layer.color))
                col = QtWidgets.QPushButton("Colour…")
                col.clicked.connect(lambda _=False, L=layer: self.pick_layer_color(it, L))
                rm = QtWidgets.QPushButton("Remove")
                rm.clicked.connect(lambda _=False, L=layer: self.remove_layer(it, L))
                row.addWidget(lab, 1)
                row.addWidget(col)
                row.addWidget(rm)
                f.addRow(f"Overlay {k + 1}", row)
        elif it.kind == "text":
            te = QtWidgets.QPlainTextEdit(it.text)
            te.setFixedHeight(80)
            te.textChanged.connect(lambda: apply("text")(te.toPlainText()))
            f.addRow("Text", te)
            f.addRow(QtWidgets.QLabel("Tokens: {Name} {Population} {Count} {Page} {Pages} {Date} {Layout} {keyword}"))
            fs = QtWidgets.QDoubleSpinBox(minimum=4, maximum=96, value=it.font_size)
            fs.valueChanged.connect(apply("font_size", float))
            f.addRow("Font size", fs)
            b = QtWidgets.QCheckBox("Bold", checked=it.bold)
            b.toggled.connect(apply("bold", bool))
            f.addRow("", b)
        elif it.kind == "shape":
            lw = QtWidgets.QDoubleSpinBox(minimum=0, maximum=20, value=it.line_width)
            lw.valueChanged.connect(apply("line_width", float))
            f.addRow("Line width", lw)
            fill = QtWidgets.QCheckBox("Filled", checked=it.fill_color is not None)
            fill.toggled.connect(lambda on: apply("fill_color")((220, 230, 250) if on else None))
            f.addRow("", fill)
        elif it.kind == "table":
            tb = QtWidgets.QComboBox()
            tb.addItems([t.name for t in self.main.tables])
            tb.setCurrentText(it.table)
            tb.currentTextChanged.connect(apply("table"))
            f.addRow("Table", tb)
            fs = QtWidgets.QDoubleSpinBox(minimum=4, maximum=48, value=it.font_size)
            fs.valueChanged.connect(apply("font_size", float))
            f.addRow("Font size", fs)

    def ask_color(self, initial) -> tuple | None:
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(*initial), self, "Overlay colour")
        return (c.red(), c.green(), c.blue()) if c.isValid() else None

    def pick_layer_color(self, it: LayoutItem, layer: Layer) -> None:
        c = self.ask_color(layer.color)
        if c:
            layer.color = c
            self.graphic(it).invalidate()
            self.changed(it)
            self._selection_changed()

    def remove_layer(self, it: LayoutItem, layer: Layer) -> None:
        it.layers.remove(layer)
        self.graphic(it).invalidate()
        self.changed(it)
        self._selection_changed()

    # --- presets and output ----------------------------------------------------------------------
    def ask_text(self, title, label, default="") -> str | None:
        t, ok = QtWidgets.QInputDialog.getText(self, title, label, text=default)
        return t.strip() if ok and t.strip() else None

    def save_preset_dialog(self) -> Path | None:
        name = self.ask_text("Save Figure Preset", "Preset name", self.layout.name)
        if not name:
            return None
        out = save_preset(name, self.layout)
        self.statusBar().showMessage(f"Saved preset {name}")
        return out

    def _fill_presets(self) -> None:
        self.preset_menu.clear()
        for name in list_presets():
            self.preset_menu.addAction(name, lambda n=name: self.main.open_layout_editor(self.from_preset(n)))
        if not self.preset_menu.actions():
            self.preset_menu.addAction("(no presets saved)").setEnabled(False)

    def from_preset(self, name: str) -> Layout:
        lay = load_preset(name)
        lay.name = self.main.unique_layout_name(lay.name)
        return lay

    def export(self, path, fmt=None, dpi: float = 150) -> list[Path]:
        return export_layout(self.main.ws, self.layout, path, fmt, tables=self.main.tables, dpi=dpi)

    def export_dialog(self) -> list[Path]:
        filters = ";;".join(FORMATS.values())
        start = str(Path(self.main.path or Path.home() / "x").parent / f"{self.layout.name}.pdf")
        path, chosen = QtWidgets.QFileDialog.getSaveFileName(self, "Batch / Export Layout", start, filters)
        if not path:
            return []
        if not Path(path).suffix:
            ext = next(k for k, v in FORMATS.items() if v == chosen)
            path += "." + ext
        out = self.export(path)
        self.statusBar().showMessage(f"Wrote {len(out)} file(s): {out[0].name}{' …' if len(out) > 1 else ''}")
        return out

    def snapshot(self) -> dict:
        return copy.deepcopy(layout_to_json(self.layout))

    def restore(self, data: dict) -> None:
        self.layout.__dict__.update(layout_from_json(data).__dict__)
        self.rebuild()
