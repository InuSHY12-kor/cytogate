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


if __name__ == "__main__":
    main()
