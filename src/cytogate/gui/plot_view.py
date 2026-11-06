"""Graph widget: one sample, one or two parameters, FlowJo-style axes."""

from __future__ import annotations

import re

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
        for item in (self.image, self.contours, self.hist):
            self.plot.addItem(item)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.title)
        layout.addWidget(self.plot, 1)
        self.last_render = None
        self.gate_items: list = []

    def clear(self) -> None:
        self.image.clear()
        self.contours.clear()
        self.hist.clear()
        self.title.setText("")
        self.clear_gates()

    def clear_gates(self) -> None:
        for item in self.gate_items:
            self.plot.removeItem(item)
        self.gate_items = []

    def show_gates(self, ps: ProcessedSample, x: str, y: str | None, gates) -> list[str]:
        """Outline child gates that use these axes. `gates` = [(name, gate, freq %)].
        Returns the names drawn."""
        self.clear_gates()
        drawn = []
        top = self.plot.getViewBox().viewRange()[1][1] if y is None else 1.0
        for name, gate, freq in gates:
            pts = gate_outline(gate, ps, x, y)
            if pts is None:
                continue
            if y is None:
                pts = pts * np.array([1.0, top * 0.95])
            curve = pg.PlotCurveItem(pts[:, 0], pts[:, 1], pen=GATE_PEN)
            quad = re.match(r"^(Q[1-4]):", name)
            if quad and y is not None:
                # FlowJo puts quadrant numbers in the outer corners
                corner = {"Q1": (0, 1), "Q2": (1, 1), "Q3": (1, 0), "Q4": (0, 0)}[quad.group(1)]
                label = pg.TextItem(f"{quad.group(1)}\n{freq:.3g}", color=(0, 0, 0),
                                    anchor=(corner[0], 1 - corner[1]))
                label.setPos(0.01 + 0.98 * corner[0], 0.01 + 0.98 * corner[1])
            else:
                label = pg.TextItem(f"{name}\n{freq:.3g}", color=(0, 0, 0), anchor=(0.5, 0.5))
                cx = float(np.clip((pts[:, 0].min() + pts[:, 0].max()) / 2, 0.05, 0.95))
                cy = float((pts[:, 1].min() + pts[:, 1].max()) / 2)
                label.setPos(cx, cy if y is None else float(np.clip(cy, 0.05, 0.95)))
            for item in (curve, label):
                item.setZValue(20)
                self.plot.addItem(item, ignoreBounds=True)
                self.gate_items.append(item)
            drawn.append(name)
        return drawn

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
        px = ps.parameter(x)
        xs = ps.scaled(px.name)
        if mask is not None:
            xs = xs[mask]
        self.title.setText(f"{ps.sample.name}\n{population}\n{xs.size:,}")
        _set_axis(self.plot.getAxis("bottom"), ps.transforms[px.name])
        _set_axis(self.plot.getAxis("top"), ps.transforms[px.name])
        self.plot.setLabel("bottom", px.label)

        if kind == PlotType.HISTOGRAM or y is None:
            edges, heights = histogram(xs, y_mode=hist_y)
            self.hist.setData(edges, heights, stepMode="center")
            top = heights.max() * 1.05 if heights.size and heights.max() > 0 else 1
            left = self.plot.getAxis("left")
            left.setTicks(None)
            self.plot.setLabel("left", hist_y.value)
            self.plot.setXRange(0, 1, padding=0)
            self.plot.setYRange(0, top, padding=0)
            self.last_render = heights
            return

        py = ps.parameter(y)
        ys = ps.scaled(py.name)
        if mask is not None:
            ys = ys[mask]
        r = render_2d(xs, ys, kind)
        self.image.setImage(r.image, autoLevels=False)
        self.image.setRect(QtCore.QRectF(0, 0, 1, 1))
        if kind == PlotType.CONTOUR and r.levels.size:
            self.contours.setData(*_contour_paths(r.density, r.levels))
        _set_axis(self.plot.getAxis("left"), ps.transforms[py.name])
        _set_axis(self.plot.getAxis("right"), ps.transforms[py.name])
        self.plot.setLabel("left", py.label)
        self.plot.setXRange(0, 1, padding=0)
        self.plot.setYRange(0, 1, padding=0)
        self.last_render = r

    def export_png(self, path: str, scale: float = 2.0) -> bool:
        """Save the graph (title + plot) as an image."""
        pixmap = QtGui.QPixmap(self.size() * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(QtCore.Qt.GlobalColor.white)
        self.render(pixmap)
        return pixmap.save(path)
