"""Where settings go: CYTOGATE_HOME, the portable folder, or ~/.cytogate."""

import sys
from pathlib import Path

from cytogate import paths
from cytogate.core.layout import presets_dir
from cytogate.core.presets import presets_file


def test_default_and_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CYTOGATE_HOME", raising=False)
    assert paths.app_home() == Path.home() / ".cytogate" and not paths.is_portable()
    monkeypatch.setenv("CYTOGATE_HOME", str(tmp_path))
    assert presets_file().parent == tmp_path and presets_dir().parent == tmp_path


def test_portable(monkeypatch, tmp_path):
    monkeypatch.delenv("CYTOGATE_HOME", raising=False)
    exe = tmp_path / "CytoGate" / "CytoGate.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert not paths.is_portable()  # an installed copy: settings stay in the profile
    (exe.parent / "portable.txt").write_text("portable")
    assert paths.is_portable()
    assert paths.app_home() == exe.parent / "settings"
    assert presets_file() == exe.parent / "settings" / "transform_presets.json"
    from cytogate.gui import main_window
    assert main_window._untitled_dir() == exe.parent / "settings"
