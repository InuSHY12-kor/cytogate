"""Reading FlowJo 10 workspace (.wsp) files.

Gives, per sample: the FCS file reference, keywords, compensation matrix,
per-parameter transforms, the gate tree, and the event count FlowJo stored
for each population (used to check our results). Writing .wsp files comes
in a later phase.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..core.compensation import Compensation
from ..core.gating import BooleanGate, EllipseGate, GatingTree, PolygonGate, RectangleGate
from ..core.stats import PARAMETER_STATS, Stat, StatSpec
from ..core.transforms import NS, Transform, from_xml

__all__ = ["WspSample", "Workspace", "read_wsp"]

_G = "{%s}" % NS["gating"]
_T = "{%s}" % NS["transforms"]
_D = "{%s}" % NS["data-type"]
_POP_TAGS = {"Population", "AndNode", "OrNode", "NotNode"}


@dataclass
class WspSample:
    sample_id: str
    uri: str
    keywords: dict[str, str]
    compensation: Compensation | None
    transforms: dict[str, Transform]
    gates: GatingTree
    flowjo_counts: dict[tuple[str, ...], int]  # path -> count FlowJo saved (-1 = not computed)

    @property
    def file_name(self) -> str:
        return Path(unquote(urlparse(self.uri).path)).name or self.uri.rsplit("/", 1)[-1]


@dataclass
class Workspace:
    path: Path
    flowjo_version: str
    samples: list[WspSample]
    groups: dict[str, list[str]] = field(default_factory=dict)  # group name -> sample ids

    def sample(self, file_name: str) -> WspSample:
        for s in self.samples:
            if s.file_name == file_name:
                return s
        raise KeyError(file_name)


def _dims(gate_el) -> list[ET.Element]:
    return gate_el.findall(_G + "dimension")


def _dim_name(dim_el) -> str:
    fd = dim_el.find(_D + "fcs-dimension")
    if fd is None:  # Gating-ML 1.5 (FlowJo 10.0)
        fd = dim_el.find(_D + "parameter")
    return fd.get(_D + "name")


def _vertices(parent) -> list[tuple[float, ...]]:
    return [tuple(float(c.get(_D + "value")) for c in v.findall(_G + "coordinate")) for v in parent.findall(_G + "vertex")]


def _opt(value: str | None) -> float | None:
    return None if value is None else float(value)


def parse_gate(gate_el: ET.Element):
    """Gate from a FlowJo <Gate> element's child (RectangleGate, PolygonGate, EllipsoidGate)."""
    kind = gate_el.tag.rsplit("}", 1)[-1]
    inside = gate_el.get("eventsInside", "1") != "0"
    dims = _dims(gate_el)
    names = tuple(_dim_name(d) for d in dims)
    if kind == "RectangleGate":
        return RectangleGate(
            names,
            tuple(_opt(d.get(_G + "min")) for d in dims),
            tuple(_opt(d.get(_G + "max")) for d in dims),
            inside,
        )
    if kind == "PolygonGate":
        return PolygonGate(names, tuple(_vertices(gate_el)), inside)
    if kind == "EllipsoidGate":
        return EllipseGate.from_flowjo(names, _vertices(gate_el.find(_G + "foci")), _vertices(gate_el.find(_G + "edge")), inside)
    raise ValueError(f"unsupported FlowJo gate type {kind!r}")


# FlowJo statistic names <-> ours
_FJ_STATS = {
    "Count": Stat.COUNT, "Freq. of Parent": Stat.FREQ_PARENT, "Freq. of Grandparent": Stat.FREQ_GRANDPARENT,
    "Freq. of Total": Stat.FREQ_TOTAL, "Freq. of": Stat.FREQ_OF, "Mean": Stat.MEAN, "Median": Stat.MEDIAN,
    "Geom. Mean": Stat.GEOMEAN, "Geometric Mean": Stat.GEOMEAN, "Mode": Stat.MODE, "SD": Stat.SD, "CV": Stat.CV,
    "Robust SD": Stat.ROBUST_SD, "Robust CV": Stat.ROBUST_CV, "Median Abs Dev": Stat.MAD, "MADP": Stat.MADP,
    "Percentile": Stat.PERCENTILE,
    # FlowJo 10 internal names
    "fj.stat.freqofparent": Stat.FREQ_PARENT, "fj.stat.freqofgrandparent": Stat.FREQ_GRANDPARENT,
    "fj.stat.freqoftotal": Stat.FREQ_TOTAL, "fj.stat.freqof": Stat.FREQ_OF,
}
# FlowJo stores label offsets in graph pixels (256 per axis); we keep display units
ANNO_SCALE = 256.0


def parse_statistic(el: ET.Element) -> StatSpec | None:
    stat = _FJ_STATS.get(el.get("name", ""))
    if stat is None:
        return None
    if stat == Stat.FREQ_OF and (el.get("ancestor") or "") in ("Total", ""):
        stat = Stat.FREQ_TOTAL  # FlowJo 10.0 writes Freq. of Total as "freq. of" ancestor "Total"
    try:
        anc = el.get("ancestor") or ""
        return StatSpec(
            stat,
            el.get("id") or None if stat in PARAMETER_STATS else None,
            float(el.get("percent")) if stat == Stat.PERCENTILE and el.get("percent") else None,
            tuple(p for p in anc.split("/") if p) if stat == Stat.FREQ_OF else None,
        )
    except (ValueError, TypeError):
        return None


def _walk(sub_el: ET.Element, tree: GatingTree, parent_path: tuple, counts: dict) -> None:
    parent = tree.get(parent_path)
    for el in sub_el:
        if el.tag == "Statistic":
            spec = parse_statistic(el)
            if spec is not None and spec not in parent.stats:
                parent.stats.append(spec)
            continue
        if el.tag not in _POP_TAGS:
            continue
        name = el.get("name")
        gate_el = None
        if el.tag == "Population":
            gate_el = next(iter(el.find("Gate")))
            gate = parse_gate(gate_el)
        else:
            op = {"AndNode": "and", "OrNode": "or", "NotNode": "not"}[el.tag]
            refs = tuple(tuple(p for p in d.get("name").split("/") if p) for d in el.find("Dependents").findall("Dependent"))
            gate = BooleanGate(op, refs)
        pop = tree.add(parent_path, name, gate)
        counts[pop.path] = int(el.get("count", "-1"))
        pop.owning_group = el.get("owningGroup") or None
        if gate_el is not None:
            ox, oy = float(gate_el.get("annoOffsetX", 0)), float(gate_el.get("annoOffsetY", 0))
            pop.label_offset = (ox / ANNO_SCALE, oy / ANNO_SCALE)
            if gate_el.get("isTinted") == "1":
                pop.tint = gate_el.get("tint")
        for sp in el.findall("Subpopulations"):
            _walk(sp, tree, pop.path, counts)


_OLD_NS = {  # FlowJo 10.0.x writes Gating-ML 1.5 namespaces; the elements are the same
    "http://flowcyt.sourceforge.net/std/Gating-ML/v1.5/gating": NS["gating"],
    "http://flowcyt.sourceforge.net/std/Gating-ML/v1.5/transformations": NS["transforms"],
    "http://flowcyt.sourceforge.net/std/Gating-ML/v1.5/datatypes": NS["data-type"],
}


def _normalize_namespaces(root: ET.Element) -> None:
    def fix(name: str) -> str:
        if name.startswith("{"):
            uri, local = name[1:].split("}", 1)
            if uri in _OLD_NS:
                return "{%s}%s" % (_OLD_NS[uri], local)
        return name

    for el in root.iter():
        el.tag = fix(el.tag)
        if any(k.startswith("{") for k in el.attrib):
            el.attrib = {fix(k): v for k, v in el.attrib.items()}


def read_wsp(path: str | Path) -> Workspace:
    path = Path(path)
    root = ET.parse(path).getroot()
    _normalize_namespaces(root)
    samples = []
    for s_el in root.iter("Sample"):
        ds = s_el.find("DataSet")
        node = s_el.find("SampleNode")
        spill = s_el.find(".//" + _T + "spilloverMatrix")
        xforms_el = s_el.find("Transformations")
        transforms = dict(from_xml(e) for e in xforms_el) if xforms_el is not None else {}
        kw_el = s_el.find("Keywords")
        keywords = {k.get("name"): k.get("value") for k in kw_el.iter("Keyword")} if kw_el is not None else {}
        tree, counts = GatingTree(), {}
        counts[()] = int(node.get("count", "-1")) if node is not None else -1
        if node is not None:
            for sp in node.findall("Subpopulations"):
                _walk(sp, tree, (), counts)
        samples.append(WspSample(
            sample_id=ds.get("sampleID") or node.get("sampleID", ""),
            uri=ds.get("uri", ""),
            keywords=keywords,
            compensation=Compensation.from_xml(spill) if spill is not None else None,
            transforms=transforms,
            gates=tree,
            flowjo_counts=counts,
        ))
    groups = {}
    for g in root.iter("GroupNode"):
        refs = g.findall(".//SampleRef")
        groups[g.get("name")] = [r.get("sampleID") for r in refs]
    return Workspace(path, root.get("flowJoVersion", ""), samples, groups)
