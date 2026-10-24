"""台词行模型与 <data>/script.json 读写。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .config import load_json, save_json

INVALID_CHARS = set('<>:"/\\|?*') | {chr(code) for code in range(32)}
AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")


@dataclass
class ScriptRow:
    idx: int
    text: str = ""
    profile: str = ""
    text_lang: str = ""
    out_name: str = ""
    enabled: bool = True
    status: str = "待生成"
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ScriptRow":
        known = {field.name for field in fields(cls)}
        kwargs = {key: value for key, value in (data or {}).items() if key in known}
        try:
            kwargs["idx"] = int(kwargs.get("idx") or 0)
        except (TypeError, ValueError):
            kwargs["idx"] = 0
        for key in ("text", "profile", "text_lang", "out_name", "status", "message"):
            kwargs[key] = str(kwargs.get(key) or "")
        kwargs["status"] = kwargs["status"] or "待生成"
        kwargs["enabled"] = bool(kwargs.get("enabled", True))
        return cls(**kwargs)


def _script_path(cfg) -> Path:
    return Path(cfg.data_dir) / "script.json"


def load_rows(cfg) -> list[ScriptRow]:
    raw = load_json(_script_path(cfg), {"rows": []})
    if isinstance(raw, dict):
        items = raw.get("rows") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    rows: list[ScriptRow] = []
    for item in items:
        if isinstance(item, dict):
            rows.append(ScriptRow.from_dict(item))
    return rows


def save_rows(cfg, rows: list[ScriptRow]) -> None:
    payload = {"rows": [row.to_dict() for row in rows]}
    save_json(_script_path(cfg), payload)


def make_out_name(row: ScriptRow, profile_name: str) -> str:
    """输出名规则：row.out_name 优先，否则 v{idx:03d}_{档案名}；统一补 .wav 并过滤非法字符。"""
    raw = str(getattr(row, "out_name", "") or "").strip()
    if not raw:
        idx = getattr(row, "idx", 0) or 0
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        voice = str(profile_name or "").strip() or "default"
        base = "v%03d_%s" % (idx, voice)
    else:
        base = raw

    lower = base.lower()
    for suffix in AUDIO_SUFFIXES:
        if lower.endswith(suffix):
            base = base[: -len(suffix)]
            break

    cleaned = "".join("_" if character in INVALID_CHARS else character for character in base)
    cleaned = cleaned.strip().strip(".").strip()
    if not cleaned:
        idx = getattr(row, "idx", 0) or 0
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        cleaned = "v%03d" % idx
    return cleaned + ".wav"
