"""A3/A4 core: keywords, keyword groups, renaming, copying statistics."""

import pytest

from cytogate.core import ProcessedSample, RectangleGate
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.gating import BooleanGate
from cytogate.core.stats import Stat, StatSpec
from cytogate.core.workspace import ALL_SAMPLES, Criterion, Workspace
from cytogate.io import load_fcs
from cytogate.io.wsp_load import load_workspace
from cytogate.io.wsp_write import write_wsp

E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def loaded(data_dir):
    return {n: load_fcs(data_dir / "8_color_data_set/fcs_files" / n) for n in (E01, E03, E05)}


@pytest.fixture
def ws(loaded):
    w = Workspace()
    for smp in loaded.values():
        w.add_sample(AnalyzedSample(ProcessedSample(smp)))
    return w


def test_keyword_lookup_and_edit(ws):
    a = ws.samples[E01]
    assert a.keyword("$TOT") == a.keyword("TOT") == "290172"
    ws.set_keyword([E01, E03], "Stim", "PMA")
    assert a.keyword("Stim") == "PMA" and ws.samples[E05].keyword("Stim") == ""
    assert "Stim" in ws.keyword_names() and "$CYT" in ws.keyword_names()
    ws.set_keyword([E01], "$CYT", "renamed")  # user edits win over the file
    assert a.keywords["$CYT"] == "renamed" and a.ps.sample.keywords["$CYT"] == "LSRII"


def test_keyword_series_find_replace_paste(ws):
    assert ws.keyword_series(list(ws.samples), "Tube", start=1, prefix="T", digits=2) == ["T01", "T02", "T03"]
    assert ws.find_replace("T0", "Tube ", keyword="Tube") == 3
    assert ws.samples[E05].keyword("Tube") == "Tube 3"
    keys = ws.paste_keywords(f"Name\tDonor\tDay\n{E03}\tD7\t2\n{E01}\tD1\t1\nnot-a-sample\tx\ty\n")
    assert keys == ["Donor", "Day"]
    assert ws.samples[E03].keyword("Donor") == "D7" and ws.samples[E01].keyword("Day") == "1"
    ws.paste_keywords("Conc\n1\n10\n100\n")  # no name column: workspace order
    assert [ws.samples[n].keyword("Conc") for n in (E01, E03, E05)] == ["1", "10", "100"]


def test_keyword_groups_stay_up_to_date(ws):
    ws.set_keyword([E01, E03], "Stim", "PMA")
    g = ws.create_group("PMA", criteria=[Criterion("Stim", "PMA")])
    assert g.samples == [E01, E03]
    ws.set_keyword([E05], "Stim", "PMA")
    assert ws.group("PMA").samples == [E01, E03, E05]
    ws.set_keyword([E01], "Stim", "none")
    assert ws.group("PMA").samples == [E03, E05]
    assert ws.create_group("LSR", criteria=[Criterion("$CYT", "lsr", "contains")]).samples == [E01, E03, E05]


def test_rename_population_follows_references(ws):
    a = ws.samples[E01]
    a.add_gate((), "Big", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    a.add_gate("Big", "Small SSC", RectangleGate(("SSC-A",), (None,), (50000.0,)))
    a.add_gate("Big", "not small", BooleanGate("not", (("Big", "Small SSC"),)))
    a.add_stat(("Big", "Small SSC"), StatSpec(Stat.FREQ_OF, ancestor=("Big",)))
    ws.apply_to_group(E01, ("Big",), ALL_SAMPLES)
    changed = ws.rename_population(E01, ("Big",), "Large")
    assert changed == [E01, E03, E05]
    for n in changed:
        b = ws.samples[n]
        assert b.gates.paths() == [("Large",), ("Large", "Small SSC"), ("Large", "not small")]
        assert b.gates.get(("Large", "not small")).gate.refs == (("Large", "Small SSC"),)
        assert b.gates.get(("Large", "Small SSC")).stats[0].ancestor == ("Large",)
        assert b.count(("Large", "not small")) == b.count(("Large",)) - b.count(("Large", "Small SSC"))
    ws.rename_population(E05, ("Large", "Small SSC"), "Low", whole_group=False)
    assert ("Large", "Low") in ws.samples[E05].gates.paths()
    assert ("Large", "Small SSC") in ws.samples[E03].gates.paths()
    with pytest.raises(ValueError):
        ws.rename_population(E01, ("Large", "Small SSC"), "not small")


def test_rename_group_and_copy_statistic(ws):
    a = ws.samples[E01]
    a.add_gate((), "Big", RectangleGate(("FSC-A",), (50000.0,), (None,)))
    ws.create_group("G", [E01, E03])
    ws.apply_to_group(E01, ("Big",), "G")
    ws.rename_group("G", "H")
    assert "H" in ws.groups and ws.samples[E03].gates.get(("Big",)).owning_group == "H"
    spec = StatSpec(Stat.MEDIAN, "FSC-A")
    assert ws.copy_statistic(spec, ("Big",), [E01, E03, E05]) == [E01, E03]  # E05 has no Big


def test_keywords_and_keyword_groups_saved(ws, tmp_path):
    ws.set_keyword([E01, E03], "Stim", "PMA")
    ws.create_group("PMA", criteria=[Criterion("Stim", "PMA")])
    path = write_wsp(tmp_path / "k.wsp", ws)
    ws2 = load_workspace(path).workspace
    assert ws2.samples[E03].keyword("Stim") == "PMA"
    assert ws2.group("PMA").criteria == [Criterion("Stim", "PMA")]
    ws2.set_keyword([E05], "Stim", "PMA")
    assert ws2.group("PMA").samples == [E01, E03, E05]
