#!/usr/bin/env python3
"""CalaVoiceStudio 启动入口。

用法：
    python main.py           启动图形界面
    python main.py --smoke   无界面自检（构建主窗口与三个页面后立即退出）

自检结果会写到 <app_dir>\\data\\smoke_result.txt，因为 PyInstaller windowed exe 没有 stdout。
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def app_directory() -> Path:
    """程序数据根目录：打包后取 exe 所在目录，源码运行取本文件所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _safe_print(text: str) -> None:
    """windowed exe 没有 stdout，打印失败不算错误。"""
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(str(text) + "\n")
        stream.flush()
    except Exception:
        pass


def _write_text(app_dir: Path, name: str, text: str) -> str:
    target = Path(app_dir) / "data" / name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(text), encoding="utf-8")
    except OSError as exc:
        return "写入 %s 失败：%s" % (target, exc)
    return str(target)


def run_smoke(app_dir: Path, window) -> int:
    """构造完成后立即返回：把结果落盘，退出码 0 表示自检通过。"""
    pages = [window.tabs.widget(index) for index in range(window.tabs.count())]
    names = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    lines = [
        "SMOKE OK 页面数=%d %s" % (len(pages), "/".join(names)),
        "页面类名=%s" % "/".join(type(page).__name__ for page in pages),
        "frozen=%s" % bool(getattr(sys, "frozen", False)),
        "app_dir=%s" % app_dir,
        "exe=%s" % (sys.executable if getattr(sys, "frozen", False) else "(源码运行)"),
        "python=%s" % sys.version.split()[0],
        "qt_platform=%s" % (os.environ.get("QT_QPA_PLATFORM") or "(默认)"),
        "cwd=%s" % os.getcwd(),
        "时间=%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]
    text = "\n".join(lines)
    written = _write_text(app_dir, "smoke_result.txt", text)
    _safe_print(text)
    _safe_print("结果文件：%s" % written)
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    smoke = "--smoke" in args
    if smoke:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app_dir = app_directory()
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    try:
        from PySide6.QtWidgets import QApplication

        from calavoice.config import AppConfig
        from calavoice.ui.main_window import MainWindow
        from calavoice.ui.theme import apply_theme, app_icon

        cfg = AppConfig.load_or_create(app_dir)
        app = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else "main.py"])
        app.setApplicationName("CalaVoiceStudio")
        app.setApplicationDisplayName("CalaVoiceStudio · 卡拉语音工作室")
        apply_theme(app)
        app.setWindowIcon(app_icon())

        window = MainWindow(cfg)
        if smoke:
            return run_smoke(app_dir, window)

        window.show()
        return int(app.exec())
    except Exception:
        detail = traceback.format_exc()
        if smoke:
            _write_text(app_dir, "smoke_result.txt", "SMOKE FAIL\n" + detail)
        else:
            _write_text(app_dir, "startup_error.txt", detail)
        _safe_print(detail)
        return 1


if __name__ == "__main__":
    sys.exit(main())