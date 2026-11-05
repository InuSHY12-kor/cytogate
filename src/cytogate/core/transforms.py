"""Axis transforms, matching FlowJo 10's transform types and parameters.

Each transform maps data values to a display scale of 0..1 (0 = bottom/left of
the axis, 1 = top/right). The parameters, and `to_xml()` / `from_xml()`, follow
the elements FlowJo writes in a .wsp workspace:

    <transforms:linear  minRange maxRange gain>
    <transforms:log     offset decades>
    <transforms:biex    length maxRange neg width pos>
    <transforms:logicle length T W M A>
    <transforms:fasinh  length maxRange T M A W>

Linear, Log and ArcSinh are computed from their closed-form definitions.
Logicle uses FlowKit's implementation of Parks et al. (2006); Biex uses
FlowKit's port of FlowJo's own lookup-table code.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np
from flowkit import transforms as _fk

__all__ = [
    "NS", "Transform", "Linear", "Log", "Biex", "Logicle", "Arcsinh",
    "from_xml", "axis_ticks", "Tick", "DEFAULT_T", "DEFAULT_DECADES",
]

NS = {
    "transforms": "http://www.isac-net.org/std/Gating-ML/v2.0/transformations",
    "data-type": "http://www.isac-net.org/std/Gating-ML/v2.0/datatypes",
    "gating": "http://www.isac-net.org/std/Gating-ML/v2.0/gating",
}
_T = "{%s}" % NS["transforms"]
_D = "{%s}" % NS["data-type"]
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)

DEFAULT_T = 262144.0
DEFAULT_DECADES = 4.418539922  # log10(262144) - 1, FlowJo's default positive decades
LN10 = math.log(10.0)


def _fmt(value: float) -> str:
    """Number formatting as in FlowJo XML: integers without decimals."""
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


class Transform:
    kind = ""

    def apply(self, x) -> np.ndarray:  # data -> display 0..1
        raise NotImplementedError

    def inverse(self, y) -> np.ndarray:  # display 0..1 -> data
        raise NotImplementedError

    def _attrs(self) -> dict[str, float]:
        raise NotImplementedError

    def to_xml(self, parameter: str) -> ET.Element:
        """FlowJo .wsp element for this transform on `parameter`."""
        el = ET.Element(_T + self.kind)
        for key, value in self._attrs().items():
            el.set(key if key == "gain" else _T + key, _fmt(value))
        ET.SubElement(el, _D + "parameter", {_D + "name": parameter})
        return el

    @property
    def top(self) -> float:
        """Data value at the top of the axis."""
        return float(self.inverse(np.array([1.0]))[0])


@dataclass(frozen=True)
class Linear(Transform):
    min_range: float = 0.0
    max_range: float = DEFAULT_T
    gain: float = 1.0
    kind = "linear"

    def apply(self, x):
        x = np.asarray(x, dtype=float)
        return (x - self.min_range) / (self.max_range - self.min_range)

    def inverse(self, y):
        return np.asarray(y, dtype=float) * (self.max_range - self.min_range) + self.min_range

    def _attrs(self):
        return {"minRange": self.min_range, "maxRange": self.max_range, "gain": self.gain}


@dataclass(frozen=True)
class Log(Transform):
    offset: float = 1.0
    decades: float = DEFAULT_DECADES
    kind = "log"

    def apply(self, x):
        x = np.maximum(np.asarray(x, dtype=float), self.offset)
        return (np.log10(x) - math.log10(self.offset)) / self.decades

    def inverse(self, y):
        return self.offset * 10.0 ** (np.asarray(y, dtype=float) * self.decades)

    def _attrs(self):
        return {"offset": self.offset, "decades": self.decades}


@dataclass(frozen=True)
class Biex(Transform):
    """FlowJo "Biex". Width basis is negative (e.g. -10, -100); larger magnitude
    compresses more of the data near zero."""

    width: float = -10.0
    negative: float = 0.0
    positive: float = DEFAULT_DECADES
    max_range: float = DEFAULT_T
    length: int = 256
    kind = "biex"
    _CHANNELS = 4096  # resolution of FlowJo's lookup table
    _impl: object = field(default=None, init=False, repr=False, compare=False)

    def _fk(self):
        if self._impl is None:
            object.__setattr__(
                self, "_impl",
                _fk.WSPBiexTransform(negative=self.negative, width=self.width,
                                     positive=self.positive, max_value=self.max_range),
            )
        return self._impl

    def apply(self, x):
        return self._fk().apply(np.asarray(x, dtype=float)) / self._CHANNELS

    def inverse(self, y):
        return self._fk().inverse(np.asarray(y, dtype=float) * self._CHANNELS)

    def _attrs(self):
        return {"length": self.length, "maxRange": self.max_range, "neg": self.negative,
                "width": self.width, "pos": self.positive}


@dataclass(frozen=True)
class Logicle(Transform):
    T: float = DEFAULT_T
    W: float = 0.5
    M: float = 4.5
    A: float = 0.0
    length: int = 256
    kind = "logicle"
    _impl: object = field(default=None, init=False, repr=False, compare=False)

    def _fk(self):
        if self._impl is None:
            object.__setattr__(self, "_impl", _fk.LogicleTransform(self.T, self.W, self.M, self.A))
        return self._impl

    def apply(self, x):
        return self._fk().apply(np.asarray(x, dtype=float))

    def inverse(self, y):
        return self._fk().inverse(np.asarray(y, dtype=float))

    def _attrs(self):
        return {"length": self.length, "T": self.T, "A": self.A, "W": self.W, "M": self.M}


@dataclass(frozen=True)
class Arcsinh(Transform):
    """FlowJo "ArcSinh" (fasinh). W and max_range are stored by FlowJo but do
    not change the mapping, which is the Gating-ML 2.0 definition."""

    T: float = DEFAULT_T
    M: float = 4.0
    A: float = 0.0
    W: float = -10.0
    max_range: float = DEFAULT_T
    length: int = 256
    kind = "fasinh"

    def apply(self, x):
        x = np.asarray(x, dtype=float)
        return (np.arcsinh(x * math.sinh(self.M * LN10) / self.T) + self.A * LN10) / ((self.M + self.A) * LN10)

    def inverse(self, y):
        y = np.asarray(y, dtype=float)
        return self.T / math.sinh(self.M * LN10) * np.sinh(y * (self.M + self.A) * LN10 - self.A * LN10)

    def _attrs(self):
        return {"length": self.length, "maxRange": self.max_range, "T": self.T,
                "A": self.A, "M": self.M, "W": self.W}


def from_xml(el: ET.Element) -> tuple[str, Transform]:
    """Parse a FlowJo .wsp transform element. Returns (parameter name, transform)."""
    kind = el.tag.rsplit("}", 1)[-1]
    param_el = el.find(_D + "parameter")
    param = param_el.get(_D + "name") if param_el is not None else ""

    def a(name, default=None):
        value = el.get(_T + name, el.get(name))
        if value is None:
            if default is None:
                raise ValueError(f"{kind} transform for {param!r} lacks '{name}'")
            return default
        return float(value)

    if kind == "linear":
        xf = Linear(a("minRange", 0.0), a("maxRange"), a("gain", 1.0))
    elif kind == "log":
        xf = Log(a("offset"), a("decades"))
    elif kind == "biex":
        xf = Biex(a("width"), a("neg", 0.0), a("pos", DEFAULT_DECADES), a("maxRange", DEFAULT_T), int(a("length", 256)))
    elif kind == "logicle":
        xf = Logicle(a("T"), a("W"), a("M"), a("A", 0.0), int(a("length", 256)))
    elif kind == "fasinh":
        xf = Arcsinh(a("T"), a("M"), a("A", 0.0), a("W", -10.0), a("maxRange", DEFAULT_T), int(a("length", 256)))
    else:
        raise ValueError(f"unsupported FlowJo transform type {kind!r}")
    return param, xf


# --- axis ticks -------------------------------------------------------------

@dataclass(frozen=True)
class Tick:
    position: float  # display 0..1
    value: float  # data value
    label: str  # "" for minor ticks
    major: bool


_SUPERSCRIPT = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def _power_label(sign: int, k: int) -> str:
    return ("-" if sign < 0 else "") + "10" + str(k).translate(_SUPERSCRIPT)


def _linear_label(value: float) -> str:
    if value == 0:
        return "0"
    if abs(value) >= 1000 and value % 1000 == 0:
        return f"{int(value // 1000)}K"
    return f"{value:g}"


def axis_ticks(xf: Transform) -> list[Tick]:
    """Tick marks and labels in FlowJo's style for an axis using `xf`.

    Linear axes: round steps labelled 0, 50K, 100K ...
    Log / biex / logicle / arcsinh axes: 0 and ±10^k majors with 2..9 minors.
    """
    lo, hi = float(xf.inverse(np.array([0.0]))[0]), xf.top
    ticks: list[Tick] = []

    def add(value: float, label: str, major: bool):
        pos = float(xf.apply(np.array([value]))[0])
        if -1e-9 <= pos <= 1 + 1e-9:
            ticks.append(Tick(min(max(pos, 0.0), 1.0), value, label, major))

    if isinstance(xf, Linear):
        span = hi - lo
        raw = span / 6
        mag = 10 ** math.floor(math.log10(raw))
        step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
        v = math.ceil(lo / step) * step
        while v <= hi + 1e-9:
            add(v, _linear_label(v), True)
            v += step
        return ticks

    if lo <= 0 <= hi and not isinstance(xf, Log):
        add(0.0, "0", True)
    for sign in (1, -1):
        bound = hi if sign > 0 else -lo
        if bound <= 1:
            continue
        for k in range(0, int(math.floor(math.log10(bound))) + 1):
            add(sign * 10.0 ** k, _power_label(sign, k), True)
            for m in range(2, 10):
                add(sign * m * 10.0 ** k, "", False)
    # Drop labels that would crowd each other (e.g. 10^0..10^2 squeezed into
    # the linear region of a biex axis). "0" goes first, then larger
    # magnitudes, so the outcome is the same on both sides of zero.
    labelled: list[float] = []
    for t in sorted((t for t in ticks if t.label), key=lambda t: (t.value != 0, -abs(t.value))):
        if all(abs(t.position - p) >= 0.04 for p in labelled):
            labelled.append(t.position)
    ticks = [t if not t.label or t.position in labelled else Tick(t.position, t.value, "", True) for t in ticks]
    # Likewise drop unlabelled ticks that would merge into a solid block.
    shown: list[Tick] = []
    for t in sorted(ticks, key=lambda t: (not t.label, not t.major, -abs(t.value))):
        if t.label or all(abs(t.position - s.position) >= 0.006 for s in shown):
            shown.append(t)
    return sorted(shown, key=lambda t: t.position)
