"""Compare CytoGate with the numbers FlowJo saved in a workspace.

For every sample of a FlowJo .wsp whose FCS file can be found, this checks
- the event count FlowJo stored for every population, and
- every statistic FlowJo stored (<Statistic ... value="...">),
recomputing each with CytoGate (FlowJo-mode binned statistics and exact).

    python scripts/validate_flowjo.py WORKSPACE.wsp [FCS_FOLDER ...] [--csv report.csv]

Prints a summary and writes one row per comparison to the CSV.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import math
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cytogate.core.stats import StatMode  # noqa: E402
from cytogate.io.wsp import _POP_TAGS, parse_statistic, read_wsp  # noqa: E402
from cytogate.io.wsp_load import load_workspace  # noqa: E402


def stored_statistics(wsp_path):
    """{sample_id: [(path, Statistic element)]} in workspace order."""
    root = ET.parse(wsp_path).getroot()
    out = defaultdict(list)

    def walk(el, sid, path):
        for c in el:
            if c.tag == "Statistic":
                out[sid].append((path, c))
            elif c.tag in _POP_TAGS:
                for sp in c.findall("Subpopulations"):
                    walk(sp, sid, path + (c.get("name"),))
            elif c.tag == "Subpopulations":
                walk(c, sid, path)

    for s in root.iter("Sample"):
        node = s.find("SampleNode")
        sid = s.find("DataSet").get("sampleID") or node.get("sampleID")
        walk(node, sid, ())
    return out


def rel_diff(a, b):
    if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return math.nan
    if a == b:
        return 0.0
    return abs(a - b) / max(abs(b), 1e-12)


def validate(wsp_path, search_dirs=(), tol_count=0, tol_stat=0.01):
    wsp_path = Path(wsp_path)
    wsp = read_wsp(wsp_path)
    opened = load_workspace(wsp_path, search_dirs)
    ws = opened.workspace
    stats = stored_statistics(wsp_path)
    rows = []
    for s in wsp.samples:
        name = s.file_name
        a = ws.samples.get(name)
        if a is None:
            continue
        for path, fj in s.flowjo_counts.items():
            if fj < 0:
                continue
            try:
                ours = a.count(path)
            except Exception as exc:  # noqa: BLE001 - reported, not hidden
                rows.append(dict(sample=name, population="/".join(path) or "Ungated", statistic="Count",
                                 parameter="", flowjo=fj, flowjo_mode="", exact="", rel_diff="", ok=False,
                                 note=f"error: {exc}"))
                continue
            rows.append(dict(sample=name, population="/".join(path) or "Ungated", statistic="Count", parameter="",
                             flowjo=fj, flowjo_mode=ours, exact=ours, rel_diff=rel_diff(ours, fj),
                             ok=abs(ours - fj) <= tol_count, note=""))
        for path, el in stats.get(s.sample_id, []):
            raw = el.get("value")
            spec = parse_statistic(el)
            label = el.get("name")
            row = dict(sample=name, population="/".join(path) or "Ungated", statistic=label,
                       parameter=el.get("id") or el.get("ancestor") or "", flowjo=raw, flowjo_mode="", exact="",
                       rel_diff="", ok="", note="")
            try:
                fj = float(raw)
            except (TypeError, ValueError):
                fj = math.nan
            if label == "fj.stat.freqof" and opened.flowjo_version.startswith("10.0") and not math.isnan(fj):
                fj *= 100  # FlowJo 10.0 stores "Freq. of" as a fraction, later versions as a percent
                row["flowjo"] = fj
            if spec is None:
                row["note"] = "statistic not supported"
            elif math.isnan(fj):
                row["note"] = "FlowJo value is NaN"
            else:
                if spec.parameter:
                    known = {q.name for q in a.ps.parameters}
                    if spec.parameter not in known:
                        alt = {q.name.replace("/", "_"): q.name for q in a.ps.parameters}
                        spec = dataclasses.replace(spec, parameter=alt.get(spec.parameter, spec.parameter))
                try:
                    v1 = a.stat(path, spec, mode=StatMode.FLOWJO)
                    v2 = a.stat(path, spec, mode=StatMode.EXACT)
                except Exception as exc:  # noqa: BLE001
                    row["note"] = f"error: {type(exc).__name__}: {exc}"
                else:
                    d = rel_diff(v1, fj)
                    row.update(flowjo_mode=v1, exact=v2, rel_diff=d, ok=bool(d <= tol_stat) if not math.isnan(d) else False)
            rows.append(row)
    return opened, rows


def summarize(rows):
    by = defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        if r["ok"] == "":
            continue
        k = r["statistic"]
        by[k][0] += 1
        by[k][1] += bool(r["ok"])
        d = r["rel_diff"]
        if isinstance(d, float) and not math.isnan(d):
            by[k][2] = max(by[k][2], d)
    return by


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("wsp")
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--csv")
    args = ap.parse_args(argv)
    opened, rows = validate(args.wsp, args.dirs)
    print(f"{Path(args.wsp).name}  FlowJo {opened.flowjo_version}  samples loaded {len(opened.workspace.samples)}"
          f"  missing {len(opened.missing)}")
    for k, (n, ok, worst) in sorted(summarize(rows).items()):
        print(f"  {k:28s} {ok:5d}/{n:<5d} match   worst relative difference {worst:.3g}")
    skipped = defaultdict(int)
    for r in rows:
        if r["note"]:
            skipped[r["note"].split(":")[0]] += 1
    for k, n in skipped.items():
        print(f"  not compared: {k} ({n})")
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return rows


if __name__ == "__main__":
    main()
