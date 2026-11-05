"""Main window: sample list on the left, graph with its controls on the right."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..core import ProcessedSample
from ..io import FcsReadError, load_fcs
from ..plots import HistogramY, PlotType
from .plot_view import PlotView
from .transform_dialog import KINDS, NAMES, TransformDialog, starter_transform

HISTOGRAM_CHOICE = "Histogram"


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CytoGate")
        self.resize(1200, 760)
        self.samples: dict[str, ProcessedSample] = {}

        # sample list (FlowJo-style workspace table)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Name", "#Cells"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(lambda *_: self._sample_changed())

        # graph controls
        self.x_box, self.y_box = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.type_box = QtWidgets.QComboBox()
        self.type_box.addItems([t.value for t in PlotType if t != PlotType.HISTOGRAM])
        self.type_box.setCurrentText(PlotType.PSEUDOCOLOR.value)  # FlowJo's default
        self.hist_y_box = QtWidgets.QComboBox()
        self.hist_y_box.addItems([h.value for h in HistogramY])
        self.x_xf, self.y_xf = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        for box in (self.x_xf, self.y_xf):
            box.addItems(KINDS)
        self.x_custom = QtWidgets.QToolButton(text="…", toolTip="Customize X axis")
        self.y_custom = QtWidgets.QToolButton(text="…", toolTip="Customize Y axis")

        controls = QtWidgets.QGridLayout()
        controls.addWidget(QtWidgets.QLabel("X"), 0, 0)
        controls.addWidget(self.x_box, 0, 1)
        controls.addWidget(self.x_xf, 0, 2)
        controls.addWidget(self.x_custom, 0, 3)
        controls.addWidget(QtWidgets.QLabel("Y"), 1, 0)
        controls.addWidget(self.y_box, 1, 1)
        controls.addWidget(self.y_xf, 1, 2)
        controls.addWidget(self.y_custom, 1, 3)
        controls.addWidget(QtWidgets.QLabel("Plot"), 0, 4)
        controls.addWidget(self.type_box, 0, 5)
        controls.addWidget(QtWidgets.QLabel("Histogram Y"), 1, 4)
        controls.addWidget(self.hist_y_box, 1, 5)
        controls.setColumnStretch(1, 1)

        self.view = PlotView()
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addLayout(controls)
        rl.addWidget(self.view, 1)

        split = QtWidgets.QSplitter()
        split.addWidget(self.tree)
        split.addWidget(right)
        split.setSizes([360, 840])
        self.setCentralWidget(split)

        bar = self.addToolBar("Main")
        bar.setMovable(False)
        bar.addAction("Add Samples…", self.add_samples_dialog)
        bar.addAction("Add Folder…", self.add_folder_dialog)
        bar.addSeparator()
        bar.addAction("Export Graph…", self.export_graph_dialog)
        self.statusBar()
        self.setAcceptDrops(True)

        for box in (self.x_box, self.y_box, self.type_box, self.hist_y_box):
            box.currentIndexChanged.connect(self.redraw)
        self.x_box.currentIndexChanged.connect(self._sync_xf_boxes)
        self.y_box.currentIndexChanged.connect(self._sync_xf_boxes)
        self.x_xf.activated.connect(lambda: self._xf_kind_chosen(self.x_box, self.x_xf))
        self.y_xf.activated.connect(lambda: self._xf_kind_chosen(self.y_box, self.y_xf))
        self.x_custom.clicked.connect(lambda: self._customize(self.x_box))
        self.y_custom.clicked.connect(lambda: self._customize(self.y_box))

    # --- samples ----------------------------------------------------------
    def add_samples(self, paths) -> list[str]:
        errors = []
        for path in paths:
            path = Path(path)
            files = sorted(p for p in path.iterdir() if p.suffix.lower() == ".fcs") if path.is_dir() else [path]
            for f in files:
                try:
                    ps = ProcessedSample(load_fcs(f))
                except (FcsReadError, KeyError, ValueError) as exc:
                    errors.append(f"{f.name}: {exc}")
                    continue
                name = ps.sample.name
                if name in self.samples:
                    continue
                self.samples[name] = ps
                item = QtWidgets.QTreeWidgetItem([name, f"{ps.event_count:,}"])
                item.setTextAlignment(1, QtCore.Qt.AlignmentFlag.AlignRight)
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, name)
                self.tree.addTopLevelItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        if errors:
            QtWidgets.QMessageBox.warning(self, "Some files could not be read", "\n".join(errors))
        self.statusBar().showMessage(f"{len(self.samples)} samples")
        return errors

    def add_samples_dialog(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Add Samples", "", "FCS files (*.fcs *.FCS);;All files (*)")
        if files:
            self.add_samples(files)

    def add_folder_dialog(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Add Folder")
        if folder:
            self.add_samples([folder])

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QtGui.QDropEvent):
        self.add_samples([u.toLocalFile() for u in e.mimeData().urls()])

    def current(self) -> ProcessedSample | None:
        item = self.tree.currentItem()
        return self.samples.get(item.data(0, QtCore.Qt.ItemDataRole.UserRole)) if item else None

    def _sample_changed(self):
        ps = self.current()
        if ps is None:
            return
        old_x, old_y = self.x_box.currentData(), self.y_box.currentData()
        for box in (self.x_box, self.y_box):
            box.blockSignals(True)
            box.clear()
        for p in ps.parameters:
            self.x_box.addItem(p.label, p.name)
            self.y_box.addItem(p.label, p.name)
        self.y_box.addItem(HISTOGRAM_CHOICE, None)
        names = [p.name for p in ps.parameters]
        default_x = next((n for n in ("FSC-A", "FSC-H") if n in names), names[0])
        default_y = next((n for n in ("SSC-A", "SSC-H") if n in names), names[min(1, len(names) - 1)])
        self.x_box.setCurrentIndex(names.index(old_x) if old_x in names else names.index(default_x))
        self.y_box.setCurrentIndex(names.index(old_y) if old_y in names else names.index(default_y))
        for box in (self.x_box, self.y_box):
            box.blockSignals(False)
        self._sync_xf_boxes()
        self.redraw()

    # --- transforms -------------------------------------------------------
    def _sync_xf_boxes(self):
        ps = self.current()
        if ps is None:
            return
        for pbox, xbox in ((self.x_box, self.x_xf), (self.y_box, self.y_xf)):
            name = pbox.currentData()
            xbox.setEnabled(name is not None)
            if name is not None:
                xbox.setCurrentText(NAMES[type(ps.transforms[name])])

    def _xf_kind_chosen(self, pbox, xbox):
        ps, name = self.current(), pbox.currentData()
        if ps is None or name is None:
            return
        ps.set_transform(name, starter_transform(xbox.currentText(), ps.parameter(name), ps.transforms[name]))
        self.redraw()

    def _customize(self, pbox):
        ps, name = self.current(), pbox.currentData()
        if ps is None or name is None:
            return
        dlg = TransformDialog(ps.parameter(name), ps.transforms[name], self)
        if dlg.exec():
            ps.set_transform(name, dlg.transform())
            self._sync_xf_boxes()
            self.redraw()

    # --- drawing ----------------------------------------------------------
    def redraw(self, *_):
        ps = self.current()
        if ps is None or self.x_box.currentData() is None:
            self.view.clear()
            return
        y = self.y_box.currentData()
        kind = PlotType.HISTOGRAM if y is None else PlotType(self.type_box.currentText())
        self.type_box.setEnabled(y is not None)
        self.hist_y_box.setEnabled(y is None)
        self.view.show_sample(ps, self.x_box.currentData(), y, kind, HistogramY(self.hist_y_box.currentText()))

    def export_graph_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Graph", "graph.png", "PNG image (*.png)")
        if path:
            self.view.export_png(path)
