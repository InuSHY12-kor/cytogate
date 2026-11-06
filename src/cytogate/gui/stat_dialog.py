"""Adding a statistic to a population (FlowJo's "Add Statistics")."""

from __future__ import annotations

import math

from PySide6 import QtWidgets

from ..core.stats import FREQUENCY_STATS, PARAMETER_STATS, Stat, StatSpec


def format_stat(stat: Stat, value: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NaN"
    if stat == Stat.COUNT:
        return f"{int(value):,}"
    if abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.3g}"


class StatDialog(QtWidgets.QDialog):
    def __init__(self, parameters, parent=None):
        """`parameters` = [(name, label)] of the sample."""
        super().__init__(parent)
        self.setWindowTitle("Add Statistic")
        self.stat_box = QtWidgets.QComboBox()
        for s in (*FREQUENCY_STATS, *PARAMETER_STATS):
            self.stat_box.addItem(s.value, s)
        self.param_box = QtWidgets.QComboBox()
        for name, label in parameters:
            self.param_box.addItem(label, name)
        self.pct_box = QtWidgets.QDoubleSpinBox(minimum=0.0, maximum=100.0, decimals=2, value=50.0)
        form = QtWidgets.QFormLayout()
        form.addRow("Statistic", self.stat_box)
        form.addRow("Parameter", self.param_box)
        form.addRow("Percentile", self.pct_box)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.stat_box.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_):
        stat = self.stat_box.currentData()
        self.param_box.setEnabled(stat in PARAMETER_STATS)
        self.pct_box.setEnabled(stat == Stat.PERCENTILE)

    def spec(self) -> StatSpec:
        stat = self.stat_box.currentData()
        return StatSpec(
            stat,
            self.param_box.currentData() if stat in PARAMETER_STATS else None,
            self.pct_box.value() if stat == Stat.PERCENTILE else None,
        )
