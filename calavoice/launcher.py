"""外部程序启动：打开文件夹 / 启动 CalaPlayer 打包工具。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class LauncherError(RuntimeError):
    """外部程序启动失败（消息为中文可读文案）。"""


def open_folder(path: str) -> None:
    """用系统文件管理器打开目录。"""
    text = str(path or "").strip()
    if not text:
        raise LauncherError("目录路径为空")
    target = Path(text)
    if not target.exists():
        raise LauncherError("目录不存在：%s" % target)
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(target))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        raise LauncherError("无法打开目录 %s：%s" % (target, exc))


def launch_exe(exe_path: str) -> int:
    """启动传入的 exe，返回进程 pid。"""
    text = str(exe_path or "").strip()
    if not text:
        raise LauncherError("程序路径为空，请先在设置页选择 CalaPlayer 程序")
    target = Path(text)
    if not target.is_file():
        raise LauncherError("程序文件不存在：%s" % target)
    try:
        process = subprocess.Popen(
            [str(target)],
            cwd=str(target.parent),
            close_fds=True,
        )
    except OSError as exc:
        raise LauncherError("无法启动程序 %s：%s" % (target, exc))
    return int(process.pid)
