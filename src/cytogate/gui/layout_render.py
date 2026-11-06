"""Drawing layout items and pages with QPainter (screen, PDF, SVG and images alike).

All coordinates are points; the painter is expected to map points to the device.
"""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui

from ..core.layout import Layout, LayoutItem, paginate, resolve_text
from ..core.transforms import axis_ticks
from ..plots.render import HistogramY, PlotType, bin2d, histogram, render_2d
from .plot_view import gate_outline

__all__ = ["paint_item", "paint_page", "page_count", "item_image", "page_image"]

AXIS_FONT = 6.5
TITLE_FONT = 7.0


def _font(size: float, bold: bool = False) -> QtGui.QFont:
    f = QtGui.QFont("Arial")
    f.setPointSizeF(max(size, 1.0))
    f.setBold(bold)
    return f


def _qcolor(c, alpha=255) -> QtGui.QColor:
    return QtGui.QColor(int(c[0]), int(c[1]), int(c[2]), alpha)


def _text_px(p: QtGui.QPainter, size: float, bold=False) -> None:
    # Fonts are given in points; the painter works in points too, so use pixel size = points.
    f = QtGui.QFont("Arial")
    f.setPixelSize(max(int(round(size)), 1))
    f.setBold(bold)
    p.setFont(f)


# --- graphs ----------------------------------------------------------------------------

def _to_qimage(rgba: np.ndarray) -> QtGui.QImage:
    """render_2d's image (indexed [x, y], y up) as a QImage (rows top-down)."""
    arr = np.ascontiguousarray(rgba.transpose(1, 0, 2)[::-1])
    img = QtGui.QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4, QtGui.QImage.Format.Format_RGBA8888)
    return img.copy()


def _axis(p, plot: QtCore.QRectF, xf, label: str, vertical: bool, scale: float) -> None:
    _text_px(p, AXIS_FONT * scale)
    fm = p.fontMetrics()
    tick = 3 * scale
    for t in axis_ticks(xf):
        length = tick if t.major else tick * 0.6
        if vertical:
            yy = plot.bottom() - t.position * plot.height()
            p.drawLine(QtCore.QPointF(plot.left() - length, yy), QtCore.QPointF(plot.left(), yy))
            if t.label:
                w = fm.horizontalAdvance(t.label)
                p.drawText(QtCore.QPointF(plot.left() - tick - 2 * scale - w, yy + fm.ascent() / 2.5), t.label)
        else:
            xx = plot.left() + t.position * plot.width()
            p.drawLine(QtCore.QPointF(xx, plot.bottom()), QtCore.QPointF(xx, plot.bottom() + length))
            if t.label:
                w = fm.horizontalAdvance(t.label)
                p.drawText(QtCore.QPointF(xx - w / 2, plot.bottom() + tick + fm.ascent() + scale), t.label)
    _label(p, plot, label, vertical, scale)


def _label(p, plot, label, vertical, scale) -> None:
    _text_px(p, AXIS_FONT * 1.1 * scale)
    fm = p.fontMetrics()
    w = fm.horizontalAdvance(label)
    if vertical:
        p.save()
        p.translate(plot.left() - 30 * scale, plot.center().y() + w / 2)
        p.rotate(-90)
        p.drawText(QtCore.QPointF(0, 0), label)
        p.restore()
    else:
        p.drawText(QtCore.QPointF(plot.center().x() - w / 2, plot.bottom() + 22 * scale), label)


def _linear_axis(p, plot, top: float, label: str, scale: float) -> None:
    _text_px(p, AXIS_FONT * scale)
    fm = p.fontMetrics()
    if top <= 0:
        top = 1.0
    raw = top / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    v = 0.0
    while v <= top + 1e-9:
        yy = plot.bottom() - v / top * plot.height()
        p.drawLine(QtCore.QPointF(plot.left() - 3 * scale, yy), QtCore.QPointF(plot.left(), yy))
        text = f"{v:g}" if v < 1000 else f"{v / 1000:g}K"
        w = fm.horizontalAdvance(text)
        p.drawText(QtCore.QPointF(plot.left() - 5 * scale - w, yy + fm.ascent() / 2.5), text)
        v += step
    _label(p, plot, label, True, scale)


def _gates(p, plot, a, item, top: float, scale: float) -> None:
    ps = a.ps
    try:
        children = a.gates.get(tuple(item.path)).children
    except KeyError:
        return
    for c in children:
        if c.gate is None or not c.gate.dims:
            continue
        try:
            pts = gate_outline(c.gate, ps, item.x_param, item.y_param)
        except KeyError:
            pts = None
        if pts is None:
            continue
        if item.y_param is None:
            pts = pts * np.array([1.0, 0.95])
        q = QtGui.QPolygonF([QtCore.QPointF(plot.left() + x * plot.width(), plot.bottom() - y * plot.height())
                             for x, y in np.clip(pts, -0.01, 1.01)])
        pen = QtGui.QPen(_qcolor(c.tint or (0, 0, 0)), 0.8 * scale)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawPolyline(q)
        freq = a.freq_of_parent(c.path)
        cx = float(np.clip((pts[:, 0].min() + pts[:, 0].max()) / 2, 0.05, 0.95)) + c.label_offset[0]
        cy = float(np.clip((pts[:, 1].min() + pts[:, 1].max()) / 2, 0.05, 0.95)) + c.label_offset[1]
        text = f"{c.name}\n{freq:.3g}"
        _text_px(p, AXIS_FONT * scale)
        fm = p.fontMetrics()
        lines = text.split("\n")
        w = max(fm.horizontalAdvance(t) for t in lines)
        hgt = fm.height() * len(lines)
        r = QtCore.QRectF(plot.left() + cx * plot.width() - w / 2, plot.bottom() - cy * plot.height() - hgt / 2, w, hgt)
        p.setPen(_qcolor(c.tint or (0, 0, 0)))
        p.drawText(r, int(QtCore.Qt.AlignmentFlag.AlignCenter), text)


def paint_graph(p: QtGui.QPainter, rect: QtCore.QRectF, ws, item: LayoutItem, sample: str | None,
                layout_name: str = "") -> None:
    scale = max(min(rect.width(), rect.height()) / 180.0, 0.4)
    p.save()
    p.setClipRect(rect.adjusted(-1, -1, 1, 1))
    p.fillRect(rect, QtGui.QColor("white"))
    a = ws.samples.get(sample) if sample else None
    path = tuple(item.path)
    if a is None or path not in a.gates.paths() and path != ():
        p.setPen(QtGui.QColor(150, 150, 150))
        p.drawRect(rect)
        _text_px(p, 8 * scale)
        p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), "no data" if a is None else f"{'/'.join(path)}\nnot in {sample}")
        p.restore()
        return
    title_h = 0.0
    if item.show_title:
        title = item.text or "{Name}\n{Population}\n{Count}"
        title = resolve_text(title, ws, sample, path, layout=layout_name)
        _text_px(p, TITLE_FONT * scale)
        fm = p.fontMetrics()
        title_h = fm.height() * (title.count("\n") + 1) + 2 * scale
        p.setPen(QtGui.QColor("black"))
        p.drawText(QtCore.QRectF(rect.left(), rect.top(), rect.width(), title_h),
                   int(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop), title)
    left, bottom = 40 * scale, 30 * scale
    plot = QtCore.QRectF(rect.left() + left, rect.top() + title_h + 4 * scale,
                         rect.width() - left - 6 * scale, rect.height() - title_h - bottom - 4 * scale)
    if plot.width() < 4 or plot.height() < 4:
        p.restore()
        return
    ps = a.ps
    try:
        xs_all = ps.scaled(item.x_param)
        xf = ps.transforms[ps.parameter(item.x_param).name]
        x_label = ps.parameter(item.x_param).label
    except KeyError:
        p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), f"no {item.x_param}")
        p.restore()
        return
    layers = [(path, sample, (0, 0, 0))] + [(tuple(l.path), l.sample or sample, l.color) for l in item.layers]

    def layer_mask(lpath, lsample):
        la = ws.samples.get(lsample)
        if la is None:
            return None, None
        try:
            return la, la.mask(lpath)
        except KeyError:
            return la, None

    p.setPen(QtGui.QPen(QtGui.QColor("black"), 0.8 * scale))
    if item.y_param is None or item.plot_type == PlotType.HISTOGRAM.value:
        mode = HistogramY(item.hist_y) if item.hist_y in [h.value for h in HistogramY] else HistogramY.MODE
        curves = []
        for lpath, lsample, color in layers:
            la, m = layer_mask(lpath, lsample)
            if m is None:
                continue
            _, h = histogram(la.ps.scaled(item.x_param)[m], 256, mode if len(layers) == 1 else HistogramY.MODE)
            curves.append((h, color))
        top = max((h.max() for h, _ in curves), default=1.0) * 1.05 or 1.0
        n = len(curves)
        for k, (h, color) in enumerate(curves):
            off = (k / max(n, 1)) * 0.5 if item.stagger and n > 1 else 0.0
            sc = (1 - 0.5) if item.stagger and n > 1 else 1.0
            pts = [QtCore.QPointF(plot.left(), plot.bottom() - off * plot.height())]
            for i, v in enumerate(h):
                xx = plot.left() + (i + 0.5) / len(h) * plot.width()
                pts.append(QtCore.QPointF(xx, plot.bottom() - (off + v / top * sc) * plot.height()))
            pts.append(QtCore.QPointF(plot.right(), plot.bottom() - off * plot.height()))
            poly = QtGui.QPolygonF(pts)
            p.setPen(QtGui.QPen(_qcolor(color), 1.0 * scale))
            p.setBrush(_qcolor(color, 60 if n > 1 else 0))
            p.drawPolygon(poly)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.setPen(QtGui.QPen(QtGui.QColor("black"), 0.8 * scale))
        _linear_axis(p, plot, 100.0 if (n > 1 or mode == HistogramY.MODE) else top,
                     "Normalized To Mode" if (n > 1 or mode == HistogramY.MODE) else "Count", scale)
        if item.show_gates and n <= 1:
            _gates(p, plot, a, item, top, scale)
    else:
        try:
            ys_all = ps.scaled(item.y_param)
            yf = ps.transforms[ps.parameter(item.y_param).name]
            y_label = ps.parameter(item.y_param).label
        except KeyError:
            p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), f"no {item.y_param}")
            p.restore()
            return
        m0 = a.mask(path)
        kind = PlotType(item.plot_type) if item.plot_type in [k.value for k in PlotType] else PlotType.PSEUDOCOLOR
        base_color = (170, 170, 170) if item.layers else (0, 0, 0)
        r = render_2d(xs_all[m0], ys_all[m0], kind if not item.layers else PlotType.DOT, res=256,
                      dot_color=base_color)
        img = _to_qimage(r.image)
        for lpath, lsample, color in layers[1:]:
            la, m = layer_mask(lpath, lsample)
            if m is None:
                continue
            occ = bin2d(la.ps.scaled(item.x_param)[m], la.ps.scaled(item.y_param)[m], 256) > 0
            over = np.zeros((256, 256, 4), dtype=np.uint8)
            over[occ] = (*color, 255)
            oimg = _to_qimage(over)
            pp = QtGui.QPainter(img)
            pp.drawImage(0, 0, oimg)
            pp.end()
        p.drawImage(plot, img)
        if kind == PlotType.CONTOUR and not item.layers and r.levels.size:
            p.setPen(QtGui.QPen(QtGui.QColor("black"), 0.6 * scale))
            for level in r.levels:
                for line in pg.functions.isocurve(r.density, level, connected=True, extendToEdge=True):
                    pts = np.asarray(line, dtype=float) / 256
                    p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(plot.left() + x * plot.width(),
                                                                   plot.bottom() - y * plot.height()) for x, y in pts]))
        p.setPen(QtGui.QPen(QtGui.QColor("black"), 0.8 * scale))
        _axis(p, plot, yf, y_label, True, scale)
        if item.show_gates:
            _gates(p, plot, a, item, 1.0, scale)
    p.setPen(QtGui.QPen(QtGui.QColor("black"), 0.8 * scale))
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    p.drawRect(plot)
    _axis(p, plot, xf, x_label, False, scale)
    if item.layers:  # legend
        _text_px(p, AXIS_FONT * scale)
        fm = p.fontMetrics()
        yy = plot.top() + fm.ascent() + 2 * scale
        entries = [(("/".join(path) or "Ungated").split("/")[-1], (0, 0, 0) if item.y_param is None else (150, 150, 150))]
        entries += [(l.label or (l.path[-1] if l.path else "Ungated") + (f" ({l.sample})" if l.sample else ""), l.color)
                    for l in item.layers]
        for text, color in entries:
            w = fm.horizontalAdvance(text)
            p.setPen(_qcolor(color))
            p.drawText(QtCore.QPointF(plot.right() - w - 3 * scale, yy), text)
            yy += fm.height()
    p.restore()


# --- other items ----------------------------------------------------------------------------

def paint_text(p, rect, text, item: LayoutItem) -> None:
    _text_px(p, item.font_size, item.bold)
    p.setPen(_qcolor(item.line_color))
    p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop
                         | QtCore.Qt.TextFlag.TextWordWrap), text)


def paint_shape(p, rect, item: LayoutItem) -> None:
    p.setPen(QtGui.QPen(_qcolor(item.line_color), item.line_width))
    p.setBrush(_qcolor(item.fill_color) if item.fill_color else QtCore.Qt.BrushStyle.NoBrush)
    if item.shape == "ellipse":
        p.drawEllipse(rect)
    elif item.shape == "line":
        p.drawLine(rect.topLeft(), rect.bottomRight())
    else:
        p.drawRect(rect)


def paint_table(p, rect, ws, item: LayoutItem, tables) -> None:
    from ..core.table import compute_table
    t = next((t for t in tables if t.name == item.table), None)
    if t is None:
        p.setPen(QtGui.QColor(150, 150, 150))
        p.drawRect(rect)
        p.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), f"table {item.table!r} not found")
        return
    df = compute_table(ws, t)
    cells = [["Name", *map(str, df.columns)]]
    for idx, row in df.iterrows():
        cells.append([str(idx), *[f"{v:.4g}" if isinstance(v, float) else str(v) for v in row]])
    _text_px(p, item.font_size)
    fm = p.fontMetrics()
    widths = [max(fm.horizontalAdvance(r[j]) for r in cells) + 6 for j in range(len(cells[0]))]
    total = sum(widths)
    f = min(rect.width() / total, 1.0) if total else 1.0
    if f < 1.0:  # shrink the font to fit the width
        _text_px(p, item.font_size * f)
        fm = p.fontMetrics()
        widths = [w * f for w in widths]
    rh = fm.height() + 2
    p.save()
    p.setClipRect(rect)
    y = rect.top()
    for i, r in enumerate(cells):
        x = rect.left()
        if i == 0:
            p.fillRect(QtCore.QRectF(x, y, sum(widths), rh), QtGui.QColor(225, 225, 225))
        for j, txt in enumerate(r):
            p.setPen(QtGui.QColor("black"))
            p.drawText(QtCore.QRectF(x + 3, y, widths[j] - 4, rh),
                       int(QtCore.Qt.AlignmentFlag.AlignVCenter | (QtCore.Qt.AlignmentFlag.AlignLeft if j == 0
                                                                    else QtCore.Qt.AlignmentFlag.AlignRight)), txt)
            x += widths[j]
        p.setPen(QtGui.QPen(QtGui.QColor(180, 180, 180), 0.5))
        p.drawLine(QtCore.QPointF(rect.left(), y + rh), QtCore.QPointF(rect.left() + sum(widths), y + rh))
        y += rh
    p.restore()


def paint_item(p, item: LayoutItem, ws, sample=None, dx=0.0, dy=0.0, tables=(), page=1, pages=1, layout_name=""):
    rect = QtCore.QRectF(item.x + dx, item.y + dy, item.w, item.h)
    if item.kind == "graph":
        paint_graph(p, rect, ws, item, sample, layout_name)
    elif item.kind == "text":
        paint_text(p, rect, resolve_text(item.text, ws, sample, (), page, pages, layout_name), item)
    elif item.kind == "shape":
        paint_shape(p, rect, item)
    elif item.kind == "table":
        paint_table(p, rect, ws, item, tables)


# --- pages --------------------------------------------------------------------------------------

def page_count(ws, lay: Layout) -> int:
    copies = paginate(ws, lay)
    return (max(c.page for c in copies) + 1) if copies else 1


def paint_page(p, ws, lay: Layout, page: int, tables=(), copies=None) -> None:
    """Draw one page (0-based) of the batched layout, header and footer included."""
    copies = paginate(ws, lay) if copies is None else copies
    pages = (max(c.page for c in copies) + 1) if copies else 1
    w, h = lay.size
    p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QColor("white"))
    for c in copies:
        if c.page != page:
            continue
        for i, it in enumerate(lay.items):
            paint_item(p, it, ws, c.samples.get(i), c.dx, c.dy, tables, page + 1, pages, lay.name)
    first = next((c for c in copies if c.page == page), None)
    sample = first.label if first and lay.iterate == "sample" else None
    for text, top in ((lay.header, True), (lay.footer, False)):
        if not text:
            continue
        t = resolve_text(text, ws, sample, (), page + 1, pages, lay.name)
        _text_px(p, 8)
        p.setPen(QtGui.QColor(60, 60, 60))
        r = QtCore.QRectF(lay.margin, 8 if top else h - lay.margin + 8, w - 2 * lay.margin, lay.margin - 14)
        p.drawText(r, int(QtCore.Qt.AlignmentFlag.AlignHCenter
                          | (QtCore.Qt.AlignmentFlag.AlignBottom if top else QtCore.Qt.AlignmentFlag.AlignTop)), t)


def page_image(ws, lay: Layout, page: int, dpi: float = 150, tables=(), copies=None) -> QtGui.QImage:
    w, h = lay.size
    k = dpi / 72.0
    img = QtGui.QImage(int(w * k), int(h * k), QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtGui.QColor("white"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
    p.scale(k, k)
    paint_page(p, ws, lay, page, tables, copies)
    p.end()
    return img


def item_image(item: LayoutItem, ws, sample=None, dpi: float = 150, tables=(), transparent=False, page=1, pages=1,
               layout_name="") -> QtGui.QImage:
    k = dpi / 72.0
    img = QtGui.QImage(max(int(item.w * k), 1), max(int(item.h * k), 1), QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtGui.QColor(0, 0, 0, 0) if transparent else QtGui.QColor("white"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.scale(k, k)
    paint_item(p, item, ws, sample, -item.x, -item.y, tables, page, pages, layout_name)
    p.end()
    return img
