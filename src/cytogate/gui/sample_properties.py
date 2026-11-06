"""Sample Properties: keywords (editable), parameters, compensation matrix, file info."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..core.analysis import AnalyzedSample


class SamplePropertiesDialog(QtWidgets.QDialog):
    keywords_changed = QtCore.Signal(dict)  # {keyword: new value}

    def __init__(self, a: AnalyzedSample, parent=None):
        super().__init__(parent)
        self.a = a
        self.setWindowTitle(f"Sample Properties: {a.name}")
        self.resize(640, 520)
        tabs = self.tabs = QtWidgets.QTabWidget()

        # Keywords
        kw = a.keywords
        self.kw_table = QtWidgets.QTableWidget(len(kw), 2)
        self.kw_table.setHorizontalHeaderLabels(["Keyword", "Value"])
        self.kw_table.horizontalHeader().setStretchLastSection(True)
        for r, (k, v) in enumerate(kw.items()):
            key = QtWidgets.QTableWidgetItem(k)
            key.setFlags(key.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            if k in a.user_keywords:
                f = key.font()
                f.setBold(True)  # added or edited in the workspace
                key.setFont(f)
            self.kw_table.setItem(r, 0, key)
            self.kw_table.setItem(r, 1, QtWidgets.QTableWidgetItem(v))
        tabs.addTab(self.kw_table, "Keywords")

        # Parameters
        ch = a.ps.sample.channel_table()
        self.param_table = QtWidgets.QTableWidget(len(ch), len(ch.columns))
        self.param_table.setHorizontalHeaderLabels([str(c) for c in ch.columns])
        for r, row in enumerate(ch.itertuples(index=False)):
            for c, v in enumerate(row):
                it = QtWidgets.QTableWidgetItem(f"{v:g}" if isinstance(v, float) else str(v))
                it.setFlags(it.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.param_table.setItem(r, c, it)
        tabs.addTab(self.param_table, "Parameters")

        # Compensation
        comp = a.ps.compensation
        if comp is None:
            tabs.addTab(QtWidgets.QLabel("No compensation matrix applied."), "Compensation")
        else:
            m = comp.spillover
            self.comp_table = QtWidgets.QTableWidget(len(m), len(m))
            self.comp_table.setHorizontalHeaderLabels(list(m.columns))
            self.comp_table.setVerticalHeaderLabels(list(m.index))
            for r in range(len(m)):
                for c in range(len(m)):
                    it = QtWidgets.QTableWidgetItem(f"{m.values[r, c]:.6f}")
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                    self.comp_table.setItem(r, c, it)
            tabs.addTab(self.comp_table, f"Compensation ({comp.name})")

        # Info
        s = a.ps.sample
        info = QtWidgets.QFormLayout()
        info_w = QtWidgets.QWidget()
        info_w.setLayout(info)
        for label, value in (("File", str(s.path)), ("FCS version", s.version), ("Events", f"{s.event_count:,}"),
                             ("Parameters", str(len(s.channels))), ("Cytometer", s.cytometer or "-"),
                             ("Acquired", s.acquisition_date or "-"), ("Time gain", f"{s.time_gain:.6g} s/unit")):
            info.addRow(label, QtWidgets.QLabel(value))
        if s.warnings:
            info.addRow("Notes", QtWidgets.QLabel("\n".join(s.warnings)))
        tabs.addTab(info_w, "Info")

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(bb)

    def changes(self) -> dict[str, str]:
        kw = self.a.keywords
        out = {}
        for r in range(self.kw_table.rowCount()):
            k, v = self.kw_table.item(r, 0).text(), self.kw_table.item(r, 1).text()
            if kw.get(k) != v:
                out[k] = v
        return out

    def accept(self) -> None:
        changed = self.changes()
        if changed:
            self.keywords_changed.emit(changed)
        super().accept()
