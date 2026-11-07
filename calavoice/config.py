"""配置读写：<app_dir>/data/config.json。

DEFAULT_CONFIG 内不含任何本机路径，所有真实路径均来自用户配置或自动检测。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

DEFAULT_CONFIG: dict = {
    "sovits_root": "",
    "material_root": "",
    "calaplayer_exe": "",
    "calaplayer_builder_exe": "",
    # CalabiYauVoice 语音工具（顶栏「打开 CalabiYau」用）：exe 或它所在文件夹
    "calabiyau_exe": "",
    # Adobe Audition（"打开 AU"用）：留空时由 paths.resolve_audition_exe 自动检测
    "audition_exe": "",
    # 台词输出目录：留空时回落 <material_root>/<sound_subdir>
    "output_dir": "",
    "sound_subdir": "Sound",
    "port": 9880,
    "version": "v2ProPlus",
    "device": "cuda",
    "is_half": True,
    "text_lang": "zh",
    "prompt_lang": "zh",
    # 留空 = 自动用 <app_dir> + 本机固定磁盘（由 paths.discovery_roots 计算），不写死任何盘符
    "search_roots": [],
    "auto_start_backend": True,
    "gpt_weight_dirs": [],
    "sovits_weight_dirs": [],
    # 合成参数：分段间隔越小越快（过小可能造成段间爆音）
    "fragment_interval": 0.1,
    # 单次请求最大字数，超过则长文本自动分段合成后拼接
    "max_chars_per_request": 48,
    # 显存告警阈值（MiB）：空闲低于该值会打一条"合成会显著变慢"的警告
    "min_free_vram_mb": 2500,
}


def _atomic_save(path: Path, text: str) -> None:
    """临时文件 + os.replace 的原子写入，避免写坏配置。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path: Path, default):
    """容错读取 json：文件缺失或损坏时返回 default（深拷贝）。"""
    path = Path(path)
    if not path.is_file():
        return copy.deepcopy(default)
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return copy.deepcopy(default)


def save_json(path: Path, obj) -> None:
    _atomic_save(path, json.dumps(obj, ensure_ascii=False, indent=2))


class AppConfig:
    """应用配置。app_dir 由调用方传入，运行时文件统一落在 <app_dir>/data。"""

    def __init__(self, app_dir, data: dict | None = None):
        self._app_dir = Path(app_dir).resolve()
        self.path = self._app_dir / "data" / "config.json"
        self.data: dict = dict(data or {})

    @classmethod
    def load_or_create(cls, app_dir: Path) -> "AppConfig":
        app_dir = Path(app_dir).resolve()
        path = app_dir / "data" / "config.json"
        raw = {}
        dirty = False
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    raw = loaded
                else:
                    dirty = True
            except (OSError, ValueError):
                dirty = True
                try:
                    path.replace(path.with_name(path.name + ".bad"))
                except OSError:
                    pass
        else:
            dirty = True

        merged: dict = {}
        for key, value in DEFAULT_CONFIG.items():
            if key in raw:
                merged[key] = raw[key]
            else:
                merged[key] = copy.deepcopy(value)
                dirty = True
        for key, value in raw.items():
            merged.setdefault(key, value)

        cfg = cls(app_dir=app_dir, data=merged)
        if dirty:
            cfg.save()
        return cfg

    def get(self, key: str, default=None):
        if key in self.data:
            return self.data[key]
        if default is not None:
            return default
        return copy.deepcopy(DEFAULT_CONFIG.get(key))

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        save_json(self.path, self.data)

    @property
    def app_dir(self) -> Path:
        return self._app_dir

    @property
    def data_dir(self) -> Path:
        path = self._app_dir / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def logs_dir(self) -> Path:
        path = self.data_dir / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def runtime_dir(self) -> Path:
        path = self.data_dir / "runtime"
        path.mkdir(parents=True, exist_ok=True)
        return path
