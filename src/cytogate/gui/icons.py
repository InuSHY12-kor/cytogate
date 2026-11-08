"""Toolbar icons drawn with QPainter (no image files), in the style of FlowJo's
graph window: gate tools as black line drawings, navigation as green/grey arrows."""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6 import QtCore, QtGui

__all__ = ["icon", "ICON_NAMES"]

BLACK = QtGui.QColor(20, 20, 20)
GREEN = QtGui.QColor(40, 160, 40)
GREY = QtGui.QColor(90, 90, 90)
ORANGE = QtGui.QColor(220, 90, 30)
BLUE = QtGui.QColor(40, 90, 200)


def _pen(color=BLACK, w=1.6):
    p = QtGui.QPen(color, w)
    p.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    return p


def _P(x, y):
    return QtCore.QPointF(x, y)


def _pointer(p):
    poly = QtGui.QPolygonF([_P(6, 3), _P(6, 19), _P(10, 15), _P(13, 21), _P(15, 20), _P(12, 14), _P(17, 14)])
    p.setPen(_pen(BLACK, 1.2))
    p.setBrush(BLACK)
    p.drawPolygon(poly)


def _rectangle(p):
    p.setPen(_pen())
    p.drawRect(QtCore.QRectF(4, 6, 16, 12))


def _polygon(p):
    p.setPen(_pen())
    p.drawPolygon(QtGui.QPolygonF([_P(4, 18), _P(7, 5), _P(19, 8), _P(16, 14), _P(20, 20)]))


def _ellipse(p):
    p.setPen(_pen())
    p.drawEllipse(QtCore.QRectF(3, 7, 18, 11))


def _quadrant(p):
    p.setPen(_pen())
    p.drawLine(_P(12, 3), _P(12, 21))
    p.drawLine(_P(3, 12), _P(21, 12))


def _spider(p):
    p.setPen(_pen())
    c = _P(12, 12)
    for a in (20, 110, 200, 290):
        r = math.radians(a)
        p.drawLine(c, _P(12 + 9 * math.cos(r), 12 + 9 * math.sin(r)))
    p.setBrush(BLACK)
    p.drawEllipse(c, 1.5, 1.5)


def _curly(p):
    p.setPen(_pen())
    path = QtGui.QPainterPath(_P(12, 3))
    path.cubicTo(_P(9, 8), _P(15, 13), _P(12, 21))
    p.drawPath(path)
    path = QtGui.QPainterPath(_P(3, 12))
    path.cubicTo(_P(8, 15), _P(14, 9), _P(21, 13))
    p.drawPath(path)


def _range(p):
    p.setPen(_pen())
    p.drawLine(_P(5, 6), _P(5, 18))
    p.drawLine(_P(19, 6), _P(19, 18))
    p.drawLine(_P(5, 12), _P(19, 12))


def _bisector(p):
    p.setPen(_pen())
    p.drawLine(_P(12, 4), _P(12, 20))
    p.drawLine(_P(3, 12), _P(21, 12))
    p.drawLine(_P(3, 9), _P(3, 15))
    p.drawLine(_P(21, 9), _P(21, 15))


def _arrow_path(p, color, points):
    p.setPen(_pen(color.darker(130), 1.0))
    p.setBrush(color)
    p.drawPolygon(QtGui.QPolygonF([_P(x, y) for x, y in points]))


def _up(p):
    _arrow_path(p, GREEN, [(12, 5), (20, 18), (4, 18)])


def _down(p):
    _arrow_path(p, GREEN, [(4, 6), (20, 6), (12, 19)])


def _left(p):
    _arrow_path(p, GREY, [(6, 12), (18, 4), (18, 20)])


def _right(p):
    _arrow_path(p, GREY, [(6, 4), (18, 12), (6, 20)])


def _curved(p, mirror):
    if mirror:
        p.translate(24, 0)
        p.scale(-1, 1)
    p.setPen(_pen(ORANGE if not mirror else GREY, 2.2))
    path = QtGui.QPainterPath(_P(19, 18))
    path.cubicTo(_P(19, 8), _P(12, 6), _P(7, 9))
    p.drawPath(path)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(ORANGE if not mirror else GREY)
    p.drawPolygon(QtGui.QPolygonF([_P(3, 9), _P(9, 4), _P(10, 13)]))


def _undo(p):
    _curved(p, False)


def _redo(p):
    _curved(p, True)


def _duplicate(p):
    p.setPen(_pen(BLUE, 1.4))
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawRect(QtCore.QRectF(8, 8, 12, 12))
    p.drawRect(QtCore.QRectF(4, 4, 12, 12))


_DRAW = {
    "Pointer": _pointer, "Rectangle": _rectangle, "Polygon": _polygon, "Ellipse": _ellipse,
    "Quadrant": _quadrant, "Spider": _spider, "Curly Quad": _curly, "Range": _range, "Bisector": _bisector,
    "up": _up, "down": _down, "left": _left, "right": _right, "undo": _undo, "redo": _redo,
    "duplicate": _duplicate,
}
ICON_NAMES = tuple(_DRAW)


@lru_cache(maxsize=None)
def icon(name: str) -> QtGui.QIcon:
    """A 24×24 icon (drawn at 1× and 2× for sharp high-DPI screens)."""
    ic = QtGui.QIcon()
    for scale in (1, 2):
        pm = QtGui.QPixmap(24 * scale, 24 * scale)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        _DRAW[name](p)
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    return ic
