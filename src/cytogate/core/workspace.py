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

__all__ = ["ALL_SAMPLES", "Criterion", "Group", "Workspace", "ApplyResult"]

ALL_SAMPLES = "All Samples"


@dataclass(frozen=True)
class Criterion:
    """Keyword rule for a group: keyword `op` value (op: "equals" | "contains" | "starts with")."""

    keyword: str
    value: str
    op: str = "equals"

    def matches(self, a: AnalyzedSample) -> bool:
        v = a.keyword(self.keyword, "")
        if self.op == "contains":
            return self.value.lower() in v.lower()
        if self.op == "starts with":
            return v.lower().startswith(self.value.lower())
        return v == self.value


@dataclass
class Group:
    name: str
    samples: list[str] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)  # live group: membership from keywords


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

    def create_group(self, name: str, samples=(), criteria=()) -> Group:
        """A group of the given samples, or (with criteria) of every sample whose
        keywords match all criteria, kept up to date by refresh_groups()."""
        name = name.strip()
        if not name or name in self.groups:
            raise ValueError(f"group name {name!r} is empty or already used")
        g = Group(name, [], list(criteria))
        self._groups[name] = g
        self.add_to_group(name, samples)
        self.refresh_groups()
        return g

    def refresh_groups(self) -> None:
        """Re-evaluate keyword-based groups (call after keywords change)."""
        for g in self._groups.values():
            if g.criteria:
                g.samples = [n for n, a in self.samples.items() if all(c.matches(a) for c in g.criteria)]

    def rename_group(self, old: str, new: str) -> None:
        new = new.strip()
        if old == ALL_SAMPLES:
            raise ValueError("All Samples cannot be renamed")
        if not new or new in self.groups:
            raise ValueError(f"group name {new!r} is empty or already used")
        g = self._groups.pop(old)
        g.name = new
        self._groups[new] = g
        for a in self.samples.values():
            for pop in a.gates:
                if pop.owning_group == old:
                    pop.owning_group = new

    # --- keywords -------------------------------------------------------------------
    def keyword_names(self) -> list[str]:
        names: dict[str, None] = {}
        for a in self.samples.values():
            names.update(dict.fromkeys(a.keywords))
        return list(names)

    def set_keyword(self, samples, keyword: str, value: str) -> None:
        for n in samples:
            self.samples[n].user_keywords[keyword] = str(value)
        self.refresh_groups()

    def keyword_series(self, samples, keyword: str, start: float = 1, step: float = 1,
                       prefix: str = "", suffix: str = "", digits: int = 0) -> list[str]:
        """FlowJo's "Add a Keyword Value Series": prefix + start, start+step, … + suffix."""
        values = []
        for i, n in enumerate(samples):
            v = start + i * step
            text = f"{int(v):0{digits}d}" if float(v).is_integer() else f"{v:g}"
            values.append(f"{prefix}{text}{suffix}")
            self.samples[n].user_keywords[keyword] = values[-1]
        self.refresh_groups()
        return values

    def find_replace(self, find: str, replace: str, keyword: str | None = None, samples=None) -> int:
        """Replace text in keyword values (all keywords, or one). Returns the number changed."""
        changed = 0
        for n in samples or list(self.samples):
            a = self.samples[n]
            for k, v in a.keywords.items():
                if (keyword is None or k == keyword) and find and find in v:
                    a.user_keywords[k] = v.replace(find, replace)
                    changed += 1
        self.refresh_groups()
        return changed

    def paste_keywords(self, text: str) -> list[str]:
        """Keywords from spreadsheet text (tab or comma separated). The first row
        names the keywords; the first column is the sample name, or — if the
        header's first cell is not "Name"/"Sample" — rows follow workspace order.
        Returns the keywords set."""
        rows = [r for r in (line.split("\t") if "\t" in line else line.split(",") for line in text.splitlines())
                if any(c.strip() for c in r)]
        if not rows:
            return []
        header = [c.strip() for c in rows[0]]
        by_name = header[0].lower() in ("name", "sample", "$fil", "file")
        keys = header[1:] if by_name else header
        order = list(self.samples)
        for i, r in enumerate(rows[1:]):
            cells = [c.strip() for c in r]
            if by_name:
                name, cells = cells[0], cells[1:]
                if name not in self.samples:
                    continue
            elif i < len(order):
                name = order[i]
            else:
                break
            for k, v in zip(keys, cells):
                if k:
                    self.samples[name].user_keywords[k] = v
        self.refresh_groups()
        return [k for k in keys if k]

    # --- renaming and statistics ---------------------------------------------------------
    def rename_population(self, sample: str, path, new_name: str, whole_group: bool = True) -> list[str]:
        """Rename a population (in every linked sample of its group, or only this
        one). Boolean gates and "Freq. of" statistics that refer to it follow."""
        path = tuple(path)
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("empty name")
        targets = self.linked_samples(sample, path) if whole_group else [sample]
        new_path = path[:-1] + (new_name,)
        for n in targets:
            a = self.samples[n]
            pop = a.gates.get(path)
            if any(c.name == new_name for c in pop.parent.children if c is not pop):
                raise ValueError(f"{'/'.join(new_path)} already exists in {n}")
        for n in targets:
            a = self.samples[n]
            a.gates.get(path).name = new_name
            if not whole_group:
                a.gates.get(new_path).owning_group = None
            _repoint(a, path, new_path)
            a.invalidate()
        return targets

    def copy_statistic(self, spec, path, targets) -> list[str]:
        """Add a statistic to population `path` of each target sample that has it."""
        done = []
        for n in targets:
            a = self.samples[n]
            try:
                a.add_stat(path, spec)
                done.append(n)
            except KeyError:
                continue
        return done

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
        every other sample in `group`. path=() copies the whole gate tree.
        The copies (and the source) become owned by the group."""
        return self.copy_populations([(source, path)], self.group(group).samples, owner=group)

    def copy_populations(self, items, targets, owner: str | None = None) -> ApplyResult:
        """Copy populations to other samples, each under the same parent path.

        items   = [(sample, path)]; path=() means the sample's whole gate tree.
                  A population whose ancestor is also listed is copied with it.
        targets = sample names (sources among them are left as they are).
        owner   = group that will own the copies, or None for sample-owned
                  copies (FlowJo: dropping a gate on one sample).
        """
        items = [(s, tuple(p)) for s, p in items]
        listed = set(items)
        items = [(s, p) for s, p in items
                 if not any((s, p[:i]) in listed for i in range(len(p)))]  # drop listed descendants
        result = ApplyResult()
        sources = {s for s, _ in items}
        for s, p in items:
            if owner is not None:
                for top in self._tops(s, p):
                    _set_owner(top, owner)
        for name in targets:
            if name in sources:
                continue
            a = self.samples[name]
            changed = added = False
            for s, p in items:
                parent_path = p[:-1] if p else ()
                try:
                    parent = a.gates.get(parent_path)
                except KeyError:
                    result.skipped[name] = f"no population {'/'.join(parent_path)}"
                    continue
                for top in self._tops(s, p):
                    if _merge(parent, top, owner):
                        changed = True
                    else:
                        added = True
            a.invalidate()
            if changed:
                result.updated.append(name)
            elif added:
                result.added.append(name)
        return result

    def _tops(self, sample: str, path) -> list[Population]:
        pop = self.samples[sample].gates.get(path)
        return list(pop.children) if not path else [pop]

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


def _replace_prefix(p: tuple, old: tuple, new: tuple) -> tuple:
    return new + p[len(old):] if p[: len(old)] == old else p


def _repoint(a: AnalyzedSample, old: tuple, new: tuple) -> None:
    """After a rename, fix Boolean references and "Freq. of" ancestors."""
    import dataclasses
    from .gating import BooleanGate
    from .stats import Stat

    for pop in [a.gates.root, *a.gates]:
        if isinstance(pop.gate, BooleanGate):
            refs = tuple(_replace_prefix(r, old, new) for r in pop.gate.refs)
            if refs != pop.gate.refs:
                pop.gate = dataclasses.replace(pop.gate, refs=refs)
        pop.stats = [dataclasses.replace(s, ancestor=_replace_prefix(s.ancestor, old, new))
                     if s.stat == Stat.FREQ_OF and s.ancestor else s for s in pop.stats]


def _set_owner(pop: Population, group: str) -> None:
    pop.owning_group = group
    for c in pop.children:
        _set_owner(c, group)


def _merge(parent: Population, src: Population, group: str | None) -> bool:
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


def _copy(src: Population, parent: Population, group: str | None) -> Population:
    pop = Population(src.name, src.gate, parent=parent, label_offset=src.label_offset,
                     stats=list(copy.copy(src.stats)), owning_group=group)
    pop.children = [_copy(c, pop, group) for c in src.children]
    return pop
