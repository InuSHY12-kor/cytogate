"""End-to-end check of a CytoGate installation (also of the packaged .exe).

Writes a small synthetic FCS file, then reads it, gates it, computes statistics
and writes every output format: .wsp, FCS/CSV export, table CSV/XLSX, layout
PDF/PNG/SVG/PPTX/HTML. Run with `cytogate --selftest [report.txt]`; exit code 0
means everything worked. Files that a packaged build forgot (Qt plugins,
python-pptx templates, FlowKit schemas …) show up here as errors.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path


def _synthetic_fcs(path: Path, n: int = 5000) -> None:
    import flowio
    import numpy as np

    rng = np.random.default_rng(0)
    fsc = np.concatenate([rng.normal(60000, 8000, n // 2), rng.normal(120000, 15000, n - n // 2)])
    ssc = np.concatenate([rng.normal(20000, 4000, n // 2), rng.normal(60000, 10000, n - n // 2)])
    cd3 = np.concatenate([rng.lognormal(7.5, 0.4, n // 2), rng.lognormal(4, 0.8, n - n // 2)])
    cd4 = rng.lognormal(6, 1.0, n)
    time = np.arange(n, dtype=float)
    data = np.column_stack([fsc, ssc, cd3, cd4, time]).clip(0, 262143)
    meta = {"SPILL": "2,FL1-A,FL2-A,1,0.1,0.05,1", "$BTIM": "10:00:00", "$ETIM": "10:01:00",
            "$CYT": "CytoGate selftest", "$TIMESTEP": "0.01"}
    with open(path, "wb") as fh:
        flowio.create_fcs(fh, data.astype(np.float32).ravel().tolist(), ["FSC-A", "SSC-A", "FL1-A", "FL2-A", "Time"],
                          opt_channel_names=["", "", "CD3", "CD4", ""], metadata_dict=meta)


def run(report=None) -> int:
    lines: list[str] = []
    failures = 0

    def step(name, fn):
        nonlocal failures
        try:
            detail = fn()
            lines.append(f"ok    {name}{'  ' + str(detail) if detail else ''}")
        except Exception:  # noqa: BLE001 - every failure is reported
            failures += 1
            lines.append(f"FAIL  {name}\n" + traceback.format_exc())

    tmp = Path(tempfile.mkdtemp(prefix="cytogate-selftest-"))
    fcs = tmp / "selftest.fcs"
    ctx: dict = {}

    def make():
        _synthetic_fcs(fcs)
        return fcs.stat().st_size

    def analyse():
        from .core import ProcessedSample, RectangleGate
        from .core.analysis import AnalyzedSample
        from .core.compensation import Compensation
        from .core.stats import Stat, StatSpec
        from .core.workspace import Workspace
        from .io import load_fcs
        s = load_fcs(fcs)
        ps = ProcessedSample(s, compensation=Compensation.from_sample(s))
        a = AnalyzedSample(ps)
        a.add_gate((), "Lymphocytes", RectangleGate(("FSC-A", "SSC-A"), (30000.0, 0.0), (90000.0, 40000.0)))
        a.add_gate("Lymphocytes", "CD3+", RectangleGate(("Comp-FL1-A",), (600.0,), (None,)))
        a.add_stat(("Lymphocytes", "CD3+"), StatSpec(Stat.MEDIAN, "Comp-FL2-A"))
        ws = Workspace()
        ws.add_sample(a)
        ctx.update(ws=ws, a=a)
        n = a.count(("Lymphocytes",))
        if not 2000 < n < 3000:
            raise AssertionError(f"unexpected Lymphocytes count {n}")
        return f"Lymphocytes {n}, CD3+ {a.freq_of_parent(('Lymphocytes', 'CD3+')):.1f} %"

    def workspace_file():
        from .io.wsp_load import load_workspace
        from .io.wsp_write import write_wsp
        out = write_wsp(tmp / "selftest.wsp", ctx["ws"])
        back = load_workspace(out or tmp / "selftest.wsp")
        a2 = back.workspace.samples["selftest.fcs"]
        if a2.count(("Lymphocytes", "CD3+")) != ctx["a"].count(("Lymphocytes", "CD3+")):
            raise AssertionError("counts changed after saving and reopening the workspace")
        return "saved and reopened"

    def export():
        from .io.export import ExportOptions, concatenate, export_populations
        files = export_populations(ctx["ws"], [("selftest.fcs", ("Lymphocytes",))], tmp / "export")
        files += export_populations(ctx["ws"], [("selftest.fcs", ("Lymphocytes",))], tmp / "export",
                                    ExportOptions(fmt="csv_scale"))
        files.append(concatenate(ctx["ws"], [("selftest.fcs", ()), ("selftest.fcs", ("Lymphocytes",))],
                                 tmp / "export" / "concat.fcs"))
        return f"{len(files)} files"

    def tables():
        from .core.table import TableDef, compute_table, export_table
        t = TableDef(name="T")
        t.add_population(("Lymphocytes", "CD3+")).heatmap = True
        df = compute_table(ctx["ws"], t)
        for ext in ("csv", "txt", "xlsx", "html"):
            export_table(df, t, tmp / f"table.{ext}")
        ctx["table"] = t
        return "csv txt xlsx html"

    def gui():
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        from .gui.main_window import MainWindow
        w = MainWindow()
        w.add_samples([str(fcs)])
        g = w.open_graph("selftest.fcs", ())
        g.redraw()
        img = g.view.to_image(1.0)
        w.close_all_graphs()
        w.dirty = False
        w.close()
        app.processEvents()
        return f"main window and graph window ({img.width()}x{img.height()})"

    def layouts():
        from PySide6 import QtWidgets
        QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        from .core.layout import Layer, Layout, LayoutItem
        from .gui.layout_output import export_layout
        lay = Layout(name="Selftest", items=[
            LayoutItem(kind="graph", x=36, y=60, x_param="FSC-A", y_param="SSC-A"),
            LayoutItem(kind="graph", x=250, y=60, path=("Lymphocytes",), x_param="Comp-FL1-A", y_param=None,
                       layers=[Layer(("Lymphocytes", "CD3+"))]),
            LayoutItem(kind="text", y=260, text="{Name}", h=20),
            LayoutItem(kind="table", y=290, w=400, h=80, table="T"),
        ])
        out = []
        for ext in ("pdf", "png", "svg", "pptx", "html"):
            out += export_layout(ctx["ws"], lay, tmp / f"layout.{ext}", tables=[ctx["table"]], dpi=60)
        return f"{len(out)} files"

    for name, fn in (("write synthetic FCS", make), ("read, compensate, gate, statistics", analyse),
                     ("FlowJo workspace save/open", workspace_file), ("export / concatenate", export),
                     ("Table Editor outputs", tables), ("windows", gui), ("Layout Editor outputs", layouts)):
        step(name, fn)
    from . import __version__
    lines.insert(0, f"CytoGate {__version__} selftest — {'PASSED' if not failures else f'{failures} FAILED'}")
    lines.append(f"files in {tmp}")
    text = "\n".join(lines)
    print(text)
    if report:
        Path(report).write_text(text, encoding="utf-8")
    return 1 if failures else 0
