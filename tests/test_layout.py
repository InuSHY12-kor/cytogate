"""F1–F4 core: layout model, batching, tokens, presets, rendering and batch output."""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from cytogate.core import ProcessedSample, RectangleGate  # noqa: E402
from cytogate.core.analysis import AnalyzedSample  # noqa: E402
from cytogate.core.layout import (Layer, Layout, LayoutItem, arrange_grid, backgate, layout_from_json,  # noqa: E402
                                  layout_to_json, list_presets, load_preset, paginate, resolve_text, save_preset)
from cytogate.core.table import TableDef  # noqa: E402
from cytogate.core.workspace import Workspace  # noqa: E402
from cytogate.io import load_fcs  # noqa: E402

E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def ws(data_dir):
    w = Workspace()
    for n in (E01, E03, E05):
        a = AnalyzedSample(ProcessedSample(load_fcs(data_dir / "8_color_data_set/fcs_files" / n)))
        a.add_gate((), "Cells", RectangleGate(("FSC-A", "SSC-A"), (50000.0, 0.0), (None, 150000.0)))
        a.add_gate("Cells", "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
        w.add_sample(a)
    w.set_keyword([E01, E03], "Donor", "D1")
    w.set_keyword([E05], "Donor", "D2")
    w.set_keyword([E01, E05], "Tube", "A")
    w.set_keyword([E03], "Tube", "B")
    return w


def graph(**kw):
    return LayoutItem(**{"kind": "graph", "x": 36, "y": 60, "w": 200, "h": 200, "x_param": "FSC-A",
                         "y_param": "SSC-A", **kw})


def test_batching(ws):
    lay = Layout(items=[graph(), LayoutItem(kind="text", text="{Name}", y=270, h=20)], iterate="sample")
    copies = paginate(ws, lay)
    assert [c.page for c in copies] == [0, 1, 2] and [c.samples[0] for c in copies] == [E01, E03, E05]
    lay.direction = "columns"  # 200pt wide copies fit twice on a Letter page
    copies = paginate(ws, lay)
    assert [(c.page, c.dx > 0, c.dy > 0) for c in copies] == [(0, False, False), (0, True, False), (0, False, True)]
    lay.direction = "rows"
    copies = paginate(ws, lay)
    # two copies fit down the page; the third starts the next column
    assert [(c.page, c.dx > 0, c.dy > 0) for c in copies] == [(0, False, False), (0, False, True), (0, True, False)]
    # a graph pinned to one sample stays on it
    lay.items[0].sample = E05
    assert {c.samples[0] for c in paginate(ws, lay)} == {E05}
    # keyword iteration with a discriminator
    k = Layout(items=[graph(discriminator="A"), graph(discriminator="B", x=260)], iterate="keyword",
               iterate_keyword="Donor", discriminator_keyword="Tube")
    copies = paginate(ws, k)
    assert [c.label for c in copies] == ["D1", "D2"]
    assert copies[0].samples == {0: E01, 1: E03} and copies[1].samples == {0: E05, 1: None}
    # no batch: every graph on the first sample
    assert paginate(ws, Layout(items=[graph()]))[0].samples == {0: E01}


def test_tokens_backgate_grid(ws):
    t = resolve_text("{Name} {Population} {Count} {Donor} p{Page}/{Pages}", ws, E05, ("Cells",), 2, 3)
    assert t == f"{E05} Cells {ws.samples[E05].count(('Cells',)):,} D2 p2/3"
    g = graph(path=("Cells", "CD3+"))
    b = backgate(g, ())
    assert b.path == () and b.layers[0].path == ("Cells", "CD3+") and b.x > g.x
    items = [graph() for _ in range(5)]
    arrange_grid(items, 2, 0, 0, 10)
    assert [(i.x, i.y) for i in items] == [(0, 0), (210, 0), (0, 210), (210, 210), (0, 420)]


def test_json_and_presets(tmp_path, monkeypatch):
    monkeypatch.setenv("CYTOGATE_HOME", str(tmp_path))
    lay = Layout(name="Fig 1", landscape=True, header="{Date}",
                 items=[graph(layers=[Layer(("A",), "s", (1, 2, 3), "x")]),
                        LayoutItem(kind="shape", shape="ellipse", fill_color=(9, 9, 9))])
    assert layout_from_json(layout_to_json(lay)) == lay
    save_preset("My figure", lay)
    assert list_presets() == ["My figure"] and load_preset("My figure") == lay
    assert lay.size == (792.0, 612.0)


def test_render_and_output(app, ws, tmp_path):
    from PySide6 import QtGui

    from cytogate.gui.layout_output import export_layout
    from cytogate.gui.layout_render import item_image, page_image

    t = TableDef(name="T1")
    t.add_population(("Cells", "CD3+"))
    lay = Layout(name="Report", iterate="sample", header="{Name}", items=[
        graph(path=("Cells",)),
        graph(path=("Cells",), x=260, x_param="Comp-CD3 APC-H7 FLR-A", y_param=None,
              layers=[Layer(("Cells", "CD3+"), None, (220, 30, 30))], stagger=True),
        LayoutItem(kind="text", text="Donor {Donor}", y=300, h=20, font_size=12, bold=True),
        LayoutItem(kind="shape", y=330, h=40, fill_color=(200, 220, 255)),
        LayoutItem(kind="table", table="T1", y=400, w=500, h=120, font_size=8),
    ])
    img = item_image(lay.items[0], ws, E01, dpi=72)
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.width(), 4)
    assert (arr[..., :3] < 200).any() and (arr[..., :3] > 250).any()  # something drawn on white
    bg = backgate(graph(path=("Cells", "CD3+"), y_param="SSC-A"), ())
    red = item_image(bg, ws, E01, dpi=72)
    a = np.frombuffer(red.constBits(), np.uint8).reshape(red.height(), red.width(), 4)
    assert ((a[..., 2] > 180) & (a[..., 1] < 60) & (a[..., 0] < 60)).any()  # BGRA: red overlay dots
    assert page_image(ws, lay, 0, dpi=50, tables=[t]).width() == int(612 * 50 / 72)
    files = {}
    for ext in ("pdf", "png", "svg", "pptx", "html"):
        files[ext] = export_layout(ws, lay, tmp_path / f"r.{ext}", tables=[t], dpi=60)
    assert len(files["pdf"]) == 1 and files["pdf"][0].read_bytes()[:4] == b"%PDF"
    assert files["pdf"][0].read_bytes().count(b"/Type /Page\n") + files["pdf"][0].read_bytes().count(b"/Type /Page ") >= 1
    assert [p.name for p in files["png"]] == ["r_001.png", "r_002.png", "r_003.png"]
    assert QtGui.QImage(str(files["png"][1])).width() == int(612 * 60 / 72)
    assert len(files["svg"]) == 3 and b"<svg" in files["svg"][0].read_bytes()
    from pptx import Presentation
    prs = Presentation(str(files["pptx"][0]))
    assert len(prs.slides) == 3
    texts = [s.text_frame.text for s in prs.slides[2].shapes if s.has_text_frame]
    assert "Donor D2" in texts and E05 in texts
    assert files["html"][0].read_text().count("<img") == 3
