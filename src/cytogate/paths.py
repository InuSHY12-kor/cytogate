"""Where CytoGate keeps its own settings (transform presets, figure presets, autosaves).

- CYTOGATE_HOME, if set;
- the portable version: a "portable.txt" next to CytoGate.exe puts everything in a
  "settings" folder beside the program, so it can run from a USB stick and leaves
  nothing behind on the computer;
- otherwise ~/.cytogate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["app_home", "is_portable", "program_dir"]


def program_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def is_portable() -> bool:
    return getattr(sys, "frozen", False) and (program_dir() / "portable.txt").is_file()


def app_home() -> Path:
    env = os.environ.get("CYTOGATE_HOME")
    if env:
        return Path(env)
    if is_portable():
        return program_dir() / "settings"
    return Path.home() / ".cytogate"
