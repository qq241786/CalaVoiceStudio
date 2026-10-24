# -*- mode: python ; coding: utf-8 -*-
"""CalaVoiceStudio PyInstaller 打包配置（onedir + windowed）。

用法（任意工作目录下均可重复执行）：
    python -m PyInstaller --noconfirm --clean build.spec

产物：
    dist/CalaVoiceStudio/CalaVoiceStudio.exe
路径全部由 SPECPATH 动态解析，不含本机绝对路径。
"""

from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()  # SPECPATH 由 PyInstaller 注入
APP_NAME = "CalaVoiceStudio"

# 只排除与本程序无关的大包；PySide6 的必需模块一个都不动。
EXCLUDES = [
    "tkinter",
    "_tkinter",
    "turtle",
    "idlelib",
    "numpy",
    "pandas",
    "matplotlib",
    "scipy",
    "PIL",
    "IPython",
    "pytest",
    "docutils",
    "pip",
]

a = Analysis(
    [str(SPEC_DIR / "main.py")],
    pathex=[str(SPEC_DIR)],
    binaries=[],
    datas=[(str(SPEC_DIR / "assets"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_DIR / "assets" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
