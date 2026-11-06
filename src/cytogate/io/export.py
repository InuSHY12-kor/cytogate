"""Exporting and concatenating populations (FlowJo's Export / Concatenate).

Formats:
- "fcs":         FCS 3.1, float32 values on the scale CytoGate (and FlowJo) shows.
- "csv_scale":   CSV of the same scale values.
- "csv_channel": CSV of channel numbers: the bin (0..4095) of each value on its
                 parameter's axis — the binned values FlowJo computes statistics on.

Parameters: "uncompensated" (all channels as acquired, the spillover matrix
kept so the file can be compensated again — the right choice for re-import),
"compensated" (scatter, time and the Comp- parameters), or a list of names.

FCS details: values are written with $PnE 0,0 and $PnG 1 (gain and log
amplification are already applied), so re-reading gives the same numbers.
Time is written in seconds with $TIMESTEP = 1; the original $BTIM/$ETIM are
kept as CYTOGATE_BTIM/CYTOGATE_ETIM so the time scaling is not recomputed on
the subset.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.stats import BINS

__all__ = ["ExportOptions", "export_populations", "concatenate", "population_table", "FORMATS"]

FORMATS = {"fcs": ".fcs", "csv_scale": ".csv", "csv_channel": ".csv"}
_SKIP_KEYWORDS = re.compile(r"^\$?(P\d+[A-Z]|BEGIN|END|NEXTDATA|TOT|PAR|DATATYPE|BYTEORD|MODE|SPILL|SPILLOVER|COMP"
                            r"|TIMESTEP|BTIM|ETIM)", re.IGNORECASE)


@dataclass
class ExportOptions:
    fmt: str = "fcs"
    parameters: str | list[str] = "uncompensated"
    max_events: int | None = None  # "Include no more than"
    prefix: str = "export_"
    suffix: str = ""
    name_template: str | None = None  # e.g. "{$FIL}_{population}" — keywords in braces
    seed: int = 0
    add_sample_id: bool = True  # concatenation: add a "SampleID" parameter (1, 2, …)
    extra_keywords: dict = field(default_factory=dict)


def _param_names(a, which) -> list[str]:
    ps = a.ps
    if isinstance(which, (list, tuple)):
        return [ps.parameter(p).name for p in which]
    if which == "compensated" and ps.compensation is not None:
        comp = set(ps.compensation.detectors)
        return [p.name for p in ps.parameters if p.name not in comp]
    return [c.pnn for c in ps.sample.channels]


def population_table(a, path, opts: ExportOptions) -> tuple[list[str], list[str], np.ndarray]:
    """(names, stains, values) of a population's events, after subsampling."""
    names = _param_names(a, opts.parameters)
    mask = a.mask(tuple(path))
    idx = np.flatnonzero(mask)
    if opts.max_events is not None and idx.size > opts.max_events:
        rng = np.random.default_rng(opts.seed)
        idx = np.sort(rng.choice(idx, opts.max_events, replace=False))
    values = np.column_stack([a.ps.values(n)[idx] for n in names]) if names else np.empty((idx.size, 0))
    if opts.fmt == "csv_channel":
        for j, n in enumerate(names):
            pos = np.clip(a.ps.transforms[n].apply(values[:, j]), 0, 1)
            values[:, j] = np.minimum((pos * BINS).astype(int), BINS - 1)
    stains = [a.ps.parameter(n).stain for n in names]
    return names, stains, values


def _file_name(a, path, opts: ExportOptions) -> str:
    pop = "_".join(path) if path else "Ungated"
    if opts.name_template:
        def kw(m):
            key = m.group(1)
            if key == "population":
                return pop
            if key == "sample":
                return Path(a.name).stem
            return a.keyword(key, "")
        base = re.sub(r"\{([^}]+)\}", kw, opts.name_template)
    else:
        base = f"{opts.prefix}{Path(a.name).stem}_{pop}{opts.suffix}"
    base = re.sub(r'[\\/:*?"<>|]+', "_", base).strip() or "export"
    return base + FORMATS[opts.fmt]


def _metadata(a, names, opts: ExportOptions, compensated: bool) -> dict:
    meta = {}
    for k, v in a.keywords.items():
        if not _SKIP_KEYWORDS.match(k) and v != "":
            meta[k.lstrip("$") if k.startswith("$") and k[1:].upper() in ("FIL",) else k] = v
    for k in ("$BTIM", "$ETIM"):
        v = a.keyword(k)
        if v:
            meta[f"CYTOGATE_{k[1:]}"] = v
    if a.ps.compensation is not None and not compensated:
        comp = a.ps.compensation
        if all(d in names for d in comp.detectors):
            vals = ",".join(f"{v:.10g}" for v in comp.spillover.values.ravel())
            meta["SPILL"] = f"{len(comp.detectors)},{','.join(comp.detectors)},{vals}"
    for i, n in enumerate(names, start=1):
        p = a.ps.parameter(n) if n != "SampleID" else None
        rng = p.channel_range if p is not None and p.channel_range else 262144
        meta[f"P{i}R"] = str(int(rng))
    meta.update(opts.extra_keywords)
    return meta


def _write(path: Path, names, stains, values, meta, fmt: str) -> Path:
    if fmt == "fcs":
        import flowio
        with open(path, "wb") as fh:
            flowio.create_fcs(fh, values.astype(np.float32).ravel().tolist(), names,
                              opt_channel_names=stains, metadata_dict={k: str(v) for k, v in meta.items()})
    else:
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow([f"{n} :: {s}" if s else n for n, s in zip(names, stains)])
            fmt_v = (lambda v: str(int(v))) if fmt == "csv_channel" else (lambda v: f"{v:.6g}")
            for row in values:
                w.writerow([fmt_v(v) for v in row])
    return path


def export_populations(workspace, items, folder, opts: ExportOptions | None = None) -> list[Path]:
    """One file per (sample, population path)."""
    opts = opts or ExportOptions()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for sample, path in items:
        a = workspace.samples[sample]
        names, stains, values = population_table(a, path, opts)
        compensated = any(n.startswith(a.ps.compensation.prefix) for n in names) if a.ps.compensation else False
        target = folder / _file_name(a, path, opts)
        out.append(_write(target, names, stains, values, _metadata(a, names, opts, compensated), opts.fmt))
    return out


def concatenate(workspace, items, out_file, opts: ExportOptions | None = None) -> Path:
    """All the populations' events in one file. Every sample must have the same
    parameters; with add_sample_id a "SampleID" parameter (1, 2, … in the order
    given) tells the events apart."""
    opts = opts or ExportOptions()
    blocks, first = [], None
    for k, (sample, path) in enumerate(items, start=1):
        a = workspace.samples[sample]
        names, stains, values = population_table(a, path, opts)
        if first is None:
            first = (names, stains, a)
        elif names != first[0]:
            raise ValueError(f"{sample} has different parameters than {first[2].name}")
        if opts.add_sample_id:
            values = np.column_stack([values, np.full(values.shape[0], k, dtype=float)])
        blocks.append(values)
    if first is None:
        raise ValueError("nothing to concatenate")
    names, stains, a0 = first
    if opts.add_sample_id:
        names, stains = [*names, "SampleID"], [*stains, ""]
    values = np.vstack(blocks)
    compensated = any(n.startswith(a0.ps.compensation.prefix) for n in names) if a0.ps.compensation else False
    meta = _metadata(a0, names, opts, compensated)
    if opts.add_sample_id:
        meta[f"P{len(names)}R"] = str(len(items) + 1)
    meta["CYTOGATE_CONCATENATED"] = "; ".join(f"{i}={s}" for i, (s, _) in enumerate(items, start=1))
    out = Path(out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    return _write(out, names, stains, values, meta, opts.fmt)


def group_items(workspace, items, mode: str = "all", n: int = 2, keyword: str | None = None) -> list[list]:
    """Split items for concatenation: "all" together, "every_n" files, or by equal keyword value."""
    items = list(items)
    if mode == "every_n":
        return [items[i:i + n] for i in range(0, len(items), max(n, 1))]
    if mode == "keyword":
        groups: dict[str, list] = {}
        for s, p in items:
            groups.setdefault(workspace.samples[s].keyword(keyword or "", ""), []).append((s, p))
        return list(groups.values())
    return [items]
