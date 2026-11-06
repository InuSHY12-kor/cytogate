"""E1 core: table definitions batched over groups, formulas, iteration, export."""

import math

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from cytogate.core import ProcessedSample, RectangleGate
from cytogate.core.analysis import AnalyzedSample
from cytogate.core.stats import Stat, StatSpec
from cytogate.core.table import (Column, TableDef, compute_table, evaluate_formula, export_table, heat_colors,
                                 table_from_json, table_to_json)
from cytogate.core.workspace import ALL_SAMPLES, Workspace
from cytogate.io import load_fcs

E01, E03, E05 = (f"101_DEN084Y5_15_{s}_clean.fcs" for s in ("E01_008", "E03_009", "E05_010"))


@pytest.fixture(scope="module")
def ws(data_dir):
    w = Workspace()
    for n in (E01, E03, E05):
        a = AnalyzedSample(ProcessedSample(load_fcs(data_dir / "8_color_data_set/fcs_files" / n)))
        a.add_gate((), "Cells", RectangleGate(("FSC-A",), (50000.0,), (None,)))
        a.add_gate("Cells", "CD3+", RectangleGate(("Comp-CD3 APC-H7 FLR-A",), (600.0,), (None,)))
        w.add_sample(a)
    w.set_keyword([E01, E03, E05], "Donor", "D1")
    w.set_keyword([E05], "Donor", "D2")
    w.set_keyword([E01, E05], "Tube", "A")
    w.set_keyword([E03], "Tube", "B")
    return w


def test_basic_table(ws):
    t = TableDef()
    t.add_population(("Cells", "CD3+"))
    t.add_statistic(("Cells", "CD3+"), StatSpec(Stat.MEDIAN, "Comp-CD4 PE-Cy7 FLR-A"))
    t.columns.append(Column("keyword", keyword="$CYT"))
    t.columns.append(Column("stat", ("Cells",), StatSpec(Stat.COUNT), heading="cells"))
    df = compute_table(ws, t)
    assert list(df.columns) == ["Cells/CD3+ | Freq. of Parent", "Cells/CD3+ | Median (Comp-CD4 PE-Cy7 FLR-A)",
                                "$CYT", "cells"]
    assert list(df.index) == [E01, E03, E05, "Mean", "SD"]
    a = ws.samples[E03]
    assert df.loc[E03, "Cells/CD3+ | Freq. of Parent"] == pytest.approx(a.freq_of_parent(("Cells", "CD3+")))
    assert df.loc[E03, "cells"] == a.count(("Cells",))
    assert df.loc["Mean", "cells"] == pytest.approx(np.mean([ws.samples[n].count(("Cells",)) for n in (E01, E03, E05)]))
    assert df.loc[E01, "$CYT"] == "LSRII"


def test_formula_control_hidden(ws):
    t = TableDef(summary_rows=False)
    t.columns += [Column("stat", ("Cells",), StatSpec(Stat.COUNT), heading="cells", show=False),
                  Column("stat", ("Cells", "CD3+"), StatSpec(Stat.COUNT), heading="cd3"),
                  Column("stat", ("Cells", "CD3+"), StatSpec(Stat.COUNT), heading="first cd3", control=True),
                  Column("formula", formula="100 * {cd3} / {cells}", heading="pct"),
                  Column("formula", formula="{pct} / {nope}", heading="bad")]
    df = compute_table(ws, t)
    assert "cells" not in df.columns
    assert (df["first cd3"] == ws.samples[E01].count(("Cells", "CD3+"))).all()
    a = ws.samples[E05]
    assert df.loc[E05, "pct"] == pytest.approx(100 * a.count(("Cells", "CD3+")) / a.count(("Cells",)))
    assert df["bad"].isna().all()


def test_evaluate_formula_is_safe():
    assert evaluate_formula("log10({a}) + sqrt(4)", {"a": 100}) == pytest.approx(4)
    assert math.isnan(evaluate_formula("{a} / 0", {"a": 1}))
    with pytest.raises(ValueError):
        evaluate_formula("__import__('os')", {})


def test_panel_and_keyword_iteration(ws):
    t = TableDef(iterate="panel", panel_size=2, summary_rows=False)
    t.columns += [Column("stat", (), StatSpec(Stat.COUNT), heading="tube1", panel_index=1),
                  Column("stat", (), StatSpec(Stat.COUNT), heading="tube2", panel_index=2)]
    df = compute_table(ws, t)
    assert list(df.index) == [E01, E05]
    assert df.loc[E01, "tube2"] == ws.samples[E03].ps.event_count
    assert math.isnan(df.loc[E05, "tube2"])  # last panel incomplete
    k = TableDef(iterate="keyword", iterate_keyword="Donor", discriminator_keyword="Tube", summary_rows=False)
    k.columns += [Column("stat", (), StatSpec(Stat.COUNT), heading="A", discriminator="A"),
                  Column("stat", (), StatSpec(Stat.COUNT), heading="B", discriminator="B")]
    df = compute_table(ws, k)
    assert list(df.index) == ["D1", "D2"]
    assert df.loc["D1", "A"] == ws.samples[E01].ps.event_count and df.loc["D1", "B"] == ws.samples[E03].ps.event_count
    assert math.isnan(df.loc["D2", "B"])


def test_export_formats_and_heat(ws, tmp_path):
    t = TableDef(name="Freqs")
    t.add_population(("Cells", "CD3+")).heatmap = True
    df = compute_table(ws, t)
    for ext in ("csv", "txt", "xlsx", "html"):
        export_table(df, t, tmp_path / f"t.{ext}")
    back = pd.read_csv(tmp_path / "t.csv", index_col=0)
    assert list(back.index) == [E01, E03, E05, "Mean", "SD"]
    np.testing.assert_allclose(back.iloc[:3, 0].to_numpy(), df.iloc[:3, 0].to_numpy().astype(float))
    wb = load_workbook(tmp_path / "t.xlsx")
    fills = [wb.active.cell(row=r, column=2).fill.fgColor.rgb for r in (2, 3, 4)]
    assert len(set(fills)) > 1  # heat-mapped
    assert "background-color" in (tmp_path / "t.html").read_text()
    assert heat_colors([1, 2, "x"])[2] is None and heat_colors([1, 2])[0] == "#fff596"


def test_json_round_trip():
    t = TableDef(name="T", group="G", iterate="panel", panel_size=3)
    t.columns += [Column("stat", ("A", "B"), StatSpec(Stat.FREQ_OF, ancestor=("A",)), heatmap=True),
                  Column("formula", formula="{x}+1", heading="y")]
    assert table_from_json(table_to_json(t)) == t
