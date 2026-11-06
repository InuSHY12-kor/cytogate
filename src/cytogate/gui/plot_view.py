"""Graph widget: one sample, one or two parameters, FlowJo-style axes.

Besides drawing, it handles the gate interactions of FlowJo's graph window:
click a gate to make it the active gate (outlined in red, with handles on its
vertices or corners), drag inside it to move it, drag a handle to reshape it,
drag its label anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import EllipseGate, PolygonGate, ProcessedSample, RectangleGate, Transform, axis_ticks
from ..plots import HistogramY, PlotType, histogram, render_2d
from .gate_tools import GateViewBox, ellipse_outline

pg.setConfigOptions(antialias=True, background="w", foreground="k", imageAxisOrder="col-major")

CONTOUR_PEN = pg.mkPen((0, 0, 0), width=1)
HIST_PEN = pg.mkPen((0, 0, 0), width=1.2)
HIST_BRUSH = pg.mkBrush(120, 120, 120, 110)
GATE_PEN = pg.mkPen((0, 0, 0), width=1.5)
ACTIVE_PEN = pg.mkPen((220, 0, 0), width=2.2)
MEDIAN_PEN = pg.mkPen((220, 0, 0), width=1.5, style=QtCore.Qt.PenStyle.DashLine)
IMAGE_FORMATS = {"png": "PNG image (*.png)", "jpg": "JPEG image (*.jpg)", "svg": "SVG vector image (*.svg)",
                 "pdf": "PDF document (*.pdf)", "bmp": "Bitmap (*.bmp)", "tif": "TIFF image (*.tif)"}


@dataclass
class DisplayOptions:
    """FlowJo graph window "Display" menu and "Options" panel settings."""

    show_grid: bool = False
    large_dots: bool = False
    show_gate_names: bool = True
    show_gate_freqs: bool = True
    show_medians: bool = False
    smooth: bool = False  # histograms
    show_outliers: bool = True  # contour plots
    foreground: tuple = (0, 0, 0)  # dots / outliers / histogram line
    background: tuple = (255, 255, 255)


def gate_outline(gate, ps: ProcessedSample, x: str, y: str | None) -> np.ndarray | None:
    """Gate shape in display units for a plot of x (and y), or None if it is on other axes."""
    px = ps.parameter(x).name
    py = ps.parameter(y).name if y else None
    dims = tuple(ps.parameter(d).name for d in gate.dims)
    if isinstance(gate, RectangleGate):
        def edge(d, v, lo):
            if v is None:
                return 0.0 if lo else 1.0
            return float(np.clip(ps.transforms[d].apply(np.array([v]))[0], 0, 1))
        if py is None and dims == (px,):
            x0, x1 = edge(px, gate.mins[0], True), edge(px, gate.maxs[0], False)
            return np.array([[x0, 0], [x0, 1], [x0, 0.5], [x1, 0.5], [x1, 1], [x1, 0]])
        if py is not None and set(dims) == {px, py}:
            ix, iy = dims.index(px), dims.index(py)
            x0, x1 = edge(px, gate.mins[ix], True), edge(px, gate.maxs[ix], False)
            y0, y1 = edge(py, gate.mins[iy], True), edge(py, gate.maxs[iy], False)
            return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])
        return None
    if py is None or len(dims) != 2:
        return None
    if isinstance(gate, PolygonGate) and dims == (px, py):
        v = gate.display_vertices(ps)
        return np.vstack([v, v[:1]])
    if isinstance(gate, EllipseGate) and dims == (px, py):
        return ellipse_outline(gate.center, gate.a, gate.b, gate.angle)
    return None


def gate_handles(gate, ps: ProcessedSample, x: str, y: str | None) -> np.ndarray | None:
    """Points the user can drag to reshape a gate (display units), or None."""
    pts = gate_outline(gate, ps, x, y)
    if pts is None:
        return None
    if isinstance(gate, PolygonGate):
        return pts[:-1]
    if isinstance(gate, RectangleGate):
        if y is None:
            return np.array([[pts[0, 0], 0.5], [pts[3, 0], 0.5]])
        return pts[:4]
    return None


def _set_axis(axis: pg.AxisItem, xf: Transform) -> None:
    ticks = axis_ticks(xf)
    axis.setTicks([
        [(t.position, t.label) for t in ticks if t.major],
        [(t.position, "") for t in ticks if not t.major],
    ])


def _contour_paths(dens: np.ndarray, levels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Contour lines in display units, joined with NaN gaps for one curve item."""
    res = dens.shape[0]
    xs, ys = [], []
    for level in levels:
        for line in pg.functions.isocurve(dens, level, connected=True, extendToEdge=True):
            pts = np.asarray(line, dtype=float)
            xs += [pts[:, 0] / res, [np.nan]]
            ys += [pts[:, 1] / res, [np.nan]]
    if not xs:
        return np.array([]), np.array([])
    return np.concatenate(xs), np.concatenate(ys)


class GateShape(pg.PlotCurveItem):
    """Gate outline. Click to select; when movable, drag inside to move it
    (only while no gate tool is active). `on_moved(dx, dy)` gets the shift in
    display units."""

    def __init__(self, pts, viewbox, y_scale, on_moved, on_click=None, active=False, tint=None):
        super().__init__(pts[:, 0], pts[:, 1], pen=ACTIVE_PEN if active else GATE_PEN)
        self.vb = viewbox
        self.y_scale = y_scale
        self.on_moved = on_moved
        self.on_click = on_click
        self.active = active
        path = QtGui.QPainterPath()
        path.addPolygon(QtGui.QPolygonF([QtCore.QPointF(float(x), float(y)) for x, y in pts]))
        path.closeSubpath()
        self._area = path
        self._tint = None
        if tint:
            c = QtGui.QColor(tint)
            c.setAlpha(70)
            self._tint = c
        if on_moved:
            self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)

    def _free(self) -> bool:
        return self.vb.tool is None

    def shape(self):
        return self._area if (self.on_moved is not None or self.on_click is not None) else super().shape()

    def paint(self, p, *args):
        if self._tint is not None:  # FlowJo "Tint": fill the gate area
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(self._tint)
            p.drawPath(self._area)
        super().paint(p, *args)

    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton and self._free() and self.on_click:
            ev.accept()
            self.on_click()
        else:
            ev.ignore()

    def mouseDragEvent(self, ev):
        if ev.button() != QtCore.Qt.MouseButton.LeftButton or self.on_moved is None or not self._free():
            ev.ignore()
            return
        ev.accept()
        p0 = self.vb.mapSceneToView(ev.buttonDownScenePos())
        p1 = self.vb.mapSceneToView(ev.scenePos())
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
        self.setPos(dx, dy)
        if ev.isFinish():
            self.setPos(0, 0)
            self.on_moved(float(dx), float(dy / self.y_scale))


class GateHandle(pg.GraphicsObject):
    """Small square on a vertex/corner of the active gate; drag it to reshape."""

    SIZE = 7

    def __init__(self, index: int, pos, viewbox, y_scale, on_moved):
        super().__init__()
        self.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.index, self.vb, self.y_scale, self.on_moved = index, viewbox, y_scale, on_moved
        self.setPos(float(pos[0]), float(pos[1] * y_scale))
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setZValue(25)

    def boundingRect(self):
        s = self.SIZE
        return QtCore.QRectF(-s, -s, 2 * s, 2 * s)

    def paint(self, p, *args):
        s = self.SIZE / 2 + 1
        p.setPen(pg.mkPen((0, 0, 0)))
        p.setBrush(pg.mkBrush((0, 0, 0)))
        p.drawRect(QtCore.QRectF(-s, -s, 2 * s, 2 * s))

    def mouseDragEvent(self, ev):
        if ev.button() != QtCore.Qt.MouseButton.LeftButton or self.vb.tool is not None:
            ev.ignore()
            return
        ev.accept()
        p = self.vb.mapSceneToView(ev.scenePos())
        self.setPos(p)
        if ev.isFinish():
            self.on_moved(self.index, (float(np.clip(p.x(), 0, 1)), float(np.clip(p.y() / self.y_scale, 0, 1))))


class GateLabel(pg.TextItem):
    """Gate name + frequency that the user can drag anywhere on the plot.

    Drag with the left button to move it; double-click to put it back.
    Ignored while a gate tool is active, so drawing over a label still works.
    """

    def __init__(self, text, anchor, base, offset, y_scale, viewbox, on_moved):
        super().__init__(text, color=(0, 0, 0), anchor=anchor)
        self.base = np.array(base, dtype=float)
        self.y_scale = y_scale  # view units per display unit on y (histogram counts)
        self.offset = np.array(offset, dtype=float)
        self.vb = viewbox
        self.on_moved = on_moved
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self._apply()

    def _apply(self):
        self.setPos(float(self.base[0] + self.offset[0]), float(self.base[1] + self.offset[1] * self.y_scale))

    def _movable(self) -> bool:
        return self.vb.tool is None and self.on_moved is not None

    def mouseDragEvent(self, ev):
        if ev.button() != QtCore.Qt.MouseButton.LeftButton or not self._movable():
            ev.ignore()
            return
        ev.accept()
        if ev.isStart():
            self._start = self.offset.copy()
        p0 = self.vb.mapSceneToView(ev.buttonDownScenePos())
        p1 = self.vb.mapSceneToView(ev.scenePos())
        self.offset = self._start + np.array([p1.x() - p0.x(), (p1.y() - p0.y()) / self.y_scale])
        self._apply()
        if ev.isFinish():
            self.on_moved((float(self.offset[0]), float(self.offset[1])))

    def mouseClickEvent(self, ev):
        if ev.double() and self._movable():
            ev.accept()
            self.offset = np.zeros(2)
            self._apply()
            self.on_moved((0.0, 0.0))
        else:
            ev.ignore()


class PlotView(QtWidgets.QWidget):
    """Shows a ProcessedSample as one of the PlotType kinds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QtWidgets.QLabel(alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("font-size: 12px;")
        self.viewbox = GateViewBox()
        self.plot = pg.PlotWidget(viewBox=self.viewbox)
        self.plot.setMenuEnabled(False)
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        self.plot.getViewBox().setDefaultPadding(0)
        for side in ("top", "right"):
            self.plot.showAxis(side)
            self.plot.getAxis(side).setStyle(showValues=False)
        self.image = pg.ImageItem()
        self.contours = pg.PlotCurveItem(pen=CONTOUR_PEN, connect="finite")
        self.hist = pg.PlotCurveItem(pen=HIST_PEN, brush=HIST_BRUSH, fillLevel=0)
        self.median_items: list = []
        for item in (self.image, self.contours, self.hist):
            self.plot.addItem(item)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.title)
        layout.addWidget(self.plot, 1)
        self.last_render = None
        self.gate_items: list = []
        self.options = DisplayOptions()
        self.y_top = 1.0  # view units of the top of the y axis (counts for histograms)

    def clear(self) -> None:
        self.image.clear()
        self.contours.clear()
        self.hist.clear()
        self.title.setText("")
        for item in self.median_items:
            self.plot.removeItem(item)
        self.median_items = []
        self.clear_gates()

    def clear_gates(self) -> None:
        for item in self.gate_items:
            self.plot.removeItem(item)
        self.gate_items = []

    def show_gates(self, ps: ProcessedSample, x: str, y: str | None, gates, on_label_moved=None,
                   on_gate_moved=None, active=None, on_select=None, on_vertex_moved=None) -> list[str]:
        """Outline child gates that use these axes.

        `gates` = [(name, gate, freq %, label offset, key)] or with a 6th item,
        the tint colour. Labels can be dragged (when no gate tool is active):
        `on_label_moved(key, offset)`. Gates other than quadrants can be dragged:
        `on_gate_moved(key, dx, dy)` (display units). Clicking a gate calls
        `on_select(key)`; the gate whose key is `active` gets handles, which call
        `on_vertex_moved(key, index, (x, y))`. Returns the names drawn.
        """
        self.clear_gates()
        drawn = []
        top = self.y_top if y is None else 1.0
        opts = self.options
        for entry in gates:
            name, gate, freq, offset, key = entry[:5]
            tint = entry[5] if len(entry) > 5 else None
            pts = gate_outline(gate, ps, x, y)
            if pts is None:
                continue
            if y is None:
                pts = pts * np.array([1.0, top * 0.95])
            quad = re.match(r"^(Q[1-4]):", name)
            movable = on_gate_moved is not None and not quad
            is_active = active is not None and key == active
            curve = GateShape(pts, self.viewbox, top,
                              (lambda dx, dy, k=key: on_gate_moved(k, dx, dy)) if movable else None,
                              on_click=(lambda k=key: on_select(k)) if on_select else None,
                              active=is_active, tint=tint)
            short = quad.group(1) if quad and y is not None else name
            text = "\n".join(t for t, show in ((short, opts.show_gate_names), (f"{freq:.3g}", opts.show_gate_freqs)) if show)
            if quad and y is not None:
                # FlowJo puts quadrant numbers in the outer corners
                corner = {"Q1": (0, 1), "Q2": (1, 1), "Q3": (1, 0), "Q4": (0, 0)}[quad.group(1)]
                anchor = (corner[0], 1 - corner[1])
                base = (0.01 + 0.98 * corner[0], 0.01 + 0.98 * corner[1])
            else:
                anchor = (0.5, 0.5)
                cx = float(np.clip((pts[:, 0].min() + pts[:, 0].max()) / 2, 0.05, 0.95))
                cy = float((pts[:, 1].min() + pts[:, 1].max()) / 2)
                base = (cx, cy if y is None else float(np.clip(cy, 0.05, 0.95)))
            curve.setZValue(19)
            self.plot.addItem(curve, ignoreBounds=True)
            self.gate_items.append(curve)
            if text:
                label = GateLabel(text, anchor, base, offset, top, self.viewbox,
                                  (lambda off, k=key: on_label_moved(k, off)) if on_label_moved else None)
                label.setZValue(20)  # labels sit above gate areas so they can be dragged
                self.plot.addItem(label, ignoreBounds=True)
                self.gate_items.append(label)
            if is_active and on_vertex_moved is not None and not quad:
                handles = gate_handles(gate, ps, x, y)
                if handles is not None:
                    for i, hp in enumerate(handles):
                        h = GateHandle(i, hp, self.viewbox, top if y is None else 1.0,
                                       lambda idx, p, k=key: on_vertex_moved(k, idx, p))
                        self.plot.addItem(h, ignoreBounds=True)
                        self.gate_items.append(h)
            drawn.append(name)
        return drawn

    def add_handles(self, points, on_moved, y_scale: float = 1.0) -> list["GateHandle"]:
        """Extra drag handles (display units); on_moved(index, (x, y))."""
        out = []
        for i, p in enumerate(points):
            h = GateHandle(i, p, self.viewbox, y_scale, on_moved)
            self.plot.addItem(h, ignoreBounds=True)
            self.gate_items.append(h)
            out.append(h)
        return out

    def gate_labels(self) -> list["GateLabel"]:
        return [i for i in self.gate_items if isinstance(i, GateLabel)]

    def gate_shapes(self) -> list["GateShape"]:
        return [i for i in self.gate_items if isinstance(i, GateShape)]

    def gate_handles(self) -> list["GateHandle"]:
        return [i for i in self.gate_items if isinstance(i, GateHandle)]

    def show_sample(
        self,
        ps: ProcessedSample,
        x: str,
        y: str | None,
        kind: PlotType = PlotType.PSEUDOCOLOR,
        hist_y: HistogramY = HistogramY.COUNT,
        population: str = "Ungated",
        mask: np.ndarray | None = None,
    ) -> None:
        self.clear()
        opts = self.options
        self.plot.setBackground(QtGui.QColor(*opts.background))
        self.plot.showGrid(x=opts.show_grid, y=opts.show_grid, alpha=0.3)
        px = ps.parameter(x)
        xs = ps.scaled(px.name)
        if mask is not None:
            xs = xs[mask]
        self.title.setText(f"{ps.sample.name}\n{population}\n{xs.size:,}")
        _set_axis(self.plot.getAxis("bottom"), ps.transforms[px.name])
        _set_axis(self.plot.getAxis("top"), ps.transforms[px.name])
        self.plot.setLabel("bottom", px.label)

        if kind == PlotType.HISTOGRAM or y is None:
            edges, heights = histogram(xs, y_mode=hist_y, smooth=opts.smooth)
            self.hist.setPen(pg.mkPen(opts.foreground, width=1.2))
            self.hist.setData(edges, heights, stepMode="center")
            top = heights.max() * 1.05 if heights.size and heights.max() > 0 else 1
            self.y_top = float(top)
            self.plot.getAxis("left").setTicks(None)
            self.plot.setLabel("left", hist_y.value)
            self.plot.setXRange(0, 1, padding=0)
            self.plot.setYRange(0, top, padding=0)
            if opts.show_medians and xs.size:
                m = float(np.median(np.clip(xs, 0, 1)))
                line = pg.InfiniteLine(m, angle=90, pen=MEDIAN_PEN)
                self.plot.addItem(line, ignoreBounds=True)
                self.median_items.append(line)
            self.last_render = heights
            return

        self.y_top = 1.0
        py = ps.parameter(y)
        ys = ps.scaled(py.name)
        if mask is not None:
            ys = ys[mask]
        r = render_2d(xs, ys, kind, show_outliers=opts.show_outliers, dot_color=tuple(opts.foreground),
                      large_dots=opts.large_dots)
        self.image.setImage(r.image, autoLevels=False)
        self.image.setRect(QtCore.QRectF(0, 0, 1, 1))
        if kind == PlotType.CONTOUR and r.levels.size:
            self.contours.setPen(pg.mkPen(opts.foreground, width=1))
            self.contours.setData(*_contour_paths(r.density, r.levels))
        _set_axis(self.plot.getAxis("left"), ps.transforms[py.name])
        _set_axis(self.plot.getAxis("right"), ps.transforms[py.name])
        self.plot.setLabel("left", py.label)
        self.plot.setXRange(0, 1, padding=0)
        self.plot.setYRange(0, 1, padding=0)
        if opts.show_medians and xs.size:
            mx, my = float(np.median(np.clip(xs, 0, 1))), float(np.median(np.clip(ys, 0, 1)))
            for item in (pg.InfiniteLine(mx, angle=90, pen=MEDIAN_PEN), pg.InfiniteLine(my, angle=0, pen=MEDIAN_PEN)):
                self.plot.addItem(item, ignoreBounds=True)
                self.median_items.append(item)
        self.last_render = r

    # --- output -------------------------------------------------------------------------
    def to_image(self, scale: float = 2.0, transparent: bool = False) -> QtGui.QImage:
        """The graph (title + plot) as an image; optionally with a transparent background."""
        img = QtGui.QImage(self.size() * scale, QtGui.QImage.Format.Format_ARGB32)
        img.setDevicePixelRatio(scale)
        img.fill(QtCore.Qt.GlobalColor.transparent if transparent else QtCore.Qt.GlobalColor.white)
        if transparent:
            self.plot.setBackground(None)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, True)
        painter = QtGui.QPainter(img)
        self.render(painter, QtCore.QPoint(), QtGui.QRegion(), QtWidgets.QWidget.RenderFlag.DrawChildren)
        painter.end()
        if transparent:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground, False)
            self.plot.setBackground(QtGui.QColor(*self.options.background))
        return img

    def export_png(self, path: str, scale: float = 2.0) -> bool:
        return self.to_image(scale).save(path)

    def export_image(self, path: str) -> bool:
        """Save as PNG, JPG, BMP, TIFF, SVG or PDF, chosen by the file extension."""
        ext = path.rsplit(".", 1)[-1].lower()
        if ext == "svg":
            from PySide6 import QtSvg
            gen = QtSvg.QSvgGenerator()
            gen.setFileName(path)
            gen.setSize(self.size())
            gen.setViewBox(QtCore.QRect(QtCore.QPoint(0, 0), self.size()))
            gen.setTitle(self.title.text().replace("\n", " "))
            painter = QtGui.QPainter(gen)
            self.render(painter, QtCore.QPoint())
            painter.end()
            return True
        if ext == "pdf":
            writer = QtGui.QPdfWriter(path)
            writer.setPageSize(QtGui.QPageSize(QtCore.QSizeF(self.width(), self.height()), QtGui.QPageSize.Unit.Point))
            writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
            writer.setResolution(72)
            painter = QtGui.QPainter(writer)
            self.render(painter, QtCore.QPoint())
            painter.end()
            return True
        if ext in ("png", "jpg", "jpeg", "bmp", "tif", "tiff"):
            return self.to_image().save(path)
        raise ValueError(f"unsupported image format: .{ext}")
