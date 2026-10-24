"""路径发现与环境检查。不写死任何本机路径，一律由配置驱动。"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MATERIAL_SUBDIRS = ("bg", "BGM", "Sound", "Ambient")

CREATE_NO_WINDOW = 0x08000000

# 全盘深度扫描的统一预算（paths 内所有查找函数共用，避免深层目录漏检/无限扫描）
SCAN_MAX_DEPTH = 8
SCAN_MAX_DIRS = 60000
SCAN_MAX_ENTRIES = 600000


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


def discover_calaplayer(
    cfg,
    progress=None,
    max_depth: int = DEFAULT_CALAPLAYER_DEPTH,
    max_dirs: int = DEFAULT_CALAPLAYER_DIRS,
    max_entries: int = DEFAULT_CALAPLAYER_ENTRIES,
) -> dict:
    """扫描本机定位 CalaPlayer 游戏程序与打包工具，返回 {"game": [...], "builder": [...]}。

    不依赖任何写死路径：搜索根来自 discovery_roots(cfg)；已配置且存在的排在第一位。
    progress 回调抛异常视为用户取消，立刻返回空结果。
    """
    configured = {
        "game": str(_config_value(cfg, "calaplayer_exe", "") or "").strip(),
        "builder": str(_config_value(cfg, "calaplayer_builder_exe", "") or "").strip(),
    }
    found: dict = {"game": {}, "builder": {}}
    for kind, path in configured.items():
        if path and Path(path).is_file():
            found[kind][os.path.abspath(path)] = -1

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
                    "正在扫描第 %d 个目录（已找到 游戏 %d 个 / 打包工具 %d 个）：%s"
                    % (
                        scanned_dirs,
                        len(found["game"]),
                        len(found["builder"]),
                        current,
                    )
                ):
                    return {"game": [], "builder": []}
            try:
                entries = list(os.scandir(current))
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
                if kind is None:
                    continue
                path = os.path.abspath(entry.path)
                previous = found[kind].get(path)
                if previous is None or priority < previous:
                    found[kind][path] = priority

    result = {"game": [], "builder": []}
    for kind in ("game", "builder"):
        ordered = sorted(found[kind].items(), key=lambda item: (item[1], item[0].lower()))
        result[kind] = [path for path, _priority in ordered]
    report(
        "查找完成：扫描 %d 个目录 / %d 个条目，游戏 %d 个、打包工具 %d 个%s"
        % (
            scanned_dirs,
            scanned_entries,
            len(result["game"]),
            len(result["builder"]),
            "（已到扫描预算上限）" if budget_hit else "",
        )
    )
    return result


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
    """找 Adobe Audition（"打开 AU"）：已配置优先，其次注册表 App Paths，其次默认安装目录，最后开始菜单。"""
    found: list[str] = []
    configured = str(_config_value(cfg, "audition_exe", "") or "").strip() if cfg is not None else ""
    if configured and Path(configured).is_file():
        found.append(configured)

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
    found = discover_audition(cfg)
    return found[0] if found else ""


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
