"""路径发现与环境检查。不写死任何本机路径，一律由配置驱动。"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

MATERIAL_SUBDIRS = ("bg", "BGM", "Sound", "Ambient")

CREATE_NO_WINDOW = 0x08000000

# 全盘深度扫描的统一预算（paths 内所有查找函数共用，避免深层目录漏检/无限扫描）
SCAN_MAX_DEPTH = 8
SCAN_MAX_DIRS = 60000
SCAN_MAX_ENTRIES = 600000

# 外部工具类路径：一键自动检测/缓存共用。顺序即界面展示顺序。
TOOL_KEYS = ("calaplayer_exe", "calaplayer_builder_exe", "calabiyau_exe", "audition_exe")
TOOL_LABELS = {
    "calaplayer_exe": "游戏 CalaPlayer",
    "calaplayer_builder_exe": "打包工具",
    "calabiyau_exe": "CalabiYau 语音工具",
    "audition_exe": "AU（Audition）",
}

# CalabiYauVoice 语音工具的主程序名（检测时按它匹配）
CALABIYAU_EXE_NAME = "CalabiYauVoice_GUI.exe"

# 检测缓存：<app_dir>/data/detect_cache.json
# 记住上次自动探测到的外部工具路径；下次启动/再次检测先验证缓存，命中即秒回，不必重复全盘扫描。
DETECT_CACHE_FILE = "detect_cache.json"
# 同一份缓存里"全盘扫描已完成"的有效期（秒）。过期后允许再扫一次，避免目录搬家后一直用旧结果。
DETECT_FULL_SCAN_TTL = 6 * 3600


@dataclass
class CheckItem:
    key: str
    label: str
    ok: bool
    detail: str
    hint: str = ""


def _notify(progress, message: str) -> None:
    if callable(progress):
        progress(message)


def _config_value(cfg, key: str, default=None):
    try:
        value = cfg.get(key, default)
    except Exception:
        value = default
    return default if value is None else value


def _new_budget(max_dirs: int, max_entries: int) -> dict:
    return {"dirs": 0, "entries": 0, "max_dirs": max_dirs, "max_entries": max_entries}


def _budget_exhausted(budget: dict) -> bool:
    return budget["dirs"] >= budget["max_dirs"] or budget["entries"] >= budget["max_entries"]


def _scan_roots(search_roots, cfg=None) -> list[str]:
    """显式传入的搜索根优先（不回落）；为空时回落 discovery_roots(cfg)，没有 cfg 再回落 系统盘+固定盘。"""
    roots: list[str] = []
    for item in search_roots or []:
        text = str(item or "").strip()
        if text:
            roots.append(text)
    if not roots:
        if cfg is not None:
            return discovery_roots(cfg)
        roots = [_system_drive()]
        for drive in fixed_drives():
            if drive.lower() not in {item.lower() for item in roots}:
                roots.append(drive)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in roots:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _walk_dirs(root: Path, max_depth: int, progress=None, budget: dict | None = None, with_children: bool = False):
    """广度优先遍历目录，深度以 root 为 0 计；progress 抛异常即中止；budget 用尽即停。

    yield (目录, 子目录名小写集合)。with_children=True 时每个目录都给出子目录名（叶子目录也扫一次、
    但不计入条目预算，等价于改造前 find_material_roots 自己再扫一遍）；False 时只有需要展开的目录才有
    名字，其余为 None，省掉一次多余的 scandir。
    """
    queue = [(root, 0)]
    scanned = 0
    while queue:
        current, depth = queue.pop(0)
        if budget is not None:
            budget["dirs"] += 1
            if _budget_exhausted(budget):
                return
        scanned += 1
        if callable(progress) and scanned % 200 == 0:
            _notify(progress, "正在扫描第 %d 个目录：%s" % (scanned, current))
        expand = depth < max_depth
        names = None
        child_links: list[Path] = []
        if expand or with_children:
            try:
                entries = list(os.scandir(current))
            except OSError:
                entries = []
            names = set()
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                names.add(entry.name.lower())
                if expand and not _skip_scan_dir(depth, entry.name):
                    child_links.append(Path(entry.path))
            if expand and budget is not None:
                budget["entries"] += len(entries)
        yield current, names
        for child in child_links:
            queue.append((child, depth + 1))


def find_sovits_roots(
    search_roots=None,
    progress=None,
    max_depth: int = SCAN_MAX_DEPTH,
    cfg=None,
    max_dirs: int = SCAN_MAX_DIRS,
    max_entries: int = SCAN_MAX_ENTRIES,
) -> list[str]:
    """查找同时含 api_v2.py 与 runtime\\python.exe 的目录。

    search_roots 为空（None / "" / []）时回落到 discovery_roots(cfg)（没有 cfg 则 系统盘+固定盘）；
    显式传入的盘符/目录列表不会回落，只在这些路径下扫描。
    """
    found: list[str] = []
    seen: set[str] = set()
    budget = _new_budget(max_dirs, max_entries)
    for base in _scan_roots(search_roots, cfg):
        if _budget_exhausted(budget):
            break
        base_text = str(base or "").strip()
        if not base_text:
            continue
        base_path = Path(base_text)
        if not base_path.is_dir():
            _notify(progress, "跳过不存在的路径：%s" % base_text)
            continue
        for candidate, _child_names in _walk_dirs(base_path, max_depth, progress, budget):
            hit = (candidate / "api_v2.py").is_file() and (
                (candidate / "runtime" / "python.exe").is_file()
                or (candidate / "runtime" / "bin" / "python").is_file()
            )
            if hit:
                key = str(candidate).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(str(candidate))
    return found


def find_material_roots(
    search_roots=None,
    progress=None,
    max_depth: int = SCAN_MAX_DEPTH,
    cfg=None,
    max_dirs: int = SCAN_MAX_DIRS,
    max_entries: int = SCAN_MAX_ENTRIES,
) -> list[str]:
    """查找 CalaPlayer 素材根：同时含 bg+Sound，或同时含 Sound+Ambient。

    搜索根回退规则与 find_sovits_roots 一致。
    """
    found: list[str] = []
    seen: set[str] = set()
    budget = _new_budget(max_dirs, max_entries)
    for base in _scan_roots(search_roots, cfg):
        if _budget_exhausted(budget):
            break
        base_text = str(base or "").strip()
        if not base_text:
            continue
        base_path = Path(base_text)
        if not base_path.is_dir():
            continue
        for candidate, child_names in _walk_dirs(base_path, max_depth, progress, budget, with_children=True):
            names = child_names or set()
            if ("bg" in names and "sound" in names) or ("sound" in names and "ambient" in names):
                key = str(candidate).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(str(candidate))
    return found


# ---------------------------------------------------------------- 动态盘符与查找


DRIVE_FIXED = 3
# 别名：与 paths 内其他查找函数共用同一份预算定义
DEFAULT_CALAPLAYER_DEPTH = SCAN_MAX_DEPTH
DEFAULT_CALAPLAYER_DIRS = SCAN_MAX_DIRS
DEFAULT_CALAPLAYER_ENTRIES = SCAN_MAX_ENTRIES

# 只在"盘根的直接子目录"这一层按名字跳过（深层同名目录不能跳，例如游戏的 Windows 目录）
_ROOT_LEVEL_SKIP = {
    "windows",
    "windows.old",
    "program files",
    "program files (x86)",
    "programdata",
    "recovery",
    "perflogs",
    "$windows.~bt",
    "config.msi",
    "$recycle.bin",
    "system volume information",
}
# 任何层级都跳过
_ANY_DEPTH_SKIP = {
    "appdata",
    "node_modules",
    ".git",
    "__pycache__",
    "$recycle.bin",
    "system volume information",
}


def _system_drive() -> str:
    root = str(os.environ.get("SystemDrive") or "").strip()
    if not root:
        root = Path(os.path.abspath(os.sep)).anchor
    if root and not root.endswith(os.sep):
        root += os.sep
    return root or os.sep


def _drive_type(root: str) -> int:
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(str(root))))
    except Exception:
        return DRIVE_FIXED


def _logical_drives() -> list[str]:
    """旧解释器（os.listdrives 之前）用 GetLogicalDrives 位图枚举盘符。"""
    try:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    except Exception:
        return []
    roots: list[str] = []
    for index in range(26):
        if mask & (1 << index):
            roots.append("%s:%s" % (chr(ord("A") + index), os.sep))
    return roots


def fixed_drives() -> list[str]:
    """本机固定磁盘根列表（盘根加分隔符）；探测失败回落系统盘。不含任何写死盘符。"""
    lister = getattr(os, "listdrives", None)
    candidates: list[str] = []
    if callable(lister):
        try:
            candidates = [str(item) for item in lister()]
        except OSError:
            candidates = []
    if not candidates:
        candidates = _logical_drives()
    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = (item or "").strip()
        if not text:
            continue
        if not text.endswith(os.sep):
            text += os.sep
        if _drive_type(text) != DRIVE_FIXED:
            continue
        if not os.path.isdir(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    if not result:
        fallback = _system_drive()
        if os.path.isdir(fallback):
            result.append(fallback)
    return result


def discovery_roots(cfg) -> list[str]:
    """搜索根：cfg.search_roots 非空则去重后使用，否则 [app_dir, *fixed_drives()]。"""
    roots: list[str] = []
    for item in _config_value(cfg, "search_roots", []) or []:
        text = str(item or "").strip()
        if text:
            roots.append(os.path.abspath(text))
    if not roots:
        app_dir = ""
        try:
            app_dir = str(getattr(cfg, "app_dir", "") or "")
        except Exception:
            app_dir = ""
        if app_dir:
            roots.append(os.path.abspath(app_dir))
        roots.extend(fixed_drives())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in roots:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _skip_scan_dir(parent_depth: int, name: str) -> bool:
    lowered = (name or "").lower()
    if lowered in _ANY_DEPTH_SKIP:
        return True
    if parent_depth == 0 and lowered in _ROOT_LEVEL_SKIP:
        return True
    return False


# ---------------------------------------------------------------- 检测缓存
# 目标：同一个路径不需要用户每次都点「自动检测」；程序启动时先读缓存，命中即用。
# 缓存只记录"上次真实探测到"的路径，读取时会再验证一次文件/目录是否还在（搬家/卸载自动失效）。


def _detect_cache_path(cfg) -> Path | None:
    if cfg is None:
        return None
    try:
        return Path(cfg.data_dir) / DETECT_CACHE_FILE
    except Exception:
        return None


def load_detect_cache(cfg) -> dict:
    """读取检测缓存；文件缺失/损坏返回 {"paths": {}, "full_scan_at": 0.0}。"""
    default = {"paths": {}, "full_scan_at": 0.0}
    path = _detect_cache_path(cfg)
    if path is None or not path.is_file():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return default
    if not isinstance(data, dict):
        return default
    paths = data.get("paths")
    if not isinstance(paths, dict):
        paths = {}
    cleaned: dict = {}
    for key, value in paths.items():
        text = str(value or "").strip()
        if text:
            cleaned[str(key)] = text
    try:
        scanned_at = float(data.get("full_scan_at") or 0.0)
    except (TypeError, ValueError):
        scanned_at = 0.0
    return {"paths": cleaned, "full_scan_at": scanned_at}


def save_detect_cache(cfg, values: dict) -> None:
    """合并写入检测缓存（values: {key: 路径}）。写失败不影响主流程。"""
    path = _detect_cache_path(cfg)
    if path is None:
        return
    cache = load_detect_cache(cfg)
    merged = dict(cache.get("paths") or {})
    changed = False
    for key, value in (values or {}).items():
        text = str(value or "").strip()
        if not text:
            continue
        if merged.get(str(key)) != text:
            merged[str(key)] = text
            changed = True
    if not changed:
        return
    payload = {"paths": merged, "full_scan_at": cache.get("full_scan_at") or 0.0}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        return


def remember_detected(cfg, key: str, value: str) -> None:
    """把一次成功探测/用户选择的路径写入缓存（下次启动直接命中）。"""
    text = str(value or "").strip()
    if text:
        save_detect_cache(cfg, {str(key): text})


def cached_detection(cfg, key: str) -> str:
    """返回缓存里仍然存在的路径；不存在/未缓存返回空串。"""
    value = str((load_detect_cache(cfg).get("paths") or {}).get(key) or "").strip()
    if not value:
        return ""
    target = Path(value)
    if target.is_file() or target.is_dir():
        return value
    return ""


def _cache_full_scan_fresh(cfg, ttl: float = DETECT_FULL_SCAN_TTL) -> bool:
    cache = load_detect_cache(cfg)
    try:
        scanned_at = float(cache.get("full_scan_at") or 0.0)
    except (TypeError, ValueError):
        scanned_at = 0.0
    return bool(scanned_at and (time.time() - scanned_at) < ttl)


def full_scan_recent(cfg, ttl: float = DETECT_FULL_SCAN_TTL) -> bool:
    """最近是否已经做过一次全盘扫描（用于避免每次启动都重扫几十万个目录）。"""
    return _cache_full_scan_fresh(cfg, ttl)


def mark_full_scan(cfg) -> None:
    """记录一次完整磁盘扫描的时间，用于避免每次启动都重扫几十万个目录。"""
    path = _detect_cache_path(cfg)
    if path is None:
        return
    cache = load_detect_cache(cfg)
    payload = {"paths": dict(cache.get("paths") or {}), "full_scan_at": time.time()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        return


def classify_calaplayer_exe(file_name: str):
    """按文件名判定 (类别, 优先级)：game/builder，数字越小越优先。不属于候选则返回 (None, None)。"""
    lowered = (file_name or "").lower()
    if not lowered.endswith(".exe"):
        return None, None
    has_calaplayer = "calaplayer" in lowered
    has_builder = "builder" in lowered
    has_gui = "gui" in lowered
    if has_calaplayer and (has_builder or has_gui):
        return "builder", 0
    if has_calaplayer and lowered == "calaplayer.exe":
        return "game", 0
    if has_calaplayer:
        return "game", 1
    if has_builder and has_gui:
        return "builder", 1
    return None, None


def _compact_name(text: str) -> str:
    return (text or "").lower().replace("_", "").replace("-", "").replace(" ", "")


def classify_calabiyau_exe(file_name: str):
    """按文件名判定是否为 CalabiYauVoice 工具：(kind, 优先级) 或 (None, None)。

    CalabiYauVoice_GUI.exe 排 0；其它名字里带 calabiyau 的 exe 排 1。
    游戏本体安装包（Calabiyau_Installer_*.exe）不是本工具，直接排除。
    """
    lowered = (file_name or "").lower()
    if not lowered.endswith(".exe"):
        return None, None
    stem = _compact_name(lowered[:-4])
    if "calabiyau" not in stem:
        return None, None
    if "installer" in stem or "uninstall" in stem or stem.startswith("unins"):
        return None, None
    if stem == _compact_name(CALABIYAU_EXE_NAME[:-4]):
        return "calabiyau", 0
    return "calabiyau", 1


# ---------------------------------------------------------------- 快速来源（免全盘扫描）
# 用户抱怨"每次都要点自动检测才能找到这两个程序"。原因之一是每次都全盘 BFS。
# 这里优先用注册表/Steam 库/已配置路径的相邻目录做浅层探测：毫秒级命中，找不到才退回全盘扫描。

_VDF_PATH_RE = re.compile(r'"path"\s+"([^"]+)"')


def _steam_common_roots() -> list[str]:
    """Steam 各库的 steamapps\\common（注册表 SteamPath + libraryfolders.vdf）。

    本机常见布局：<库盘>\\SteamLibrary\\steamapps\\common\\<游戏>。
    只读注册表与 vdf，不写任何东西；读不到就返回空列表（不写死任何盘符）。
    """
    steam_dirs: list[Path] = []
    try:
        import winreg
    except ImportError:
        winreg = None
    if winreg is not None:
        for hive, sub in (
            (winreg.HKEY_CURRENT_USER, "Software\\Valve\\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\Valve\\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Valve\\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    for value_name in ("SteamPath", "InstallPath"):
                        try:
                            value, _kind = winreg.QueryValueEx(key, value_name)
                        except OSError:
                            continue
                        text = str(value or "").strip().strip('"')
                        if text and Path(text).is_dir():
                            steam_dirs.append(Path(text))
            except OSError:
                continue

    libraries: list[Path] = list(steam_dirs)
    for steam in list(steam_dirs):
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _VDF_PATH_RE.finditer(text):
            candidate = Path(match.group(1).replace("\\\\", "\\"))
            if candidate.is_dir():
                libraries.append(candidate)

    result: list[str] = []
    seen: set[str] = set()
    for library in libraries:
        common = library / "steamapps" / "common"
        if not common.is_dir():
            continue
        key = str(common).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(str(common))
    return result


def _quick_scan_game_dir(game_dir: Path) -> list[tuple[str, str, int]]:
    """在一个游戏目录里做浅层探测（只读、深度 <=2），返回 [(kind, exe 路径, 优先级)]。"""
    hits: list[tuple[str, str, int]] = []
    root = Path(game_dir)
    if not root.is_dir():
        return hits

    def classify_file(candidate: Path, base_priority: int) -> None:
        name = candidate.name
        kind, priority = classify_calaplayer_exe(name)
        if kind is not None:
            hits.append((kind, str(candidate), base_priority + int(priority or 0)))
        calabiyau_kind, calabiyau_priority = classify_calabiyau_exe(name)
        if calabiyau_kind is not None:
            hits.append((calabiyau_kind, str(candidate), base_priority + int(calabiyau_priority or 0)))

    def add_file(candidate: Path, base_priority: int) -> None:
        try:
            if candidate.is_file():
                classify_file(candidate, base_priority)
        except OSError:
            return

    def add_folder_exes(folder: Path, base_priority: int, limit: int = 60) -> None:
        try:
            with os.scandir(folder) as iterator:
                entries = list(iterator)[:limit]
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".exe"):
                    classify_file(Path(entry.path), base_priority)
            except OSError:
                continue

    # 1) 标准布局：<游戏目录>\Windows\CalaPlayer.exe、<游戏目录>\CalabiYauVoice_GUI\...
    add_file(root / "Windows" / "CalaPlayer.exe", 0)
    add_file(root / "CalaPlayer.exe", 1)
    add_file(root / "CalabiYauVoice_GUI" / CALABIYAU_EXE_NAME, 0)
    add_file(root / CALABIYAU_EXE_NAME, 1)

    # 2) 直接位于游戏目录的 exe
    add_folder_exes(root, 2)

    # 3) 游戏目录的直接子目录：名字相关才进去看一眼，避免把整个游戏扫一遍
    try:
        with os.scandir(root) as iterator:
            children = [item for item in iterator if item.is_dir(follow_symlinks=False)][:80]
    except OSError:
        children = []
    for entry in children:
        name = (entry.name or "").lower()
        folder = Path(entry.path)
        if "calabiyau" in name:
            add_file(folder / CALABIYAU_EXE_NAME, 0)
            add_folder_exes(folder, 1)
        elif "calaplayer" in name or "builder" in name or "srcm" in name:
            add_folder_exes(folder, 1)
    return hits


def _fast_tool_candidates(cfg) -> dict:
    """不扫全盘就能命中的候选：检测缓存 + Steam 库 + 已配置路径的相邻目录。

    返回 {"game"/"builder"/"calabiyau"/"audition": {绝对路径: 优先级}}，越小越优先。
    """
    result: dict = {"game": {}, "builder": {}, "calabiyau": {}, "audition": {}}

    def add(kind: str, path, priority: int) -> None:
        text = str(path or "").strip()
        if not text:
            return
        target = Path(text)
        try:
            if not (target.is_file() or target.is_dir()):
                return
        except OSError:
            return
        absolute = os.path.abspath(str(target))
        previous = result[kind].get(absolute)
        if previous is None or priority < previous:
            result[kind][absolute] = priority

    # 1) 检测缓存（上次真实探测结果，读取时已验证存在）
    for key, kind in (
        ("calaplayer_exe", "game"),
        ("calaplayer_builder_exe", "builder"),
        ("calabiyau_exe", "calabiyau"),
        ("audition_exe", "audition"),
    ):
        cached = cached_detection(cfg, key)
        if cached:
            add(kind, cached, -2)

    # 2) Steam 库（最常见的安装位置，秒级命中）——只补还没命中的类别
    missing_kinds = {kind for kind in ("game", "builder", "calabiyau") if not result[kind]}
    if missing_kinds:
        for common in _steam_common_roots():
            try:
                with os.scandir(common) as iterator:
                    entries = list(iterator)[:400]
            except OSError:
                continue
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                for kind, path, priority in _quick_scan_game_dir(Path(entry.path)):
                    if kind in missing_kinds:
                        add(kind, path, priority)

    # 3) 已配置路径的相邻目录（例如 <游戏目录>\Windows\CalaPlayer.exe → <游戏目录>）
    seeds: list[Path] = []
    seeded: set[str] = set()
    for key, kind in (
        ("calaplayer_exe", "game"),
        ("calaplayer_builder_exe", "builder"),
        ("calabiyau_exe", "calabiyau"),
    ):
        if result[kind]:
            continue
        text = str(_config_value(cfg, key, "") or "").strip() if cfg is not None else ""
        if not text:
            continue
        target = Path(text)
        for parent in (target.parent, target.parent.parent):
            try:
                if not parent.is_dir():
                    continue
            except OSError:
                continue
            marker = str(parent).lower()
            if marker in seeded:
                continue
            seeded.add(marker)
            seeds.append(parent)
    for seed in seeds:
        if "calabiyau" in seed.name.lower():
            add("calabiyau", seed, -1)
        for kind, path, priority in _quick_scan_game_dir(seed):
            add(kind, path, priority)
    return result


def _scan_machine_tools(
    cfg,
    progress=None,
    max_depth: int = SCAN_MAX_DEPTH,
    max_dirs: int = SCAN_MAX_DIRS,
    max_entries: int = SCAN_MAX_ENTRIES,
) -> dict:
    """对 discovery_roots 做一次全盘 BFS，一次扫描同时找 CalaPlayer 游戏/打包工具/CalabiYau。"""
    found: dict = {"game": {}, "builder": {}, "calabiyau": {}}
    scanned_dirs = 0
    scanned_entries = 0
    budget_hit = False

    def report(message: str) -> bool:
        """返回 False 表示用户取消。"""
        if not callable(progress):
            return True
        try:
            progress(message)
        except Exception:
            return False
        return True

    def add(kind: str, path: str, priority: int) -> None:
        absolute = os.path.abspath(path)
        previous = found[kind].get(absolute)
        if previous is None or priority < previous:
            found[kind][absolute] = priority

    for root in discovery_roots(cfg):
        if budget_hit:
            break
        if not os.path.isdir(root):
            continue
        queue = [(root, 0)]
        while queue:
            current, depth = queue.pop(0)
            scanned_dirs += 1
            if scanned_dirs >= max_dirs or scanned_entries >= max_entries:
                budget_hit = True
                break
            if scanned_dirs % 200 == 0:
                if not report(
                    "正在扫描第 %d 个目录（游戏 %d / 打包工具 %d / CalabiYau %d）：%s"
                    % (
                        scanned_dirs,
                        len(found["game"]),
                        len(found["builder"]),
                        len(found["calabiyau"]),
                        current,
                    )
                ):
                    return {"game": {}, "builder": {}, "calabiyau": {}}
            try:
                with os.scandir(current) as iterator:
                    entries = list(iterator)
            except OSError:
                continue
            scanned_entries += len(entries)
            for entry in entries:
                name = entry.name
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if depth + 1 <= max_depth and not _skip_scan_dir(depth, name):
                        queue.append((entry.path, depth + 1))
                    continue
                kind, priority = classify_calaplayer_exe(name)
                if kind is not None:
                    add(kind, entry.path, 10 + int(priority or 0))
                calabiyau_kind, calabiyau_priority = classify_calabiyau_exe(name)
                if calabiyau_kind is not None:
                    add(calabiyau_kind, entry.path, 10 + int(calabiyau_priority or 0))
    report(
        "查找完成：扫描 %d 个目录 / %d 个条目，游戏 %d 个、打包工具 %d 个、CalabiYau %d 个%s"
        % (
            scanned_dirs,
            scanned_entries,
            len(found["game"]),
            len(found["builder"]),
            len(found["calabiyau"]),
            "（已到扫描预算上限）" if budget_hit else "",
        )
    )
    return found


def _ordered(found: dict) -> list[str]:
    return [path for path, _priority in sorted(found.items(), key=lambda item: (item[1], item[0].lower()))]


def discover_calaplayer(
    cfg,
    progress=None,
    max_depth: int = DEFAULT_CALAPLAYER_DEPTH,
    max_dirs: int = DEFAULT_CALAPLAYER_DIRS,
    max_entries: int = DEFAULT_CALAPLAYER_ENTRIES,
) -> dict:
    """扫描本机定位 CalaPlayer 游戏程序与打包工具，返回 {"game": [...], "builder": [...]}。

    顺序：已配置（用户手选）→ 检测缓存 → Steam 库/相邻目录（毫秒级）→ 全盘扫描。
    不依赖任何写死路径；progress 回调抛异常视为用户取消，立刻返回空结果。
    """
    found: dict = {"game": {}, "builder": {}}

    def add(kind: str, path: str, priority: int) -> None:
        absolute = os.path.abspath(str(path))
        previous = found[kind].get(absolute)
        if previous is None or priority < previous:
            found[kind][absolute] = priority

    for kind, key in (("game", "calaplayer_exe"), ("builder", "calaplayer_builder_exe")):
        text = str(_config_value(cfg, key, "") or "").strip()
        if text and Path(text).is_file():
            add(kind, text, -3)

    fast = _fast_tool_candidates(cfg)
    for kind in ("game", "builder"):
        for path, priority in (fast.get(kind) or {}).items():
            add(kind, path, priority)

    scanned = _scan_machine_tools(
        cfg, progress=progress, max_depth=max_depth, max_dirs=max_dirs, max_entries=max_entries
    )
    for kind in ("game", "builder"):
        for path, priority in (scanned.get(kind) or {}).items():
            add(kind, path, priority)

    return {kind: _ordered(found[kind]) for kind in ("game", "builder")}


def discover_calabiyau(
    cfg,
    progress=None,
    max_depth: int = DEFAULT_CALAPLAYER_DEPTH,
    max_dirs: int = DEFAULT_CALAPLAYER_DIRS,
    max_entries: int = DEFAULT_CALAPLAYER_ENTRIES,
) -> list[str]:
    """定位 CalabiYauVoice 语音工具（CalabiYauVoice_GUI.exe）；先缓存/Steam 库，再全盘扫描。"""
    found: dict = {}

    def add(path, priority: int) -> None:
        text = str(path or "").strip()
        if not text:
            return
        absolute = os.path.abspath(text)
        previous = found.get(absolute)
        if previous is None or priority < previous:
            found[absolute] = priority

    configured = str(_config_value(cfg, "calabiyau_exe", "") or "").strip()
    if configured:
        target = Path(configured)
        if target.is_dir():
            exe = target / CALABIYAU_EXE_NAME
            add(exe if exe.is_file() else target, -3)
        elif target.is_file():
            add(target, -3)

    fast = _fast_tool_candidates(cfg)
    for path, priority in (fast.get("calabiyau") or {}).items():
        add(path, priority)

    scanned = _scan_machine_tools(
        cfg, progress=progress, max_depth=max_depth, max_dirs=max_dirs, max_entries=max_entries
    )
    for path, priority in (scanned.get("calabiyau") or {}).items():
        add(path, priority)
    return _ordered(found)


def resolve_calabiyau_target(cfg) -> str:
    """返回 CalabiYauVoice 工具的可启动目标（exe 或文件夹）；只走快速来源，不扫全盘。

    找不到返回空串。文件夹也算合法目标（打开所在文件夹）。
    """
    configured = str(_config_value(cfg, "calabiyau_exe", "") or "").strip()
    target = Path(configured) if configured else None
    if target is not None:
        try:
            if target.is_file():
                return str(target)
            if target.is_dir():
                exe = target / CALABIYAU_EXE_NAME
                return str(exe) if exe.is_file() else str(target)
        except OSError:
            pass
    cached = cached_detection(cfg, "calabiyau_exe")
    if cached:
        return cached
    fast = _fast_tool_candidates(cfg)
    ordered = _ordered(fast.get("calabiyau") or {})
    if not ordered:
        return ""
    value = Path(ordered[0])
    try:
        if value.is_dir():
            exe = value / CALABIYAU_EXE_NAME
            return str(exe) if exe.is_file() else str(value)
    except OSError:
        pass
    return ordered[0]


def calabiyau_prefs_path() -> Path:
    """CalabiYauVoice_GUI 的唯一持久配置：%APPDATA%\\CalabiYauVoice\\prefs.json。"""
    appdata = str(os.environ.get("APPDATA") or "").strip()
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "CalabiYauVoice" / "prefs.json"


def ensure_calabiyau_prefs(cfg, output_dir: str = "") -> dict:
    """把 CalabiYauVoice_GUI 的默认保存/转换目录指向本软件的语音输出目录（"链接"两个工具）。

    只补 prefs.json 里缺失/为空的键，绝不覆盖用户已设置的值；文件存在但无法解析时保持原样。
    返回 {"path":..., "changed": [...], "skipped": 说明, "saved": bool}。
    """
    prefs = calabiyau_prefs_path()
    target = str(output_dir or "").strip() or resolve_output_dir(cfg)
    if not target:
        return {"path": str(prefs), "changed": [], "skipped": "未设置输出目录，跳过", "saved": False}
    output = os.path.abspath(target)

    data: dict = {}
    if prefs.is_file():
        try:
            loaded = json.loads(prefs.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("prefs.json 不是对象")
            data = loaded
        except (OSError, ValueError):
            return {
                "path": str(prefs),
                "changed": [],
                "skipped": "prefs.json 无法解析，保持原样",
                "saved": False,
            }

    changed: list[str] = []
    for key in ("savePath", "converterSavePath", "assetToolsOutputPath"):
        current = data.get(key)
        if isinstance(current, str) and current.strip():
            continue
        data[key] = output
        changed.append(key)
    if not changed:
        return {"path": str(prefs), "changed": [], "skipped": "已存在，未改动", "saved": True}
    try:
        prefs.parent.mkdir(parents=True, exist_ok=True)
        tmp = prefs.with_name(prefs.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, prefs)
    except OSError as exc:
        return {"path": str(prefs), "changed": [], "skipped": "写入失败：%s" % exc, "saved": False}
    return {"path": str(prefs), "changed": changed, "skipped": "", "saved": True}


def calabiyau_temp_dir(cfg) -> str:
    """给 CalabiYauVoice（Java/JNA）准备一个一定可写的临时目录，避免系统 TEMP 权限问题。"""
    target = Path(cfg.data_dir) / "tmp" / "calabiyau"
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def calabiyau_launch_env(cfg) -> dict:
    """启动 CalabiYauVoice 用的环境变量：TEMP/TMP + JAVA_TOOL_OPTIONS 指向软件内临时目录。

    Java 的 JNA 会把 jnidispatch.dll 解压到 java.io.tmpdir/jna.tmpdir；系统 TEMP 不可写
    （或进程完整性级别较低）时会报 "Failed to create temporary file ... 拒绝访问"。
    这里强制指到 <app_dir>/data/tmp/calabiyau，软件目录一定可写。
    """
    temp_dir = calabiyau_temp_dir(cfg)
    env = dict(os.environ)
    env["TEMP"] = temp_dir
    env["TMP"] = temp_dir
    options = "-Djava.io.tmpdir=%s -Djna.tmpdir=%s -Djna.nosys=true" % (temp_dir, temp_dir)
    existing = str(env.get("JAVA_TOOL_OPTIONS") or "").strip()
    env["JAVA_TOOL_OPTIONS"] = (existing + " " + options).strip() if existing else options
    return env


def detect_all(cfg, progress=None, full_scan: bool = True) -> dict:
    """一次性探测全部外部工具，返回 {key: [候选路径...]}（key 见 TOOL_KEYS）。

    顺序：已配置 → 检测缓存 → Steam 库/相邻目录（毫秒级）→ 全盘扫描（full_scan=True 时）。
    这是"不用逐项手动检测"的入口：一次调用就能补齐多个路径框。
    """
    priorities: dict = {key: {} for key in TOOL_KEYS}
    scanned_now = False

    def add(key: str, path, priority: int) -> None:
        text = str(path or "").strip()
        if not text:
            return
        absolute = os.path.abspath(text)
        previous = priorities[key].get(absolute)
        if previous is None or priority < previous:
            priorities[key][absolute] = priority

    def valid(key: str, path: str) -> bool:
        target = Path(path)
        try:
            if key in ("calaplayer_exe", "calaplayer_builder_exe"):
                return target.is_file()
            return target.is_file() or target.is_dir()
        except OSError:
            return False

    for key in TOOL_KEYS:
        configured = str(_config_value(cfg, key, "") or "").strip()
        if configured and valid(key, configured):
            add(key, configured, -3)
        cached = cached_detection(cfg, key)
        if cached:
            add(key, cached, -2)

    fast = _fast_tool_candidates(cfg)
    kind_by_key = {
        "calaplayer_exe": "game",
        "calaplayer_builder_exe": "builder",
        "calabiyau_exe": "calabiyau",
        "audition_exe": "audition",
    }
    for key in TOOL_KEYS:
        for path, priority in (fast.get(kind_by_key[key]) or {}).items():
            add(key, path, priority)

    tools_missing = any(not priorities[key] for key in ("calaplayer_exe", "calaplayer_builder_exe", "calabiyau_exe"))
    if full_scan and tools_missing:
        scanned_now = True
        _notify(progress, "正在扫描本机定位外部工具（首次约 5~10 秒，之后会走缓存）")
        scanned = _scan_machine_tools(cfg, progress=progress)
        for path, priority in (scanned.get("game") or {}).items():
            add("calaplayer_exe", path, priority)
        for path, priority in (scanned.get("builder") or {}).items():
            add("calaplayer_builder_exe", path, priority)
        for path, priority in (scanned.get("calabiyau") or {}).items():
            add("calabiyau_exe", path, priority)

    if not priorities["audition_exe"]:
        _notify(progress, "正在检测 Adobe Audition")
        for path in discover_audition(cfg, progress=None):
            add("audition_exe", path, 0)

    if scanned_now:
        mark_full_scan(cfg)
    return {key: _ordered(priorities[key]) for key in TOOL_KEYS}


# ---------------------------------------------------------------- 外部工具与输出目录


def _registry_app_paths(exe_name: str) -> list[str]:
    """读取注册表 App Paths 里登记的 exe 全路径（HKLM 与 HKCU 都查）。"""
    found: list[str] = []
    try:
        import winreg
    except ImportError:
        return found
    sub_key = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths"
    for hive in (getattr(winreg, "HKEY_LOCAL_MACHINE", None), getattr(winreg, "HKEY_CURRENT_USER", None)):
        if hive is None:
            continue
        try:
            with winreg.OpenKey(hive, sub_key + "\\" + exe_name) as key:
                value, _kind = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        text = str(value or "").strip().strip('"')
        if text:
            found.append(text)
    return found


def _start_menu_audition() -> list[str]:
    """兜底：解析开始菜单里 Audition 快捷方式的目标（拿不到就返回空）。"""
    script = (
        "$sh = New-Object -ComObject WScript.Shell; "
        "Get-ChildItem $env:ProgramData,'$env:APPDATA' -ErrorAction SilentlyContinue | Out-Null; "
        "$roots = @(\"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\","
        "\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\"); "
        "foreach ($r in $roots) { Get-ChildItem $r -Recurse -Filter '*Audition*.lnk' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $sh.CreateShortcut($_.FullName).TargetPath } }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout:
        return []
    text = result.stdout.decode("utf-8", "replace")
    return [line.strip().strip('"') for line in text.splitlines() if line.strip()]


def discover_audition(cfg=None, progress=None) -> list[str]:
    """找 Adobe Audition（"打开 AU"）：已配置优先，其次检测缓存，然后注册表 App Paths、默认安装目录、开始菜单。"""
    found: list[str] = []
    configured = str(_config_value(cfg, "audition_exe", "") or "").strip() if cfg is not None else ""
    if configured and Path(configured).is_file():
        found.append(configured)
    if cfg is not None:
        cached = cached_detection(cfg, "audition_exe")
        if cached:
            found.append(cached)

    _notify(progress, "正在检测 Adobe Audition")
    for name in ("Adobe Audition.exe", "Audition.exe"):
        found.extend(_registry_app_paths(name))

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        adobe = Path(base) / "Adobe"
        if not adobe.is_dir():
            continue
        try:
            folders = [item for item in adobe.glob("Adobe Audition*") if item.is_dir()]
        except OSError:
            folders = []
        for folder in folders:
            try:
                found.extend(str(item) for item in folder.glob("*.exe"))
            except OSError:
                continue

    if not found:
        found.extend(_start_menu_audition())

    result: list[str] = []
    seen: set[str] = set()
    for item in found:
        text = str(item or "").strip()
        if not text or not Path(text).is_file():
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def resolve_audition_exe(cfg=None) -> str:
    """返回可用的 Audition 可执行文件路径；找不到返回空串。"""
    configured = str(_config_value(cfg, "audition_exe", "") or "").strip() if cfg is not None else ""
    if configured and Path(configured).is_file():
        return configured
    if cfg is not None:
        cached = cached_detection(cfg, "audition_exe")
        if cached:
            return cached
    found = discover_audition(cfg)
    return found[0] if found else ""


def resolve_audition_cached(cfg=None) -> str:
    """只查配置与检测缓存（毫秒级，不触发扫描），供界面刷新按钮状态用。"""
    configured = str(_config_value(cfg, "audition_exe", "") or "").strip() if cfg is not None else ""
    if configured and Path(configured).is_file():
        return configured
    if cfg is not None:
        cached = cached_detection(cfg, "audition_exe")
        if cached:
            return cached
    return ""


def resolve_output_dir(cfg) -> str:
    """台词输出目录：cfg.output_dir 优先，否则 <material_root>/<sound_subdir>；都没有返回空串。"""
    explicit = str(_config_value(cfg, "output_dir", "") or "").strip()
    if explicit:
        return explicit
    material_root = str(_config_value(cfg, "material_root", "") or "").strip()
    if not material_root:
        return ""
    subdir = str(_config_value(cfg, "sound_subdir", "Sound") or "Sound").strip() or "Sound"
    return str(Path(material_root) / subdir)


def discover_output_dirs(cfg, progress=None) -> list[str]:
    """输出目录候选：当前配置优先，其次每个素材根下的 Sound 子目录（供"自动检测"用）。"""
    result: list[str] = []
    seen: set[str] = set()
    current = resolve_output_dir(cfg)
    if current:
        seen.add(current.lower())
        result.append(current)
    subdir = str(_config_value(cfg, "sound_subdir", "Sound") or "Sound").strip() or "Sound"
    for material_root in find_material_roots(None, progress=progress, cfg=cfg):
        candidate = str(Path(material_root) / subdir)
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def ensure_output_dir(cfg) -> str:
    """确保输出目录存在并返回；解析不出来抛 ValueError。"""
    target = resolve_output_dir(cfg)
    if not target:
        raise ValueError("未设置输出目录，请先在设置页选择素材根目录，或直接填写输出目录")
    Path(target).mkdir(parents=True, exist_ok=True)
    return target


def ensure_material_tree(root: str) -> list[str]:
    """创建 bg / BGM / Sound / Ambient，返回本次新建的目录名。"""
    root_text = str(root or "").strip()
    if not root_text:
        raise ValueError("素材根目录为空，请先在设置页选择 CalaPlayer 素材根目录")
    root_path = Path(root_text)
    root_path.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name in MATERIAL_SUBDIRS:
        target = root_path / name
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            created.append(name)
    return created


DEFAULT_MATERIAL_ROOT_NAME = "CalaVoiceMaterials"


def create_material_root(parent_dir: str, name: str = "", progress=None) -> tuple[str, list[str]]:
    """在 parent_dir 下**新建一个素材根文件夹**，并在其中建 bg / BGM / Sound / Ambient。

    返回 (素材根绝对路径, 本次新建的子目录名列表)。已存在的目录不重复创建，
    父目录本身不会被当成素材根使用（避免把游戏目录直接污染成素材根）。
    """
    parent_text = str(parent_dir or "").strip()
    if not parent_text:
        raise ValueError("请先选择父目录（例如游戏目录下的 Windows 文件夹）")
    parent_path = Path(parent_text)
    if not parent_path.is_dir():
        raise ValueError("父目录不存在：%s" % parent_path)

    folder_name = str(name or "").strip() or DEFAULT_MATERIAL_ROOT_NAME
    illegal = set('\\/:*?"<>|')
    folder_name = "".join("_" if ch in illegal else ch for ch in folder_name).strip(" .")
    if not folder_name:
        raise ValueError("素材根文件夹名不合法：%r" % name)

    if progress:
        progress("正在创建素材根：%s" % folder_name)
    root_path = parent_path / folder_name
    root_path.mkdir(parents=True, exist_ok=True)
    created = ensure_material_tree(str(root_path))
    return str(root_path), created


def _resolve_tool(cfg, exe_name: str) -> str:
    root = str(_config_value(cfg, "sovits_root", "") or "").strip()
    if root:
        candidate = Path(root) / "runtime" / exe_name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(exe_name) or ""


def resolve_ffmpeg(cfg) -> str:
    """优先用 GPT-SoVITS 自带 ffmpeg，其次 PATH；都找不到返回空串。"""
    return _resolve_tool(cfg, "ffmpeg.exe")


def resolve_ffprobe(cfg) -> str:
    """优先用 GPT-SoVITS 自带 ffprobe，其次 PATH；都找不到返回空串。"""
    return _resolve_tool(cfg, "ffprobe.exe")


def check_environment(cfg, progress=None) -> list[CheckItem]:
    """固定 10 条环境检查项，供设置页表格与 selftest 使用。"""
    items: list[CheckItem] = []
    root = str(_config_value(cfg, "sovits_root", "") or "").strip()
    root_path = Path(root) if root else None
    root_ok = bool(root_path is not None and root_path.is_dir())

    _notify(progress, "正在检查 GPT-SoVITS 根目录")
    if not root:
        items.append(
            CheckItem(
                "sovits_root",
                "GPT-SoVITS 根目录",
                False,
                "未设置",
                "在『设置』页选择 GPT-SoVITS 根目录，或点『自动检测』",
            )
        )
    elif not root_ok:
        items.append(
            CheckItem(
                "sovits_root",
                "GPT-SoVITS 根目录",
                False,
                "路径不存在：%s" % root,
                "请重新选择正确的 GPT-SoVITS 根目录",
            )
        )
    else:
        api_ok = (root_path / "api_v2.py").is_file()
        detail = root
        if not api_ok:
            detail = "%s（缺少 api_v2.py）" % root
        items.append(
            CheckItem(
                "sovits_root",
                "GPT-SoVITS 根目录",
                api_ok,
                detail,
                "" if api_ok else "所选目录不是 GPT-SoVITS 根目录（应含 api_v2.py）",
            )
        )

    _notify(progress, "正在检查内置 Python")
    runtime_python = None
    if root_ok:
        for rel in (("runtime", "python.exe"), ("runtime", "bin", "python")):
            candidate = root_path.joinpath(*rel)
            if candidate.is_file():
                runtime_python = candidate
                break
    items.append(
        CheckItem(
            "runtime_python",
            "内置 Python",
            runtime_python is not None,
            str(runtime_python) if runtime_python else ("未找到：%s" % (Path(root) / "runtime" / "python.exe") if root else "未设置 GPT-SoVITS 根目录"),
            "" if runtime_python else "GPT-SoVITS 根目录下应存在 runtime\\python.exe",
        )
    )

    _notify(progress, "正在检查 api_v2.py")
    api_v2 = root_path / "api_v2.py" if root_ok else None
    items.append(
        CheckItem(
            "api_v2",
            "后端脚本 api_v2.py",
            bool(api_v2 is not None and api_v2.is_file()),
            str(api_v2) if (api_v2 is not None and api_v2.is_file()) else ("未找到：%s" % api_v2 if api_v2 is not None else "未设置 GPT-SoVITS 根目录"),
            "" if (api_v2 is not None and api_v2.is_file()) else "请确认根目录下存在 api_v2.py",
        )
    )

    _notify(progress, "正在检查 ffmpeg")
    ffmpeg = resolve_ffmpeg(cfg)
    items.append(
        CheckItem(
            "ffmpeg",
            "ffmpeg",
            bool(ffmpeg),
            ffmpeg or "未找到（runtime\\ffmpeg.exe 与系统 PATH 均无）",
            "" if ffmpeg else "请确认 GPT-SoVITS 根目录含 runtime\\ffmpeg.exe，或将其加入 PATH",
        )
    )

    _notify(progress, "正在检查 ffprobe")
    ffprobe = resolve_ffprobe(cfg)
    items.append(
        CheckItem(
            "ffprobe",
            "ffprobe",
            bool(ffprobe),
            ffprobe or "未找到（runtime\\ffprobe.exe 与系统 PATH 均无）",
            "" if ffprobe else "请确认 GPT-SoVITS 根目录含 runtime\\ffprobe.exe，或将其加入 PATH",
        )
    )

    _notify(progress, "正在检查权重文件")
    gpt_count = sovits_count = 0
    weights_detail = "未设置 GPT-SoVITS 根目录"
    if root_ok:
        from . import profiles as _profiles

        found = _profiles.discover_weights(cfg)
        gpt_count = len(found.get("gpt") or [])
        sovits_count = len(found.get("sovits") or [])
        weights_detail = "GPT 权重 %d 个 / SoVITS 权重 %d 个" % (gpt_count, sovits_count)
        if gpt_count and sovits_count:
            weights_detail += "（%s）" % Path(found["sovits"][0]).name
    items.append(
        CheckItem(
            "weights",
            "权重文件",
            bool(root_ok and gpt_count > 0 and sovits_count > 0),
            weights_detail,
            "" if (root_ok and gpt_count and sovits_count) else "未发现 *.ckpt / *.pth，可在设置页指定权重目录（底模会自动使用）",
        )
    )

    _notify(progress, "正在检查素材根目录")
    material_root = str(_config_value(cfg, "material_root", "") or "").strip()
    material_ok = bool(material_root) and Path(material_root).is_dir()
    if not material_root:
        material_detail = "未设置"
    elif not material_ok:
        material_detail = "目录不存在：%s" % material_root
    else:
        material_detail = material_root
    items.append(
        CheckItem(
            "material_root",
            "CalaPlayer 素材根",
            material_ok,
            material_detail,
            "" if material_ok else "在『设置』页选择素材根目录，或点『创建素材结构』",
        )
    )

    _notify(progress, "正在检查输出目录")
    sound_subdir = str(_config_value(cfg, "sound_subdir", "Sound") or "Sound")
    if material_root:
        sound_dir = Path(material_root) / sound_subdir
        sound_ok = sound_dir.is_dir()
        if not sound_dir.parent.is_dir():
            sound_detail = "素材根目录不存在，无法确认：%s" % sound_dir
        elif sound_ok:
            sound_detail = str(sound_dir)
        else:
            sound_detail = "目录不存在：%s" % sound_dir
    else:
        sound_dir = None
        sound_ok = False
        sound_detail = "未设置素材根目录"
    items.append(
        CheckItem(
            "sound_dir",
            "台词输出目录",
            sound_ok,
            sound_detail,
            "" if sound_ok else "点『创建素材结构』将自动创建 bg / BGM / Sound / Ambient",
        )
    )

    _notify(progress, "正在检查 CalaPlayer")
    exe = str(_config_value(cfg, "calaplayer_exe", "") or "").strip()
    exe_ok = bool(exe) and Path(exe).is_file()
    if not exe:
        exe_detail = "未设置"
    elif not exe_ok:
        exe_detail = "文件不存在：%s" % exe
    else:
        exe_detail = exe
    items.append(
        CheckItem(
            "calaplayer_exe",
            "CalaPlayer 程序",
            exe_ok,
            exe_detail,
            "" if exe_ok else "在『设置』页选择 CalaPlayer 的 exe 文件",
        )
    )

    _notify(progress, "正在检查 CalaPlayer 打包工具")
    builder = str(_config_value(cfg, "calaplayer_builder_exe", "") or "").strip()
    builder_ok = bool(builder) and Path(builder).is_file()
    if not builder:
        builder_detail = "未设置"
    elif not builder_ok:
        builder_detail = "文件不存在：%s" % builder
    else:
        builder_detail = builder
    items.append(
        CheckItem(
            "calaplayer_builder",
            "CalaPlayer 打包工具",
            builder_ok,
            builder_detail,
            "" if builder_ok else "在『设置』页选择 CalaPlayerSrcmBuilderGUI.exe，或点『自动检测』",
        )
    )

    _notify(progress, "正在检查 CalabiYauVoice 语音工具")
    calabiyau = ""
    try:
        calabiyau = str(resolve_calabiyau_target(cfg) or "").strip()
    except Exception:
        calabiyau = ""
    calabiyau_ok = bool(calabiyau) and Path(calabiyau).exists()
    if not calabiyau:
        calabiyau_detail = "未设置（可选：只影响顶栏「打开 CalabiYau」按钮）"
    elif not calabiyau_ok:
        calabiyau_detail = "路径不存在：%s" % calabiyau
    else:
        calabiyau_detail = calabiyau
    items.append(
        CheckItem(
            "calabiyau_exe",
            "CalabiYauVoice 语音工具",
            calabiyau_ok,
            calabiyau_detail,
            ""
            if calabiyau_ok
            else "在『设置』页选择 CalabiYauVoice_GUI.exe（或它所在的文件夹），或点『自动检测』",
        )
    )

    _notify(progress, "正在检查 Adobe Audition")
    audition = ""
    try:
        audition = str(resolve_audition_exe(cfg) or "").strip()
    except Exception:
        audition = ""
    items.append(
        CheckItem(
            "audition_exe",
            "AU 程序（Audition）",
            bool(audition),
            audition or "未找到（可手选；只影响「放入 AU」相关按钮）",
            "" if audition else "在『设置』页选择 Adobe Audition.exe，或点『自动检测』",
        )
    )

    _notify(progress, "环境检查完成")
    return items


def check_python_env() -> CheckItem:
    """当前解释器是否 >=3.11 且可 import PySide6（不真的导入，避免拖慢 core）。"""
    version = "%d.%d.%d" % sys.version_info[:3]
    version_ok = sys.version_info[:2] >= (3, 11)
    try:
        pyside = importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        pyside = False
    ok = bool(version_ok and pyside)
    problems = []
    if not version_ok:
        problems.append("Python 版本过低（需要 3.11 及以上）")
    if not pyside:
        problems.append("未安装 PySide6")
    detail = "Python %s%s" % (version, "" if pyside else "，未检测到 PySide6")
    return CheckItem(
        "python_env",
        "运行环境",
        ok,
        detail,
        "" if ok else "；".join(problems),
    )
