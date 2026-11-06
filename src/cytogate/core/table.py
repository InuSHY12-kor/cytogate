"""Tables of statistics, FlowJo Table Editor style.

A table definition is a list of columns; each column is a statistic of a
population, a keyword, or a formula over other columns. The definition is
batched over the samples of a group:

- iterate "sample":  one row per sample;
- iterate "panel":   one row per `panel_size` consecutive samples, each column
                     taking its value from tube `panel_index` (1-based) of the panel;
- iterate "keyword": one row per value of `iterate_keyword`; each column takes
                     its value from the sample whose `discriminator_keyword`
                     equals the column's `discriminator`.

Column options as in FlowJo: a heading (default: FlowJo's "Pop/Path | Stat
(Param)"), Control (keep the first row's value for every row), Show (hide
helper columns used by formulas) and heat-map colouring. Mean and SD rows
are appended like FlowJo's batched tables.
"""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .stats import Stat, StatSpec, column_name

__all__ = ["Column", "TableDef", "compute_table", "evaluate_formula", "export_table", "heat_colors",
           "column_from_json", "table_from_json"]


@dataclass
class Column:
    kind: str = "stat"  # "stat" | "keyword" | "formula"
    path: tuple[str, ...] = ()
    spec: StatSpec | None = None
    keyword: str = ""
    formula: str = ""
    heading: str = ""
    control: bool = False
    show: bool = True
    heatmap: bool = False
    panel_index: int = 1  # iterate "panel": which tube of the panel
    discriminator: str = ""  # iterate "keyword": which tube (its discriminator keyword value)

    def header(self, parameter_label=None) -> str:
        if self.heading:
            return self.heading
        if self.kind == "keyword":
            return self.keyword
        if self.kind == "formula":
            return self.formula
        return column_name(tuple(self.path), self.spec, parameter_label)


@dataclass
class TableDef:
    name: str = "Table"
    columns: list[Column] = field(default_factory=list)
    group: str = "All Samples"
    iterate: str = "sample"  # "sample" | "panel" | "keyword"
    panel_size: int = 1
    iterate_keyword: str = ""
    discriminator_keyword: str = ""
    summary_rows: bool = True  # Mean and SD at the bottom

    def add_population(self, path) -> Column:
        """Dragging a gate in: a Freq. of Parent column (FlowJo)."""
        c = Column("stat", tuple(path), StatSpec(Stat.FREQ_PARENT))
        self.columns.append(c)
        return c

    def add_statistic(self, path, spec: StatSpec) -> Column:
        c = Column("stat", tuple(path), spec)
        self.columns.append(c)
        return c


# --- formulas ---------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"log": math.log, "log10": math.log10, "log2": math.log2, "sqrt": math.sqrt, "abs": abs,
          "min": min, "max": max, "exp": math.exp, "round": round}


def evaluate_formula(formula: str, values: dict[str, float]) -> float:
    """Arithmetic over column values: columns are written in braces,
    e.g. "{CD4} / {CD8}" or "100 * {A} / ({A} + {B})"."""
    names = {}

    def sub(m_name):
        key = f"__c{len(names)}"
        names[key] = m_name
        return key

    import re
    expr = re.sub(r"\{([^}]+)\}", lambda m: sub(m.group(1)), formula)
    tree = ast.parse(expr, mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name) and n.id in names:
            v = values.get(names[n.id])
            if v is None:
                raise KeyError(names[n.id])
            return float(v)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError(f"not allowed in a formula: {ast.dump(n)[:40]}")

    try:
        return ev(tree)
    except ZeroDivisionError:
        return math.nan


# --- batching ----------------------------------------------------------------------

def _rows(ws, t: TableDef) -> list[tuple[str, dict[int, str]]]:
    """[(row label, {column index: sample name})]."""
    samples = list(ws.group(t.group).samples)
    cols = range(len(t.columns))
    if t.iterate == "panel" and t.panel_size > 1:
        out = []
        for i in range(0, len(samples), t.panel_size):
            panel = samples[i:i + t.panel_size]
            out.append((panel[0], {c: panel[t.columns[c].panel_index - 1]
                                   for c in cols if t.columns[c].panel_index - 1 < len(panel)}))
        return out
    if t.iterate == "keyword" and t.iterate_keyword:
        groups: dict[str, list[str]] = {}
        for s in samples:
            groups.setdefault(ws.samples[s].keyword(t.iterate_keyword, ""), []).append(s)
        out = []
        for value, members in groups.items():
            pick = {}
            for c in cols:
                d = t.columns[c].discriminator
                match = [s for s in members if not d or ws.samples[s].keyword(t.discriminator_keyword, "") == d]
                if match:
                    pick[c] = match[0]
            out.append((value, pick))
        return out
    return [(s, {c: s for c in cols}) for s in samples]


def _value(ws, col: Column, sample: str):
    a = ws.samples[sample]
    if col.kind == "keyword":
        return a.keyword(col.keyword, "")
    try:
        return a.stat(tuple(col.path), col.spec)
    except KeyError:
        return math.nan


def compute_table(ws, t: TableDef, include_hidden: bool = False) -> pd.DataFrame:
    """The batched table: one row per iteration, the sample (or keyword value) as index."""
    rows = _rows(ws, t)
    headers = []
    for c in t.columns:
        label = None
        if c.kind == "stat" and c.spec is not None and c.spec.parameter and ws.samples:
            first = next(iter(ws.samples.values()))
            try:
                label = first.ps.parameter(c.spec.parameter).label
            except KeyError:
                label = None
        headers.append(c.header(label))
    data = []
    for _, pick in rows:
        rec = {}
        for i, c in enumerate(t.columns):
            if c.kind == "formula":
                continue
            rec[headers[i]] = _value(ws, c, pick[i]) if i in pick else math.nan
        data.append(rec)
    # control columns: the first row's value everywhere
    for i, c in enumerate(t.columns):
        if c.control and c.kind != "formula" and data:
            for rec in data[1:]:
                rec[headers[i]] = data[0][headers[i]]
    # formulas, in column order (may use earlier formulas)
    for i, c in enumerate(t.columns):
        if c.kind != "formula":
            continue
        for rec in data:
            try:
                rec[headers[i]] = evaluate_formula(c.formula, {k: v for k, v in rec.items()
                                                               if isinstance(v, (int, float))})
            except (KeyError, ValueError, TypeError, OverflowError):
                rec[headers[i]] = math.nan
    df = pd.DataFrame(data, columns=headers, index=[label for label, _ in rows])
    df.index.name = "Name"
    if not include_hidden:
        df = df[[h for h, c in zip(headers, t.columns) if c.show]]
    if t.summary_rows and len(df):
        num = df.apply(pd.to_numeric, errors="coerce")
        df.loc["Mean"] = num.mean(numeric_only=False)
        df.loc["SD"] = num.std(ddof=1, numeric_only=False)
    return df


# --- heat map and export ----------------------------------------------------------------

def heat_colors(values) -> list[str | None]:
    """FlowJo-style yellow (low) → blue (high) per column; None for non-numbers."""
    v = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(v)
    out: list[str | None] = [None] * len(v)
    if ok.sum() == 0:
        return out
    lo, hi = np.nanmin(v[ok]), np.nanmax(v[ok])
    for i, x in enumerate(v):
        if not ok[i]:
            continue
        t = 0.5 if hi == lo else (x - lo) / (hi - lo)
        low, high = np.array([255, 245, 150]), np.array([90, 140, 230])
        r, g, b = (low + (high - low) * t).astype(int)
        out[i] = f"#{r:02x}{g:02x}{b:02x}"
    return out


def export_table(df: pd.DataFrame, t: TableDef, path, fmt: str | None = None) -> str:
    """Write as csv, txt (tab separated), xlsx or html (heat-mapped columns coloured)."""
    from pathlib import Path
    path = Path(path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    heat = {c.header() for c in t.columns if c.heatmap}
    heat |= {h for h in df.columns if any(c.heatmap and h.endswith(c.header()) for c in t.columns)}
    body = df.drop(index=[i for i in ("Mean", "SD") if i in df.index]) if len(df) else df
    if fmt == "csv":
        df.to_csv(path)
    elif fmt in ("txt", "text", "tsv"):
        df.to_csv(path, sep="\t")
    elif fmt == "xlsx":
        from openpyxl.styles import PatternFill
        df.to_excel(path, sheet_name=t.name[:31] or "Table")
        from openpyxl import load_workbook
        wb = load_workbook(path)
        ws = wb.active
        for j, h in enumerate(df.columns, start=2):
            if h in heat:
                for i, color in enumerate(heat_colors(body[h]), start=2):
                    if color:
                        ws.cell(row=i, column=j).fill = PatternFill("solid", fgColor=color[1:])
        wb.save(path)
    elif fmt in ("html", "htm"):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for h in df.columns:
            if h in heat:
                for idx, color in zip(body.index, heat_colors(body[h])):
                    if color:
                        styles.loc[idx, h] = f"background-color: {color}"
        html = df.style.apply(lambda _: styles, axis=None).set_caption(t.name).to_html()
        path.write_text(f"<!doctype html><meta charset='utf-8'><title>{t.name}</title>{html}")
    else:
        raise ValueError(f"unknown table format {fmt!r}")
    return str(path)


# --- saving definitions --------------------------------------------------------------------

def column_to_json(c: Column) -> dict:
    d = asdict(c)
    d["path"] = list(c.path)
    d["spec"] = None if c.spec is None else {"stat": c.spec.stat.value, "parameter": c.spec.parameter,
                                              "percentile": c.spec.percentile,
                                              "ancestor": list(c.spec.ancestor) if c.spec.ancestor else None}
    return d


def column_from_json(d: dict) -> Column:
    d = dict(d)
    s = d.pop("spec")
    spec = None if s is None else StatSpec(Stat(s["stat"]), s.get("parameter"), s.get("percentile"),
                                           tuple(s["ancestor"]) if s.get("ancestor") else None)
    d["path"] = tuple(d.get("path", ()))
    return Column(spec=spec, **d)


def table_to_json(t: TableDef) -> dict:
    d = asdict(t)
    d["columns"] = [column_to_json(c) for c in t.columns]
    return d


def table_from_json(d: dict) -> TableDef:
    d = dict(d)
    cols = [column_from_json(c) for c in d.pop("columns", [])]
    return TableDef(columns=cols, **d)
