"""D1/D2 core: compensation from single-stain controls, AutoSpill, applying
matrices, copying transforms, transform presets."""

import numpy as np
import pandas as pd
import pytest

from cytogate.core import Compensation, Linear, Logicle, ProcessedSample, RectangleGate
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.comp_tools import Control, autospill, spillover_from_controls
from cytogate.core.history import History
from cytogate.core.presets import apply_presets, load_presets, remove_preset, save_preset
from cytogate.core.workspace import Workspace
from cytogate.io import load_fcs

DETS = ["A", "B", "C"]
TRUE = np.array([[1, 0.15, 0.02], [0.05, 1, 0.30], [0.0, 0.08, 1]])
E01, E03 = "101_DEN084Y5_15_E01_008_clean.fcs", "101_DEN084Y5_15_E03_009_clean.fcs"


def controls(seed=1, n=40000):
    rng = np.random.default_rng(seed)
    out = []
    for i, d in enumerate(DETS):
        true = np.abs(rng.normal(50, 30, (n, 3)))  # autofluorescence
        true[:, i] += np.where(rng.random(n) < 0.5, rng.lognormal(8, 0.6, n), 0)  # half the cells stained
        out.append(Control(d, true @ TRUE + rng.normal(0, 20, (n, 3))))
    unstained = np.abs(rng.normal(50, 30, (20000, 3))) @ TRUE
    return out, unstained


def test_classic_method_recovers_known_spillover():
    cs, unstained = controls()
    m = spillover_from_controls(DETS, cs, unstained)
    np.testing.assert_allclose(m.spillover.values, TRUE, atol=0.003)
    # without an unstained tube: dim events of each control are the negatives
    m2 = spillover_from_controls(DETS, cs)
    np.testing.assert_allclose(m2.spillover.values, TRUE, atol=0.01)


def test_autospill_recovers_known_spillover():
    cs, _ = controls(seed=2)
    m = autospill(DETS, cs)
    np.testing.assert_allclose(m.spillover.values, TRUE, atol=0.002)
    # compensating the controls with it removes the spillover
    comp = cs[0].events @ np.linalg.inv(m.spillover.values)
    stained = cs[0].events[:, 0] > np.percentile(cs[0].events[:, 0], 80)
    assert abs(np.median(comp[stained, 1]) - 50) < 15  # back to background level


def test_no_signal_rejected():
    rng = np.random.default_rng(0)
    flat = Control("A", rng.normal(0, 1, (1000, 3)), positive=np.zeros(1000, bool) | (np.arange(1000) < 10),
                   negative=np.arange(1000) >= 10)
    flat.events[:10, 0] = -5
    with pytest.raises(ValueError):
        spillover_from_controls(DETS, [flat])


@pytest.fixture
def ws(data_dir):
    w = Workspace()
    for n in (E01, E03):
        w.add_sample(AnalyzedSample(ProcessedSample(load_fcs(data_dir / "8_color_data_set/fcs_files" / n))))
    return w


def test_apply_and_remove_compensation(ws):
    a = ws.samples[E01]
    a.add_gate((), "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
    before = a.count(("CD3+",))
    acq = a.ps.compensation
    a.ps.set_transform("Comp-CD3 APC-H7 FLR-A", Logicle(262144, 1, 4.5, 0))
    edited = acq.spillover.copy()
    edited.loc["CD3 APC-H7 FLR-A", "CD4 PE-Cy7 FLR-A"] += 0.2
    new = Compensation(edited, name="Edited")
    done, skipped = ws.apply_compensation(new, [E01, E03])
    assert done == [E01, E03] and not skipped
    assert a.ps.compensation.name == "Edited"
    assert a.ps.transforms["Comp-CD3 APC-H7 FLR-A"] == Logicle(262144, 1, 4.5, 0)  # kept
    # the gate keeps working on the newly compensated values
    assert a.count(("CD3+",)) == int(a.gates.get(("CD3+",)).gate.contains(a.ps).sum())
    assert abs(a.count(("CD3+",)) - before) < 0.01 * before
    ws.apply_compensation(None, [E01])
    assert a.ps.compensation is None and not any(p.compensated for p in a.ps.parameters)
    bad = Compensation(pd.DataFrame(np.eye(1), index=["nope"], columns=["nope"]))
    assert ws.apply_compensation(bad, [E03])[1] == {E03: "no detector nope"}


def test_undo_restores_compensation(ws):
    a = ws.samples[E01]
    h = History(ws)
    h.record()
    ws.apply_compensation(None, [E01])
    h.undo()
    assert a.ps.compensation is not None and len(a.ps.parameters) == 23


def test_copy_transforms(ws):
    ws.samples[E01].ps.set_transform("FSC-A", Linear(0, 100000))
    ws.samples[E01].ps.set_transform("Comp-CD4 PE-Cy7 FLR-A", Logicle(262144, 1, 4.5, 0))
    assert ws.copy_transforms(E01, [E01, E03], parameters=["FSC-A"]) == [E03]
    t = ws.samples[E03].ps.transforms
    assert t["FSC-A"] == Linear(0, 100000) and t["Comp-CD4 PE-Cy7 FLR-A"] != Logicle(262144, 1, 4.5, 0)
    ws.copy_transforms(E01, [E03])
    assert t["Comp-CD4 PE-Cy7 FLR-A"] == Logicle(262144, 1, 4.5, 0)


def test_transform_presets(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("CYTOGATE_HOME", str(tmp_path))
    assert load_presets() == {}
    save_preset("Comp-CD4 PE-Cy7 FLR-A", Logicle(262144, 0.8, 4.5, 0))
    save_preset("NotHere", Linear(0, 1))
    assert apply_presets(ws.samples[E03].ps) == ["Comp-CD4 PE-Cy7 FLR-A"]
    assert ws.samples[E03].ps.transforms["Comp-CD4 PE-Cy7 FLR-A"] == Logicle(262144, 0.8, 4.5, 0)
    remove_preset("NotHere")
    assert list(load_presets()) == ["Comp-CD4 PE-Cy7 FLR-A"]
