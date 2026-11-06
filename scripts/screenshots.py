"""Render example graphs and windows to docs/images/ (works headless).

Usage: QT_QPA_PLATFORM=offscreen python scripts/screenshots.py
Needs the test data (scripts/fetch_test_data.py).
"""

from pathlib import Path

from PySide6 import QtGui, QtWidgets

from cytogate.core import ProcessedSample
from cytogate.core.stats import Stat, StatSpec
from cytogate.gui.graph_window import GraphWindow
from cytogate.gui.main_window import MainWindow
from cytogate.gui.plot_view import PlotView
from cytogate.io import load_fcs
from cytogate.plots import HistogramY, PlotType

ROOT = Path(__file__).resolve().parent.parent
FCS = ROOT / "tests/data/8_color_data_set/fcs_files/101_DEN084Y5_15_E01_008_clean.fcs"
OUT = ROOT / "docs/images"
E01 = FCS.name
E05 = "101_DEN084Y5_15_E05_010_clean.fcs"


def plot_types(app) -> None:
    ps = ProcessedSample(load_fcs(FCS))
    view = PlotView()
    view.resize(520, 560)
    for name, x, y, kind in [
        ("dot", "FSC-A", "SSC-A", PlotType.DOT),
        ("pseudocolor", "FSC-A", "SSC-A", PlotType.PSEUDOCOLOR),
        ("contour", "FSC-A", "SSC-A", PlotType.CONTOUR),
        ("density", "FSC-A", "SSC-A", PlotType.DENSITY),
        ("pseudocolor_cd4_cd8", "Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A", PlotType.PSEUDOCOLOR),
        ("histogram_cd3", "Comp-CD3 APC-H7 FLR-A", None, PlotType.HISTOGRAM),
    ]:
        view.show_sample(ps, x, y, kind, HistogramY.COUNT)
        app.processEvents()
        view.export_png(str(OUT / f"{name}.png"))
        print("saved", name)


def side_by_side(app, widgets, name: str) -> None:
    """One image of several windows next to each other."""
    app.processEvents()
    shots = [w.grab() for w in widgets]
    width = sum(s.width() for s in shots) + 12 * (len(shots) - 1)
    canvas = QtGui.QPixmap(width, max(s.height() for s in shots))
    canvas.fill(QtGui.QColor("#d8d8d8"))
    p = QtGui.QPainter(canvas)
    x = 0
    for s in shots:
        p.drawPixmap(x, 0, s)
        x += s.width() + 12
    p.end()
    canvas.save(str(OUT / f"{name}.png"))
    print("saved", name)


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    plot_types(app)

    win = MainWindow()
    win.resize(520, 700)
    win.show()
    win.add_samples([FCS.parent])
    names = iter(["Lymphocytes", "CD3+"])
    GraphWindow.ask_gate_name = lambda self, default: next(names)

    g1 = win.open_graph(E01)  # Lymphocytes on FSC x SSC
    g1.resize(500, 600)
    g1.shape_drawn("Polygon", [(0.3, 0.06), (0.62, 0.06), (0.8, 0.13), (0.62, 0.3), (0.35, 0.3), (0.27, 0.16)])
    g2 = win.open_graph(E01, ("Lymphocytes",))  # CD3+ inside lymphocytes
    g2.resize(500, 600)
    g2.set_axes("Comp-CD3 APC-H7 FLR-A", "SSC-A")
    ps = win.samples[E01].ps
    pos = lambda n, v: float(ps.transforms[n].apply([v])[0])  # noqa: E731
    g2.shape_drawn("Rectangle", [(pos("Comp-CD3 APC-H7 FLR-A", 600), 0.0), (1.0, 0.4)])
    g3 = win.open_graph(E01, ("Lymphocytes", "CD3+"))  # CD4 / CD8 quadrants inside CD3+
    g3.resize(500, 600)
    g3.set_axes("Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A")
    g3.shape_drawn("Quadrant", [(pos("Comp-CD4 PE-Cy7 FLR-A", 1000), pos("Comp-CD8 PerCP-Cy55 FLR-A", 2000))])
    a = win.samples[E01]
    for spec in (StatSpec(Stat.MEDIAN, "Comp-CD4 PE-Cy7 FLR-A"), StatSpec(Stat.GEOMEAN, "Comp-CD8 PerCP-Cy55 FLR-A")):
        a.add_stat(("Lymphocytes", "CD3+"), spec)
    a.gates.get(("Lymphocytes", "CD3+")).label_offset = (0.05, 0.3)
    win.data_changed([E01])
    side_by_side(app, [win, g2, g3], "gating")
    g3.grab().save(str(OUT / "gating_quadrants.png"))

    # copy the whole tree to every sample (as dropping it on "All Samples"), then ▶ ▶ to E05
    win.drop_on_group([(E01, ("Lymphocytes",))], "All Samples")
    g3.step_sample(+1)
    g3.step_sample(+1)
    side_by_side(app, [win, g3], "groups")

    # axis transform window from the "T" button
    ed = g3.open_transform_editor("x")
    ed._edit(width=-100.0)
    app.processEvents()
    ed.grab().save(str(OUT / "transform_window.png"))
    print("saved transform_window")
    ed.reject()


if __name__ == "__main__":
    main()
