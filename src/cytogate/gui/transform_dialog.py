"""Choosing and customizing an axis transform (like FlowJo's "Customize Axis")."""

from __future__ import annotations

import dataclasses
import math

from PySide6 import QtWidgets

from ..core import Arcsinh, Biex, Linear, Log, Logicle, Parameter, Transform

KINDS = {"Linear": Linear, "Log": Log, "Biex": Biex, "Logicle": Logicle, "ArcSinh": Arcsinh}
NAMES = {cls: name for name, cls in KINDS.items()}

LABELS = {
    "min_range": "Minimum", "max_range": "Maximum", "gain": "Gain",
    "offset": "Offset", "decades": "Decades",
    "width": "Width basis", "negative": "Extra negative decades", "positive": "Positive decades",
    "T": "Top of scale (T)", "W": "Linearization width (W)", "M": "Positive decades (M)",
    "A": "Additional negative decades (A)",
}
HIDDEN = {"length", "gain", "_impl"}


def starter_transform(name: str, param: Parameter, current: Transform) -> Transform:
    """A sensible transform of the given type for this parameter."""
    top = param.channel_range or 262144.0
    if name == "Linear":
        return Linear(0.0, top) if param.kind != "time" else current if isinstance(current, Linear) else Linear(0.0, top)
    if name == "Log":
        return Log(1.0, round(math.log10(top), 4))
    if name == "Biex":
        return Biex(max_range=top)
    if name == "Logicle":
        return Logicle(T=top, W=1.0, M=4.418539922, A=0.0)
    return Arcsinh(T=top, M=4.0, A=0.0, max_range=top)


class TransformDialog(QtWidgets.QDialog):
    def __init__(self, param: Parameter, current: Transform, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Axis: {param.label}")
        self.param = param
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(KINDS)
        self.kind.setCurrentText(NAMES[type(current)])
        self.form = QtWidgets.QFormLayout()
        self.fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        top = QtWidgets.QFormLayout()
        top.addRow("Transform", self.kind)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(self.form)
        layout.addWidget(buttons)
        self._load(current)
        self.kind.currentTextChanged.connect(lambda name: self._load(starter_transform(name, param, current)))

    def _load(self, xf: Transform) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields.clear()
        self._cls = type(xf)
        self._base = xf
        for f in dataclasses.fields(xf):
            if f.name in HIDDEN or not f.init:
                continue
            box = QtWidgets.QDoubleSpinBox(decimals=6, minimum=-1e9, maximum=1e9)
            box.setValue(getattr(xf, f.name))
            self.fields[f.name] = box
            self.form.addRow(LABELS.get(f.name, f.name), box)

    def transform(self) -> Transform:
        values = {}
        for name, box in self.fields.items():
            original = getattr(self._base, name)
            # The box shows 6 decimals; keep the exact value if the user did not change it.
            values[name] = original if round(original, box.decimals()) == box.value() else box.value()
        return dataclasses.replace(self._base, **values)
