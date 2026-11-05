"""Graph widget: one sample, one or two parameters, FlowJo-style axes."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..core import ProcessedSample, Transform, axis_ticks
from ..plots import HistogramY, PlotType, histogram, render_2d

pg.setConfigOptions(antialias=True, background="w", foreground="k", imageAxisOrder="col-major")

CONTOUR_PEN = pg.mkPen((0, 0, 0), width=1)
HIST_PEN = pg.mkPen((0, 0, 0), width=1.2)
HIST_BRUSH = pg.mkBrush(120, 120, 120, 110)


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
        self.plot = pg.PlotWidget()
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

    def clear(self) -> None:
        self.image.clear()
        self.contours.clear()
        self.hist.clear()
        self.title.setText("")

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
