"""音频转换与探测：全部交给 ffmpeg / ffprobe 子进程，不引入第三方库。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
CONVERT_TIMEOUT = 120.0
PROBE_TIMEOUT = 60.0


class AudioError(RuntimeError):
    """音频处理失败（消息为中文可读文案）。"""


def _creation_flags() -> int:
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def _tail(text: str, limit: int = 300) -> str:
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _resolve_exe(exe: str, default_name: str) -> str:
    candidate = str(exe or "").strip()
    if candidate and Path(candidate).is_file():
        return candidate
    name = Path(candidate).name if candidate else default_name
    located = shutil.which(name) or ""
    return located


def _run(command: list[str], timeout: float, tool: str):
    try:
        return subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        raise AudioError("未找到 %s 可执行文件：%s" % (tool, command[0]))
    except subprocess.TimeoutExpired:
        raise AudioError("%s 执行超时（%.0f 秒）" % (tool, timeout))
    except OSError as exc:
        raise AudioError("%s 启动失败：%s" % (tool, exc))


def convert_to_material_wav(src: "str | bytes", dst_path: str, ffmpeg: str) -> str:
    """转成 48000Hz / 16bit / 单声道 wav。src 为 bytes 时先落临时文件再清理。"""
    tool = _resolve_exe(ffmpeg, "ffmpeg")
    if not tool:
        raise AudioError(
            "未找到 ffmpeg：请确认已设置 GPT-SoVITS 根目录（其中应含 runtime\\ffmpeg.exe），"
            "或系统 PATH 中可执行 ffmpeg"
        )
    dst_text = str(dst_path or "").strip()
    if not dst_text:
        raise AudioError("输出文件路径为空")

    temp_files: list[str] = []
    try:
        if isinstance(src, (bytes, bytearray)):
            if not src:
                raise AudioError("音频数据为空，无法转换")
            handle, temp_path = tempfile.mkstemp(prefix="calavoice_src_", suffix=".bin")
            with os.fdopen(handle, "wb") as fh:
                fh.write(bytes(src))
            temp_files.append(temp_path)
            src_path = temp_path
        else:
            src_path = str(src or "").strip()
            if not src_path:
                raise AudioError("源音频路径为空")
            if not Path(src_path).is_file():
                raise AudioError("源音频不存在：%s" % src_path)

        dst = Path(dst_text)
        if not dst.is_absolute():
            dst = dst.absolute()
        dst.parent.mkdir(parents=True, exist_ok=True)
        command = [
            tool,
            "-y",
            "-i",
            src_path,
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
        result = _run(command, CONVERT_TIMEOUT, "ffmpeg")
        if result.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
            raise AudioError("ffmpeg 转换失败：%s" % (_tail(_decode(result.stderr)) or "未知错误"))
        return str(dst)
    finally:
        for temp_path in temp_files:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def merge_wavs(paths: list[str], dst: str, ffmpeg: str) -> str:
    """按顺序把多个 wav 拼接成一个（ffmpeg concat，输出 pcm_s16le）。"""
    tool = _resolve_exe(ffmpeg, "ffmpeg")
    if not tool:
        raise AudioError(
            "未找到 ffmpeg：请确认已设置 GPT-SoVITS 根目录（其中应含 runtime\\ffmpeg.exe），"
            "或系统 PATH 中可执行 ffmpeg"
        )
    items = [str(item or "").strip() for item in (paths or [])]
    items = [item for item in items if item]
    if not items:
        raise AudioError("没有可合并的音频文件")
    for item in items:
        if not Path(item).is_file():
            raise AudioError("待合并的音频不存在：%s" % item)
    dst_text = str(dst or "").strip()
    if not dst_text:
        raise AudioError("合并输出路径为空")
    target = Path(dst_text)
    if not target.is_absolute():
        target = target.absolute()
    target.parent.mkdir(parents=True, exist_ok=True)

    handle, list_path = tempfile.mkstemp(prefix="cala_concat_", suffix=".txt")
    try:
        lines = []
        for item in items:
            # concat 列表要求正斜杠；单引号按 ffmpeg 规则转义
            safe = str(Path(item).absolute()).replace("\\", "/").replace("'", "'\\''")
            lines.append("file '%s'" % safe)
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        command = [
            tool,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
        result = _run(command, CONVERT_TIMEOUT, "ffmpeg")
        if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            raise AudioError("ffmpeg 合并失败：%s" % (_tail(_decode(result.stderr)) or "未知错误"))
        return str(target)
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


def probe(path: str, ffprobe: str) -> dict:
    """返回 {"duration", "sample_rate", "channels", "codec_name"}。"""
    tool = _resolve_exe(ffprobe, "ffprobe")
    if not tool:
        raise AudioError(
            "未找到 ffprobe：请确认已设置 GPT-SoVITS 根目录（其中应含 runtime\\ffprobe.exe），"
            "或系统 PATH 中可执行 ffprobe"
        )
    target = Path(str(path or "").strip())
    if not target.is_file():
        raise AudioError("音频文件不存在：%s" % target)

    command = [tool, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(target)]
    result = _run(command, PROBE_TIMEOUT, "ffprobe")
    if result.returncode != 0:
        raise AudioError("ffprobe 读取失败：%s" % (_tail(_decode(result.stderr)) or "未知错误"))
    text = _decode(result.stdout)
    try:
        data = json.loads(text)
    except ValueError:
        raise AudioError("ffprobe 输出无法解析：%s" % _tail(text, 200))
    streams = [item for item in (data.get("streams") or []) if item.get("codec_type") == "audio"]
    if not streams:
        raise AudioError("该文件不包含音频流：%s" % target)
    stream = streams[0]
    fmt = data.get("format") or {}

    duration = _to_float(stream.get("duration") or fmt.get("duration") or 0.0)
    return {
        "duration": duration,
        "sample_rate": _to_int(stream.get("sample_rate")),
        "channels": _to_int(stream.get("channels")),
        "codec_name": str(stream.get("codec_name") or ""),
    }


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
