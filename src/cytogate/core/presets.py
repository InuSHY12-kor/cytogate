"""Saved transform presets (FlowJo's "Save" in the transform window).

A preset is stored per parameter name in a JSON file in the user's settings
folder and is used as the starting transform whenever a sample with that
parameter is loaded.
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .transforms import Transform, from_xml

__all__ = ["presets_file", "load_presets", "save_preset", "remove_preset", "apply_presets"]


def presets_file() -> Path:
    from ..paths import app_home
    base = app_home()
    return Path(base) / "transform_presets.json"


def load_presets() -> dict[str, Transform]:
    f = presets_file()
    if not f.is_file():
        return {}
    out = {}
    for name, xml in json.loads(f.read_text()).items():
        try:
            out[name] = from_xml(ET.fromstring(xml))[1]
        except (ET.ParseError, ValueError):
            continue
    return out


def _write(presets: dict[str, Transform]) -> None:
    f = presets_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({k: ET.tostring(v.to_xml(k), encoding="unicode") for k, v in presets.items()}, indent=1))


def save_preset(parameter: str, xf: Transform) -> None:
    p = load_presets()
    p[parameter] = xf
    _write(p)


def remove_preset(parameter: str) -> None:
    p = load_presets()
    p.pop(parameter, None)
    _write(p)


def apply_presets(ps, presets: dict[str, Transform] | None = None) -> list[str]:
    """Set saved transforms on a ProcessedSample; returns the parameters changed."""
    presets = load_presets() if presets is None else presets
    names = {p.name for p in ps.parameters}
    done = [k for k in presets if k in names]
    for k in done:
        ps.transforms[k] = presets[k]
    return done
