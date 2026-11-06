"""Writing FlowJo 10 workspace (.wsp) files.

The layout follows the workspaces FlowJo 10.8 writes (see tests/data): a
Matrices block, Groups (GroupNode with SampleRefs and the group-owned gates),
and a SampleList with, per sample, the FCS file URI, compensation matrix,
transforms, keywords and the gate tree with its statistics and FlowJo's
cached event counts.

What FlowJo has no place for (exact label positions, CytoGate tables and
layouts, ...) goes in a side file "<name>.wsp.cytogate.json", so the .wsp
stays a plain FlowJo file.
"""

from __future__ import annotations

import datetime
import itertools
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from ..core.gating import BooleanGate, EllipseGate, PolygonGate, Population, RectangleGate
from ..core.stats import Stat
from ..core.transforms import NS, _fmt
from .wsp import ANNO_SCALE

__all__ = ["write_wsp", "sidecar_path", "FLOWJO_VERSION"]

FLOWJO_VERSION = "10.8.1"  # the format version we write (attributes as FlowJo 10.8 writes them)
_G = "{%s}" % NS["gating"]
_D = "{%s}" % NS["data-type"]
_FJ_NAME = {Stat.GEOMEAN: "Geom. Mean"}  # FlowJo's own names where they differ from ours

ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")


def sidecar_path(wsp_path) -> Path:
    p = Path(wsp_path)
    return p.with_name(p.name + ".cytogate.json")


def _sub(parent, tag, **attrs) -> ET.Element:
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


def _num(v: float) -> str:
    return _fmt(v)


def _dimension(gate_el, name: str, lo=None, hi=None) -> None:
    d = _sub(gate_el, _G + "dimension")
    if lo is not None:
        d.set(_G + "min", _num(lo))
    if hi is not None:
        d.set(_G + "max", _num(hi))
    _sub(d, _D + "fcs-dimension", **{_D + "name": name})


def _vertex(parent, coords) -> None:
    v = _sub(parent, _G + "vertex")
    for c in coords:
        _sub(v, _G + "coordinate", **{_D + "value": _num(c)})


def _gate_xml(gate_el: ET.Element, pop: Population, gid: str, parent_gid: str | None) -> None:
    g = pop.gate
    common = {
        "eventsInside": "1" if g.inside else "0",
        "annoOffsetX": str(int(round(pop.label_offset[0] * ANNO_SCALE))),
        "annoOffsetY": str(int(round(pop.label_offset[1] * ANNO_SCALE))),
        "tint": pop.tint or "#000000",
        "isTinted": "1" if pop.tint else "0",
        "lineWeight": "Normal",
        "userDefined": "1",
        _G + "id": gid,
    }
    if parent_gid:
        common[_G + "parent_id"] = parent_gid
    if isinstance(g, RectangleGate):
        el = _sub(gate_el, _G + "RectangleGate", percentX="0", percentY="0", **common)
        for name, lo, hi in zip(g.dims, g.mins, g.maxs):
            _dimension(el, name, lo, hi)
    elif isinstance(g, PolygonGate):
        el = _sub(gate_el, _G + "PolygonGate", quadId="-1", gateResolution="256", **common)
        for name in g.dims:
            _dimension(el, name)
        for vtx in g.vertices:
            _vertex(el, vtx)
    elif isinstance(g, EllipseGate):
        foci, edges = g.flowjo_points()
        el = _sub(gate_el, _G + "EllipsoidGate", **common, **{_G + "distance": _num(2 * g.a * 256)})
        for name in g.dims:
            _dimension(el, name)
        f = _sub(el, _G + "foci")
        for p in foci:
            _vertex(f, p)
        e = _sub(el, _G + "edge")
        for p in edges:
            _vertex(e, p)
    else:
        raise TypeError(f"cannot write {type(g).__name__}")


def _statistic_xml(parent, spec, owner: str) -> None:
    attrs = {"name": _FJ_NAME.get(spec.stat, spec.stat.value), "annotation": "", "owningGroup": owner,
             "expanded": "0", "sortPriority": "5", "ancestor": "", "correlate": ""}
    if spec.parameter:
        attrs["id"] = spec.parameter
    if spec.stat == Stat.PERCENTILE:
        attrs["percent"] = _num(spec.percentile)
    if spec.stat == Stat.FREQ_OF:
        attrs["ancestor"] = "/".join(spec.ancestor)
    _sub(parent, "Statistic", **attrs)


def _population_xml(parent_el, pop: Population, analysis, ids, parent_gid=None, counts=True) -> None:
    owner = pop.owning_group or ""
    count = str(analysis.count(pop.path)) if counts and analysis is not None else "-1"
    common = {"name": pop.name, "annotation": "", "owningGroup": owner, "expanded": "1", "count": count}
    gid = None
    if isinstance(pop.gate, BooleanGate):
        tag = {"and": "AndNode", "or": "OrNode", "not": "NotNode"}[pop.gate.op]
        el = _sub(parent_el, tag, sortPriority="15", **common)
        deps = _sub(el, "Dependents")
        for ref in pop.gate.refs:
            _sub(deps, "Dependent", name="/".join(ref))
    else:
        el = _sub(parent_el, "Population", sortPriority="10", **common)
        gid = f"ID{next(ids)}"
        gate_el = _sub(el, "Gate", **{_G + "id": gid})
        if parent_gid:
            gate_el.set(_G + "parent_id", parent_gid)
        _gate_xml(gate_el, pop, gid, parent_gid)
    if pop.children or pop.stats:
        sub = _sub(el, "Subpopulations")
        for spec in pop.stats:
            _statistic_xml(sub, spec, owner)
        for c in pop.children:
            _population_xml(sub, c, analysis, ids, gid or parent_gid, counts)


def _keywords_xml(parent, keywords: dict, version: str) -> None:
    kw = _sub(parent, "Keywords")
    _sub(kw, "Keyword", name="FJ_FCS_VERSION", value=version)
    for k, v in keywords.items():
        _sub(kw, "Keyword", name=k, value=v)


def write_wsp(path, workspace, extras: dict | None = None) -> Path:
    """Save a core.workspace.Workspace as a FlowJo 10 .wsp (+ side file).

    `extras` is stored in the side file next to CytoGate's own per-sample data.
    """
    path = Path(path)
    ids = itertools.count(1)
    root = ET.Element("Workspace", {
        "version": "20.0",
        "modDate": datetime.datetime.now().astimezone().strftime("%a %b %d %H:%M:%S %Z %Y"),
        "flowJoVersion": FLOWJO_VERSION,
        "curGroup": "All Samples",
    })
    names = list(workspace.samples)
    sample_ids = {n: str(i + 1) for i, n in enumerate(names)}

    matrices = _sub(root, "Matrices")
    seen_ids = set()
    for a in workspace.samples.values():
        comp = a.ps.compensation
        if comp is not None and comp.id not in seen_ids:
            seen_ids.add(comp.id)
            matrices.append(comp.to_xml(version=f"FlowJo-{FLOWJO_VERSION}"))

    groups_el = _sub(root, "Groups")
    for gname, group in workspace.groups.items():
        node = _sub(groups_el, "GroupNode", name=gname, annotation="", owningGroup=gname, expanded="1",
                    sortPriority="10", count="-1")
        g = _sub(node, "Group", name=gname, live="1", role="ws.group.dlog.test", key="", synchronized="0")
        _sub(g, "Criteria")
        refs = _sub(g, "SampleRefs")
        for n in group.samples:
            _sub(refs, "SampleRef", sampleID=sample_ids[n])
        # the group's own gates: those it owns, taken from its first sample that has them
        template = [c for n in group.samples for c in workspace.samples[n].gates.root.children
                    if c.owning_group == gname]
        if template:
            sub = _sub(node, "Subpopulations")
            done = set()
            for pop in template:
                if pop.name not in done:
                    done.add(pop.name)
                    _population_xml(sub, pop, None, ids, counts=False)

    samples_el = _sub(root, "SampleList")
    side = {"version": 1, "samples": {}}
    for n in names:
        a = workspace.samples[n]
        s_el = _sub(samples_el, "Sample")
        _sub(s_el, "DataSet", uri=a.ps.sample.path.resolve().as_uri(), sampleID=sample_ids[n])
        if a.ps.compensation is not None:
            s_el.append(a.ps.compensation.to_xml(version=f"FlowJo-{FLOWJO_VERSION}"))
        xforms = _sub(s_el, "Transformations")
        for p in a.ps.parameters:
            xforms.append(a.ps.transforms[p.name].to_xml(p.name))
        keywords = dict(a.ps.sample.keywords)
        keywords.update(getattr(a, "user_keywords", {}) or {})
        _keywords_xml(s_el, keywords, a.ps.sample.version)
        node = _sub(s_el, "SampleNode", name=n, annotation="", owningGroup="", expanded="1", sortPriority="10",
                    count=str(a.ps.event_count), sampleID=sample_ids[n])
        if a.gates.root.children or a.gates.root.stats:
            sub = _sub(node, "Subpopulations")
            for spec in a.gates.root.stats:
                _statistic_xml(sub, spec, "")
            for pop in a.gates.root.children:
                _population_xml(sub, pop, a, ids)
        side["samples"][n] = {
            "label_offsets": {"/".join(p.path): list(p.label_offset) for p in a.gates if p.label_offset != (0.0, 0.0)},
        }
    ET.indent(root, space="  ")
    path.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8"))
    side["groups"] = {g.name: [vars(c) for c in g.criteria] for g in workspace._groups.values() if g.criteria}
    side["extras"] = extras or {}
    sidecar_path(path).write_text(json.dumps(side, indent=1, default=str))
    return path
