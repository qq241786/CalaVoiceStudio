"""下载 / 依赖页：把 GPT-SoVITS、打包工具、CalabiYau、本软件的官方下载链接放进软件里。

点按钮 = 用系统默认浏览器打开链接（音频/压缩包会直接开始下载）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from calavoice import downloads, launcher
from calavoice.ui.widgets import make_button


class DownloadsPage(QWidget):
    """依赖下载清单页。"""

    log = Signal(str)

    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._buttons: list[tuple[str, str, object]] = []
        self._build_ui()

    # ---------- 构建 ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = QLabel("下载 / 依赖", self)
        title.setObjectName("SectionTitle")
        title.setFont(title.font())
        title.setStyleSheet("font-size: 14pt; font-weight: 600;")
        root.addWidget(title)

        hint = QLabel(downloads.INSTALL_ORDER_NOTE, self)
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget(scroll)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(8)

        for group in downloads.DOWNLOAD_GROUPS:
            container_layout.addWidget(self._build_group(group, container))
        container_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        note = QLabel(downloads.GAME_AND_AU_NOTE, self)
        note.setObjectName("HintLabel")
        note.setWordWrap(True)
        footer.addWidget(note, 1)
        self.btn_home = make_button("打开软件主页", icon_name="link")
        self.btn_home.clicked.connect(
            lambda: self._open(downloads.APP_REPO, "CalaVoiceStudio 项目主页")
        )
        footer.addWidget(self.btn_home, 0)
        root.addLayout(footer)

    def _build_group(self, group: dict, parent) -> QGroupBox:
        box = QGroupBox(str(group.get("title") or "下载"), parent)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(4)

        note_text = str(group.get("note") or "").strip()
        if note_text:
            note = QLabel(note_text, box)
            note.setObjectName("HintLabel")
            note.setWordWrap(True)
            layout.addWidget(note)

        for item in group.get("items") or ():
            layout.addWidget(self._build_item(item, box))
        return box

    def _build_item(self, item: dict, parent) -> QWidget:
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        label_text = str(item.get("label") or "下载链接")
        detail = str(item.get("detail") or "").strip()
        text = label_text + ("（%s）" % detail if detail else "")
        label = QLabel(text, row)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label, 1)

        kind = str(item.get("kind") or "page")
        if kind == "file":
            button = make_button("直接下载", icon_name="download")
        else:
            button = make_button("打开页面", icon_name="link")
        url = str(item.get("url") or "").strip()
        button.setToolTip(url)
        button.clicked.connect(lambda _checked=False, u=url, n=label_text: self._open(u, n))
        layout.addWidget(button, 0)
        self._buttons.append((label_text, url, button))
        return row

    # ---------- 行为 ----------

    def _open(self, url: str, label: str) -> None:
        try:
            launcher.open_url(url)
        except Exception as exc:
            message = "无法打开链接：\n%s\n\n%s" % (url, exc)
            self.log.emit(message.replace("\n", " "))
            QMessageBox.warning(self, "打开链接失败", message)
            return
        self.log.emit("已在默认浏览器打开下载链接：%s（%s）" % (label, url))

    def buttons(self) -> list[tuple[str, str, object]]:
        """给测试用：[(标签, URL, 按钮), ...]。"""
        return list(self._buttons)
