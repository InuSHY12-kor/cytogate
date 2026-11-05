"""Start the CytoGate desktop app: `cytogate` or `python -m cytogate gui [files…]`."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6 import QtWidgets

    from .main_window import MainWindow

    argv = sys.argv[1:] if argv is None else argv
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("CytoGate")
    win = MainWindow()
    win.show()
    if argv:
        win.add_samples(argv)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
