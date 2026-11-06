"""A sample under analysis: processed data plus its gate tree, with cached results."""

from __future__ import annotations

import numpy as np

from .gating import Gate, GatingTree, Population
from .processing import ProcessedSample
from .stats import GeoMean, StatMode, StatSpec, compute

__all__ = ["AnalyzedSample"]


class AnalyzedSample:
    def __init__(self, ps: ProcessedSample, gates: GatingTree | None = None):
        self.ps = ps
        self.gates = gates or GatingTree()
        self.user_keywords: dict[str, str] = {}  # keywords added/edited in the workspace
        self._masks: dict[tuple[str, ...], np.ndarray] | None = None

    @property
    def name(self) -> str:
        return self.ps.sample.name

    # --- keywords ---------------------------------------------------------------
    @property
    def keywords(self) -> dict[str, str]:
        """FCS keywords with workspace edits on top."""
        return {**self.ps.sample.keywords, **self.user_keywords}

    def keyword(self, name: str, default: str = "") -> str:
        """Keyword value; FCS standard keywords may be given with or without "$"."""
        kw = self.keywords
        if name in kw:
            return kw[name]
        want = name.upper().lstrip("$")
        for k, v in kw.items():
            if k.upper().lstrip("$") == want:
                return v
        return default

    def set_compensation(self, comp) -> None:
        """Apply a compensation matrix (None removes it). Transforms of parameters
        that still exist are kept; gates on Comp- parameters keep working when
        the new matrix covers the same detectors."""
        from .processing import ProcessedSample
        keep = dict(self.ps.transforms)
        self.ps = ProcessedSample(self.ps.sample, compensation=comp,
                                  transforms={k: v for k, v in keep.items()
                                              if k in {p.name for p in ProcessedSample.parameter_names(self.ps.sample, comp)}})
        self.invalidate()

    def invalidate(self) -> None:
        """Call after changing gates or transforms."""
        self._masks = None

    @property
    def masks(self) -> dict[tuple[str, ...], np.ndarray]:
        if self._masks is None:
            self._masks = self.gates.compute(self.ps)
        return self._masks

    def mask(self, path) -> np.ndarray:
        return self.masks[tuple(path)]

    def count(self, path) -> int:
        return int(self.mask(path).sum())

    def freq_of_parent(self, path) -> float:
        """Percent of the parent population (FlowJo "Freq. of Parent")."""
        path = tuple(path)
        if not path:
            return 100.0
        parent = self.count(path[:-1])
        return 100.0 * self.count(path) / parent if parent else 0.0

    stat_mode: StatMode = StatMode.FLOWJO  # class-wide default; see stats.StatMode

    def stat(self, path, spec: StatSpec, geomean: GeoMean = GeoMean.GRAPH, mode: StatMode | None = None) -> float:
        return compute(self, path, spec, geomean, mode or self.stat_mode)

    def add_stat(self, path, spec: StatSpec) -> None:
        pop = self.gates.get(path)
        if spec not in pop.stats:
            pop.stats.append(spec)

    def remove_stat(self, path, spec: StatSpec) -> None:
        self.gates.get(path).stats.remove(spec)

    def add_gate(self, parent_path, name: str, gate: Gate) -> Population:
        pop = self.gates.add(parent_path, name, gate)
        self.invalidate()
        return pop

    def remove_gate(self, path) -> None:
        self.gates.remove(path)
        self.invalidate()

    def unique_name(self, parent_path, base: str) -> str:
        taken = {c.name for c in self.gates.get(parent_path).children}
        if base not in taken:
            return base
        n = 2
        while f"{base} {n}" in taken:
            n += 1
        return f"{base} {n}"
