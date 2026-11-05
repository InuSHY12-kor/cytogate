"""A sample with compensation and axis transforms applied: what plots, gates
and statistics work on.

Parameters follow FlowJo: every FCS channel, plus a "Comp-" copy of each
compensated detector. Values are in data units (compensated where it applies);
`scaled()` gives the 0..1 display position under the parameter's transform.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..io.fcs import FcsSample
from .compensation import Compensation
from .transforms import DEFAULT_DECADES, Biex, Linear, Transform

__all__ = ["Parameter", "ProcessedSample", "default_transform", "AUTO"]

AUTO = "auto"  # use the matrix stored in the FCS file, if any


@dataclass(frozen=True)
class Parameter:
    name: str  # PnN, or e.g. "Comp-FITC-A"
    stain: str  # PnS ("" if none)
    kind: str  # "scatter" | "time" | "fluorescence"
    compensated: bool
    channel_range: float

    @property
    def label(self) -> str:
        """FlowJo display name: "Comp-FITC-A :: CD3"."""
        return f"{self.name} :: {self.stain}" if self.stain else self.name


def default_transform(param: Parameter, values: np.ndarray) -> Transform:
    """Transform FlowJo would start with for a parameter.

    Scatter: linear 0..range. Time: linear over the recorded time span.
    Fluorescence: FlowJo's default Biex (width basis -10, 4.42 positive decades).
    """
    top = param.channel_range or 262144.0
    if param.kind == "scatter":
        return Linear(0.0, top)
    if param.kind == "time":
        lo, hi = (float(values.min()), float(values.max())) if values.size else (0.0, 1.0)
        return Linear(lo, hi if hi > lo else lo + 1.0)
    return Biex(width=-10.0, negative=0.0, positive=DEFAULT_DECADES, max_range=top)


class ProcessedSample:
    def __init__(
        self,
        sample: FcsSample,
        compensation: Compensation | str | None = AUTO,
        transforms: dict[str, Transform] | None = None,
    ):
        self.sample = sample
        if compensation == AUTO:
            compensation = Compensation.from_sample(sample)
        self.compensation: Compensation | None = compensation

        raw = sample.events
        names = [c.pnn for c in sample.channels]
        params, columns = [], []
        for c in sample.channels:
            kind = "time" if c.is_time else "scatter" if c.is_scatter else "fluorescence"
            params.append(Parameter(c.pnn, c.pns, kind, False, c.range))
        columns.append(raw)
        if self.compensation is not None:
            columns.append(self.compensation.compensate(raw, names))
            stains = {c.pnn: c.pns for c in sample.channels}
            ranges = {c.pnn: c.range for c in sample.channels}
            for d in self.compensation.detectors:
                params.append(Parameter(self.compensation.comp_name(d), stains[d], "fluorescence", True, ranges[d]))

        self.parameters: list[Parameter] = params
        self._data = np.hstack(columns)
        self._index = {p.name: i for i, p in enumerate(params)}
        self.transforms: dict[str, Transform] = {
            p.name: default_transform(p, self._data[:, i]) for i, p in enumerate(params)
        }
        if transforms:
            for name, xf in transforms.items():
                self.set_transform(name, xf)

    @property
    def event_count(self) -> int:
        return self._data.shape[0]

    def parameter(self, key: str) -> Parameter:
        """Look up by parameter name, FlowJo label, or stain."""
        if key in self._index:
            return self.parameters[self._index[key]]
        matches = [p for p in self.parameters if key in (p.label, p.stain)]
        # A stain names both the raw and the compensated parameter; prefer compensated.
        matches.sort(key=lambda p: not p.compensated)
        if not matches:
            raise KeyError(f"{self.sample.name}: no parameter {key!r}")
        return matches[0]

    def set_transform(self, key: str, xf: Transform) -> None:
        self.transforms[self.parameter(key).name] = xf

    def values(self, key: str) -> np.ndarray:
        """Data values (compensated for Comp- parameters)."""
        return self._data[:, self._index[self.parameter(key).name]]

    def scaled(self, key: str) -> np.ndarray:
        """Display positions 0..1 under the parameter's transform."""
        name = self.parameter(key).name
        return self.transforms[name].apply(self._data[:, self._index[name]])

    @property
    def data(self) -> np.ndarray:
        """All parameters, shape (event_count, len(parameters))."""
        return self._data

    def __repr__(self) -> str:
        comp = self.compensation.name if self.compensation else "none"
        return f"<ProcessedSample {self.sample.name!r} {len(self.parameters)} params, compensation={comp}>"
