"""Axis transform window, opened with the "T" button beside each axis.

Modelled on FlowJo's: a histogram preview of the parameter on top, with
(−)(+) buttons at both ends of the axis; the Scale (transform type) with
Min/Max; and sliders for the transform's settings. Changes preview live and
take effect on Apply.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from ..core import Arcsinh, Biex, Linear, Log, Logicle, Parameter, Transform
from ..core.axis_tools import WIDTH_MAX, WIDTH_MIN, auto_width_basis, axis_max, axis_min, expand_high, expand_low
from ..plots import histogram
from .plot_view import HIST_BRUSH, HIST_PEN, _set_axis
from .transform_dialog import KINDS, NAMES, starter_transform


class _SliderRow(QtWidgets.QWidget):
    """Slider + number box, kept in sync; emits changed(float)."""

    changed = QtCore.Signal(float)

    def __init__(self, lo: float, hi: float, decimals: int, ticks: int = 1000):
        super().__init__()
        self.lo, self.hi, self.ticks = lo, hi, ticks
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, ticks)
        self.box = QtWidgets.QDoubleSpinBox(decimals=decimals, minimum=lo, maximum=hi)
        self.box.setSingleStep(10 ** -decimals * 10)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.box)
        self.slider.valueChanged.connect(self._from_slider)
        self.box.valueChanged.connect(self._from_box)

    def value(self) -> float:
        return self.box.value()

    def set_value(self, v: float) -> None:
        for w in (self.slider, self.box):
            w.blockSignals(True)
        self.box.setValue(v)
        self.slider.setValue(round((v - self.lo) / (self.hi - self.lo) * self.ticks))
        for w in (self.slider, self.box):
            w.blockSignals(False)

    def _from_slider(self, pos: int) -> None:
        v = self.lo + (self.hi - self.lo) * pos / self.ticks
        self.box.blockSignals(True)
        self.box.setValue(v)
        self.box.blockSignals(False)
        self.changed.emit(self.box.value())

    def _from_box(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(round((v - self.lo) / (self.hi - self.lo) * self.ticks))
        self.slider.blockSignals(False)
        self.changed.emit(v)


class TransformEditor(QtWidgets.QDialog):
    """Emits applied(transform, to_whole_group) when the user presses Apply."""

    applied = QtCore.Signal(object, bool)

    def __init__(self, param: Parameter, current: Transform, values: np.ndarray, title: str,
                 group: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Transform of {title}")
        self.param = param
        self.values = np.asarray(values, dtype=float)
        self.original = current
        self.xf = current
        self._loading = False

        # histogram preview with (−)(+) at both ends
        self.plot = pg.PlotWidget()
        self.plot.setMouseEnabled(False, False)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setMinimumHeight(260)
        self.curve = pg.PlotCurveItem(pen=HIST_PEN, brush=HIST_BRUSH, fillLevel=0)
        self.plot.addItem(self.curve)
        self.plot.setLabel("bottom", param.label)
        ends = QtWidgets.QHBoxLayout()
        self.low_minus, self.low_plus = QtWidgets.QToolButton(text="−"), QtWidgets.QToolButton(text="+")
        self.high_minus, self.high_plus = QtWidgets.QToolButton(text="−"), QtWidgets.QToolButton(text="+")
        for b, tip in ((self.low_minus, "Show less of the low end"), (self.low_plus, "Show more of the low end"),
                       (self.high_minus, "Show less of the high end"), (self.high_plus, "Show more of the high end")):
            b.setToolTip(tip)
        ends.addWidget(self.low_minus)
        ends.addWidget(self.low_plus)
        ends.addStretch(1)
        ends.addWidget(self.high_minus)
        ends.addWidget(self.high_plus)
        self.low_minus.clicked.connect(lambda: self.adjust_low(-1))
        self.low_plus.clicked.connect(lambda: self.adjust_low(+1))
        self.high_minus.clicked.connect(lambda: self.adjust_high(-1))
        self.high_plus.clicked.connect(lambda: self.adjust_high(+1))

        # Scale: type, Min, Max
        self.scale_box = QtWidgets.QComboBox()
        self.scale_box.addItems(KINDS)
        self.min_edit, self.max_edit = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        scale = QtWidgets.QGroupBox("Scale")
        sl = QtWidgets.QHBoxLayout(scale)
        sl.addWidget(QtWidgets.QLabel("Scale"))
        sl.addWidget(self.scale_box)
        sl.addWidget(QtWidgets.QLabel("Min"))
        sl.addWidget(self.min_edit)
        sl.addWidget(QtWidgets.QLabel("Max"))
        sl.addWidget(self.max_edit)
        self.scale_box.activated.connect(lambda: self.set_transform(
            starter_transform(self.scale_box.currentText(), param, self.xf)))
        self.min_edit.editingFinished.connect(self._min_edited)
        self.max_edit.editingFinished.connect(self._max_edited)

        # Transform settings (rows shown per type)
        self.neg = _SliderRow(0.0, 1.0, 2)  # Biex extra negative decades
        self.width = _SliderRow(0.0, 3.0, 2)  # Biex log10 |width basis|
        self.width_edit = QtWidgets.QLineEdit()
        self.pos = QtWidgets.QDoubleSpinBox(decimals=4, minimum=0.5, maximum=8.0, singleStep=0.1)
        self.calc_width = QtWidgets.QPushButton("Calculate Width Basis")
        self.lw = _SliderRow(0.0, 3.0, 3)  # Logicle W
        self.m = QtWidgets.QDoubleSpinBox(decimals=4, minimum=0.5, maximum=8.0, singleStep=0.1)
        self.a = QtWidgets.QDoubleSpinBox(decimals=3, minimum=0.0, maximum=5.0, singleStep=0.1)
        self.form = QtWidgets.QFormLayout()
        self.rows = {
            "neg": ("Extra Neg. Decades", self.neg),
            "width": ("Width Basis (log₁₀ |w|)", self.width),
            "width_value": ("Width Basis", self.width_edit),
            "pos": ("Positive Decades", self.pos),
            "calc": ("", self.calc_width),
            "W": ("Linearization Width (W)", self.lw),
            "M": ("Positive Decades (M)", self.m),
            "A": ("Extra Neg. Decades (A)", self.a),
        }
        for label, widget in self.rows.values():
            self.form.addRow(label, widget)
        box = QtWidgets.QGroupBox("Transforms")
        box.setLayout(self.form)
        self.neg.changed.connect(lambda v: self._edit(negative=v))
        self.width.changed.connect(lambda v: self._edit(width=-(10 ** v)))
        self.width_edit.editingFinished.connect(self._width_edited)
        self.pos.valueChanged.connect(lambda v: self._edit(positive=v))
        self.calc_width.clicked.connect(self.calculate_width_basis)
        self.lw.changed.connect(lambda v: self._edit(W=v))
        self.m.valueChanged.connect(lambda v: self._edit(M=v))
        self.a.valueChanged.connect(lambda v: self._edit(A=v))

        self.group_check = QtWidgets.QCheckBox(f"Apply to all samples in group \"{group}\"" if group else "")
        self.group_check.setVisible(bool(group))
        buttons = QtWidgets.QDialogButtonBox()
        self.save_button = buttons.addButton("Save as Default", QtWidgets.QDialogButtonBox.ButtonRole.ResetRole)
        self.save_button.setToolTip("Use this transform for this parameter in every sample loaded from now on")
        self.save_button.clicked.connect(self.save_preset)
        self.apply_button = buttons.addButton("Apply", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.plot, 1)
        layout.addLayout(ends)
        layout.addWidget(scale)
        layout.addWidget(box)
        layout.addWidget(self.group_check)
        layout.addWidget(buttons)
        self.resize(460, 640)
        self.set_transform(current)

    # --- state ------------------------------------------------------------------
    def set_transform(self, xf: Transform) -> None:
        """Show `xf` in all controls and the preview."""
        self.xf = xf
        self._loading = True
        self.scale_box.setCurrentText(NAMES[type(xf)])
        visible = {
            Biex: {"neg", "width", "width_value", "pos", "calc"},
            Logicle: {"W", "M", "A"},
            Arcsinh: {"M", "A"},
        }.get(type(xf), set())
        for row, key in enumerate(self.rows):
            self.form.setRowVisible(row, key in visible)
        if isinstance(xf, Biex):
            self.neg.set_value(xf.negative)
            self.width.set_value(math.log10(min(max(abs(xf.width), WIDTH_MIN), WIDTH_MAX)))
            self.width_edit.setText(f"{xf.width:g}")
            self.pos.setValue(xf.positive)
        elif isinstance(xf, Logicle):
            self.lw.set_value(xf.W)
            self.m.setValue(xf.M)
            self.a.setValue(xf.A)
        elif isinstance(xf, Arcsinh):
            self.m.setValue(xf.M)
            self.a.setValue(xf.A)
        editable_min = isinstance(xf, (Linear, Log))
        self.min_edit.setReadOnly(not editable_min)
        self.min_edit.setText(f"{axis_min(xf):.6g}")
        self.max_edit.setText(f"{axis_max(xf):.6g}")
        self._loading = False
        self._preview()

    def _edit(self, **changes) -> None:
        if self._loading:
            return
        try:
            self.set_transform(dataclasses.replace(self.xf, **changes))
        except (ValueError, ZeroDivisionError, FloatingPointError):
            pass

    def _width_edited(self) -> None:
        try:
            w = -abs(float(self.width_edit.text()))
        except ValueError:
            return
        self._edit(width=float(np.clip(w, -WIDTH_MAX, -WIDTH_MIN)))

    def _min_edited(self) -> None:
        try:
            v = float(self.min_edit.text())
        except ValueError:
            return
        if isinstance(self.xf, Linear) and v < self.xf.max_range:
            self._edit(min_range=v)
        elif isinstance(self.xf, Log) and v > 0:
            top = axis_max(self.xf)
            self._edit(offset=v, decades=math.log10(top / v))

    def _max_edited(self) -> None:
        try:
            v = float(self.max_edit.text())
        except ValueError:
            return
        xf = self.xf
        if isinstance(xf, Linear) and v > xf.min_range:
            self._edit(max_range=v)
        elif isinstance(xf, Log) and v > xf.offset:
            self._edit(decades=math.log10(v / xf.offset))
        elif isinstance(xf, Biex) and v > 0:
            self._edit(max_range=v)
        elif isinstance(xf, (Logicle, Arcsinh)) and v > 0:
            self._edit(T=v)

    # --- actions ------------------------------------------------------------------
    def adjust_low(self, step: int) -> None:
        self.set_transform(expand_low(self.xf, step))

    def adjust_high(self, step: int) -> None:
        self.set_transform(expand_high(self.xf, step))

    def calculate_width_basis(self) -> None:
        if isinstance(self.xf, Biex):
            self.set_transform(dataclasses.replace(self.xf, width=auto_width_basis(self.values, self.xf)))

    def save_preset(self) -> None:
        """FlowJo's "Save": remember this transform as the default for the parameter."""
        from ..core.presets import save_preset
        save_preset(self.param.name, self.xf)

    def apply(self) -> None:
        self.applied.emit(self.xf, self.group_check.isChecked())
        self.accept()

    def _preview(self) -> None:
        edges, heights = histogram(self.xf.apply(self.values)) if self.values.size else (np.linspace(0, 1, 257), np.zeros(256))
        self.curve.setData(edges, heights, stepMode="center")
        _set_axis(self.plot.getAxis("bottom"), self.xf)
        self.plot.setXRange(0, 1, padding=0)
        self.plot.setYRange(0, max(float(heights.max()), 1.0) * 1.05, padding=0)
        self.last_heights = heights
