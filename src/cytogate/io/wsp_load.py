"""Opening a FlowJo / CytoGate workspace (.wsp) as a core.workspace.Workspace.

FCS files are looked for at the path stored in the .wsp, then next to the
.wsp, then in the extra folders given; anything still missing can be asked
for through `locate(file_name) -> path | None` (FlowJo's "reconnect samples").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..core import ProcessedSample
from ..core.analysis import AnalyzedSample
from ..core.workspace import ALL_SAMPLES, Workspace
from .fcs import load_fcs
from .wsp import read_wsp
from .wsp_write import sidecar_path

__all__ = ["OpenedWorkspace", "load_workspace", "find_fcs"]


@dataclass
class OpenedWorkspace:
    workspace: Workspace
    missing: list[str] = field(default_factory=list)  # FCS files that could not be found
    extras: dict = field(default_factory=dict)  # CytoGate data from the side file
    flowjo_version: str = ""


def _uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        p = unquote(parsed.path)
        if len(p) > 3 and p[0] == "/" and p[2] == ":":  # file:/C:/...
            p = p[1:]
        return Path(p)
    return Path(unquote(uri))


def find_fcs(uri: str, file_name: str, search_dirs) -> Path | None:
    p = _uri_path(uri)
    if p.is_file():
        return p
    for d in search_dirs:
        d = Path(d)
        if (d / file_name).is_file():
            return d / file_name
        for hit in d.rglob(file_name):
            if hit.is_file():
                return hit
    return None


def _restore_parameter_names(s, pnn: list[str]) -> None:
    """FlowJo writes "/" in parameter names as "_" (FITC/Alexa 488-A -> FITC_Alexa 488-A).
    Map those back to the file's names in the compensation, transforms, gates and statistics."""
    import dataclasses

    names = set(pnn)
    base = {p.replace("/", "_"): p for p in pnn if "/" in p and p.replace("/", "_") not in names}
    if not base:
        return
    mapping = dict(base)
    prefix = s.compensation.prefix if s.compensation is not None else "Comp-"
    mapping.update({prefix + k: prefix + v for k, v in base.items()})
    if s.compensation is not None:
        sp = s.compensation.spillover.rename(index=mapping, columns=mapping)
        s.compensation = dataclasses.replace(s.compensation, spillover=sp)
    s.transforms = {mapping.get(k, k): v for k, v in s.transforms.items()}
    for pop in s.gates:
        if pop.gate is not None and getattr(pop.gate, "dims", ()):
            pop.gate = pop.gate.renamed(mapping)
        pop.stats = [dataclasses.replace(sp, parameter=mapping.get(sp.parameter, sp.parameter)) if sp.parameter else sp
                     for sp in pop.stats]


def load_workspace(path, search_dirs=(), locate=None) -> OpenedWorkspace:
    path = Path(path)
    wsp = read_wsp(path)
    ws = Workspace()
    names_by_id = {}
    missing = []
    dirs = [path.parent, *search_dirs]
    for s in wsp.samples:
        fcs = find_fcs(s.uri, s.file_name, dirs)
        if fcs is None and locate is not None:
            got = locate(s.file_name)
            fcs = Path(got) if got else None
            if fcs is not None:
                dirs.append(fcs.parent)  # other missing files are probably there too
        if fcs is None:
            missing.append(s.file_name)
            continue
        sample = load_fcs(fcs)
        _restore_parameter_names(s, [c.pnn for c in sample.channels])
        ps = ProcessedSample(sample, compensation=s.compensation, transforms=s.transforms)
        a = AnalyzedSample(ps, s.gates)
        file_kw = sample.keywords
        a.user_keywords = {k: v for k, v in s.keywords.items()
                           if not k.startswith("FJ_") and k not in file_kw and not k.startswith("$P")}
        ws.add_sample(a)
        names_by_id[s.sample_id] = a.name
    for gname, ids in wsp.groups.items():
        members = [names_by_id[i] for i in ids if i in names_by_id]
        if gname == ALL_SAMPLES or not members:
            continue
        ws.create_group(gname, members)
    extras = {}
    side = sidecar_path(path)
    if side.is_file():
        data = json.loads(side.read_text())
        for name, info in data.get("samples", {}).items():
            a = ws.samples.get(name)
            if a is None:
                continue
            for p, off in info.get("label_offsets", {}).items():
                try:
                    a.gates.get(p).label_offset = tuple(off)
                except KeyError:
                    pass
        from ..core.workspace import Criterion
        for gname, crit in data.get("groups", {}).items():
            if gname in ws._groups:
                ws._groups[gname].criteria = [Criterion(**c) for c in crit]
        ws.refresh_groups()
        extras = data.get("extras", {})
    return OpenedWorkspace(ws, missing, extras, wsp.flowjo_version)
