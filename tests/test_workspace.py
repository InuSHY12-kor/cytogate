"""Phase 6: groups and applying gates across samples."""

import numpy as np
import pytest

from cytogate.core import Linear, ProcessedSample, RectangleGate
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.gating import EllipseGate, PolygonGate, translate_gate
from cytogate.core.stats import Stat, StatSpec
from cytogate.core.workspace import ALL_SAMPLES, Workspace
from cytogate.io import load_fcs
from cytogate.io.wsp import read_wsp

FOLDER = "8_color_data_set/fcs_files"
E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def processed(data_dir):
    return {n: ProcessedSample(load_fcs(data_dir / FOLDER / n)) for n in (E01, E03, E05)}


@pytest.fixture
def ws(processed):
    w = Workspace()
    for n, ps in processed.items():
        w.add_sample(AnalyzedSample(ps))
    return w


def lymph_tree(a):
    a.add_gate((), "Lymphocytes", RectangleGate(("FSC-A", "SSC-A"), (50000.0, 10000.0), (200000.0, 90000.0)))
    a.add_gate("Lymphocytes", "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
    a.add_stat(("Lymphocytes", "CD3+"), StatSpec(Stat.MEDIAN, "Comp-CD4 PE-Cy7 FLR-A"))
    a.gates.get(("Lymphocytes",)).label_offset = (0.1, 0.2)


def test_groups(ws):
    assert list(ws.groups) == [ALL_SAMPLES]
    assert ws.group(ALL_SAMPLES).samples == [E01, E03, E05]
    ws.create_group("Stim", [E03, E05])
    assert ws.groups_of(E03) == [ALL_SAMPLES, "Stim"]
    with pytest.raises(ValueError):
        ws.create_group("Stim")
    with pytest.raises(ValueError):
        ws.delete_group(ALL_SAMPLES)
    with pytest.raises(KeyError):
        ws.add_to_group("Stim", ["nope.fcs"])
    ws.delete_group("Stim")
    assert list(ws.groups) == [ALL_SAMPLES]


def test_apply_tree_to_group(ws):
    lymph_tree(ws.samples[E01])
    r = ws.apply_to_group(E01, ("Lymphocytes",), ALL_SAMPLES)
    assert r.added == [E03, E05] and not r.updated and not r.skipped
    for n in (E03, E05):
        a = ws.samples[n]
        assert a.gates.paths() == [("Lymphocytes",), ("Lymphocytes", "CD3+")]
        pop = a.gates.get(("Lymphocytes", "CD3+"))
        assert pop.owning_group == ALL_SAMPLES
        assert pop.stats == [StatSpec(Stat.MEDIAN, "Comp-CD4 PE-Cy7 FLR-A")]
        assert a.gates.get(("Lymphocytes",)).label_offset == (0.1, 0.2)
        # same gate, each sample's own events
        x = a.ps.values("FSC-A")
        assert a.count(("Lymphocytes",)) == int(a.gates.get(("Lymphocytes",)).gate.contains(a.ps).sum())
        assert 0 < a.count(("Lymphocytes",)) < x.size
    assert ws.samples[E01].gates.get(("Lymphocytes",)).owning_group == ALL_SAMPLES
    # applying again replaces instead of duplicating
    r = ws.apply_to_group(E01, ("Lymphocytes",), ALL_SAMPLES)
    assert r.updated == [E03, E05]
    assert ws.samples[E03].gates.paths() == [("Lymphocytes",), ("Lymphocytes", "CD3+")]


def test_apply_whole_tree_and_missing_parent(ws):
    lymph_tree(ws.samples[E01])
    ws.create_group("Two", [E01, E03])
    r = ws.apply_to_group(E01, (), "Two")
    assert r.added == [E03]
    assert ws.samples[E05].gates.paths() == []  # not in the group
    # a child cannot go where its parent is missing
    r = ws.apply_to_group(E01, ("Lymphocytes", "CD3+"), ALL_SAMPLES)
    assert list(r.skipped) == [E05] and r.updated == [E03]


def test_group_edit_and_sample_specific_edit(ws):
    lymph_tree(ws.samples[E01])
    ws.apply_to_group(E01, ("Lymphocytes",), ALL_SAMPLES)
    wider = RectangleGate(("FSC-A", "SSC-A"), (40000.0, 5000.0), (220000.0, 100000.0))
    changed = ws.update_gate(E03, ("Lymphocytes",), wider)
    assert changed == [E01, E03, E05]
    assert all(ws.samples[n].gates.get(("Lymphocytes",)).gate == wider for n in changed)
    # E05 gets its own adjustment and leaves the group
    own = RectangleGate(("FSC-A", "SSC-A"), (60000.0, 5000.0), (220000.0, 100000.0))
    assert ws.update_gate(E05, ("Lymphocytes",), own, whole_group=False) == [E05]
    assert ws.samples[E05].gates.get(("Lymphocytes",)).owning_group is None
    assert ws.linked_samples(E01, ("Lymphocytes",)) == [E01, E03]
    # a later group-wide change leaves E05 alone
    ws.update_gate(E01, ("Lymphocytes",), wider)
    assert ws.samples[E05].gates.get(("Lymphocytes",)).gate == own
    # children are kept when a gate changes
    assert ws.samples[E05].gates.paths() == [("Lymphocytes",), ("Lymphocytes", "CD3+")]
    # counts follow the new gates
    a5 = ws.samples[E05]
    assert a5.count(("Lymphocytes",)) == int(own.contains(a5.ps).sum())


def test_delete_group_releases_gates(ws):
    lymph_tree(ws.samples[E01])
    ws.create_group("G", [E01, E03])
    ws.apply_to_group(E01, ("Lymphocytes",), "G")
    ws.delete_group("G")
    assert all(p.owning_group is None for n in (E01, E03) for p in ws.samples[n].gates)


def test_flowjo_group_gates_spread_like_flowjo(data_dir, processed):
    """In FlowJo's DEN workspace every gate is owned by group "DEN". Applying
    E01's tree to the group must reproduce what each sample's own tree gives."""
    wsp = read_wsp(data_dir / "8_color_data_set/8_color_ICS.wsp")
    assert all(p.owning_group == "DEN" for s in wsp.samples for p in s.gates)
    w = Workspace()
    own = {}
    for s in wsp.samples:
        ps = ProcessedSample(processed[s.file_name].sample, compensation=s.compensation, transforms=s.transforms)
        own[s.file_name] = s.gates.counts(ps)
        w.add_sample(AnalyzedSample(ps, s.gates if s.file_name == E01 else None))
    w.create_group("DEN", [E01, E03, E05])
    w.apply_to_group(E01, (), "DEN")
    for name in (E03, E05):
        assert w.samples[name].gates.counts(w.samples[name].ps) == own[name]


# --- moving gates on the plot -------------------------------------------------------

def test_translate_gate(processed):
    ps = processed[E01]
    r = RectangleGate(("FSC-A", "Comp-CD3 APC-H7 FLR-A"), (50000.0, 600.0), (None, 5000.0))
    moved = translate_gate(r, ps, {"FSC-A": 0.1, "Comp-CD3 APC-H7 FLR-A": -0.05})
    assert moved.mins[0] == pytest.approx(50000 + 0.1 * 262144)
    assert moved.maxs[0] is None  # open side stays open
    cd3 = ps.transforms["Comp-CD3 APC-H7 FLR-A"]
    assert cd3.apply([moved.mins[1]])[0] == pytest.approx(cd3.apply([600.0])[0] - 0.05, abs=1e-6)
    p = PolygonGate(("FSC-A", "SSC-A"), ((1e4, 1e4), (2e4, 1e4), (2e4, 2e4)))
    pm = translate_gate(p, ps, {"SSC-A": 0.5})
    assert [v[1] for v in pm.vertices] == pytest.approx([1e4 + 131072, 1e4 + 131072, 2e4 + 131072])
    e = EllipseGate(("FSC-A", "SSC-A"), (0.3, 0.3), 0.1, 0.05, 0.0)
    assert translate_gate(e, ps, {"FSC-A": 0.2}).center == pytest.approx((0.5, 0.3))


# --- gate reshaping and undo (graph window support) ----------------------------------------

def test_scale_gate(processed):
    from cytogate.core.gating import scale_gate
    ps = processed[E01]
    e = EllipseGate(("FSC-A", "SSC-A"), (0.3, 0.3), 0.1, 0.05, 0.0)
    assert scale_gate(e, ps, 1.1).a == pytest.approx(0.11)
    p = PolygonGate(("FSC-A", "SSC-A"), ((1e4, 1e4), (3e4, 1e4), (2e4, 3e4)))
    big = scale_gate(p, ps, 2.0)
    v = np.array(big.vertices)
    assert v.mean(axis=0) == pytest.approx(np.array(p.vertices).mean(axis=0))
    assert v[1, 0] - v[0, 0] == pytest.approx(2 * 2e4)
    r = RectangleGate(("FSC-A",), (1.0,), (2.0,))
    assert scale_gate(r, ps, 2.0) is r  # FlowJo leaves rectangles alone


def test_move_vertex_range(processed):
    from cytogate.core.gating import move_vertex
    ps = processed[E01]
    r = RectangleGate(("FSC-A",), (26214.4,), (131072.0,))
    m = move_vertex(r, ps, "FSC-A", None, 0, (0.0, 0.5))  # low edge onto the axis: unbounded
    assert m.mins == (None,) and m.maxs == (131072.0,)
    m = move_vertex(r, ps, "FSC-A", None, 1, (0.05, 0.5))  # dragged past the other edge
    assert m.mins[0] < m.maxs[0]


def test_history(ws):
    from cytogate.core.history import History
    h = History(ws)
    assert not h.can_undo
    h.record()
    lymph_tree(ws.samples[E01])
    ws.samples[E01].ps.set_transform("FSC-A", Linear(0, 1000))
    h.record()
    ws.create_group("G", [E01])
    assert h.undo() and "G" not in ws.groups
    h.undo()
    assert ws.samples[E01].gates.paths() == []
    assert ws.samples[E01].ps.transforms["FSC-A"] == Linear(0, 262144)
    assert h.can_redo
    h.redo()
    assert ws.samples[E01].gates.paths() == [("Lymphocytes",), ("Lymphocytes", "CD3+")]
    assert ws.samples[E01].ps.transforms["FSC-A"] == Linear(0, 1000)
    h.record()
    assert not h.can_redo  # a new change drops the redo steps
