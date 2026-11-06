"""G1: exporting and concatenating populations; exported FCS files read back with the same values."""

import csv

import numpy as np
import pytest

from cytogate.core import ProcessedSample, RectangleGate
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.workspace import Workspace
from cytogate.io import load_fcs
from cytogate.io.export import ExportOptions, concatenate, export_populations, group_items

E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def ws(data_dir):
    w = Workspace()
    for n in (E01, E03, E05):
        a = AnalyzedSample(ProcessedSample(load_fcs(data_dir / "8_color_data_set/fcs_files" / n)))
        a.add_gate((), "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
        w.add_sample(a)
    w.set_keyword([E01, E03], "Stim", "PMA")
    w.set_keyword([E05], "Stim", "none")
    return w


def test_fcs_round_trip_uncompensated(ws, tmp_path):
    a = ws.samples[E01]
    [out] = export_populations(ws, [(E01, ("CD3+",))], tmp_path)
    assert out.name == "export_101_DEN084Y5_15_E01_008_clean_CD3+.fcs"
    b = load_fcs(out)
    assert b.event_count == a.count(("CD3+",))
    mask = a.mask(("CD3+",))
    np.testing.assert_allclose(b.events, a.ps.sample.events[mask], rtol=1e-6, atol=1e-6)  # float32
    assert b.spillover() is not None  # can be compensated again
    np.testing.assert_allclose(b.spillover().values, a.ps.compensation.spillover.values, rtol=1e-9)
    assert b.keyword("CYTOGATE_BTIM") == a.keyword("$BTIM") and b.time_gain == 1.0
    # re-gating the exported file gives every event again
    ab = AnalyzedSample(ProcessedSample(b))
    ab.add_gate((), "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
    assert ab.count(("CD3+",)) == b.event_count


def test_compensated_subsampled_csv(ws, tmp_path):
    opts = ExportOptions(fmt="csv_scale", parameters="compensated", max_events=1000, prefix="", suffix="_s")
    [out] = export_populations(ws, [(E03, ())], tmp_path, opts)
    rows = list(csv.reader(open(out)))
    assert len(rows) == 1 + 1000
    assert "Comp-CD4 PE-Cy7 FLR-A" in rows[0] and "CD4 PE-Cy7 FLR-A" not in rows[0]
    assert out.name == "101_DEN084Y5_15_E03_009_clean_Ungated_s.csv"


def test_channel_csv_and_template(ws, tmp_path):
    opts = ExportOptions(fmt="csv_channel", parameters=["FSC-A"], name_template="{Stim}_{sample}_{population}")
    [out] = export_populations(ws, [(E05, ("CD3+",))], tmp_path, opts)
    assert out.name == "none_101_DEN084Y5_15_E05_010_clean_CD3+.csv"
    vals = np.array([int(r[0]) for r in list(csv.reader(open(out)))[1:]])
    assert vals.min() >= 0 and vals.max() <= 4095
    a = ws.samples[E05]
    expected = np.minimum((np.clip(a.ps.scaled("FSC-A")[a.mask(("CD3+",))], 0, 1) * 4096).astype(int), 4095)
    np.testing.assert_array_equal(vals, expected)


def test_concatenate(ws, tmp_path):
    items = [(E01, ("CD3+",)), (E03, ("CD3+",))]
    out = concatenate(ws, items, tmp_path / "cat.fcs")
    b = load_fcs(out)
    n1, n3 = ws.samples[E01].count(("CD3+",)), ws.samples[E03].count(("CD3+",))
    assert b.event_count == n1 + n3
    sid = b.channel_events("SampleID")
    assert (sid == 1).sum() == n1 and (sid == 2).sum() == n3
    assert E03 in b.keyword("CYTOGATE_CONCATENATED")
    groups = group_items(ws, [(E01, ()), (E03, ()), (E05, ())], "keyword", keyword="Stim")
    assert [[s for s, _ in g] for g in groups] == [[E01, E03], [E05]]
    assert len(group_items(ws, [(E01, ()), (E03, ()), (E05, ())], "every_n", n=2)) == 2
