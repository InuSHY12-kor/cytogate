"""Render example graphs and the main window to docs/images/ (works headless).

Usage: QT_QPA_PLATFORM=offscreen python scripts/screenshots.py
Needs the test data (scripts/fetch_test_data.py).
"""

from pathlib import Path

from PySide6 import QtWidgets

from cytogate.core import ProcessedSample
from cytogate.gui.main_window import MainWindow
from cytogate.gui.plot_view import PlotView
from cytogate.io import load_fcs
from cytogate.plots import HistogramY, PlotType

ROOT = Path(__file__).resolve().parent.parent
FCS = ROOT / "tests/data/8_color_data_set/fcs_files/101_DEN084Y5_15_E01_008_clean.fcs"
OUT = ROOT / "docs/images"


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    ps = ProcessedSample(load_fcs(FCS))
    view = PlotView()
    view.resize(520, 560)
    shots = [
        ("dot", "FSC-A", "SSC-A", PlotType.DOT),
        ("pseudocolor", "FSC-A", "SSC-A", PlotType.PSEUDOCOLOR),
        ("contour", "FSC-A", "SSC-A", PlotType.CONTOUR),
        ("density", "FSC-A", "SSC-A", PlotType.DENSITY),
        ("pseudocolor_cd4_cd8", "Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A", PlotType.PSEUDOCOLOR),
        ("histogram_cd3", "Comp-CD3 APC-H7 FLR-A", None, PlotType.HISTOGRAM),
    ]
    for name, x, y, kind in shots:
        view.show_sample(ps, x, y, kind, HistogramY.COUNT)
        app.processEvents()
        view.export_png(str(OUT / f"{name}.png"))
        print("saved", name)

    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    win.add_samples([FCS.parent])
    win.x_box.setCurrentIndex(win.x_box.findData("Comp-CD4 PE-Cy7 FLR-A"))
    win.y_box.setCurrentIndex(win.y_box.findData("Comp-CD8 PerCP-Cy55 FLR-A"))
    app.processEvents()
    win.grab().save(str(OUT / "main_window.png"))
    print("saved main_window")

    # gating example: lymphocytes -> CD3+ -> CD4/CD8 quadrants
    names = iter(["Lymphocytes", "CD3+"])
    win.ask_gate_name = lambda default: next(names)
    e01 = FCS.name
    win.select(e01)
    win.x_box.setCurrentIndex(win.x_box.findData("FSC-A"))
    win.y_box.setCurrentIndex(win.y_box.findData("SSC-A"))
    win.shape_drawn("Polygon", [(0.3, 0.06), (0.62, 0.06), (0.8, 0.13), (0.62, 0.3), (0.35, 0.3), (0.27, 0.16)])
    win.select(e01, ("Lymphocytes",))
    win.x_box.setCurrentIndex(win.x_box.findData("Comp-CD3 APC-H7 FLR-A"))
    win.y_box.setCurrentIndex(win.y_box.findData("SSC-A"))
    ps_e01 = win.samples[e01].ps
    pos = lambda name, v: float(ps_e01.transforms[name].apply([v])[0])  # noqa: E731
    win.shape_drawn("Rectangle", [(pos("Comp-CD3 APC-H7 FLR-A", 600), 0.0), (1.0, 0.4)])
    win.select(e01, ("Lymphocytes", "CD3+"))
    win.x_box.setCurrentIndex(win.x_box.findData("Comp-CD4 PE-Cy7 FLR-A"))
    win.y_box.setCurrentIndex(win.y_box.findData("Comp-CD8 PerCP-Cy55 FLR-A"))
    win.shape_drawn("Quadrant", [(pos("Comp-CD4 PE-Cy7 FLR-A", 1000), pos("Comp-CD8 PerCP-Cy55 FLR-A", 2000))])
    app.processEvents()
    win.grab().save(str(OUT / "gating_quadrants.png"))
    print("saved gating_quadrants")
    # statistics under CD3+ and a gate label dragged off the data
    from cytogate.core.stats import Stat, StatSpec
    a = win.samples[e01]
    for spec in (StatSpec(Stat.MEDIAN, "Comp-CD4 PE-Cy7 FLR-A"), StatSpec(Stat.GEOMEAN, "Comp-CD8 PerCP-Cy55 FLR-A"),
                 StatSpec(Stat.ROBUST_CV, "Comp-CD4 PE-Cy7 FLR-A")):
        a.add_stat(("Lymphocytes", "CD3+"), spec)
    a.gates.get(("Lymphocytes", "CD3+")).label_offset = (0.05, 0.3)
    win.refresh_tree(e01)
    win.select(e01, ("Lymphocytes",))
    win.x_box.setCurrentIndex(win.x_box.findData("Comp-CD3 APC-H7 FLR-A"))
    win.y_box.setCurrentIndex(win.y_box.findData("SSC-A"))
    app.processEvents()
    win.grab().save(str(OUT / "gating.png"))
    print("saved gating")


if __name__ == "__main__":
    main()
