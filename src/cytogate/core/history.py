"""Undo / redo for a workspace (FlowJo's Undo/Redo arrows).

Each step saves the whole analysis state that the user can change: every
sample's gate tree (gates, statistics, label positions, tints, group
ownership), every sample's axis transforms, and the groups. Gates are
immutable, so a snapshot is cheap.
"""

from __future__ import annotations

import copy

__all__ = ["History"]


class History:
    def __init__(self, workspace, limit: int = 100):
        self.ws = workspace
        self.limit = limit
        self._undo: list = []
        self._redo: list = []

    def _snapshot(self):
        return (
            {n: (copy.deepcopy(a.gates.root), dict(a.ps.transforms)) for n, a in self.ws.samples.items()},
            copy.deepcopy(self.ws._groups),
        )

    def _restore(self, snap) -> list[str]:
        samples, groups = snap
        for n, (root, transforms) in samples.items():
            if n in self.ws.samples:
                a = self.ws.samples[n]
                a.gates.root = root
                a.ps.transforms = dict(transforms)
                a.invalidate()
        self.ws._groups = groups
        return list(samples)

    def record(self) -> None:
        """Call before every change that should be undoable."""
        self._undo.append(self._snapshot())
        del self._undo[:-self.limit]
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> list[str]:
        """Go back one step; returns the samples that changed."""
        if not self._undo:
            return []
        self._redo.append(self._snapshot())
        return self._restore(self._undo.pop())

    def redo(self) -> list[str]:
        if not self._redo:
            return []
        self._undo.append(self._snapshot())
        return self._restore(self._redo.pop())
