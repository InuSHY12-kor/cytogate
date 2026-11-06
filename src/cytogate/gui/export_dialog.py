"""Export / Concatenate dialog (FlowJo's Data Export / Concatenate)."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtWidgets

from ..io.export import ExportOptions, FORMATS, concatenate, export_populations, group_items

FORMAT_LABELS = {"fcs": "FCS 3.1", "csv_scale": "CSV - Scale values", "csv_channel": "CSV - Channel values"}


class ExportDialog(QtWidgets.QDialog):
    def __init__(self, main, items, parent=None):
        """`items` = [(sample, population path)] to export."""
        super().__init__(parent)
        self.main = main
        self.items = list(items)
        self.setWindowTitle(f"Export / Concatenate ({len(self.items)} population(s))")
        self.fmt = QtWidgets.QComboBox()
        for k, v in FORMAT_LABELS.items():
            self.fmt.addItem(v, k)
        self.folder = QtWidgets.QLineEdit(str(Path.home()))
        browse = QtWidgets.QPushButton("…")
        browse.clicked.connect(self._browse)
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        self.limit = QtWidgets.QCheckBox("Include no more than")
        self.limit_n = QtWidgets.QSpinBox(minimum=1, maximum=100_000_000, value=10000)
        limit_row = QtWidgets.QHBoxLayout()
        limit_row.addWidget(self.limit)
        limit_row.addWidget(self.limit_n)
        limit_row.addWidget(QtWidgets.QLabel("events"))
        self.params = QtWidgets.QComboBox()
        self.params.addItems(["All uncompensated parameters", "All compensated parameters"])
        self.prefix, self.suffix = QtWidgets.QLineEdit("export_"), QtWidgets.QLineEdit()
        self.template = QtWidgets.QLineEdit(placeholderText="optional, e.g. {$FIL}_{population}")
        self.concat = QtWidgets.QCheckBox("Concatenate")
        self.concat_mode = QtWidgets.QComboBox()
        self.concat_mode.addItems(["All files together", "Every n files", "Files with equal keyword value"])
        self.concat_n = QtWidgets.QSpinBox(minimum=1, maximum=10000, value=2)
        self.concat_kw = QtWidgets.QComboBox(editable=True)
        self.concat_kw.addItems(sorted(main.ws.keyword_names()))
        self.sample_id = QtWidgets.QCheckBox("Add SampleID parameter", checked=True)
        self.concat_name = QtWidgets.QLineEdit("concatenated")
        form = QtWidgets.QFormLayout()
        form.addRow("Format", self.fmt)
        form.addRow("Destination", folder_row)
        form.addRow("Events", limit_row)
        form.addRow("Parameters", self.params)
        form.addRow("Prefix", self.prefix)
        form.addRow("Suffix", self.suffix)
        form.addRow("Name from keywords", self.template)
        form.addRow(self.concat)
        form.addRow("Concatenate", self.concat_mode)
        form.addRow("n", self.concat_n)
        form.addRow("Keyword", self.concat_kw)
        form.addRow("", self.sample_id)
        form.addRow("Concatenated file name", self.concat_name)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.go = bb.addButton("Export", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        bb.accepted.connect(self.run)
        bb.rejected.connect(self.reject)
        self.status = QtWidgets.QLabel()
        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.status)
        lay.addWidget(bb)
        self.concat.toggled.connect(self._sync)
        self.concat_mode.currentIndexChanged.connect(self._sync)
        self._sync()

    def _browse(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Destination", self.folder.text())
        if d:
            self.folder.setText(d)

    def _sync(self, *_) -> None:
        on = self.concat.isChecked()
        for w in (self.concat_mode, self.sample_id, self.concat_name):
            w.setEnabled(on)
        self.concat_n.setEnabled(on and self.concat_mode.currentIndex() == 1)
        self.concat_kw.setEnabled(on and self.concat_mode.currentIndex() == 2)
        self.go.setText("Concatenate" if on else "Export")
        n_files = len(self._groups()) if on else len(self.items)
        self.status.setText(f"{n_files} file(s) will be written")

    def _groups(self):
        mode = ("all", "every_n", "keyword")[self.concat_mode.currentIndex()]
        return group_items(self.main.ws, self.items, mode, self.concat_n.value(), self.concat_kw.currentText())

    def options(self) -> ExportOptions:
        return ExportOptions(
            fmt=self.fmt.currentData(),
            parameters="compensated" if self.params.currentIndex() == 1 else "uncompensated",
            max_events=self.limit_n.value() if self.limit.isChecked() else None,
            prefix=self.prefix.text(), suffix=self.suffix.text(),
            name_template=self.template.text().strip() or None,
            add_sample_id=self.sample_id.isChecked(),
        )

    def run(self) -> list[Path]:
        opts = self.options()
        folder = Path(self.folder.text())
        try:
            if self.concat.isChecked():
                groups = self._groups()
                ext = FORMATS[opts.fmt]
                written = []
                for i, g in enumerate(groups, start=1):
                    tag = "" if len(groups) == 1 else f"_{i}"
                    written.append(concatenate(self.main.ws, g, folder / f"{self.concat_name.text()}{tag}{ext}", opts))
            else:
                written = export_populations(self.main.ws, self.items, folder, opts)
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Export", str(exc))
            return []
        self.main.statusBar().showMessage(f"Wrote {len(written)} file(s) to {folder}")
        self.written = written
        self.accept()
        return written
