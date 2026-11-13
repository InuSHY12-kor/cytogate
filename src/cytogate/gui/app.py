"""Start the CytoGate desktop app: `cytogate` or `python -m cytogate gui [files…]`."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--selftest":  # `CytoGate.exe --selftest [report.txt]`
        from ..selftest import run
        return run(argv[1] if len(argv) > 1 else None)
    if argv and argv[0] == "--version":
        from .. import __version__
        print(__version__)
        return 0

    from PySide6 import QtGui, QtWidgets

    from .main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("CytoGate")
    icon = _icon_path()
    if icon:
        app.setWindowIcon(QtGui.QIcon(str(icon)))
    win = MainWindow()
    win.show()
    workspaces = [a for a in argv if a.lower().endswith(".wsp")]
    others = [a for a in argv if not a.lower().endswith(".wsp")]
    for path in workspaces:  # double-clicked .wsp files: each in its own workspace window
        win.open_in_window(path)
    if others:
        win.add_samples(others)
    return app.exec()


def _icon_path():
    from pathlib import Path
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for p in (here / "cytogate" / "resources" / "cytogate.png", here / "resources" / "cytogate.png"):
        if p.is_file():
            return p
    return None


if __name__ == "__main__":
    sys.exit(main())
