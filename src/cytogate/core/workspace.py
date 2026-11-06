"""Samples and groups, and applying gates across a group (FlowJo's workspace model).

- "All Samples" always contains every sample; other groups are user-made.
- Applying a population to a group copies it (with its child gates, statistics
  and label positions) to every sample in the group, under the same parent.
  The copies are owned by that group.
- Changing a group-owned gate can update every sample in the group that still
  shares it, or only one sample. A sample-only change detaches that sample's
  gate from the group, so later group-wide changes leave it alone.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .analysis import AnalyzedSample
from .gating import Gate, Population

__all__ = ["ALL_SAMPLES", "Group", "Workspace", "ApplyResult"]

ALL_SAMPLES = "All Samples"


@dataclass
class Group:
    name: str
    samples: list[str] = field(default_factory=list)


@dataclass
class ApplyResult:
    added: list[str] = field(default_factory=list)  # sample names that got new populations
    updated: list[str] = field(default_factory=list)  # existing populations whose gates were replaced
    skipped: dict[str, str] = field(default_factory=dict)  # sample -> reason


class Workspace:
    def __init__(self):
        self.samples: dict[str, AnalyzedSample] = {}
        self._groups: dict[str, Group] = {}

    # --- samples ----------------------------------------------------------
    def add_sample(self, a: AnalyzedSample) -> bool:
        if a.name in self.samples:
            return False
        self.samples[a.name] = a
        return True

    def remove_sample(self, name: str) -> None:
        del self.samples[name]
        for g in self._groups.values():
            if name in g.samples:
                g.samples.remove(name)

    # --- groups -----------------------------------------------------------
    @property
    def groups(self) -> dict[str, Group]:
        """All groups, "All Samples" first."""
        return {ALL_SAMPLES: Group(ALL_SAMPLES, list(self.samples)), **self._groups}

    def group(self, name: str) -> Group:
        return self.groups[name]

    def create_group(self, name: str, samples=()) -> Group:
        name = name.strip()
        if not name or name in self.groups:
            raise ValueError(f"group name {name!r} is empty or already used")
        g = Group(name, [])
        self._groups[name] = g
        self.add_to_group(name, samples)
        return g

    def delete_group(self, name: str) -> None:
        if name == ALL_SAMPLES:
            raise ValueError("All Samples cannot be deleted")
        del self._groups[name]
        # gates it owned now belong to their samples
        for a in self.samples.values():
            for pop in a.gates:
                if pop.owning_group == name:
                    pop.owning_group = None

    def add_to_group(self, name: str, samples) -> None:
        if name == ALL_SAMPLES:
            return
        g = self._groups[name]
        for s in samples:
            if s not in self.samples:
                raise KeyError(s)
            if s not in g.samples:
                g.samples.append(s)

    def groups_of(self, sample: str) -> list[str]:
        return [n for n, g in self.groups.items() if sample in g.samples]

    # --- applying gates -----------------------------------------------------
    def apply_to_group(self, source: str, path, group: str) -> ApplyResult:
        """Copy population `path` of sample `source` (and everything under it) to
        every other sample in `group`. path=() copies the whole gate tree."""
        path = tuple(path)
        src_pop = self.samples[source].gates.get(path)
        tops = list(src_pop.children) if not path else [src_pop]
        parent_path = path[:-1] if path else ()
        result = ApplyResult()
        # the source's own copies become group-owned too
        for top in tops:
            _set_owner(top, group)
        for name in self.group(group).samples:
            if name == source:
                continue
            a = self.samples[name]
            try:
                parent = a.gates.get(parent_path)
            except KeyError:
                result.skipped[name] = f"no population {'/'.join(parent_path)}"
                continue
            changed = False
            for top in tops:
                changed |= _merge(parent, top, group)
            a.invalidate()
            (result.updated if changed else result.added).append(name)
        return result

    def linked_samples(self, sample: str, path) -> list[str]:
        """Samples whose population `path` shares the group-owned gate of `sample`."""
        pop = self.samples[sample].gates.get(path)
        group = pop.owning_group
        if group is None or group not in self.groups:
            return [sample]
        out = []
        for name in self.group(group).samples:
            try:
                other = self.samples[name].gates.get(path)
            except KeyError:
                continue
            if other.owning_group == group:
                out.append(name)
        return out or [sample]

    def update_gate(self, sample: str, path, gate: Gate, whole_group: bool = True) -> list[str]:
        """Replace a gate. For a group-owned gate with whole_group=True, every
        linked sample changes; otherwise only `sample`, which leaves the group."""
        path = tuple(path)
        targets = self.linked_samples(sample, path) if whole_group else [sample]
        for name in targets:
            a = self.samples[name]
            pop = a.gates.replace_gate(path, gate)
            if not whole_group:
                pop.owning_group = None
            a.invalidate()
        return targets


def _set_owner(pop: Population, group: str) -> None:
    pop.owning_group = group
    for c in pop.children:
        _set_owner(c, group)


def _merge(parent: Population, src: Population, group: str) -> bool:
    """Put a copy of `src` under `parent`. Returns True if a population was replaced."""
    existing = next((c for c in parent.children if c.name == src.name), None)
    if existing is None:
        parent.children.append(_copy(src, parent, group))
        return False
    existing.gate = src.gate  # gates are immutable, so sharing is safe
    existing.owning_group = group
    existing.label_offset = src.label_offset
    for spec in src.stats:
        if spec not in existing.stats:
            existing.stats.append(spec)
    for child in src.children:
        _merge(existing, child, group)
    return True


def _copy(src: Population, parent: Population, group: str) -> Population:
    pop = Population(src.name, src.gate, parent=parent, label_offset=src.label_offset,
                     stats=list(copy.copy(src.stats)), owning_group=group)
    pop.children = [_copy(c, pop, group) for c in src.children]
    return pop
