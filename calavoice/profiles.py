"""人声档案读写：<data>/voices.json。"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import load_json, save_json

AUDIO_EXTS = (".ckpt", ".pth")


@dataclass
class VoiceProfile:
    name: str
    ref_audio: str = ""
    prompt_text: str = ""
    prompt_lang: str = "zh"
    text_lang: str = "zh"
    version: str = "v2ProPlus"
    device: str = "cuda"
    gpt_weights: str = ""
    sovits_weights: str = ""
    speed: float = 1.0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceProfile":
        known = {field.name for field in fields(cls)}
        kwargs = {key: value for key, value in (data or {}).items() if key in known}
        kwargs["name"] = str(kwargs.get("name") or "").strip()
        for key in ("ref_audio", "prompt_text", "prompt_lang", "text_lang", "version", "device", "gpt_weights", "sovits_weights", "note"):
            kwargs[key] = str(kwargs.get(key) or "")
        kwargs["prompt_lang"] = kwargs["prompt_lang"] or "zh"
        kwargs["text_lang"] = kwargs["text_lang"] or "zh"
        kwargs["version"] = kwargs["version"] or "v2ProPlus"
        kwargs["device"] = kwargs["device"] or "cuda"
        try:
            kwargs["speed"] = float(kwargs.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            kwargs["speed"] = 1.0
        return cls(**kwargs)


def _voices_path(cfg) -> Path:
    return Path(cfg.data_dir) / "voices.json"


def load_profiles(cfg) -> list[VoiceProfile]:
    raw = load_json(_voices_path(cfg), {"profiles": []})
    if isinstance(raw, dict):
        items = raw.get("profiles") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    result: list[VoiceProfile] = []
    for item in items:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            result.append(VoiceProfile.from_dict(item))
    return result


def _save_profiles(cfg, items: list[VoiceProfile]) -> None:
    save_json(_voices_path(cfg), {"profiles": [item.to_dict() for item in items]})


def upsert_profile(cfg, p: VoiceProfile) -> None:
    name = str(getattr(p, "name", "") or "").strip()
    if not name:
        raise ValueError("人声档案名称不能为空")
    p.name = name
    items = load_profiles(cfg)
    for index, existing in enumerate(items):
        if existing.name == name:
            items[index] = p
            break
    else:
        items.append(p)
    _save_profiles(cfg, items)


def delete_profile(cfg, name: str) -> bool:
    target = str(name or "").strip()
    if not target:
        return False
    items = load_profiles(cfg)
    remaining = [item for item in items if item.name != target]
    if len(remaining) == len(items):
        return False
    _save_profiles(cfg, remaining)
    return True


def _collect_files(directory: Path, suffixes: tuple[str, ...]) -> list[str]:
    """收集本层与下一层的权重文件（递归一层）。"""
    found: list[str] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.is_file():
                if os.path.splitext(entry.name)[1].lower() in suffixes:
                    found.append(str(Path(entry.path)))
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            sub_entries = list(os.scandir(entry.path))
        except OSError:
            continue
        for sub in sub_entries:
            if os.path.splitext(sub.name)[1].lower() not in suffixes:
                continue
            try:
                if sub.is_file():
                    found.append(str(Path(sub.path)))
            except OSError:
                continue
    return found


def _root_subdirs(root: str, prefix: str) -> list[str]:
    if not root:
        return []
    root_path = Path(root)
    if not root_path.is_dir():
        return []
    try:
        return sorted(str(entry) for entry in root_path.glob(prefix + "*") if entry.is_dir())
    except OSError:
        return []


def discover_weights(cfg, progress=None) -> dict:
    """扫描配置目录与 <sovits_root>\\GPT_weights* / SoVITS_weights*，返回去重排序后的绝对路径。"""
    root = str(cfg.get("sovits_root", "") or "").strip()
    gpt_dirs = [str(item) for item in (cfg.get("gpt_weight_dirs", []) or []) if str(item).strip()]
    sovits_dirs = [str(item) for item in (cfg.get("sovits_weight_dirs", []) or []) if str(item).strip()]
    gpt_dirs += _root_subdirs(root, "GPT_weights")
    sovits_dirs += _root_subdirs(root, "SoVITS_weights")

    def scan(dirs: list[str], suffixes: tuple[str, ...], label: str) -> list[str]:
        seen: dict[str, None] = {}
        for directory in dirs:
            path = Path(str(directory))
            if not path.is_dir():
                continue
            if callable(progress):
                progress("正在扫描%s：%s" % (label, path))
            for item in _collect_files(path, suffixes):
                seen.setdefault(item, None)
        return sorted(seen, key=lambda value: value.lower())

    gpt = scan(gpt_dirs, (".ckpt",), " GPT 权重")
    sovits = scan(sovits_dirs, (".pth",), " SoVITS 权重")
    if callable(progress):
        progress("权重扫描完成：GPT %d 个 / SoVITS %d 个" % (len(gpt), len(sovits)))
    return {"gpt": gpt, "sovits": sovits}
