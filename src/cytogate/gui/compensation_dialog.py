"""Compensation matrix editor and the "create from single-stain controls" wizard."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
from PySide6 import QtCore, QtWidgets

from ..core import Compensation
from ..core.comp_tools import Control, autospill, spillover_from_controls

NONE = "(none)"


def guess_control(detector: str, stain: str, samples: dict) -> str | None:
    """Sample whose name mentions the detector's dye or stain (e.g. "FITC", "CD3")."""
    words = [w for w in re.split(r"[\s_\-]+", f"{detector} {stain}") if len(w) > 2 and not w.upper().endswith(("FLR", "-A"))]
    for name in samples:
        low = name.lower()
        if any(w.lower() in low for w in words if w.lower() not in ("comp",)):
            return name
    return None


class ControlsWizard(QtWidgets.QDialog):
    """Pick a single-stain control (and optional unstained tube) for each detector."""

    def __init__(self, main, detectors: list[str], parent=None):
        super().__init__(parent)
        self.main = main
        self.detectors = detectors
        self.setWindowTitle("Create Matrix from Controls")
        self.resize(620, 420)
        names = list(main.samples)
        any_sample = main.samples[names[0]] if names else None
        stains = {c.pnn: c.pns for c in any_sample.ps.sample.channels} if any_sample else {}
        self.table = QtWidgets.QTableWidget(len(detectors), 2)
        self.table.setHorizontalHeaderLabels(["Detector", "Single-stain control"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.boxes = {}
        for r, d in enumerate(detectors):
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(d))
            box = QtWidgets.QComboBox()
            box.addItems([NONE, *names])
            guess = guess_control(d, stains.get(d, ""), main.samples)
            if guess:
                box.setCurrentText(guess)
            self.table.setCellWidget(r, 1, box)
            self.boxes[d] = box
        self.unstained = QtWidgets.QComboBox()
        self.unstained.addItems([NONE, *names])
        guess = next((n for n in names if "unstain" in n.lower() or "blank" in n.lower()), None)
        if guess:
            self.unstained.setCurrentText(guess)
        self.method = QtWidgets.QComboBox()
        self.method.addItems(["AutoSpill (no gates)", "Classic (positive vs negative medians)"])
        form = QtWidgets.QFormLayout()
        form.addRow("Unstained control", self.unstained)
        form.addRow("Method", self.method)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _raw(self, name: str) -> np.ndarray:
        s = self.main.samples[name].ps.sample
        idx = [s.channel_index(d) for d in self.detectors]
        return s.events[:, idx]

    def matrix(self) -> Compensation:
        controls = [Control(d, self._raw(b.currentText())) for d, b in self.boxes.items() if b.currentText() != NONE]
        if not controls:
            raise ValueError("choose at least one single-stain control")
        if self.method.currentIndex() == 0:
            return autospill(self.detectors, controls, name="AutoSpill")
        unstained = self._raw(self.unstained.currentText()) if self.unstained.currentText() != NONE else None
        return spillover_from_controls(self.detectors, controls, unstained, name="Compensation")


class CompensationDialog(QtWidgets.QDialog):
    """View and edit a spillover matrix; apply it to the selected samples or the
    current group, or remove compensation."""

    def __init__(self, main, sample: str, parent=None):
        super().__init__(parent)
        self.main = main
        self.sample = sample
        self.setWindowTitle("Compensation Matrix Editor")
        self.resize(760, 520)
        a = main.samples[sample]
        comp = a.ps.compensation
        if comp is None:  # start from an identity over the fluorescence detectors
            dets = [c.pnn for c in a.ps.sample.channels if c.is_fluorescence]
            comp = Compensation(pd.DataFrame(np.eye(len(dets)), index=dets, columns=dets), name="New Matrix")
        self.name = QtWidgets.QLineEdit(comp.name)
        self.table = QtWidgets.QTableWidget()
        self.load_matrix(comp)
        btns = QtWidgets.QHBoxLayout()
        for text, slot in (("Create from Controls…", self.from_controls), ("Load CSV…", self.load_csv),
                           ("Save CSV…", self.save_csv), ("Identity", self.identity)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        self.target = QtWidgets.QComboBox()
        self.target.addItems(["Selected samples", f"Current group ({main.current_group()})"])
        apply_row = QtWidgets.QHBoxLayout()
        apply_row.addWidget(QtWidgets.QLabel("Apply to"))
        apply_row.addWidget(self.target, 1)
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.remove_btn = QtWidgets.QPushButton("Remove Compensation")
        self.apply_btn.clicked.connect(self.apply)
        self.remove_btn.clicked.connect(self.remove)
        apply_row.addWidget(self.apply_btn)
        apply_row.addWidget(self.remove_btn)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        apply_row.addWidget(close)
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        form.addRow("Matrix name", self.name)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel("Row = fluorochrome's detector, column = detector it spills into."))
        lay.addWidget(self.table, 1)
        lay.addLayout(btns)
        lay.addLayout(apply_row)

    # --- matrix <-> table ---------------------------------------------------------
    def load_matrix(self, comp: Compensation) -> None:
        m = comp.spillover
        self.detectors = list(m.index)
        self.table.clear()
        self.table.setRowCount(len(m))
        self.table.setColumnCount(len(m))
        self.table.setHorizontalHeaderLabels(self.detectors)
        self.table.setVerticalHeaderLabels(self.detectors)
        for r in range(len(m)):
            for c in range(len(m)):
                it = QtWidgets.QTableWidgetItem(f"{m.values[r, c]:.6f}")
                if r == c:
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.name.setText(comp.name)

    def matrix(self) -> Compensation:
        n = len(self.detectors)
        values = np.eye(n)
        for r in range(n):
            for c in range(n):
                if r != c:
                    values[r, c] = float(self.table.item(r, c).text())
        return Compensation(pd.DataFrame(values, index=self.detectors, columns=self.detectors),
                            name=self.name.text().strip() or "Compensation")

    # --- actions -----------------------------------------------------------------
    def identity(self) -> None:
        n = len(self.detectors)
        self.load_matrix(Compensation(pd.DataFrame(np.eye(n), index=self.detectors, columns=self.detectors),
                                      name=self.name.text()))

    def from_controls(self, wizard: ControlsWizard | None = None) -> Compensation | None:
        wiz = wizard or ControlsWizard(self.main, self.detectors, self)
        if wizard is None and not wiz.exec():
            return None
        try:
            comp = wiz.matrix()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Compensation", str(exc))
            return None
        self.load_matrix(comp)
        return comp

    def load_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Matrix", "", "CSV (*.csv);;All files (*)")
        if path:
            try:
                self.load_matrix(Compensation.from_csv(path))
            except (ValueError, OSError) as exc:
                QtWidgets.QMessageBox.warning(self, "Load Matrix", str(exc))

    def save_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Matrix", f"{self.name.text()}.csv", "CSV (*.csv)")
        if path:
            self.matrix().to_csv(path)

    def _targets(self) -> list[str]:
        if self.target.currentIndex() == 0:
            return self.main.selected_sample_names() or [self.sample]
        return self.main.ws.group(self.main.current_group()).samples

    def apply(self, comp: Compensation | None = None) -> list[str]:
        return self.main.apply_compensation(self.matrix() if comp is None else comp, self._targets())

    def remove(self) -> list[str]:
        return self.main.apply_compensation(None, self._targets())
