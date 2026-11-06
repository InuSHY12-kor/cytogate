"""Drawing gates on a plot with the mouse.

All coordinates here are display units (0..1 on each axis). The main window
converts finished shapes into gates.

Tools (like FlowJo's gate toolbar):
- Rectangle: drag a box.        - Ellipse: drag the box that bounds it.
- Polygon: click vertices; double-click (or click the first vertex) to close.
- Quadrant: click the crossing point.
- Range (histograms): drag across the x axis.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore

TOOLS = ("Rectangle", "Polygon", "Ellipse", "Quadrant", "Spider", "Curly Quad", "Range", "Bisector")
CLICK_TOOLS = ("Quadrant", "Spider", "Curly Quad", "Bisector")  # one click places the gate
HISTOGRAM_TOOLS = ("Range", "Bisector")
PREVIEW_PEN = pg.mkPen((220, 0, 0), width=1.5, style=QtCore.Qt.PenStyle.DashLine)
CLOSE_DISTANCE = 0.015  # display units


def _clip(p: QtCore.QPointF) -> tuple[float, float]:
    return (min(max(p.x(), 0.0), 1.0), min(max(p.y(), 0.0), 1.0))


def ellipse_outline(center, a, b, angle, n=100) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, n)
    c, s = np.cos(angle), np.sin(angle)
    x = a * np.cos(t)
    y = b * np.sin(t)
    return np.column_stack([center[0] + x * c - y * s, center[1] + x * s + y * c])


class GateViewBox(pg.ViewBox):
    """ViewBox that turns mouse gestures into shapes while a tool is active."""

    shapeDrawn = QtCore.Signal(str, object)  # tool name, list of (x, y) display points

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tool: str | None = None
        self.histogram_mode = False
        self._poly: list[tuple[float, float]] = []
        self.preview = pg.PlotCurveItem(pen=PREVIEW_PEN)
        self.preview.setZValue(50)
        self.addItem(self.preview, ignoreBounds=True)

    def set_tool(self, tool: str | None) -> None:
        self.tool = tool
        self._poly = []
        self.preview.clear()

    # --- drags: rectangle, ellipse, range --------------------------------------
    def mouseDragEvent(self, ev, axis=None):
        if self.tool not in ("Rectangle", "Ellipse", "Range"):
            return super().mouseDragEvent(ev, axis)
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        ev.accept()
        p0 = _clip(self.mapSceneToView(ev.buttonDownScenePos()))
        p1 = _clip(self.mapSceneToView(ev.scenePos()))
        if ev.isFinish():
            self.preview.clear()
            if self.tool == "Range":
                lo, hi = sorted((p0[0], p1[0]))
                if hi - lo > 1e-3:
                    self.shapeDrawn.emit("Range", [(lo, 0.0), (hi, 0.0)])
            elif abs(p1[0] - p0[0]) > 1e-3 and abs(p1[1] - p0[1]) > 1e-3:
                self.shapeDrawn.emit(self.tool, [p0, p1])
            return
        self._draw_preview(p0, p1)

    def _draw_preview(self, p0, p1):
        (x0, y0), (x1, y1) = p0, p1
        if self.tool == "Range":
            top = self.viewRange()[1][1] * 0.9
            self.preview.setData([x0, x0, x0, x1, x1, x1], [0, top, top / 2, top / 2, top, 0])
        elif self.tool == "Rectangle":
            self.preview.setData([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])
        else:
            pts = ellipse_outline(((x0 + x1) / 2, (y0 + y1) / 2), abs(x1 - x0) / 2, abs(y1 - y0) / 2, 0.0)
            self.preview.setData(pts[:, 0], pts[:, 1])

    # --- clicks: polygon, quadrant ---------------------------------------------
    def mouseClickEvent(self, ev):
        if self.tool not in ("Polygon", *CLICK_TOOLS) or ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return super().mouseClickEvent(ev)
        ev.accept()
        p = _clip(self.mapSceneToView(ev.scenePos()))
        if self.tool in CLICK_TOOLS:
            self.shapeDrawn.emit(self.tool, [p])
            return
        self.add_polygon_point(p, close=ev.double())

    def add_polygon_point(self, p, close: bool = False) -> None:
        if self._poly and len(self._poly) >= 3:
            first = np.array(self._poly[0])
            if close or np.linalg.norm(np.array(p) - first) < CLOSE_DISTANCE:
                pts, self._poly = self._poly, []
                self.preview.clear()
                self.shapeDrawn.emit("Polygon", pts)
                return
        if not close:
            self._poly.append(p)
        xs = [q[0] for q in self._poly]
        ys = [q[1] for q in self._poly]
        self.preview.setData(xs, ys)
