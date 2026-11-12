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


# --- workspace / editor icons ---------------------------------------------------------

def _plus(p, x=18, y=18, color=GREEN):
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(_P(x, y), 5, 5)
    p.setPen(_pen(QtGui.QColor(255, 255, 255), 1.6))
    p.drawLine(_P(x - 3, y), _P(x + 3, y))
    p.drawLine(_P(x, y - 3), _P(x, y + 3))


def _cross(p, x=18, y=18):
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(210, 40, 40))
    p.drawEllipse(_P(x, y), 5, 5)
    p.setPen(_pen(QtGui.QColor(255, 255, 255), 1.6))
    p.drawLine(_P(x - 2.5, y - 2.5), _P(x + 2.5, y + 2.5))
    p.drawLine(_P(x - 2.5, y + 2.5), _P(x + 2.5, y - 2.5))


def _text(p, text, size=13, color=BLACK, rect=(0, 0, 24, 24), bold=True):
    f = QtGui.QFont("Arial")
    f.setPixelSize(size)
    f.setBold(bold)
    p.setFont(f)
    p.setPen(color)
    p.drawText(QtCore.QRectF(*rect), int(QtCore.Qt.AlignmentFlag.AlignCenter), text)


def _page(p, color=QtGui.QColor(255, 255, 255)):
    p.setPen(_pen(GREY, 1.2))
    p.setBrush(color)
    p.drawPolygon(QtGui.QPolygonF([_P(5, 2), _P(15, 2), _P(19, 6), _P(19, 22), _P(5, 22)]))


def _folder(p, color=QtGui.QColor(240, 190, 70)):
    p.setPen(_pen(color.darker(150), 1.1))
    p.setBrush(color)
    p.drawPolygon(QtGui.QPolygonF([_P(2, 6), _P(9, 6), _P(11, 8), _P(21, 8), _P(21, 19), _P(2, 19)]))


def tube(p, x0=0.0, y0=0.0, k=1.0):
    """A sample tube (FlowJo uses one for samples)."""
    p.save()
    p.translate(x0, y0)
    p.scale(k, k)
    p.setPen(_pen(GREY, 1.1))
    p.setBrush(QtGui.QColor(250, 250, 250))
    p.drawRoundedRect(QtCore.QRectF(8, 2, 8, 19), 4, 4)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(240, 160, 40))
    p.drawRoundedRect(QtCore.QRectF(9, 11, 6, 9), 3, 3)
    p.setBrush(QtGui.QColor(200, 60, 40))
    p.drawRect(QtCore.QRectF(7, 1, 10, 3))
    p.restore()


def gate_glyph(p, x0=0.0, y0=0.0, k=1.0):
    """A population: a gate drawn around a few dots."""
    p.save()
    p.translate(x0, y0)
    p.scale(k, k)
    p.setPen(_pen(QtGui.QColor(20, 110, 60), 1.6))
    p.setBrush(QtGui.QColor(200, 240, 210))
    p.drawEllipse(QtCore.QRectF(3, 5, 18, 14))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    for (x, y), c in zip(((9, 11), (13, 13), (15, 9), (11, 15)), (BLUE, GREEN, ORANGE, BLUE)):
        p.setBrush(c)
        p.drawEllipse(_P(x, y), 1.8, 1.8)
    p.restore()


def _new(p):
    _page(p)
    _plus(p)


def _open(p):
    _folder(p)
    p.setPen(_pen(GREEN, 2))
    p.drawLine(_P(11, 16), _P(11, 10))
    p.drawLine(_P(8, 12), _P(11, 9))
    p.drawLine(_P(14, 12), _P(11, 9))


def _save(p):
    p.setPen(_pen(BLUE.darker(130), 1.2))
    p.setBrush(BLUE)
    p.drawRoundedRect(QtCore.QRectF(3, 3, 18, 18), 2, 2)
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawRect(QtCore.QRectF(7, 3, 10, 6))
    p.drawRect(QtCore.QRectF(6, 13, 12, 8))


def _save_as(p):
    _save(p)
    p.setPen(_pen(ORANGE, 2))
    p.drawLine(_P(13, 20), _P(21, 12))


def _add_samples(p):
    tube(p, -3, 0)
    _plus(p)


def _add_folder(p):
    _folder(p)
    _plus(p)


def _export(p):
    _page(p)
    p.setPen(_pen(GREEN, 2))
    p.drawLine(_P(9, 13), _P(21, 13))
    p.drawLine(_P(17, 9), _P(21, 13))
    p.drawLine(_P(17, 17), _P(21, 13))


def _group(p, badge=None):
    p.setPen(_pen(GREY, 1.1))
    for i, c in enumerate((QtGui.QColor(160, 200, 250), QtGui.QColor(190, 220, 250), QtGui.QColor(225, 240, 255))):
        p.setBrush(c)
        p.drawRect(QtCore.QRectF(3 + 2 * i, 4 + 3 * i, 13, 4))
    if badge:
        badge(p)


def _group_new(p):
    _group(p, _plus)


def _group_delete(p):
    _group(p, _cross)


def _group_keyword(p):
    _group(p)
    _text(p, "K", 11, BLUE, (11, 11, 13, 13))


def _group_rename(p):
    _group(p)
    _pencil(p, 6)


def _apply_group(p):
    gate_glyph(p, -2, -4, 0.75)
    p.setPen(_pen(GREEN, 2))
    p.drawLine(_P(10, 18), _P(20, 18))
    p.drawLine(_P(16, 14), _P(20, 18))
    p.drawLine(_P(16, 22), _P(20, 18))


def _statistic(p):
    _text(p, "Σ", 18, BLUE)


def _pencil(p, dx=0):
    p.save()
    p.translate(dx, 0)
    p.setPen(_pen(BLACK, 1))
    p.setBrush(QtGui.QColor(250, 200, 60))
    p.drawPolygon(QtGui.QPolygonF([_P(6, 16), _P(16, 6), _P(19, 9), _P(9, 19)]))
    p.setBrush(QtGui.QColor(60, 60, 60))
    p.drawPolygon(QtGui.QPolygonF([_P(6, 16), _P(9, 19), _P(5, 20)]))
    p.restore()


def _rename(p):
    _text(p, "ab", 10, GREY, (1, 1, 14, 12), bold=False)
    _pencil(p, 2)


def _gate_duplicate(p):
    gate_glyph(p, 4, 4, 0.8)
    gate_glyph(p, -1, -2, 0.8)


def _delete(p):
    _cross(p, 12, 12)
    p.setPen(QtCore.Qt.PenStyle.NoPen)


def _boolean(p, word):
    p.setPen(_pen(BLACK, 1.2))
    p.setBrush(QtGui.QColor(190, 220, 250, 160))
    p.drawEllipse(QtCore.QRectF(2, 5, 13, 13))
    p.setBrush(QtGui.QColor(250, 200, 190, 160))
    p.drawEllipse(QtCore.QRectF(9, 5, 13, 13))
    _text(p, word, 7, BLACK, (0, 17, 24, 7))


def _and(p):
    _boolean(p, "AND")


def _or(p):
    _boolean(p, "OR")


def _not(p):
    p.setPen(_pen(BLACK, 1.2))
    p.setBrush(QtGui.QColor(190, 220, 250))
    p.drawRect(QtCore.QRectF(2, 3, 20, 15))
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawEllipse(QtCore.QRectF(7, 5, 10, 10))
    _text(p, "NOT", 7, BLACK, (0, 17, 24, 7))


def _combos(p):
    for i, c in enumerate((BLUE, GREEN, ORANGE)):
        p.setPen(_pen(c, 1.3))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawEllipse(QtCore.QRectF(2 + 5 * i, 4 + (3 if i == 1 else 0), 11, 11))


def _bifur(p):
    p.setPen(_pen(GREY, 1.2))
    path = QtGui.QPainterPath(_P(2, 20))
    path.cubicTo(_P(6, 20), _P(6, 6), _P(9, 6))
    path.cubicTo(_P(12, 6), _P(11, 18), _P(13, 18))
    path.cubicTo(_P(16, 18), _P(16, 8), _P(18, 8))
    path.cubicTo(_P(20, 8), _P(20, 20), _P(22, 20))
    p.drawPath(path)
    p.setPen(_pen(QtGui.QColor(210, 40, 40), 1.8))
    p.drawLine(_P(12, 3), _P(12, 21))


def _keyword(p):
    p.setPen(_pen(GREY, 1.2))
    p.setBrush(QtGui.QColor(255, 230, 150))
    p.drawPolygon(QtGui.QPolygonF([_P(3, 8), _P(14, 8), _P(20, 13), _P(14, 18), _P(3, 18)]))
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawEllipse(_P(15, 13), 1.5, 1.5)
    _text(p, "K", 9, BLACK, (3, 8, 10, 10))


def _series(p):
    _text(p, "1", 9, BLACK, (2, 2, 8, 9))
    _text(p, "2", 9, BLACK, (8, 8, 8, 9))
    _text(p, "3", 9, BLACK, (14, 14, 8, 9))


def _find(p):
    p.setPen(_pen(BLACK, 2))
    p.setBrush(QtGui.QColor(220, 240, 255))
    p.drawEllipse(QtCore.QRectF(3, 3, 12, 12))
    p.drawLine(_P(13, 13), _P(20, 20))


def _paste(p):
    p.setPen(_pen(GREY, 1.2))
    p.setBrush(QtGui.QColor(210, 170, 120))
    p.drawRoundedRect(QtCore.QRectF(4, 4, 15, 18), 2, 2)
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawRect(QtCore.QRectF(7, 7, 9, 12))
    p.setBrush(GREY)
    p.drawRect(QtCore.QRectF(8, 2, 7, 4))


def _columns(p):
    p.setPen(_pen(GREY, 1.1))
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawRect(QtCore.QRectF(3, 4, 18, 16))
    p.setBrush(QtGui.QColor(190, 220, 250))
    p.drawRect(QtCore.QRectF(3, 4, 18, 4))
    for x in (9, 15):
        p.drawLine(_P(x, 4), _P(x, 20))


def _properties(p):
    p.setPen(_pen(BLUE, 1.4))
    p.setBrush(QtGui.QColor(230, 240, 255))
    p.drawEllipse(QtCore.QRectF(3, 3, 18, 18))
    _text(p, "i", 13, BLUE, (3, 3, 18, 18))


def _matrix(p, badge=None):
    p.setPen(_pen(GREY, 1))
    for i in range(3):
        for j in range(3):
            c = QtGui.QColor(70, 120, 220) if i == j else QtGui.QColor(215, 225, 245)
            p.setBrush(c)
            p.drawRect(QtCore.QRectF(3 + 6 * j, 3 + 6 * i, 5.5, 5.5))
    if badge:
        badge(p)


def _comp(p):
    _matrix(p)


def _comp_remove(p):
    _matrix(p, _cross)


def _transforms(p):
    p.setPen(_pen(BLACK, 1))
    p.setBrush(QtGui.QColor(245, 245, 245))
    p.drawRect(QtCore.QRectF(3, 3, 18, 18))
    _text(p, "T", 14, BLACK)


def _table(p):
    p.setPen(_pen(GREY, 1))
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawRect(QtCore.QRectF(2, 4, 20, 16))
    p.setBrush(QtGui.QColor(110, 170, 90))
    p.drawRect(QtCore.QRectF(2, 4, 20, 4))
    for y in (12, 16):
        p.drawLine(_P(2, y), _P(22, y))
    for x in (9, 15):
        p.drawLine(_P(x, 4), _P(x, 20))


def _layout(p):
    p.setPen(_pen(GREY, 1))
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawRect(QtCore.QRectF(4, 2, 16, 20))
    p.setBrush(QtGui.QColor(250, 170, 60))
    p.drawRect(QtCore.QRectF(6, 4, 6, 6))
    p.setBrush(QtGui.QColor(90, 150, 230))
    p.drawRect(QtCore.QRectF(13, 4, 5, 6))
    p.setPen(_pen(GREY, 1))
    for y in (13, 16, 19):
        p.drawLine(_P(6, y), _P(18, y))


def _new_table(p):
    _table(p)
    _plus(p)


def _new_layout(p):
    _layout(p)
    _plus(p)


def _text_item(p):
    _text(p, "A", 17, BLACK)


def _rect_item(p):
    p.setPen(_pen(BLACK, 1.6))
    p.setBrush(QtGui.QColor(220, 230, 250))
    p.drawRect(QtCore.QRectF(4, 6, 16, 12))


def _ellipse_item(p):
    p.setPen(_pen(BLACK, 1.6))
    p.setBrush(QtGui.QColor(220, 230, 250))
    p.drawEllipse(QtCore.QRectF(3, 6, 18, 12))


def _line_item(p):
    p.setPen(_pen(BLACK, 2))
    p.drawLine(_P(4, 20), _P(20, 4))


def _stack(p, front):
    a = QtGui.QColor(190, 190, 190)
    b = QtGui.QColor(70, 120, 220)
    p.setPen(_pen(GREY, 1))
    p.setBrush(b if not front else a)
    p.drawRect(QtCore.QRectF(3, 3, 12, 12))
    p.setBrush(a if not front else b)
    p.drawRect(QtCore.QRectF(9, 9, 12, 12))
    if not front:
        p.setBrush(b)
        p.drawRect(QtCore.QRectF(3, 3, 12, 12))


def _front(p):
    _stack(p, True)


def _back(p):
    _stack(p, False)


def _grid(p):
    p.setPen(_pen(GREY, 1))
    p.setBrush(QtGui.QColor(190, 220, 250))
    for i in range(2):
        for j in range(2):
            p.drawRect(QtCore.QRectF(3 + 10 * j, 3 + 10 * i, 8, 8))


def _backgate(p):
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    for x, y in ((6, 15), (9, 12), (12, 16), (15, 9), (17, 14), (7, 8), (19, 6), (11, 6)):
        p.setBrush(QtGui.QColor(160, 160, 160))
        p.drawEllipse(_P(x, y), 1.6, 1.6)
    for x, y in ((9, 12), (12, 16), (11, 13)):
        p.setBrush(QtGui.QColor(220, 30, 30))
        p.drawEllipse(_P(x, y), 2, 2)
    p.setPen(_pen(BLACK, 1))
    p.drawLine(_P(3, 3), _P(3, 21))
    p.drawLine(_P(3, 21), _P(21, 21))


def _formula(p):
    _text(p, "fx", 14, BLUE, bold=False)


def _remove(p):
    p.setPen(_pen(QtGui.QColor(210, 40, 40), 2.4))
    p.drawLine(_P(6, 12), _P(18, 12))


def _run(p):
    _arrow_path(p, GREEN, [(7, 4), (19, 12), (7, 20)])


def _copy(p):
    p.setPen(_pen(GREY, 1.2))
    p.setBrush(QtGui.QColor(255, 255, 255))
    p.drawRect(QtCore.QRectF(8, 7, 12, 14))
    p.drawRect(QtCore.QRectF(4, 3, 12, 14))


def _sample(p):
    tube(p)


def _gate(p):
    gate_glyph(p)


_DRAW.update({
    "new": _new, "open": _open, "save": _save, "save_as": _save_as, "add_samples": _add_samples,
    "add_folder": _add_folder, "export": _export, "group_new": _group_new, "group_delete": _group_delete,
    "group_keyword": _group_keyword, "group_rename": _group_rename, "apply_group": _apply_group,
    "statistic": _statistic, "rename": _rename, "gate_duplicate": _gate_duplicate, "delete": _delete,
    "and": _and, "or": _or, "not": _not, "combos": _combos, "bifur": _bifur, "keyword": _keyword,
    "series": _series, "find": _find, "paste": _paste, "columns": _columns, "properties": _properties,
    "comp": _comp, "comp_remove": _comp_remove, "transforms": _transforms, "table": _table, "layout": _layout,
    "new_table": _new_table, "new_layout": _new_layout, "text": _text_item, "rect": _rect_item,
    "ellipse": _ellipse_item, "line": _line_item, "front": _front, "back": _back, "grid": _grid,
    "backgate": _backgate, "formula": _formula, "remove": _remove, "run": _run, "copy": _copy,
    "sample": _sample, "gate": _gate,
})
ICON_NAMES = tuple(_DRAW)
