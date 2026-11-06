"""Gates and the population tree, evaluated the way FlowJo does.

Gate coordinates are kept in data units (as FlowJo stores them), except
ellipses, which FlowJo stores in display units. Membership is decided in
display space, i.e. after each dimension's axis transform:

- a polygon's edges are straight lines on the plot, not in raw data;
- events beyond an axis are piled on its edge, as on the plot;
- a gate boundary drawn on (or past) an axis edge is open-ended on that side,
  so off-scale events piled on the edge are included.

Populations form a tree. A child's events are its parent's events that fall in
its gate. Boolean gates (AND / OR / NOT) combine other populations by path and
are limited to their parent's events.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

__all__ = [
    "Gate", "RectangleGate", "PolygonGate", "EllipseGate", "BooleanGate",
    "Population", "GatingTree", "quadrant_gates", "EDGE",
]

# A boundary this close to an axis edge (in display units) counts as being on
# the edge. FlowJo's gating grid is 256 channels per axis.
EDGE = 1.0 / 256


def _display(ps, name: str) -> tuple[np.ndarray, object]:
    xf = ps.transforms[ps.parameter(name).name]
    pos = np.clip(np.nan_to_num(xf.apply(ps.values(name)), nan=0.0), 0.0, 1.0)
    return pos, xf


def _edge_open(pos: float) -> float:
    """Move a display coordinate on/over an axis edge well outside it."""
    if pos <= EDGE:
        return -1.0
    if pos >= 1.0 - EDGE:
        return 2.0
    return pos


class Gate:
    dims: tuple[str, ...]
    inside: bool  # False = FlowJo eventsInside="0" (complement)

    def contains(self, ps) -> np.ndarray:
        mask = self._contains(ps)
        return mask if self.inside else ~mask

    def _contains(self, ps) -> np.ndarray:
        raise NotImplementedError

    def renamed(self, mapping: dict[str, str]) -> "Gate":
        """Same gate with dimension names replaced (e.g. raw -> Comp-)."""
        import dataclasses
        return dataclasses.replace(self, dims=tuple(mapping.get(d, d) for d in self.dims))


@dataclass(frozen=True)
class RectangleGate(Gate):
    """Rectangle (2 dims) or Range (1 dim). None = unbounded on that side."""

    dims: tuple[str, ...]
    mins: tuple[float | None, ...]
    maxs: tuple[float | None, ...]
    inside: bool = True

    def _contains(self, ps) -> np.ndarray:
        mask = np.ones(ps.event_count, dtype=bool)
        for d, lo, hi in zip(self.dims, self.mins, self.maxs):
            pos, xf = _display(ps, d)
            if lo is not None:
                p = float(xf.apply(np.array([lo]))[0])
                if p > EDGE:
                    mask &= pos >= p
            if hi is not None:
                p = float(xf.apply(np.array([hi]))[0])
                if p < 1.0 - EDGE:
                    mask &= pos <= p
        return mask


def _points_in_polygon(x: np.ndarray, y: np.ndarray, vx: np.ndarray, vy: np.ndarray) -> np.ndarray:
    """Even-odd rule, vectorised over events."""
    inside = np.zeros(x.shape, dtype=bool)
    n = len(vx)
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = vx[i], vy[i], vx[j], vy[j]
        if yi != yj:
            crosses = (yi > y) != (yj > y)
            x_at = (xj - xi) * (y - yi) / (yj - yi) + xi
            inside ^= crosses & (x < x_at)
        j = i
    return inside


@dataclass(frozen=True)
class PolygonGate(Gate):
    dims: tuple[str, str]
    vertices: tuple[tuple[float, float], ...]  # data units
    inside: bool = True

    def display_vertices(self, ps) -> np.ndarray:
        xfx = ps.transforms[ps.parameter(self.dims[0]).name]
        xfy = ps.transforms[ps.parameter(self.dims[1]).name]
        v = np.array(self.vertices, dtype=float)
        return np.column_stack([xfx.apply(v[:, 0]), xfy.apply(v[:, 1])])

    def _contains(self, ps) -> np.ndarray:
        x, _ = _display(ps, self.dims[0])
        y, _ = _display(ps, self.dims[1])
        v = self.display_vertices(ps)
        vx = np.array([_edge_open(p) for p in v[:, 0]])
        vy = np.array([_edge_open(p) for p in v[:, 1]])
        return _points_in_polygon(x, y, vx, vy)


@dataclass(frozen=True)
class EllipseGate(Gate):
    """Ellipse in display units (0..1 on each axis).

    `center`, semi-axes `a` (along `angle`) and `b`, `angle` in radians.
    """

    dims: tuple[str, str]
    center: tuple[float, float]
    a: float
    b: float
    angle: float
    inside: bool = True

    @classmethod
    def from_flowjo(cls, dims, foci, edges, inside=True) -> "EllipseGate":
        """FlowJo stores two foci and four edge points in 256-channel display units."""
        f = np.asarray(foci, dtype=float) / 256.0
        e = np.asarray(edges, dtype=float) / 256.0
        center = (f[0] + f[1]) / 2
        angle = math.atan2(f[1][1] - f[0][1], f[1][0] - f[0][0])
        c, s = math.cos(-angle), math.sin(-angle)
        rot = np.array([[c, -s], [s, c]])
        # Semi-major axis: farthest edge point along the focal axis.
        local = (e - center) @ rot.T
        a = float(np.max(np.abs(local[:, 0])))
        focal = float(np.linalg.norm(f[1] - f[0]) / 2)
        b = math.sqrt(max(a * a - focal * focal, 0.0))
        return cls(tuple(dims), (float(center[0]), float(center[1])), a, b, angle, inside)

    def flowjo_points(self) -> tuple[np.ndarray, np.ndarray]:
        """(foci, edge points) in FlowJo's 256-channel units."""
        u = np.array([math.cos(self.angle), math.sin(self.angle)])
        v = np.array([-u[1], u[0]])
        c = np.array(self.center)
        focal = math.sqrt(max(self.a ** 2 - self.b ** 2, 0.0))
        foci = np.array([c - focal * u, c + focal * u])
        edges = np.array([c + self.a * u, c - self.a * u, c + self.b * v, c - self.b * v])
        return foci * 256.0, edges * 256.0

    def _contains(self, ps) -> np.ndarray:
        x, _ = _display(ps, self.dims[0])
        y, _ = _display(ps, self.dims[1])
        c, s = math.cos(self.angle), math.sin(self.angle)
        dx, dy = x - self.center[0], y - self.center[1]
        u = dx * c + dy * s
        v = -dx * s + dy * c
        if self.a <= 0 or self.b <= 0:
            return np.zeros(x.shape, dtype=bool)
        return (u / self.a) ** 2 + (v / self.b) ** 2 <= 1.0


@dataclass(frozen=True)
class BooleanGate(Gate):
    """AND / OR / NOT of other populations, referenced by path."""

    op: str  # "and" | "or" | "not"
    refs: tuple[tuple[str, ...], ...]
    dims: tuple[str, ...] = ()
    inside: bool = True

    def __post_init__(self):
        if self.op not in ("and", "or", "not"):
            raise ValueError(f"unknown boolean op {self.op!r}")
        if self.op == "not" and len(self.refs) != 1:
            raise ValueError("NOT takes exactly one population")

    def _contains(self, ps):  # evaluated by GatingTree, which knows the other populations
        raise TypeError("BooleanGate is evaluated through GatingTree")

    def combine(self, masks: list[np.ndarray]) -> np.ndarray:
        if self.op == "and":
            return np.logical_and.reduce(masks)
        if self.op == "or":
            return np.logical_or.reduce(masks)
        return ~masks[0]


def quadrant_gates(x: str, y: str, x_split: float, y_split: float, x_name: str = "", y_name: str = ""):
    """FlowJo-style quadrants as four (name, RectangleGate) pairs: Q1 top-left, then clockwise."""
    xn, yn = x_name or x, y_name or y
    return [
        (f"Q1: {xn}- , {yn}+", RectangleGate((x, y), (None, y_split), (x_split, None))),
        (f"Q2: {xn}+ , {yn}+", RectangleGate((x, y), (x_split, y_split), (None, None))),
        (f"Q3: {xn}+ , {yn}-", RectangleGate((x, y), (x_split, None), (None, y_split))),
        (f"Q4: {xn}- , {yn}-", RectangleGate((x, y), (None, None), (x_split, y_split))),
    ]


@dataclass
class Population:
    name: str
    gate: Gate | None  # None for the root ("Ungated")
    children: list["Population"] = field(default_factory=list)
    parent: "Population | None" = field(default=None, repr=False, compare=False)

    @property
    def path(self) -> tuple[str, ...]:
        """Names from the first gate down, e.g. ("Lymphocytes", "Single Cells")."""
        if self.parent is None:
            return ()
        return self.parent.path + (self.name,)

    def child(self, name: str) -> "Population":
        for c in self.children:
            if c.name == name:
                return c
        raise KeyError(name)


class GatingTree:
    def __init__(self):
        self.root = Population("Ungated", None)

    # --- building ---------------------------------------------------------
    def get(self, path) -> Population:
        node = self.root
        for name in _as_path(path):
            node = node.child(name)
        return node

    def add(self, parent_path, name: str, gate: Gate) -> Population:
        parent = self.get(parent_path)
        if any(c.name == name for c in parent.children):
            raise ValueError(f"{'/'.join(parent.path + (name,))} already exists")
        pop = Population(name, gate, parent=parent)
        parent.children.append(pop)
        return pop

    def remove(self, path) -> None:
        pop = self.get(path)
        pop.parent.children.remove(pop)

    def __iter__(self) -> Iterator[Population]:
        """All populations, depth first (root excluded)."""
        stack = list(reversed(self.root.children))
        while stack:
            p = stack.pop()
            yield p
            stack.extend(reversed(p.children))

    def paths(self) -> list[tuple[str, ...]]:
        return [p.path for p in self]

    # --- evaluating -------------------------------------------------------
    def compute(self, ps) -> dict[tuple[str, ...], np.ndarray]:
        """Event masks for every population; key () is all events."""
        masks: dict[tuple[str, ...], np.ndarray] = {(): np.ones(ps.event_count, dtype=bool)}
        visiting: set[tuple[str, ...]] = set()

        def mask_of(pop: Population) -> np.ndarray:
            key = pop.path
            if key in masks:
                return masks[key]
            if key in visiting:
                raise ValueError(f"circular boolean reference at {'/'.join(key)}")
            visiting.add(key)
            parent = mask_of(pop.parent)
            if isinstance(pop.gate, BooleanGate):
                own = pop.gate.combine([mask_of(self.get(r)) for r in pop.gate.refs])
            else:
                own = pop.gate.contains(ps)
            masks[key] = parent & own
            visiting.discard(key)
            return masks[key]

        for pop in self:
            mask_of(pop)
        return masks

    def counts(self, ps) -> dict[tuple[str, ...], int]:
        return {k: int(v.sum()) for k, v in self.compute(ps).items()}


def _as_path(path) -> tuple[str, ...]:
    if isinstance(path, str):
        return tuple(p for p in path.split("/") if p)
    return tuple(path)
