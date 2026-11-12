"""Small shared widgets: a layout that wraps buttons onto the next line, icon
button rows, and the workspace ribbon (text tabs over icon+label buttons)."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .icons import icon

__all__ = ["FlowLayout", "ButtonRow", "Ribbon", "tool_button"]


class FlowLayout(QtWidgets.QLayout):
    """Lays widgets left to right and wraps to a new line when the width runs out
    (so nothing is hidden behind a "»" overflow menu when the window is narrow)."""

    def __init__(self, parent=None, spacing: int = 2, margin: int = 2):
        super().__init__(parent)
        self._items: list[QtWidgets.QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        sp = self.spacing()
        for it in self._items:
            w = it.widget()
            if w is not None and w.isHidden():
                continue
            hint = it.sizeHint()
            if isinstance(w, Separator):  # a separator never starts a line
                if x == r.x():
                    continue
            if x + hint.width() > r.right() + 1 and line_h > 0:
                x, y = r.x(), y + line_h + sp
                line_h = 0
                if isinstance(w, Separator):
                    if apply:
                        it.setGeometry(QtCore.QRect(QtCore.QPoint(-100, -100), hint))
                    continue
            if apply:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class Separator(QtWidgets.QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        self.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.setFixedWidth(8)


def tool_button(text: str, icon_name: str | None = None, slot=None, tip: str | None = None,
                style=QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly, action: QtGui.QAction | None = None,
                size: int = 22) -> QtWidgets.QToolButton:
    b = QtWidgets.QToolButton()
    if action is not None:
        b.setDefaultAction(action)
    else:
        b.setText(text)
        if slot is not None:
            b.clicked.connect(lambda _=False: slot())
    if icon_name:
        b.setIcon(icon(icon_name))
    b.setIconSize(QtCore.QSize(size, size))
    b.setToolButtonStyle(style)
    b.setAutoRaise(True)
    b.setToolTip(tip or text)
    b.setObjectName(text)
    return b


class ButtonRow(QtWidgets.QWidget):
    """A row of buttons that wraps onto further lines when the window is narrow."""

    def __init__(self, parent=None, style=QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly, size: int = 22):
        super().__init__(parent)
        self.flow = FlowLayout(self)
        self.style_ = style
        self.size_ = size
        self.buttons: dict[str, QtWidgets.QToolButton] = {}
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Preferred)

    def add(self, text, icon_name=None, slot=None, tip=None, action=None) -> QtWidgets.QToolButton:
        b = tool_button(text, icon_name, slot, tip, self.style_, action, self.size_)
        self.flow.addWidget(b)
        self.buttons[text] = b
        return b

    def add_widget(self, w: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self.flow.addWidget(w)
        return w

    def separator(self) -> None:
        self.flow.addWidget(Separator())

    def button(self, text) -> QtWidgets.QToolButton:
        return self.buttons[text]


class Ribbon(QtWidgets.QWidget):
    """Text tabs (second line) choosing a page of icon+label buttons (third line)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = QtWidgets.QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(True)
        self.pages: dict[str, ButtonRow] = {}
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.tabs)
        self._stack = QtWidgets.QVBoxLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._stack)
        self.tabs.currentChanged.connect(self._show)

    def page(self, title: str) -> ButtonRow:
        row = ButtonRow(style=QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon, size=24)
        self.pages[title] = row
        self._stack.addWidget(row)
        self.tabs.addTab(title)
        self._show(self.tabs.currentIndex())
        return row

    def _show(self, index: int) -> None:
        current = self.tabs.tabText(index) if index >= 0 else None
        for title, row in self.pages.items():
            row.setVisible(title == current)

    def select(self, title: str) -> None:
        self.tabs.setCurrentIndex(list(self.pages).index(title))

    def current(self) -> str:
        return self.tabs.tabText(self.tabs.currentIndex())

    def button(self, title: str, text: str) -> QtWidgets.QToolButton:
        return self.pages[title].button(text)
