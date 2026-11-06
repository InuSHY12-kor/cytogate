"""Entry point of the packaged app (CytoGate.exe)."""

import sys

from cytogate.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
