"""统一主题：深色毛玻璃 / 赛博玻璃幕（附录 A.2）+ 资产定位 + 真模糊背景。

配色取色与尺寸完全照 CONTRACT.md 附录 A.2，不得自创。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
)

# ---------- A.2 取色 ----------
BASE = "#0B1220"
GLASS = "rgba(14, 22, 38, 0.55)"
GLASS_LIGHT = "rgba(30, 44, 70, 0.45)"
STROKE = "rgba(255, 255, 255, 0.10)"
STROKE_HL = "rgba(98, 230, 255, 0.45)"
CYAN = "#62E6FF"
BLUE = "#2B6CF6"
PURPLE = "#B18CFF"
TEXT = "#EAF2FF"
TEXT_DIM = "#9FB3D1"
OK = "#57E39B"
WARN = "#FFC861"
ERROR = "#FF6B81"

RADIUS_PANEL = 14
RADIUS_CONTROL = 10
BLUR_RADIUS = 18
DIM_RGBA = "rgba(6, 10, 18, 0.35)"
OVERLAY = QColor(6, 10, 18, 89)  # 0.35 * 255

STATE_COLORS = {"ok": OK, "warn": WARN, "bad": ERROR, "idle": TEXT_DIM}

UI_FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Noto Sans CJK SC", "Segoe UI"]
MONO_FONT_FAMILIES = ["Consolas", "Cascadia Mono", "Courier New"]

ICON_CANDIDATES = ("icon_256.png", "icon_512.png", "icon_128.png", "icon_64.png")


# ---------- 资产定位（源码 / PyInstaller onedir 都能用） ----------
def assets_dir() -> Path:
    """<app_dir>/assets；打包后优先 _MEIPASS/assets，其次 exe 同目录 assets。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "assets"
        if bundled.is_dir():
            return bundled
    if getattr(sys, "frozen", False):
        beside_exe = Path(sys.executable).resolve().parent / "assets"
        if beside_exe.is_dir():
            return beside_exe
    return Path(__file__).resolve().parent.parent.parent / "assets"


def load_icon(name: str = "icon_256.png") -> QIcon:
    path = assets_dir() / name
    if path.is_file():
        return QIcon(str(path))
    return QIcon()


def app_icon() -> QIcon:
    for name in ICON_CANDIDATES:
        icon = load_icon(name)
        if not icon.isNull():
            return icon
    return QIcon()


def wallpaper_path() -> Path:
    for name in ("bg_wallpaper.png", "bg_wallpaper.jpg"):
        path = assets_dir() / name
        if path.is_file():
            return path
    return Path()


def avatar_pixmap(size: int = 24) -> QPixmap:
    icon = load_icon("icon_256.png")
    if icon.isNull():
        return QPixmap()
    return icon.pixmap(QSize(size, size))


# ---------- 字体 ----------
def ui_font(point_size: float = 10.5) -> QFont:
    font = QFont()
    font.setFamilies(UI_FONT_FAMILIES)
    font.setPointSizeF(float(point_size))
    return font


def title_font(point_size: float = 13.0) -> QFont:
    font = ui_font(point_size)
    font.setBold(True)
    return font


def monospace_font(point_size: float = 9.0) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FONT_FAMILIES)
    font.setPointSizeF(float(point_size))
    return font



# ---------- 图标：自绘线框（Fusion 标准图标是纯黑色，深色主题下等于看不见） ----------
ICON_DEFAULT = "#EAF2FF"
ICON_ON_PRIMARY = "#06121C"
ICON_DANGER = "#FFB3BE"

_ICON_CACHE: dict = {}


def _pen(painter: QPainter, color: str, width: float = 1.4) -> None:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _folder(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color)
    path = QPainterPath()
    path.moveTo(1.8, 4.4)
    path.lineTo(6.0, 4.4)
    path.lineTo(7.4, 5.9)
    path.lineTo(14.2, 5.9)
    path.lineTo(14.2, 12.6)
    path.lineTo(1.8, 12.6)
    path.closeSubpath()
    painter.drawPath(path)


def _folder_plus(painter: QPainter, accent: str, color: str) -> None:
    _folder(painter, accent, color)
    _pen(painter, accent, 1.6)
    painter.drawLine(QPointF(9.2, 9.6), QPointF(13.0, 9.6))
    painter.drawLine(QPointF(11.1, 7.7), QPointF(11.1, 11.5))


def _play(painter: QPainter, accent: str, color: str) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPolygon(QPolygonF([QPointF(5.0, 3.4), QPointF(13.0, 8.0), QPointF(5.0, 12.6)]))


def _stop(painter: QPainter, accent: str, color: str) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(4.6, 4.6, 6.8, 6.8), 1.4, 1.4)


def _refresh(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color, 1.5)
    painter.drawArc(QRectF(3.2, 3.2, 9.6, 9.6), 50 * 16, 285 * 16)
    _pen(painter, accent, 1.6)
    painter.drawLine(QPointF(9.2, 1.6), QPointF(12.6, 3.2))
    painter.drawLine(QPointF(9.2, 1.6), QPointF(9.6, 5.1))


def _save(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color)
    painter.drawRoundedRect(QRectF(3.0, 2.6, 10.0, 10.8), 1.4, 1.4)
    painter.drawRect(QRectF(5.6, 2.6, 4.4, 3.2))
    _pen(painter, accent, 1.3)
    painter.drawRect(QRectF(5.4, 9.2, 5.2, 4.2))


def _plus(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color, 1.6)
    painter.drawLine(QPointF(8.0, 3.2), QPointF(8.0, 12.8))
    painter.drawLine(QPointF(3.2, 8.0), QPointF(12.8, 8.0))


def _trash(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color)
    painter.drawLine(QPointF(3.2, 4.6), QPointF(12.8, 4.6))
    painter.drawLine(QPointF(6.2, 2.6), QPointF(9.8, 2.6))
    path = QPainterPath()
    path.moveTo(4.6, 4.6)
    path.lineTo(5.2, 13.2)
    path.lineTo(10.8, 13.2)
    path.lineTo(11.4, 4.6)
    painter.drawPath(path)
    _pen(painter, accent, 1.2)
    painter.drawLine(QPointF(6.8, 6.8), QPointF(7.1, 11.0))
    painter.drawLine(QPointF(9.2, 6.8), QPointF(8.9, 11.0))


def _arrow_up(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color, 1.6)
    painter.drawLine(QPointF(8.0, 13.0), QPointF(8.0, 3.4))
    painter.drawLine(QPointF(4.6, 6.8), QPointF(8.0, 3.4))
    painter.drawLine(QPointF(11.4, 6.8), QPointF(8.0, 3.4))


def _arrow_down(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color, 1.6)
    painter.drawLine(QPointF(8.0, 3.0), QPointF(8.0, 12.6))
    painter.drawLine(QPointF(4.6, 9.2), QPointF(8.0, 12.6))
    painter.drawLine(QPointF(11.4, 9.2), QPointF(8.0, 12.6))


def _import_txt(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color)
    path = QPainterPath()
    path.moveTo(3.6, 2.8)
    path.lineTo(9.6, 2.8)
    path.lineTo(12.4, 5.6)
    path.lineTo(12.4, 13.2)
    path.lineTo(3.6, 13.2)
    path.closeSubpath()
    painter.drawPath(path)
    _pen(painter, accent, 1.3)
    painter.drawLine(QPointF(5.4, 7.4), QPointF(10.6, 7.4))
    painter.drawLine(QPointF(5.4, 9.6), QPointF(10.6, 9.6))
    painter.drawLine(QPointF(5.4, 11.6), QPointF(8.6, 11.6))


def _volume(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color)
    body = QPainterPath()
    body.moveTo(2.8, 6.2)
    body.lineTo(5.4, 6.2)
    body.lineTo(8.6, 3.4)
    body.lineTo(8.6, 12.6)
    body.lineTo(5.4, 9.8)
    body.lineTo(2.8, 9.8)
    body.closeSubpath()
    painter.drawPath(body)
    _pen(painter, accent, 1.3)
    painter.drawArc(QRectF(8.0, 5.0, 4.6, 6.0), -70 * 16, 140 * 16)
    painter.drawArc(QRectF(9.4, 3.6, 5.4, 8.8), -62 * 16, 124 * 16)


def _checklist(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color)
    painter.drawRoundedRect(QRectF(2.8, 3.0, 3.4, 3.4), 0.8, 0.8)
    painter.drawRoundedRect(QRectF(2.8, 8.6, 3.4, 3.4), 0.8, 0.8)
    painter.drawLine(QPointF(7.6, 4.7), QPointF(13.2, 4.7))
    painter.drawLine(QPointF(7.6, 10.3), QPointF(13.2, 10.3))
    _pen(painter, accent, 1.5)
    painter.drawLine(QPointF(3.4, 4.7), QPointF(4.4, 5.8))
    painter.drawLine(QPointF(4.4, 5.8), QPointF(6.0, 3.4))


def _computer(painter: QPainter, accent: str, color: str) -> None:
    _pen(painter, color)
    painter.drawRoundedRect(QRectF(2.2, 3.4, 11.6, 7.4), 1.2, 1.2)
    painter.drawLine(QPointF(8.0, 10.8), QPointF(8.0, 12.8))
    painter.drawLine(QPointF(5.4, 13.0), QPointF(10.6, 13.0))
    _pen(painter, accent, 1.2)
    painter.drawLine(QPointF(4.4, 6.0), QPointF(8.0, 6.0))



_ICON_DRAWERS = {
    "folder": _folder,
    "folder_plus": _folder_plus,
    "play": _play,
    "stop": _stop,
    "refresh": _refresh,
    "save": _save,
    "plus": _plus,
    "trash": _trash,
    "arrow_up": _arrow_up,
    "arrow_down": _arrow_down,
    "import_txt": _import_txt,
    "volume": _volume,
    "checklist": _checklist,
    "computer": _computer,
}


def ui_icon(name: str, color: str = ICON_DEFAULT, accent: str = CYAN, size: int = 32) -> QIcon:
    """自绘 16x16 逻辑尺寸的线框图标（按 2x 渲染，深色/主色按钮都能看清）。"""
    key = (name, color, accent, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    drawer = _ICON_DRAWERS.get(name)
    if drawer is None:
        icon = app_icon()
        _ICON_CACHE[key] = icon
        return icon
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(size / 16.0, size / 16.0)
    try:
        drawer(painter, accent, color)
    finally:
        painter.end()
    icon = QIcon(pixmap)
    _ICON_CACHE[key] = icon
    return icon


def button_icon(name: str, primary: bool = False, danger: bool = False) -> QIcon:
    """按钮图标配色：主按钮用深色（青蓝渐变底），危险按钮用浅红，其余用亮白。"""
    if primary:
        return ui_icon(name, ICON_ON_PRIMARY, ICON_ON_PRIMARY)
    if danger:
        return ui_icon(name, ICON_DANGER, ICON_DANGER)
    return ui_icon(name, ICON_DEFAULT, CYAN)


# ---------- QSS ----------
QSS = """
QWidget { color: #EAF2FF; background-color: transparent; }
QMainWindow { background-color: transparent; }
QLabel { background-color: transparent; color: #EAF2FF; }
QLabel#AppTitle { font-size: 13pt; font-weight: 700; color: #EAF2FF; }
QLabel#AppSubtitle { color: rgba(159, 179, 209, 0.90); }
QLabel#HintLabel { color: rgba(159, 179, 209, 0.85); }

QGroupBox {
    background-color: rgba(14, 22, 38, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 14px;
    margin-top: 10px;
    padding: 3px 8px 3px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0px 6px;
    color: #62E6FF;
    font-weight: 600;
}

QPushButton {
    background-color: rgba(30, 44, 70, 0.45);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px;
    padding: 5px 12px;
    color: #EAF2FF;
}
QPushButton:hover {
    background-color: rgba(30, 44, 70, 0.70);
    border: 1px solid rgba(98, 230, 255, 0.45);
}
QPushButton:pressed { background-color: rgba(11, 18, 32, 0.90); }
QPushButton:disabled {
    color: rgba(159, 179, 209, 0.45);
    background-color: rgba(14, 22, 38, 0.30);
    border: 1px solid rgba(255, 255, 255, 0.06);
}
QPushButton[primary="true"] {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #62E6FF, stop:1 #2B6CF6);
    color: #06121C;
    font-weight: 700;
    border: 1px solid rgba(98, 230, 255, 0.60);
}
QPushButton[primary="true"]:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8DF0FF, stop:1 #4B84FF);
    border: 1px solid rgba(180, 245, 255, 0.85);
}
QPushButton[primary="true"]:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3FC9E6, stop:1 #1F55C8);
}
QPushButton[primary="true"]:disabled {
    background-color: rgba(98, 230, 255, 0.22);
    color: rgba(6, 18, 28, 0.55);
    border: 1px solid rgba(98, 230, 255, 0.20);
}
QPushButton[danger="true"] {
    background-color: rgba(255, 107, 129, 0.10);
    border: 1px solid rgba(255, 107, 129, 0.65);
    color: #FFB3BE;
}
QPushButton[danger="true"]:hover {
    background-color: rgba(255, 107, 129, 0.24);
    border: 1px solid rgba(255, 107, 129, 0.90);
}
QPushButton[danger="true"]:disabled {
    color: rgba(255, 179, 190, 0.40);
    border: 1px solid rgba(255, 107, 129, 0.22);
    background-color: rgba(255, 107, 129, 0.05);
}

QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    min-height: 16px;
    background-color: rgba(14, 22, 38, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 10px;
    padding: 2px 10px;
    color: #EAF2FF;
    selection-background-color: rgba(98, 230, 255, 0.35);
    selection-color: #06121C;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid rgba(98, 230, 255, 0.55);
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: rgba(159, 179, 209, 0.45);
    background-color: rgba(14, 22, 38, 0.30);
}
QPlainTextEdit#LogView {
    background-color: rgba(8, 14, 26, 0.60);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
}

QComboBox { min-width: 110px; }
QComboBox::down-arrow {
    image: url(__CHEVRON__);
    width: 10px;
    height: 6px;
    margin-right: 8px;
}
QComboBox::drop-down {
    border: 0px;
    width: 22px;
    background-color: transparent;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}
QComboBox QAbstractItemView {
    background-color: rgba(11, 18, 32, 0.98);
    border: 1px solid rgba(98, 230, 255, 0.35);
    border-radius: 10px;
    padding: 4px;
    outline: 0px;
    color: #EAF2FF;
    selection-background-color: rgba(98, 230, 255, 0.25);
    selection-color: #EAF2FF;
}
QComboBox QAbstractItemView::item { padding: 5px 8px; border-radius: 6px; }

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background-color: rgba(30, 44, 70, 0.55);
    border: 0px;
    width: 18px;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: rgba(98, 230, 255, 0.30);
}

QTableWidget, QTableView {
    background-color: rgba(14, 22, 38, 0.55);
    alternate-background-color: rgba(30, 44, 70, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 14px;
    gridline-color: transparent;
    color: #EAF2FF;
    selection-background-color: rgba(98, 230, 255, 0.18);
    selection-color: #EAF2FF;
}
QTableWidget::item, QTableView::item { border: 0px; padding: 4px 6px; }
QTableWidget::item:selected, QTableView::item:selected {
    background-color: rgba(98, 230, 255, 0.18);
    color: #EAF2FF;
}
QHeaderView { background-color: transparent; }
QHeaderView::section {
    background-color: rgba(30, 44, 70, 0.55);
    color: rgba(159, 179, 209, 0.95);
    padding: 5px 8px;
    border: 0px;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
    border-bottom: 1px solid rgba(255, 255, 255, 0.10);
}
QTableCornerButton::section { background-color: rgba(30, 44, 70, 0.55); border: 0px; }

QListWidget {
    background-color: rgba(14, 22, 38, 0.55);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 14px;
    padding: 6px;
    outline: 0px;
    color: #EAF2FF;
}
QListWidget::item { padding: 7px 10px; border-radius: 8px; margin-bottom: 2px; }
QListWidget::item:hover { background-color: rgba(30, 44, 70, 0.55); }
QListWidget::item:selected {
    background-color: rgba(98, 230, 255, 0.20);
    color: #EAF2FF;
    border: 1px solid rgba(98, 230, 255, 0.35);
}

QTabWidget::pane {
    background-color: rgba(14, 22, 38, 0.42);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 14px;
    top: -1px;
}
QTabBar { background-color: transparent; }
QTabBar::tab {
    background-color: transparent;
    color: rgba(159, 179, 209, 0.95);
    padding: 6px 18px;
    margin-right: 6px;
    border: 1px solid transparent;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}
QTabBar::tab:hover { color: #EAF2FF; background-color: rgba(30, 44, 70, 0.35); }
QTabBar::tab:selected {
    background-color: rgba(30, 44, 70, 0.75);
    color: #EAF2FF;
    font-weight: 600;
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-bottom: 1px solid rgba(98, 230, 255, 0.45);
}

QScrollBar:vertical { background-color: transparent; width: 8px; margin: 2px 0px 2px 0px; }
QScrollBar::handle:vertical {
    background-color: rgba(98, 230, 255, 0.35);
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background-color: rgba(98, 230, 255, 0.60); }
QScrollBar:horizontal { background-color: transparent; height: 8px; margin: 0px 2px 0px 2px; }
QScrollBar::handle:horizontal {
    background-color: rgba(98, 230, 255, 0.35);
    border-radius: 4px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background-color: rgba(98, 230, 255, 0.60); }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; background: transparent; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QCheckBox { color: #EAF2FF; spacing: 8px; background-color: transparent; }
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid rgba(255, 255, 255, 0.28);
    border-radius: 5px;
    background-color: rgba(14, 22, 38, 0.65);
}
QCheckBox::indicator:hover { border: 1px solid rgba(98, 230, 255, 0.65); }
QCheckBox::indicator:checked { background-color: #62E6FF; border: 1px solid #62E6FF; }

QToolTip {
    background-color: rgba(11, 18, 32, 0.98);
    color: #EAF2FF;
    border: 1px solid rgba(98, 230, 255, 0.45);
    border-radius: 8px;
    padding: 6px 8px;
}

QMenu {
    background-color: rgba(11, 18, 32, 0.98);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    padding: 6px;
    color: #EAF2FF;
}
QMenu::item { padding: 6px 20px; border-radius: 6px; background: transparent; }
QMenu::item:selected { background-color: rgba(98, 230, 255, 0.20); color: #EAF2FF; }
QMenu::separator { height: 1px; background-color: rgba(255, 255, 255, 0.10); margin: 4px 8px; }

QMessageBox { background-color: rgba(11, 18, 32, 0.98); }
QMessageBox QLabel { color: #EAF2FF; background: transparent; }
QMessageBox QPushButton { min-width: 78px; }
QDialog { background-color: rgba(11, 18, 32, 0.98); }
QInputDialog, QFileDialog { background-color: rgba(11, 18, 32, 0.98); color: #EAF2FF; }

QDockWidget {
    color: rgba(159, 179, 209, 0.95);
    background-color: rgba(14, 22, 38, 0.45);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
}
QDockWidget::title {
    background-color: rgba(30, 44, 70, 0.55);
    padding: 6px 10px;
    border-top-left-radius: 13px;
    border-top-right-radius: 13px;
    color: rgba(159, 179, 209, 0.95);
}

QToolBar {
    background-color: rgba(14, 22, 38, 0.50);
    border: 0px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.10);
    padding: 5px 10px;
    spacing: 6px;
}
QToolBar::separator {
    background-color: rgba(255, 255, 255, 0.10);
    width: 1px;
    margin: 4px 8px;
}

QStatusBar {
    background-color: rgba(14, 22, 38, 0.55);
    border-top: 1px solid rgba(255, 255, 255, 0.10);
    color: rgba(159, 179, 209, 0.95);
}
QStatusBar QLabel { padding: 0px 8px; background: transparent; }
QStatusBar::item { border: 0px; }

QSplitter::handle { background-color: rgba(255, 255, 255, 0.08); }

QScrollArea { background-color: transparent; border: 0px; }
QScrollArea > QWidget > QWidget { background-color: transparent; }
QScrollArea QWidget#SettingsContent { background-color: transparent; }
"""


def dark_palette() -> QPalette:
    """深色调色板：让 Fusion 自带的箭头/勾选/滚动按钮也变成浅色，避免黑底黑图。"""
    palette = QPalette()
    window = QColor(BASE)
    base_color = QColor("#0E1626")
    text = QColor(TEXT)
    dim = QColor(159, 179, 209)
    disabled = QColor(110, 126, 152)
    highlight = QColor(CYAN)
    highlighted_text = QColor("#06121C")
    for role, color in (
        (QPalette.ColorRole.Window, window),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base_color),
        (QPalette.ColorRole.AlternateBase, QColor("#111C30")),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, QColor("#141F33")),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.BrightText, QColor(ERROR)),
        (QPalette.ColorRole.ToolTipBase, QColor("#0B1220")),
        (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.Highlight, highlight),
        (QPalette.ColorRole.HighlightedText, highlighted_text),
        (QPalette.ColorRole.Link, QColor(CYAN)),
        (QPalette.ColorRole.PlaceholderText, dim),
    ):
        palette.setColor(role, color)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def chevron_icon() -> str:
    """运行时画一个青色小三角给 QSS 的 url() 用（写系统临时目录，不写死任何本机路径）。"""
    target = Path(tempfile.gettempdir()) / "calavoice_ui" / "chevron_down.png"
    try:
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            pixmap = QPixmap(10, 6)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(CYAN))
            painter.drawPolygon(QPolygonF([QPointF(0.0, 1.0), QPointF(10.0, 1.0), QPointF(5.0, 6.0)]))
            painter.end()
            pixmap.save(str(target), "PNG")
        return target.as_posix() if target.is_file() else ""
    except Exception:
        return ""


def build_qss() -> str:
    arrow = chevron_icon()
    if arrow:
        return QSS.replace("__CHEVRON__", arrow)
    return QSS.replace("image: url(__CHEVRON__);", "image: none;")


def apply_theme(app) -> None:
    """统一字体、深色玻璃外观与调色板。"""
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setFont(ui_font(10.5))
    app.setStyleSheet(build_qss())


class GlassBackground:
    """窗口底：壁纸 KeepAspectRatioByExpanding 铺满 + QGraphicsBlurEffect 真模糊 + 压暗层。

    resize 时由 schedule() 限频（单次 60ms）重算，避免拖窗口时反复重算大图。
    """

    def __init__(self, widget, blur_radius: float = BLUR_RADIUS, overlay: QColor = OVERLAY):
        self._widget = widget
        self._blur_radius = float(blur_radius)
        self._overlay = QColor(overlay)
        self._source = self._load_wallpaper()
        self._cache = None
        self._cache_size = QSize()
        self._timer = QTimer(widget)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.refresh_now)

    def _load_wallpaper(self) -> QPixmap:
        path = wallpaper_path()
        if path and path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap
        return QPixmap()

    def source_available(self) -> bool:
        return not self._source.isNull()

    def schedule(self) -> None:
        self._timer.start()

    def refresh_now(self) -> None:
        self._cache = None
        self._cache_size = QSize()
        self._widget.update()

    def paint(self, painter: QPainter) -> None:
        size = self._widget.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        if self._cache is None or self._cache_size != size:
            self._cache = self._render(size)
            self._cache_size = QSize(size)
        if self._cache is not None:
            painter.drawPixmap(0, 0, self._cache)

    def _render(self, size: QSize) -> QPixmap:
        if self._source.isNull():
            canvas = QPixmap(size)
            painter = QPainter(canvas)
            painter.fillRect(0, 0, size.width(), size.height(), QColor(BASE))
            painter.fillRect(0, 0, size.width(), size.height(), QColor(43, 108, 246, 26))
            painter.end()
            return canvas

        scaled = self._source.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        crop_x = max(0, (scaled.width() - size.width()) // 2)
        crop_y = max(0, (scaled.height() - size.height()) // 2)
        cropped = scaled.copy(crop_x, crop_y, size.width(), size.height())

        blurred = self._blur(cropped, size)
        painter = QPainter(blurred)
        painter.fillRect(0, 0, size.width(), size.height(), self._overlay)
        painter.end()
        return blurred

    def _blur(self, pixmap: QPixmap, size: QSize) -> QPixmap:
        canvas = QPixmap(size)
        canvas.fill(Qt.GlobalColor.transparent)
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pixmap)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(self._blur_radius)
        effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        painter = QPainter(canvas)
        scene.render(
            painter,
            QRectF(0.0, 0.0, float(size.width()), float(size.height())),
            QRectF(0.0, 0.0, float(size.width()), float(size.height())),
        )
        painter.end()
        scene.clear()
        return canvas