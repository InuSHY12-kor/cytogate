"""A1: saving and opening workspaces (.wsp).

- Round trip: everything we can build comes back identical.
- FlowJo's own workspaces survive open -> save -> open with the same counts.
- FlowKit (an independent .wsp reader) reads our files and gets our counts.
"""

import shutil

import flowkit as fk
import numpy as np
import pytest

from cytogate.core import Logicle, ProcessedSample, RectangleGate
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.gating import BooleanGate, EllipseGate, PolygonGate, quadrant_gates
from cytogate.core.stats import Stat, StatSpec
from cytogate.core.workspace import Workspace
from cytogate.io import load_fcs
from cytogate.io.wsp import read_wsp
from cytogate.io.wsp_load import load_workspace
from cytogate.io.wsp_write import sidecar_path, write_wsp

FOLDER = "8_color_data_set/fcs_files"
E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def samples(data_dir):
    return {n: load_fcs(data_dir / FOLDER / n) for n in (E01, E03, E05)}


def build(samples, with_ellipse=True):
    ws = Workspace()
    for n, smp in samples.items():
        ws.add_sample(AnalyzedSample(ProcessedSample(smp)))
    a = ws.samples[E01]
    a.add_gate((), "Lymph", PolygonGate(("FSC-A", "SSC-A"), ((40000.0, 5000.0), (200000.0, 5000.0),
                                                              (220000.0, 90000.0), (50000.0, 90000.0))))
    a.add_gate("Lymph", "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
    for qn, gate in quadrant_gates("Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A", 1000.0, 2000.0, "CD4", "CD8"):
        a.add_gate(("Lymph", "CD3+"), qn, gate)
    a.add_gate(("Lymph", "CD3+"), "not CD4", BooleanGate("not", (("Lymph", "CD3+", "Q3: CD4+ , CD8-"),)))
    a.add_gate(("Lymph", "CD3+"), "Q1 or Q3", BooleanGate("or", (("Lymph", "CD3+", "Q1: CD4- , CD8+"),
                                                                 ("Lymph", "CD3+", "Q3: CD4+ , CD8-"))))
    a.add_gate("Lymph", "big", RectangleGate(("FSC-A", "SSC-A"), (100000.0, None), (None, 60000.0), inside=False))
    if with_ellipse:
        a.add_gate("Lymph", "blob", EllipseGate(("Comp-CD4 PE-Cy7 FLR-A", "Comp-CD8 PerCP-Cy55 FLR-A"),
                                                (0.6, 0.4), 0.12, 0.06, 0.4))
    a.add_stat(("Lymph", "CD3+"), StatSpec(Stat.MEDIAN, "Comp-CD4 PE-Cy7 FLR-A"))
    a.add_stat(("Lymph", "CD3+"), StatSpec(Stat.FREQ_OF, ancestor=("Lymph",)))
    a.add_stat(("Lymph", "CD3+"), StatSpec(Stat.PERCENTILE, "FSC-A", 95.0))
    a.add_stat((), StatSpec(Stat.COUNT))
    a.gates.get(("Lymph",)).label_offset = (0.123, -0.0456)
    a.gates.get(("Lymph", "CD3+")).tint = "#00ff00"
    a.user_keywords = {"Donor": "D1", "Stim": "PMA"}
    ws.create_group("Stim", [E01, E03])
    ws.apply_to_group(E01, ("Lymph",), "Stim")
    ws.samples[E05].ps.set_transform("Comp-CD4 PE-Cy7 FLR-A", Logicle(262144, 1, 4.5, 0))
    return ws


def _norm(gate):
    """Ellipses go through FlowJo's foci/edge form, which costs ~1e-16."""
    import dataclasses
    if isinstance(gate, EllipseGate):
        return dataclasses.replace(gate, center=tuple(round(c, 12) for c in gate.center), a=round(gate.a, 12),
                                   b=round(gate.b, 12), angle=round(gate.angle, 12))
    return gate


def snapshot(ws):
    out = {}
    for n, a in ws.samples.items():
        out[n] = {
            "pops": [(p.path, _norm(p.gate), tuple(p.stats), p.owning_group, p.tint) for p in a.gates],
            "root_stats": list(a.gates.root.stats),
            "counts": a.gates.counts(a.ps),
            "transforms": dict(a.ps.transforms),
            "comp": None if a.ps.compensation is None else a.ps.compensation.spillover.values.tolist(),
            "offsets": {p.path: p.label_offset for p in a.gates},
            "kw": dict(a.user_keywords),
        }
    return out


def test_round_trip(samples, tmp_path):
    ws = build(samples)
    path = write_wsp(tmp_path / "exp.wsp", ws, extras={"note": "hi"})
    assert sidecar_path(path).is_file()
    opened = load_workspace(path)
    assert opened.missing == [] and opened.extras == {"note": "hi"}
    ws2 = opened.workspace
    assert list(ws2.samples) == [E01, E03, E05]
    assert {k: g.samples for k, g in ws2.groups.items()} == {k: g.samples for k, g in ws.groups.items()}
    a, b = snapshot(ws), snapshot(ws2)
    for n in a:
        for key in a[n]:
            if key == "comp":
                np.testing.assert_allclose(b[n][key], a[n][key], rtol=1e-12)
            else:
                assert b[n][key] == a[n][key], (n, key)


def test_without_side_file_label_offsets_are_flowjo_pixels(samples, tmp_path):
    ws = build(samples)
    path = write_wsp(tmp_path / "exp.wsp", ws)
    sidecar_path(path).unlink()
    a = load_workspace(path).workspace.samples[E01]
    assert a.gates.get(("Lymph",)).label_offset == pytest.approx((0.123, -0.0456), abs=1 / 256)


def test_written_counts_match_ours(samples, tmp_path):
    ws = build(samples)
    path = write_wsp(tmp_path / "exp.wsp", ws)
    raw = read_wsp(path)
    for s in raw.samples:
        ours = ws.samples[s.file_name].gates.counts(ws.samples[s.file_name].ps)
        assert {k: v for k, v in s.flowjo_counts.items() if k} == {k: v for k, v in ours.items() if k}


def test_flowkit_reads_our_workspace(samples, tmp_path, data_dir):
    ws = build(samples, with_ellipse=False)  # FlowKit approximates ellipses with polygons
    path = write_wsp(tmp_path / "exp.wsp", ws)
    fkw = fk.Workspace(str(path), fcs_samples=str(data_dir / FOLDER))
    fkw.analyze_samples(group_name="Stim", use_mp=False)
    report = fkw.get_analysis_report(group_name="Stim")
    checked = 0
    for _, row in report.iterrows():
        gp = row.gate_path if isinstance(row.gate_path, tuple) else eval(row.gate_path)
        path_ = tuple(gp[1:]) + (row.gate_name,)
        a = ws.samples[row.sample_id]
        assert row["count"] == a.count(path_), path_
        checked += 1
    assert checked >= 8


@pytest.mark.parametrize("wsp", ["8_color_data_set/8_color_ICS_boolean_gate_testing.wsp",
                                 "8_color_data_set/reused_quad_gate_with_child.wsp",
                                 "8_color_data_set/8_color_ICS_with_ellipse.wsp"])
def test_flowjo_workspace_open_save_open(data_dir, tmp_path, wsp):
    first = load_workspace(data_dir / wsp, search_dirs=[data_dir / FOLDER])
    assert first.missing == []
    path = write_wsp(tmp_path / "again.wsp", first.workspace)
    second = load_workspace(path)
    a, b = snapshot(first.workspace), snapshot(second.workspace)
    for n in a:
        assert b[n]["counts"] == a[n]["counts"]
        assert [p[:2] for p in b[n]["pops"]] == [p[:2] for p in a[n]["pops"]]
    assert {k: g.samples for k, g in second.workspace.groups.items()} == \
        {k: g.samples for k, g in first.workspace.groups.items()}


def test_reconnect_moved_files(samples, tmp_path, data_dir):
    src = tmp_path / "data"
    src.mkdir()
    shutil.copy(data_dir / FOLDER / E01, src / E01)
    ws = Workspace()
    ws.add_sample(AnalyzedSample(ProcessedSample(load_fcs(src / E01))))
    (tmp_path / "ws").mkdir()
    path = write_wsp(tmp_path / "ws" / "exp.wsp", ws)
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(str(src / E01), moved / E01)
    assert load_workspace(path).missing == [E01]
    asked = []
    opened = load_workspace(path, locate=lambda name: asked.append(name) or moved / name)
    assert asked == [E01] and list(opened.workspace.samples) == [E01]
    assert load_workspace(path, search_dirs=[moved]).missing == []
