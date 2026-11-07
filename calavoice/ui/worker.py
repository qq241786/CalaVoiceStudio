"""后台工作线程：环境检测、单行合成试听、批量生成。

界面上所有阻塞操作都必须走这里，绝不在 UI 线程直接调用 core 的阻塞函数。
"""

from __future__ import annotations

import os
import shutil
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from calavoice import paths
from calavoice.audio import convert_to_material_wav, probe
from calavoice.profiles import VoiceProfile, load_profiles
from calavoice.script_model import ScriptRow, make_out_name

STATUS_WAIT = "待生成"
STATUS_RUNNING = "生成中"
STATUS_DONE = "完成"
STATUS_FAIL = "失败"
STATUS_SKIP = "已跳过"

AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac")


def friendly_error(exc: BaseException) -> str:
    """把任意异常翻译成中文可读消息。"""
    if isinstance(exc, FileNotFoundError):
        target = getattr(exc, "filename", "") or str(exc)
        return "找不到文件或目录：%s" % target
    if isinstance(exc, PermissionError):
        target = getattr(exc, "filename", "") or str(exc)
        return "没有访问权限：%s" % target
    if isinstance(exc, TimeoutError):
        return "操作超时，请稍后重试"
    message = str(exc).strip()
    if message:
        return message
    return "发生未知错误（%s）" % type(exc).__name__


def resolve_tool(cfg, name: str) -> str:
    """定位 ffmpeg/ffprobe：优先用 core 提供的 resolver，找不到再本地兜底。"""
    helper = getattr(paths, "resolve_ffmpeg" if name == "ffmpeg" else "resolve_ffprobe", None)
    if callable(helper):
        try:
            found = str(helper(cfg) or "").strip()
        except Exception:
            found = ""
        if found and Path(found).is_file():
            return found

    candidates = []
    root = str(cfg.get("sovits_root") or "").strip()
    if root:
        base = Path(root)
        candidates.append(base / "runtime" / name)
        candidates.append(base / name)
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ValueError(
        "未找到 %s.exe，请在设置里确认 GPT-SoVITS 目录（应含 runtime\\%s.exe）" % (name, name)
    )


def material_sound_dir(cfg) -> Path:
    root = str(cfg.get("material_root") or "").strip()
    if not root:
        raise ValueError("还没有设置素材目录，请先到「设置」页选择素材目录")
    subdir = str(cfg.get("sound_subdir") or "Sound").strip() or "Sound"
    return Path(root) / subdir


def render_one(
    cfg,
    engine,
    *,
    text: str,
    profile: VoiceProfile,
    out_dir: Path,
    out_name: str,
    log=None,
    ensure_state: dict | None = None,
) -> tuple[str, dict]:
    """单条完整链路：ensure_running → apply_weights → synthesize → 转码 → 校验。

    切版本要重启后端（实测冷启动 40~60 秒），切权重是热切换；批量生成时把
    ensure_state 传进来，同一 (版本, 设备) 只拉起一次后端。
    """
    say = log or (lambda _msg: None)
    text = (text or "").strip()
    if not text:
        raise ValueError("台词内容为空")
    if profile is None:
        raise ValueError("没有可用的人声档案")
    if not str(profile.ref_audio or "").strip():
        raise ValueError("人声档案「%s」没有设置参考音频" % profile.name)

    version = str(profile.version or cfg.get("version") or "v2ProPlus")
    device = str(profile.device or cfg.get("device") or "cuda")
    state = ensure_state if ensure_state is not None else {}
    pair = (version, device)
    if state.get("pair") != pair or not engine.is_running():
        say("确认后端就绪（%s / %s）…首次启动约需 40~60 秒，请耐心等待" % (version, device))
        _register_prewarm(engine, profile)
        engine.ensure_running(version, device)
        state["pair"] = pair
    else:
        say("沿用已运行的后端（%s / %s）" % (version, device))

    if profile.gpt_weights or profile.sovits_weights:
        say("切换权重…")
    engine.apply_weights(profile.gpt_weights, profile.sovits_weights)

    say("合成中（%d 字）…" % len(text))
    synth = getattr(engine, "synthesize_long", None) or engine.synthesize
    audio_bytes = synth(
        text=text,
        text_lang=str(profile.text_lang or cfg.get("text_lang") or "zh"),
        ref_audio_path=profile.ref_audio,
        prompt_text=str(profile.prompt_text or ""),
        prompt_lang=str(profile.prompt_lang or cfg.get("prompt_lang") or "zh"),
        speed_factor=float(profile.speed or 1.0),
    )

    ffmpeg = resolve_tool(cfg, "ffmpeg")
    ffprobe = resolve_tool(cfg, "ffprobe")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    dst = str(Path(out_dir) / out_name)
    say("转换为 48kHz/16bit/单声道 …")
    convert_to_material_wav(audio_bytes, dst, ffmpeg)

    info = probe(dst, ffprobe)
    sample_rate = int(info.get("sample_rate") or 0)
    channels = int(info.get("channels") or 0)
    if sample_rate != 48000 or channels != 1:
        raise ValueError(
            "导出音频规格不正确：%d Hz / %d 声道（应为 48000 Hz / 单声道）" % (sample_rate, channels)
        )
    return dst, info


def _register_prewarm(engine, profile) -> None:
    """把当前档案的参考音频登记给引擎，后端启动完成后会顺便预热 CUDA。"""
    register = getattr(engine, "prewarm_ref", None)
    if register is None:
        return
    try:
        register(
            profile.ref_audio,
            str(profile.prompt_text or ""),
            str(profile.prompt_lang or "zh"),
            str(profile.text_lang or "zh"),
        )
    except Exception:
        pass


def _profiles_map(cfg) -> dict:
    try:
        return {p.name: p for p in load_profiles(cfg)}
    except Exception:
        return {}


def _copy_row(row: ScriptRow) -> ScriptRow:
    return ScriptRow(
        idx=row.idx,
        text=row.text,
        profile=row.profile,
        text_lang=row.text_lang,
        out_name=row.out_name,
        enabled=row.enabled,
    )


class GenerateWorker(QThread):
    """逐行批量生成。"""

    row_status = Signal(int, str, str)
    row_done = Signal(int, str)
    finished_all = Signal(str)
    log = Signal(str)

    def __init__(self, cfg, engine, rows, default_profile: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.default_profile = default_profile or ""
        self.rows = [_copy_row(row) for row in rows]
        self._stop = False

    def stop(self) -> None:
        """请求停止：先置标志，再关后端以打断当前阻塞的合成请求。"""
        self._stop = True
        try:
            self.engine.stop()
        except Exception:
            pass

    def run(self) -> None:  # noqa: C901 - 逐行流程，保持单函数便于阅读
        profiles = _profiles_map(self.cfg)
        ensure_state: dict = {}
        total = len(self.rows)
        done = 0
        failed = 0
        skipped = 0
        try:
            sound_dir = material_sound_dir(self.cfg)
        except Exception as exc:
            message = friendly_error(exc)
            for row in self.rows:
                self.row_status.emit(row.idx, STATUS_FAIL, message)
            self.finished_all.emit("无法开始生成：%s" % message)
            return

        for position, row in enumerate(self.rows, start=1):
            if self._stop:
                skipped += 1
                self.row_status.emit(row.idx, STATUS_SKIP, "已手动停止")
                self.log.emit("第 %d 行已跳过（手动停止）" % row.idx)
                continue

            profile_name = row.profile or self.default_profile
            profile = profiles.get(profile_name)
            if profile is None:
                failed += 1
                message = "未找到人声档案「%s」" % (profile_name or "未选择")
                self.row_status.emit(row.idx, STATUS_FAIL, message)
                self.log.emit("第 %d 行失败：%s" % (row.idx, message))
                continue

            self.row_status.emit(row.idx, STATUS_RUNNING, "生成中…")
            self.log.emit("第 %d/%d 行开始：档案「%s」" % (position, total, profile.name))
            try:
                out_name = make_out_name(row, profile.name)
                path, info = render_one(
                    self.cfg,
                    self.engine,
                    text=row.text,
                    profile=profile,
                    out_dir=sound_dir,
                    out_name=out_name,
                    log=lambda msg, idx=row.idx: self.log.emit("第 %d 行：%s" % (idx, msg)),
                    ensure_state=ensure_state,
                )
            except Exception as exc:
                failed += 1
                message = friendly_error(exc)
                self.row_status.emit(row.idx, STATUS_FAIL, message)
                self.log.emit("第 %d 行失败：%s" % (row.idx, message))
                self.log.emit(traceback.format_exc().strip())
                continue

            done += 1
            self.row_status.emit(row.idx, STATUS_DONE, path)
            self.row_done.emit(row.idx, path)
            self.log.emit(
                "第 %d 行完成：%s（%.1f 秒）"
                % (row.idx, os.path.basename(path), float(info.get("duration") or 0.0))
            )

        summary = "生成结束：成功 %d 条，失败 %d 条，跳过 %d 条" % (done, failed, skipped)
        self.finished_all.emit(summary)


class PreviewWorker(QThread):
    """单条合成（试听用），完成后回报文件路径。"""

    log = Signal(str)
    done = Signal(bool, str)

    def __init__(self, cfg, engine, text: str, profile: VoiceProfile, out_name: str = "preview.wav", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.text = text
        self.profile = profile
        self.out_name = out_name

    def run(self) -> None:
        try:
            preview_dir = Path(self.cfg.data_dir) / "preview"
            path, _info = render_one(
                self.cfg,
                self.engine,
                text=self.text,
                profile=self.profile,
                out_dir=preview_dir,
                out_name=self.out_name,
                log=self.log.emit,
            )
        except Exception as exc:
            self.log.emit(traceback.format_exc().strip())
            self.done.emit(False, friendly_error(exc))
            return
        self.log.emit("试听音频已生成：%s" % path)
        self.done.emit(True, path)


class CheckWorker(QThread):
    """环境检测 + 自动探测 GPT-SoVITS / 素材根目录 / 全部外部工具路径。"""

    progress = Signal(str)
    done = Signal(object)

    def __init__(self, cfg, autodetect: bool = False, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.autodetect = autodetect

    def _search_roots(self) -> list:
        roots = self.cfg.get("search_roots") or []
        roots = [str(r) for r in roots if str(r).strip()]
        return roots

    def _configured_ok(self, key: str) -> bool:
        """当前配置的路径是否仍然有效（文件/目录还在）。"""
        value = str(self.cfg.get(key) or "").strip()
        if not value:
            return False
        target = Path(value)
        try:
            if key in ("calaplayer_exe", "calaplayer_builder_exe"):
                return target.is_file()
            return target.is_file() or target.is_dir()
        except OSError:
            return False

    def _detect_tools(self, result: dict) -> None:
        """一次性探测全部外部工具路径：空的自动填好并落盘，用户不用逐项点『自动检测』。

        先走缓存/Steam 库等快速来源（毫秒级）；只有存在缺失且最近没全盘扫过时才真正扫盘，
        避免每次启动都花 5~10 秒。
        """
        tool_keys = list(getattr(paths, "TOOL_KEYS", ()) or ())
        if not tool_keys:
            return
        missing = [key for key in tool_keys if not self._configured_ok(key)]
        if not missing:
            return

        full_scan = any(key != "audition_exe" for key in missing)
        if full_scan and paths.full_scan_recent(self.cfg):
            full_scan = False
            self.progress.emit("检测缓存仍在有效期内：先按缓存/Steam 库快速定位，不重复全盘扫描。")
        detected = paths.detect_all(self.cfg, progress=self.progress.emit, full_scan=full_scan)
        result["detected"] = detected

        applied: dict = {}
        for key in missing:
            values = list(detected.get(key) or [])
            if not values:
                continue
            chosen = str(values[0])
            paths.remember_detected(self.cfg, key, chosen)
            self.cfg.set(key, chosen)
            applied[key] = chosen
            label = str((getattr(paths, "TOOL_LABELS", {}) or {}).get(key) or key)
            self.progress.emit("已自动填入%s：%s" % (label, chosen))
            for position, value in enumerate(values[1:5], start=2):
                self.progress.emit("  候选%d：%s" % (position, value))
        if applied:
            result.setdefault("applied", {}).update(applied)

        still_missing = [key for key in missing if key not in applied]
        if still_missing and not full_scan:
            names = "、".join(
                str((getattr(paths, "TOOL_LABELS", {}) or {}).get(key) or key) for key in still_missing
            )
            self.progress.emit("未找到：%s（已用快速定位；如需全盘重扫，请点『自动检测』）" % names)

    def _sovits_ok(self) -> bool:
        """GPT-SoVITS 目录是否仍然有效（含 api_v2.py 与内置 python）。"""
        value = str(self.cfg.get("sovits_root") or "").strip()
        if not value:
            return False
        root = Path(value)
        try:
            if not root.is_dir() or not (root / "api_v2.py").is_file():
                return False
            return (root / "runtime" / "python.exe").is_file() or (root / "runtime" / "bin" / "python").is_file()
        except OSError:
            return False

    def run(self) -> None:
        result = {"items": [], "sovits_roots": [], "material_roots": [], "applied": {}}
        try:
            if self.autodetect:
                roots = self._search_roots()
                # 只对"缺失/失效"的项做扫描：已经配好的不再重复扫盘（启动更快）
                if not self._sovits_ok():
                    self.progress.emit("正在自动探测 GPT-SoVITS 目录…")
                    found = paths.find_sovits_roots(roots, progress=self.progress.emit)
                    result["sovits_roots"] = list(found or [])
                    current = str(self.cfg.get("sovits_root") or "").strip()
                    chosen = ""
                    if current and current in result["sovits_roots"]:
                        chosen = current
                    elif result["sovits_roots"]:
                        chosen = result["sovits_roots"][0]
                    if chosen and not current:
                        self.cfg.set("sovits_root", chosen)
                        result["applied"]["sovits_root"] = chosen
                        self.progress.emit("已自动填入 GPT-SoVITS 目录：%s" % chosen)
                else:
                    result["sovits_roots"] = [str(self.cfg.get("sovits_root") or "").strip()]

                if not self._configured_ok("material_root"):
                    self.progress.emit("正在自动探测素材目录…")
                    materials = paths.find_material_roots(roots, progress=self.progress.emit)
                    result["material_roots"] = list(materials or [])
                    current_material = str(self.cfg.get("material_root") or "").strip()
                    if materials and not current_material:
                        self.cfg.set("material_root", materials[0])
                        result["applied"]["material_root"] = materials[0]
                        self.progress.emit("已自动填入素材目录：%s" % materials[0])
                else:
                    result["material_roots"] = [str(self.cfg.get("material_root") or "").strip()]

                self.progress.emit("正在自动探测外部工具路径…")
                self._detect_tools(result)

            self.progress.emit("正在检查运行环境…")
            result["items"] = list(paths.check_environment(self.cfg, progress=self.progress.emit) or [])
        except Exception as exc:
            result["error"] = friendly_error(exc)
            result["traceback"] = traceback.format_exc().strip()
        self.done.emit(result)


class TaskWorker(QThread):
    """通用后台任务：跑一个可调用对象，把结果/异常带回界面。"""

    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, pass_progress: bool = False, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._pass_progress = pass_progress
        self.error_trace = ""

    def _emit_progress(self, text) -> None:
        self.progress.emit(str(text))

    def run(self) -> None:
        try:
            if self._pass_progress:
                result = self._fn(self._emit_progress, *self._args, **self._kwargs)
            else:
                result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            self.error_trace = traceback.format_exc().strip()
            self.failed.emit(friendly_error(exc))
            return
        self.done.emit(result)