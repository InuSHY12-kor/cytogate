# PyInstaller build of the CytoGate desktop app.
#   pyinstaller packaging/cytogate.spec --noconfirm
# Result: dist/CytoGate/CytoGate(.exe) — a folder build (starts faster than one-file).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
datas, binaries, hidden = [], [], []
for pkg in ("flowkit", "flowio", "flowutils", "pptx"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
datas += collect_data_files("pyqtgraph", excludes=["**/examples/**"])
hidden += collect_submodules(
    "pyqtgraph", filter=lambda m: not any(x in m for x in (".examples", ".opengl", ".jupyter", ".canvas", "pyqtgraph.util.cupy")))
datas += [(str(ROOT / "src" / "cytogate" / "resources" / "cytogate.png"), "cytogate/resources")]
hidden += collect_submodules("cytogate") + ["openpyxl", "scipy.ndimage", "scipy.optimize", "PySide6.QtSvg"]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "pytest", "PyQt5", "PyQt6", "PySide2",
              "pyarrow",  # optional for pandas; 160 MB
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.Qt3DCore",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtSql",
              "PySide6.QtVirtualKeyboard"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CytoGate",
    console=False,  # a windowed app; --selftest writes its report to a file
    icon=str(ROOT / "packaging" / "cytogate.ico"),
    version=str(ROOT / "packaging" / "version_info.txt") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="CytoGate")
