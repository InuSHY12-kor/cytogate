"""Layouts: pages of graphs, text, shapes and tables (FlowJo's Layout Editor).

Pure data and batching; drawing lives in gui.layout_render.

Coordinates are points (1/72 inch) from the top-left of the page.

Graph items show a population of a sample on one or two parameters. A graph
whose `sample` is None follows the batch: when the layout is batched over a
group, every copy shows the next sample (FlowJo's "Batch"). Extra `layers`
are overlays — other populations (or the same population of other samples)
drawn on top in their own colour; backgating is an overlay of a descendant on
an ancestor's plot.

Batch directions:
- "pages":   one copy of the layout per page;
- "columns": copies side by side, wrapping onto new pages;
- "rows":    copies one below the other, wrapping onto new pages.
Iterating by keyword makes one copy per keyword value; then a graph with
`discriminator` set shows the sample of that value whose discriminator
keyword matches (like the Table Editor).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["Layer", "LayoutItem", "Layout", "PAGE_SIZES", "BatchCopy", "paginate", "resolve_text",
           "layout_to_json", "layout_from_json", "presets_dir", "list_presets", "save_preset", "load_preset",
           "backgate", "arrange_grid", "snap", "OVERLAY_COLORS"]

PAGE_SIZES = {"Letter": (612.0, 792.0), "A4": (595.0, 842.0), "Legal": (612.0, 1008.0), "A3": (842.0, 1191.0)}
OVERLAY_COLORS = [(220, 30, 30), (30, 90, 220), (20, 160, 60), (230, 140, 0), (150, 40, 180), (0, 160, 170)]


@dataclass
class Layer:
    path: tuple[str, ...] = ()
    sample: str | None = None  # None = the graph's sample
    color: tuple = (220, 30, 30)
    label: str = ""


@dataclass
class LayoutItem:
    kind: str = "graph"  # "graph" | "text" | "shape" | "table"
    x: float = 72.0
    y: float = 72.0
    w: float = 180.0
    h: float = 180.0
    # graph
    sample: str | None = None  # None = follows the batch
    path: tuple[str, ...] = ()
    x_param: str = ""
    y_param: str | None = None  # None = histogram
    plot_type: str = "Pseudocolor"
    hist_y: str = "Normalized To Mode"
    layers: list[Layer] = field(default_factory=list)
    show_gates: bool = True
    show_title: bool = True
    stagger: bool = False  # histogram overlays offset upwards
    discriminator: str = ""
    # text (graph titles and header/footer use the same {tokens})
    text: str = ""
    font_size: float = 10.0
    bold: bool = False
    # shape
    shape: str = "rect"  # "rect" | "ellipse" | "line"
    line_color: tuple = (0, 0, 0)
    fill_color: tuple | None = None
    line_width: float = 1.0
    # table
    table: str = ""

    def bounds(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.x + self.w, self.y + self.h


@dataclass
class Layout:
    name: str = "Layout"
    page: str = "Letter"
    landscape: bool = False
    items: list[LayoutItem] = field(default_factory=list)
    header: str = ""
    footer: str = "{Layout} — page {Page} of {Pages}"
    margin: float = 36.0
    grid: float = 18.0
    show_grid: bool = True
    snap: bool = True
    # batch
    group: str = "All Samples"
    iterate: str = "none"  # "none" | "sample" | "keyword"
    iterate_keyword: str = ""
    discriminator_keyword: str = ""
    direction: str = "pages"  # "pages" | "columns" | "rows"
    spacing: float = 18.0

    @property
    def size(self) -> tuple[float, float]:
        w, h = PAGE_SIZES.get(self.page, PAGE_SIZES["Letter"])
        return (h, w) if self.landscape else (w, h)

    def content_bounds(self) -> tuple[float, float, float, float]:
        if not self.items:
            return 0.0, 0.0, 0.0, 0.0
        b = [i.bounds() for i in self.items]
        return min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)


# --- batching ----------------------------------------------------------------------------

@dataclass
class BatchCopy:
    """One copy of the layout: which sample each item shows and where it goes."""
    page: int
    dx: float
    dy: float
    samples: dict[int, str | None]  # item index -> sample (None: nothing to show)
    label: str = ""  # sample name or keyword value of this copy


def _iterations(ws, lay: Layout) -> list[tuple[str, dict[int, str | None]]]:
    n = len(lay.items)
    if lay.iterate == "none" or not ws.samples:
        first = next(iter(ws.samples), None)
        return [("", {i: lay.items[i].sample or first for i in range(n)})]
    samples = list(ws.groups[lay.group].samples) if lay.group in ws.groups else list(ws.samples)
    if lay.iterate == "keyword" and lay.iterate_keyword:
        groups: dict[str, list[str]] = {}
        for s in samples:
            groups.setdefault(ws.samples[s].keyword(lay.iterate_keyword, ""), []).append(s)
        out = []
        for value, members in groups.items():
            pick = {}
            for i, it in enumerate(lay.items):
                if it.sample is not None:
                    pick[i] = it.sample
                    continue
                m = [s for s in members if not it.discriminator
                     or ws.samples[s].keyword(lay.discriminator_keyword, "") == it.discriminator]
                pick[i] = m[0] if m else None
            out.append((value, pick))
        return out
    return [(s, {i: lay.items[i].sample or s for i in range(n)}) for s in samples]


def paginate(ws, lay: Layout) -> list[BatchCopy]:
    """Where every copy of the layout goes (pages counted from 0)."""
    its = _iterations(ws, lay)
    if lay.iterate == "none" or lay.direction == "pages":
        return [BatchCopy(k, 0.0, 0.0, pick, label) for k, (label, pick) in enumerate(its)]
    x0, y0, x1, y1 = lay.content_bounds()
    pw, ph = lay.size
    cw, ch = x1 - x0 + lay.spacing, y1 - y0 + lay.spacing
    m = lay.margin
    if lay.direction == "columns":
        per_line = max(int((pw - 2 * m - (x1 - x0)) // cw) + 1, 1) if cw > 0 else 1
        lines = max(int((ph - 2 * m - (y1 - y0)) // ch) + 1, 1) if ch > 0 else 1
    else:  # rows
        per_line = max(int((ph - 2 * m - (y1 - y0)) // ch) + 1, 1) if ch > 0 else 1
        lines = max(int((pw - 2 * m - (x1 - x0)) // cw) + 1, 1) if cw > 0 else 1
    per_page = per_line * lines
    out = []
    for k, (label, pick) in enumerate(its):
        page, slot = divmod(k, per_page)
        line, pos = divmod(slot, per_line)
        if lay.direction == "columns":
            dx, dy = pos * cw, line * ch
        else:
            dx, dy = line * cw, pos * ch
        out.append(BatchCopy(page, dx, dy, pick, label))
    return out


# --- text tokens --------------------------------------------------------------------------------

def resolve_text(text: str, ws=None, sample: str | None = None, path=(), page: int = 1, pages: int = 1,
                 layout: str = "") -> str:
    """Replace {tokens}: {Name}/{Sample}, {Population}, {Count}, {Page}, {Pages},
    {Date}, {Layout}, and any keyword, e.g. {$FIL} or {Donor}."""
    a = ws.samples.get(sample) if ws is not None and sample else None

    def sub(m):
        key = m.group(1)
        if key in ("Name", "Sample"):
            return sample or ""
        if key == "Population":
            return "/".join(path) if path else "Ungated"
        if key == "Count":
            try:
                return f"{a.count(tuple(path)):,}" if a else ""
            except KeyError:
                return ""
        if key == "Page":
            return str(page)
        if key == "Pages":
            return str(pages)
        if key == "Date":
            return _dt.date.today().isoformat()
        if key == "Layout":
            return layout
        return a.keyword(key, "") if a else ""

    return re.sub(r"\{([^{}]+)\}", sub, text)


# --- editing helpers ----------------------------------------------------------------------------

def snap(v: float, grid: float) -> float:
    return round(v / grid) * grid if grid > 0 else v


def backgate(item: LayoutItem, ancestor=(), color=(220, 30, 30)) -> LayoutItem:
    """A graph of `ancestor` with the item's population highlighted on top."""
    child = tuple(item.path)
    g = LayoutItem(**{**asdict(item), "layers": []})
    g.path = tuple(ancestor)
    g.layers = [Layer(child, None, tuple(color), child[-1] if child else "Ungated")]
    g.x, g.y = item.x + item.w + 18, item.y
    return g


def arrange_grid(items: list[LayoutItem], columns: int, x0: float, y0: float, spacing: float = 18.0) -> None:
    """Line items up in rows of `columns`, each cell as large as the biggest item."""
    if not items:
        return
    cw = max(i.w for i in items) + spacing
    ch = max(i.h for i in items) + spacing
    for k, it in enumerate(items):
        r, c = divmod(k, max(columns, 1))
        it.x, it.y = x0 + c * cw, y0 + r * ch


# --- saving -----------------------------------------------------------------------------------

def _item_to_json(i: LayoutItem) -> dict:
    d = asdict(i)
    d["path"] = list(i.path)
    d["layers"] = [{**asdict(l), "path": list(l.path), "color": list(l.color)} for l in i.layers]
    return d


def _item_from_json(d: dict) -> LayoutItem:
    d = dict(d)
    layers = [Layer(tuple(l.get("path", ())), l.get("sample"), tuple(l.get("color", (220, 30, 30))), l.get("label", ""))
              for l in d.pop("layers", [])]
    for k in ("line_color",):
        if k in d and d[k] is not None:
            d[k] = tuple(d[k])
    if d.get("fill_color") is not None:
        d["fill_color"] = tuple(d["fill_color"])
    d["path"] = tuple(d.get("path", ()))
    known = LayoutItem.__dataclass_fields__
    return LayoutItem(layers=layers, **{k: v for k, v in d.items() if k in known})


def layout_to_json(lay: Layout) -> dict:
    d = asdict(lay)
    d["items"] = [_item_to_json(i) for i in lay.items]
    return d


def layout_from_json(d: dict) -> Layout:
    d = dict(d)
    items = [_item_from_json(i) for i in d.pop("items", [])]
    known = Layout.__dataclass_fields__
    return Layout(items=items, **{k: v for k, v in d.items() if k in known})


# --- figure presets (F4) ---------------------------------------------------------------------------

def presets_dir() -> Path:
    base = os.environ.get("CYTOGATE_HOME") or Path.home() / ".cytogate"
    return Path(base) / "layout_presets"


def _preset_file(name: str) -> Path:
    return presets_dir() / (re.sub(r'[\\/:*?"<>|]+', "_", name).strip() + ".json")


def list_presets() -> list[str]:
    d = presets_dir()
    return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []


def save_preset(name: str, lay: Layout) -> Path:
    """Keep a layout as a reusable figure template."""
    f = _preset_file(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(layout_to_json(lay), indent=1))
    return f


def load_preset(name: str) -> Layout:
    return layout_from_json(json.loads(_preset_file(name).read_text()))
