"""Batch output of layouts: PDF, PNG/JPG/TIFF pages, SVG, PowerPoint and a web page.

PowerPoint: one slide per page, the slide the size of the page; graphs and
tables are pictures and text items are text boxes, so they can be moved and
edited there. Web page: one HTML file with every page as an embedded picture.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

from PySide6 import QtCore, QtGui

from ..core.layout import Layout, paginate, resolve_text
from .layout_render import item_image, page_image, paint_page

__all__ = ["export_layout", "FORMATS"]

FORMATS = {"pdf": "PDF document (*.pdf)", "png": "PNG images (*.png)", "jpg": "JPEG images (*.jpg)",
           "tif": "TIFF images (*.tif)", "svg": "SVG vector images (*.svg)", "pptx": "PowerPoint (*.pptx)",
           "html": "Web page (*.html)"}


def _numbered(path: Path, page: int, pages: int) -> Path:
    return path if pages == 1 else path.with_name(f"{path.stem}_{page + 1:03d}{path.suffix}")


def export_layout(ws, lay: Layout, path, fmt: str | None = None, tables=(), dpi: float = 150) -> list[Path]:
    """Write the batched layout; returns the files written."""
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    fmt = {"jpeg": "jpg", "tiff": "tif", "htm": "html", "ppt": "pptx"}.get(fmt, fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    copies = paginate(ws, lay)
    pages = (max(c.page for c in copies) + 1) if copies else 1
    w, h = lay.size
    if fmt == "pdf":
        writer = QtGui.QPdfWriter(str(path))
        writer.setPageSize(QtGui.QPageSize(QtCore.QSizeF(w, h), QtGui.QPageSize.Unit.Point))
        writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
        writer.setResolution(300)
        writer.setTitle(lay.name)
        p = QtGui.QPainter(writer)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.scale(300 / 72.0, 300 / 72.0)
        for k in range(pages):
            if k:
                writer.newPage()
            paint_page(p, ws, lay, k, tables, copies)
        p.end()
        return [path]
    if fmt in ("png", "jpg", "tif", "bmp"):
        out = []
        for k in range(pages):
            target = _numbered(path, k, pages)
            page_image(ws, lay, k, dpi, tables, copies).save(str(target))
            out.append(target)
        return out
    if fmt == "svg":
        from PySide6 import QtSvg
        out = []
        for k in range(pages):
            target = _numbered(path, k, pages)
            gen = QtSvg.QSvgGenerator()
            gen.setFileName(str(target))
            gen.setSize(QtCore.QSize(int(w), int(h)))
            gen.setViewBox(QtCore.QRectF(0, 0, w, h))
            gen.setTitle(lay.name)
            p = QtGui.QPainter(gen)
            paint_page(p, ws, lay, k, tables, copies)
            p.end()
            out.append(target)
        return out
    if fmt == "pptx":
        return [_pptx(ws, lay, path, tables, copies, pages, dpi)]
    if fmt == "html":
        parts = []
        for k in range(pages):
            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            page_image(ws, lay, k, dpi, tables, copies).save(buf, "PNG")
            data = base64.b64encode(bytes(buf.data())).decode()
            parts.append(f'<figure><img alt="page {k + 1}" src="data:image/png;base64,{data}"'
                         f' style="width:{w:.0f}pt;max-width:100%"><figcaption>Page {k + 1} of {pages}</figcaption></figure>')
        title = html.escape(lay.name)
        path.write_text("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
                        "content='width=device-width,initial-scale=1'>"
                        f"<title>{title}</title><style>body{{font-family:sans-serif;background:#eee;margin:16px}}"
                        "figure{background:#fff;margin:0 auto 24px;padding:8px;width:fit-content;max-width:100%;"
                        "box-shadow:0 1px 4px #0003}img{display:block}figcaption{color:#666;font-size:12px}</style>"
                        f"</head><body><h1>{title}</h1>{''.join(parts)}</body></html>")
        return [path]
    raise ValueError(f"unknown layout format {fmt!r}")


def _pptx(ws, lay, path, tables, copies, pages, dpi) -> Path:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Pt
    import io

    prs = Presentation()
    w, h = lay.size
    prs.slide_width, prs.slide_height = Pt(w), Pt(h)
    blank = prs.slide_layouts[6]
    for k in range(pages):
        slide = prs.slides.add_slide(blank)
        for c in copies:
            if c.page != k:
                continue
            for i, it in enumerate(lay.items):
                x, y = it.x + c.dx, it.y + c.dy
                if it.kind == "text":
                    box = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(it.w), Pt(it.h))
                    tf = box.text_frame
                    tf.word_wrap = True
                    text = resolve_text(it.text, ws, c.samples.get(i), (), k + 1, pages, lay.name)
                    for n, line in enumerate(text.split("\n")):
                        para = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
                        run = para.add_run()
                        run.text = line
                        run.font.size = Pt(it.font_size)
                        run.font.bold = it.bold
                        run.font.color.rgb = RGBColor(*[int(v) for v in it.line_color])
                    continue
                img = item_image(it, ws, c.samples.get(i), dpi, tables, transparent=it.kind == "shape",
                                 page=k + 1, pages=pages, layout_name=lay.name)
                buf = QtCore.QBuffer()
                buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
                img.save(buf, "PNG")
                slide.shapes.add_picture(io.BytesIO(bytes(buf.data())), Pt(x), Pt(y), Pt(it.w), Pt(it.h))
        sample = next((c.label for c in copies if c.page == k), None) if lay.iterate == "sample" else None
        for text, top in ((lay.header, True), (lay.footer, False)):
            if text:
                box = slide.shapes.add_textbox(Pt(lay.margin), Pt(6 if top else h - lay.margin + 6),
                                               Pt(w - 2 * lay.margin), Pt(lay.margin - 12))
                run = box.text_frame.paragraphs[0].add_run()
                run.text = resolve_text(text, ws, sample, (), k + 1, pages, lay.name)
                run.font.size = Pt(8)
    prs.save(str(path))
    return path
