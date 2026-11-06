"""Table Editor (FlowJo's Table Editor): define columns, batch over a group, export.

Drag populations from the workspace to add Freq. of Parent columns, or
statistic nodes to add those statistics. Each row of the editor is a column of
the output table; the cells edit the column options in place.
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..core.stats import Stat, StatSpec
from ..core.table import Column, TableDef, compute_table, export_table, heat_colors, table_from_json, table_to_json
from ..core.workspace import ALL_SAMPLES

EXPORT_FILTER = "CSV (*.csv);;Text, tab separated (*.txt);;Excel (*.xlsx);;Web page (*.html)"
HEADERS = ["Column", "Kind", "Heading", "Show", "Control", "Heat map", "Tube", "Discriminator"]


class _DefinitionTable(QtWidgets.QTableWidget):
    def __init__(self, editor):
        super().__init__(0, len(HEADERS))
        self.editor = editor
        self.setHorizontalHeaderLabels(HEADERS)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.horizontalHeader().setStretchLastSection(True)

    def dragEnterEvent(self, e):
        from .main_window import POP_MIME
        if e.mimeData().hasFormat(POP_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        self.editor.drop(e.mimeData())
        e.acceptProposedAction()


class TableEditor(QtWidgets.QMainWindow):
    def __init__(self, main, table: TableDef | None = None):
        super().__init__(main)
        self.main = main
        self.table = table or TableDef()
        self._filling = False
        self.resize(900, 420)

        bar = self.addToolBar("Table")
        bar.setMovable(False)
        bar.addAction("Add Statistic…", self.add_stat_dialog)
        bar.addAction("Add Keyword…", self.add_keyword_dialog)
        bar.addAction("Add Formula…", self.add_formula_dialog)
        bar.addAction("Remove", self.remove_selected)
        bar.addAction("▲", lambda: self.move_selected(-1))
        bar.addAction("▼", lambda: self.move_selected(1))
        bar.addSeparator()
        bar.addAction("Create Table", self.show_result)
        bar.addAction("To File…", self.export_dialog)
        bar.addAction("Copy", self.copy_to_clipboard)

        self.name_edit = QtWidgets.QLineEdit(self.table.name)
        self.name_edit.textChanged.connect(self._options_changed)
        self.group_box = QtWidgets.QComboBox()
        self.iterate_box = QtWidgets.QComboBox()
        self.iterate_box.addItem("Sample", "sample")
        self.iterate_box.addItem("Panel (n tubes per row)", "panel")
        self.iterate_box.addItem("Keyword value", "keyword")
        self.panel_spin = QtWidgets.QSpinBox(minimum=1, maximum=100)
        self.iter_kw = QtWidgets.QComboBox(editable=True)
        self.disc_kw = QtWidgets.QComboBox(editable=True)
        self.summary = QtWidgets.QCheckBox("Mean / SD rows")
        for w, sig in ((self.group_box, "currentIndexChanged"), (self.iterate_box, "currentIndexChanged"),
                       (self.panel_spin, "valueChanged"), (self.iter_kw, "currentTextChanged"),
                       (self.disc_kw, "currentTextChanged"), (self.summary, "toggled")):
            getattr(w, sig).connect(self._options_changed)
        top = QtWidgets.QFormLayout()
        top.addRow("Name", self.name_edit)
        top.addRow("Group", self.group_box)
        row = QtWidgets.QHBoxLayout()
        for w in (self.iterate_box, QtWidgets.QLabel("tubes"), self.panel_spin, QtWidgets.QLabel("by"),
                  self.iter_kw, QtWidgets.QLabel("tube keyword"), self.disc_kw):
            row.addWidget(w)
        top.addRow("Iterate by", row)
        top.addRow("", self.summary)

        self.grid = _DefinitionTable(self)
        self.grid.itemChanged.connect(self._cell_changed)
        self.result = QtWidgets.QTableWidget()
        self.result.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        split.addWidget(self.grid)
        split.addWidget(self.result)
        central = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(QtWidgets.QLabel("Drag populations or statistics here from the workspace."))
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self.reload_choices()
        self.refresh()

    # --- choices / options -------------------------------------------------------------
    def reload_choices(self) -> None:
        self._filling = True
        t = self.table
        self.group_box.clear()
        self.group_box.addItems(list(list(self.main.ws.groups)))
        self.group_box.setCurrentText(t.group if t.group in self.main.ws.groups else ALL_SAMPLES)
        kws = sorted(self.main.ws.keyword_names())
        for box, value in ((self.iter_kw, t.iterate_keyword), (self.disc_kw, t.discriminator_keyword)):
            box.clear()
            box.addItems(kws)
            box.setCurrentText(value)
        self.iterate_box.setCurrentIndex(max(self.iterate_box.findData(t.iterate), 0))
        self.panel_spin.setValue(max(t.panel_size, 1))
        self.summary.setChecked(t.summary_rows)
        self._filling = False
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        it = self.iterate_box.currentData()
        self.panel_spin.setEnabled(it == "panel")
        self.iter_kw.setEnabled(it == "keyword")
        self.disc_kw.setEnabled(it == "keyword")

    def _options_changed(self, *_) -> None:
        if self._filling:
            return
        t = self.table
        t.name = self.name_edit.text() or "Table"
        t.group = self.group_box.currentText() or ALL_SAMPLES
        t.iterate = self.iterate_box.currentData()
        t.panel_size = self.panel_spin.value()
        t.iterate_keyword = self.iter_kw.currentText()
        t.discriminator_keyword = self.disc_kw.currentText()
        t.summary_rows = self.summary.isChecked()
        self.setWindowTitle(f"Table Editor — {t.name}")
        self._sync_enabled()
        self.main.store_tables()

    # --- column definition grid --------------------------------------------------------------
    def refresh(self) -> None:
        self._filling = True
        self.setWindowTitle(f"Table Editor — {self.table.name}")
        cols = self.table.columns
        self.grid.setRowCount(len(cols))
        for r, c in enumerate(cols):
            def text(col, value, editable=False):
                it = QtWidgets.QTableWidgetItem(str(value))
                flags = QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable
                if editable:
                    flags |= QtCore.Qt.ItemFlag.ItemIsEditable
                it.setFlags(flags)
                self.grid.setItem(r, col, it)

            def check(col, on):
                it = QtWidgets.QTableWidgetItem()
                it.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                            | QtCore.Qt.ItemFlag.ItemIsSelectable)
                it.setCheckState(QtCore.Qt.CheckState.Checked if on else QtCore.Qt.CheckState.Unchecked)
                self.grid.setItem(r, col, it)

            text(0, Column(c.kind, c.path, c.spec, c.keyword, c.formula).header())
            text(1, c.kind)
            text(2, c.heading, editable=True)
            check(3, c.show)
            check(4, c.control)
            check(5, c.heatmap)
            text(6, c.panel_index, editable=True)
            text(7, c.discriminator, editable=True)
        self._filling = False

    def _cell_changed(self, item) -> None:
        if self._filling:
            return
        c = self.table.columns[item.row()]
        col = item.column()
        on = item.checkState() == QtCore.Qt.CheckState.Checked
        if col == 2:
            c.heading = item.text().strip()
        elif col == 3:
            c.show = on
        elif col == 4:
            c.control = on
        elif col == 5:
            c.heatmap = on
        elif col == 6:
            try:
                c.panel_index = max(int(item.text()), 1)
            except ValueError:
                pass
        elif col == 7:
            c.discriminator = item.text().strip()
        self.main.store_tables()

    def add_column(self, col: Column) -> Column:
        self.table.columns.append(col)
        self.refresh()
        self.main.store_tables()
        return col

    def drop(self, md) -> list[Column]:
        from .main_window import decode_populations, decode_statistics
        added = [Column("stat", p, StatSpec(Stat.FREQ_PARENT))
                 for _, p in decode_populations(md) if p]
        added += [Column("stat", p, spec) for _, p, spec in decode_statistics(md)]
        seen = {(tuple(c.path), c.spec) for c in self.table.columns if c.kind == "stat"}
        added = [c for c in added if (tuple(c.path), c.spec) not in seen]
        self.table.columns += added
        self.refresh()
        self.main.store_tables()
        return added

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.grid.selectedIndexes()})

    def remove_selected(self) -> None:
        for r in reversed(self.selected_rows()):
            del self.table.columns[r]
        self.refresh()
        self.main.store_tables()

    def move_selected(self, step: int) -> None:
        rows = self.selected_rows()
        if len(rows) != 1:
            return
        r, cols = rows[0], self.table.columns
        j = r + step
        if 0 <= j < len(cols):
            cols[r], cols[j] = cols[j], cols[r]
            self.refresh()
            self.grid.selectRow(j)
            self.main.store_tables()

    # --- adding columns with dialogs (ask_* replaced in tests) ---------------------------------
    def ask_population(self) -> tuple[str, ...] | None:
        a = next(iter(self.main.ws.samples.values()), None)
        if a is None:
            return None
        paths = [()] + [tuple(p) for p in a.gates.paths()]
        names = ["Ungated"] + ["/".join(p) for p in paths[1:]]
        name, ok = QtWidgets.QInputDialog.getItem(self, "Add Statistic", "Population", names, 0, False)
        return paths[names.index(name)] if ok else None

    def add_stat_dialog(self) -> Column | None:
        path = self.ask_population()
        if path is None:
            return None
        a = next(iter(self.main.ws.samples.values()))
        spec = self.main.ask_stat(a, path)
        return self.add_column(Column("stat", path, spec)) if spec is not None else None

    def add_keyword_dialog(self) -> Column | None:
        kws = sorted(self.main.ws.keyword_names())
        name, ok = QtWidgets.QInputDialog.getItem(self, "Add Keyword", "Keyword", kws, 0, True)
        return self.add_column(Column("keyword", keyword=name)) if ok and name else None

    def add_formula_dialog(self) -> Column | None:
        heads = ", ".join("{" + c.header() + "}" for c in self.table.columns if c.kind != "formula")
        f, ok = QtWidgets.QInputDialog.getText(self, "Add Formula",
                                               f"Formula; refer to columns in braces:\n{heads}")
        if not ok or not f.strip():
            return None
        h, ok = QtWidgets.QInputDialog.getText(self, "Add Formula", "Heading", text=f.strip())
        return self.add_column(Column("formula", formula=f.strip(), heading=h.strip() if ok else ""))

    # --- output --------------------------------------------------------------------------
    def compute(self):
        return compute_table(self.main.ws, self.table)

    def show_result(self):
        df = self.compute()
        self.result.clear()
        self.result.setRowCount(len(df))
        self.result.setColumnCount(len(df.columns))
        self.result.setHorizontalHeaderLabels([str(c) for c in df.columns])
        self.result.setVerticalHeaderLabels([str(i) for i in df.index])
        body = [i for i in df.index if i not in ("Mean", "SD")]
        heat = {c.header() for c in self.table.columns if c.heatmap}
        for j, h in enumerate(df.columns):
            colors = heat_colors(df.loc[body, h]) if h in heat else [None] * len(body)
            for i, v in enumerate(df[h]):
                it = QtWidgets.QTableWidgetItem(f"{v:.4g}" if isinstance(v, float) else str(v))
                if i < len(colors) and colors[i]:
                    it.setBackground(QtGui.QColor(colors[i]))
                self.result.setItem(i, j, it)
        self.result.resizeColumnsToContents()
        return df

    def export(self, path, fmt=None) -> str:
        return export_table(self.compute(), self.table, path, fmt)

    def export_dialog(self) -> str | None:
        start = str(Path(self.main.path or Path.home() / "x").parent / f"{self.table.name}.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Table", start, EXPORT_FILTER)
        if not path:
            return None
        out = self.export(path)
        self.statusBar().showMessage(f"Wrote {out}")
        return out

    def copy_to_clipboard(self) -> str:
        text = self.compute().to_csv(sep="\t")
        QtWidgets.QApplication.clipboard().setText(text)
        return text


def tables_to_extras(tables: list[TableDef]) -> list[dict]:
    return [table_to_json(t) for t in tables]


def tables_from_extras(data) -> list[TableDef]:
    out = []
    for d in data or []:
        try:
            out.append(table_from_json(d))
        except (KeyError, TypeError, ValueError):
            continue
    return out
