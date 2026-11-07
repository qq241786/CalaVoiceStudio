"""外部程序启动：打开文件夹 / 启动 CalaPlayer 打包工具 / 把音频交给 Adobe Audition。"""

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


def launch_exe(exe_path: str, args=None) -> int:
    """启动传入的 exe（可带参数），返回进程 pid。"""
    text = str(exe_path or "").strip()
    if not text:
        raise LauncherError("程序路径为空，请先在设置页选择对应程序")
    target = Path(text)
    if not target.is_file():
        raise LauncherError("程序文件不存在：%s" % target)
    command = [str(target)]
    for item in args or []:
        piece = str(item or "").strip()
        if piece:
            command.append(piece)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(target.parent),
            close_fds=True,
        )
    except OSError as exc:
        raise LauncherError("无法启动程序 %s：%s" % (target, exc))
    return int(process.pid)


def launch_target(target_path: str) -> int:
    """启动一个 exe，或用文件管理器打开一个文件夹（自动判断）。返回 pid（打开文件夹时为 0）。"""
    text = str(target_path or "").strip()
    if not text:
        raise LauncherError("路径为空")
    target = Path(text)
    if target.is_file():
        return launch_exe(str(target))
    if target.is_dir():
        open_folder(str(target))
        return 0
    raise LauncherError("路径不存在：%s" % target)


def open_in_tool(exe_path: str, files) -> int:
    """用一个外部程序（例如 Adobe Audition）打开若干音频文件，返回进程 pid。"""
    items = [str(item or "").strip() for item in (files or [])]
    items = [item for item in items if item]
    if not items:
        raise LauncherError("没有可打开的音频文件")
    for item in items:
        if not Path(item).is_file():
            raise LauncherError("音频文件不存在：%s" % item)
    return launch_exe(exe_path, items)
