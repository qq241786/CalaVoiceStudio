"""可复用的界面小部件。"""

from __future__ import annotations

import os
from datetime import datetime
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QWidget,
)

from calavoice.ui.theme import STATE_COLORS, button_icon, monospace_font, ui_icon


class StatusLight(QWidget):
    """彩色状态灯 + 一行摘要文字。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self._dot = QLabel(self)
        self._dot.setFixedSize(12, 12)
        self._label = QLabel(text, self)
        # Ignored 允许窗口变窄时压缩这一行文字（否则工具条会把右侧按钮挤进溢出菜单）
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._label.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._dot)
        layout.addWidget(self._label, 1)
        self.set_state("idle", text)

    def set_state(self, state: str, text: str | None = None) -> None:
        color = STATE_COLORS.get(state, STATE_COLORS["idle"])
        self._dot.setStyleSheet("background-color: %s; border-radius: 6px;" % color)
        if text is not None:
            self._full_text = text
            self.setToolTip(text)
            self._apply_elided_text()

    def _apply_elided_text(self) -> None:
        """按当前可用宽度画省略号；完整文本放 tooltip。"""
        width = max(0, self._label.width())
        metrics = self._label.fontMetrics()
        self._label.setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._apply_elided_text()

    def current_text(self) -> str:
        return self._label.text()


class LogView(QPlainTextEdit):
    """只读运行日志窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogView")
        self.setReadOnly(True)
        self.setMaximumBlockCount(2000)
        self.setFont(monospace_font(9))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMinimumHeight(84)

    def append_log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        content = str(text).strip() or "-"
        for line in content.splitlines():
            self.appendPlainText("[%s] %s" % (stamp, line))
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class PathRow(QWidget):
    """路径输入行：可手填、可浏览，另可挂一条下拉通道快速选历史/已发现路径。"""

    changed = Signal(str)

    def __init__(
        self,
        mode: str = "dir",
        file_filter: str = "所有文件 (*.*)",
        with_combo: bool = False,
        placeholder: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._mode = "file" if mode == "file" else "dir"
        self._filter = file_filter
        self.combo = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        self.edit.installEventFilter(self)
        layout.addWidget(self.edit, 1)

        if with_combo:
            self.combo = QComboBox(self)
            self.combo.setMinimumWidth(150)
            self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.combo.setMinimumContentsLength(16)
            self.combo.addItem("下拉选择…", "")
            self.combo.activated.connect(self._on_combo_activated)
            layout.addWidget(self.combo, 0)

        self.browse_btn = QPushButton("浏览…", self)
        self.browse_btn.setIcon(ui_icon("folder"))
        self.browse_btn.setIconSize(QSize(16, 16))
        self.browse_btn.clicked.connect(self.browse)
        layout.addWidget(self.browse_btn, 0)

    def browse(self) -> None:
        start = self.text().strip() or os.path.expanduser("~")
        if self._mode == "file":
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", start, self._filter)
        else:
            path = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if path:
            self.edit.setText(os.path.normpath(path))

    def _on_combo_activated(self, index: int) -> None:
        if self.combo is None:
            return
        data = self.combo.itemData(index)
        if data:
            self.edit.setText(str(data))

    def set_choices(self, paths) -> None:
        """刷新下拉通道的可选项。"""
        if self.combo is None:
            return
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("下拉选择…", "")
        for path in paths or []:
            text = str(path)
            if not text:
                continue
            self.combo.addItem(text, text)
            self.combo.setItemData(self.combo.count() - 1, text, Qt.ItemDataRole.ToolTipRole)
        self.combo.blockSignals(False)

    def text(self) -> str:
        return self.edit.text().strip()

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        if obj is self.edit and event.type() == QEvent.Type.Resize:
            self._apply_path_view()
        return super().eventFilter(obj, event)

    def _apply_path_view(self) -> None:
        """放得下就从头部显示（不裁字）；放不下才滚到行尾，优先看到文件名。"""
        text = self.edit.text()
        if not text:
            return
        available = self.edit.width() - 26  # 左右内边距
        if self.edit.fontMetrics().horizontalAdvance(text) > available:
            self.edit.setCursorPosition(len(text))
        else:
            self.edit.setCursorPosition(0)

    def set_text(self, value: str) -> None:
        text = str(value or "")
        self.edit.setText(text)
        self._apply_path_view()


LANG_ITEMS = [
    ("跟随档案", ""),
    ("中文", "zh"),
    ("英文", "en"),
    ("日文", "ja"),
    ("韩文", "ko"),
    ("粤语", "yue"),
    ("多语混合", "auto"),
]

VERSION_ITEMS = ["v1", "v2", "v3", "v4", "v2Pro", "v2ProPlus"]

DEVICE_ITEMS = [("显卡 (cuda)", "cuda"), ("处理器 (cpu)", "cpu")]


def fill_combo(combo: QComboBox, items) -> None:
    for item in items or []:
        if isinstance(item, (tuple, list)):
            combo.addItem(str(item[0]), str(item[1]))
        else:
            combo.addItem(str(item), str(item))


def select_combo(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


def make_button(
    text: str,
    icon_name: str | None = None,
    primary: bool = False,
    tooltip: str = "",
    parent=None,
    danger: bool = False,
) -> QPushButton:
    """icon_name 是自绘语义图标名（见 theme.ui_icon）；不用 emoji，避免深色下变豆腐块。"""
    button = QPushButton(text, parent)
    if icon_name:
        button.setIcon(button_icon(icon_name, primary=primary, danger=danger))
        size = 18 if primary else 16
        button.setIconSize(QSize(size, size))
    button.setProperty("primary", bool(primary))
    button.setProperty("danger", bool(danger))
    if tooltip:
        button.setToolTip(tooltip)
    button.setMinimumHeight(28)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def setup_table(
    table: QTableWidget,
    column_widths: dict | None = None,
    stretch_column: int | None = None,
    row_height: int = 30,
) -> None:
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for column, width in (column_widths or {}).items():
        table.setColumnWidth(column, width)
    if stretch_column is not None:
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)