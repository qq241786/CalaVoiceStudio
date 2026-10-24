"""GPT-SoVITS 后端进程管理与 HTTP 合成客户端（core 核心模块）。

要点：
- 后端命令行 [runtime_python, api_v2.py, -a, 127.0.0.1, -p, port, -c, yaml]，cwd=sovits_root
- 每次启动写 <data>/runtime/tts_infer_<version>.yaml，custom 段决定版本/设备/初始权重
- 就绪判定：GET /docs 任何 <500 响应即就绪；进程提前退出立刻抛错并附日志尾部
- 换版本必须重启进程，ensure_running 会自动处理
"""

from __future__ import annotations

import atexit
import http.client
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .text_norm import split_for_tts

CREATE_NO_WINDOW = 0x08000000
HOST = "127.0.0.1"
CONNECT_TIMEOUT = 10.0
DOCS_TIMEOUT = 2.0
# 后端加载模型后大致占用（MiB），用于显存不足时给出可执行建议
VRAM_NEEDED_MB = 1900

DEFAULT_BASE_PATHS = {
    "bert_base_path": "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
    "cnhuhbert_base_path": "GPT_SoVITS/pretrained_models/chinese-hubert-base",
}

# 与 GPT_SoVITS/TTS_infer_pack/TTS.py 的 TTS_Config.default_configs 一致（相对路径，无本机信息）。
DEFAULT_VERSION_WEIGHTS = {
    "v1": {
        "t2s_weights_path": "GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
        "vits_weights_path": "GPT_SoVITS/pretrained_models/s2G488k.pth",
    },
    "v2": {
        "t2s_weights_path": "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
        "vits_weights_path": "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth",
    },
    "v3": {
        "t2s_weights_path": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
        "vits_weights_path": "GPT_SoVITS/pretrained_models/s2Gv3.pth",
    },
    "v4": {
        "t2s_weights_path": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
        "vits_weights_path": "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth",
    },
    "v2Pro": {
        "t2s_weights_path": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
        "vits_weights_path": "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth",
    },
    "v2ProPlus": {
        "t2s_weights_path": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
        "vits_weights_path": "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth",
    },
}
VERSION_ORDER = ("v1", "v2", "v3", "v4", "v2Pro", "v2ProPlus")
CUSTOM_KEYS = (
    "bert_base_path",
    "cnhuhbert_base_path",
    "device",
    "is_half",
    "t2s_weights_path",
    "vits_weights_path",
    "version",
)

_SECTION_RE = re.compile(r"^([A-Za-z0-9_.-]+):[ \t]*(?:#.*)?$")
_ITEM_RE = re.compile(r"^[ \t]+([A-Za-z0-9_]+):[ \t]*(.*)$")


class EngineError(RuntimeError):
    """后端启动/合成失败（消息为中文可读文案）。"""


class EngineConnectionError(EngineError):
    """与后端的连接瞬时中断（调用方可安全重试）。"""


class EngineTimeoutError(EngineError):
    """后端连续无响应（超时/连接中断），自愈重启后仍失败。"""


# ---------------------------------------------------------------- yaml 处理


def _parse_sections(text: str):
    """把 yaml 文本按顶层段落切开，返回 (前导行, [(段名, [整段行])])。"""
    preamble: list[str] = []
    sections: list[list] = []
    current: list | None = None
    for line in (text or "").splitlines():
        match = _SECTION_RE.match(line)
        if match and not line[:1].isspace():
            current = [match.group(1), [line]]
            sections.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current[1].append(line)
    return preamble, [(name, lines) for name, lines in sections]


def _unquote(raw: str) -> str:
    value = (raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


def _section_values(lines: list[str]) -> dict:
    values: dict = {}
    for line in lines[1:]:
        match = _ITEM_RE.match(line)
        if not match:
            continue
        raw = match.group(2).strip()
        if not raw or raw.startswith("#"):
            continue
        values[match.group(1)] = _unquote(raw)
    return values


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if text == "":
        return "''"
    if any(character in text for character in ":#") or text[0] in "&*!%@\"[{,'":
        return json.dumps(text, ensure_ascii=False)
    return text


def _default_sections() -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    for version in VERSION_ORDER:
        body = ["%s:" % version]
        merged = dict(DEFAULT_VERSION_WEIGHTS[version])
        merged.update(DEFAULT_BASE_PATHS)
        merged.update({"device": "cpu", "is_half": False, "version": version})
        for key in CUSTOM_KEYS:
            body.append("  %s: %s" % (key, _format_value(merged[key])))
        sections.append((version, body))
    return sections


def build_yaml_text(base_text: str, version: str, device: str, is_half: bool) -> str:
    """复制基础 yaml 的全部段落，仅重写 custom 段。"""
    preamble, sections = _parse_sections(base_text or "")
    names = [name for name, _ in sections]
    if not sections:
        sections = _default_sections()
        names = [name for name, _ in sections]
        preamble = []

    known = set(DEFAULT_VERSION_WEIGHTS) | (set(names) - {"custom"})
    if version not in known:
        raise ValueError("不支持的 GPT-SoVITS 版本：%s（可用：%s）" % (version, "/".join(sorted(known))))

    source: dict = {}
    if version in names:
        source = _section_values(dict(sections)[version])
    fallback = DEFAULT_VERSION_WEIGHTS.get(version, {})
    merged = {
        "bert_base_path": source.get("bert_base_path") or DEFAULT_BASE_PATHS["bert_base_path"],
        "cnhuhbert_base_path": source.get("cnhuhbert_base_path") or DEFAULT_BASE_PATHS["cnhuhbert_base_path"],
        "device": device,
        "is_half": bool(is_half) if str(device).lower() != "cpu" else False,
        "t2s_weights_path": source.get("t2s_weights_path") or fallback.get("t2s_weights_path", ""),
        "vits_weights_path": source.get("vits_weights_path") or fallback.get("vits_weights_path", ""),
        "version": version,
    }
    custom_body = ["custom:"] + ["  %s: %s" % (key, _format_value(merged[key])) for key in CUSTOM_KEYS]
    if "custom" in names:
        rebuilt = [(name, custom_body if name == "custom" else lines) for name, lines in sections]
    else:
        rebuilt = [("custom", custom_body)] + sections

    output: list[str] = list(preamble)
    for _, lines in rebuilt:
        output.extend(lines)
    while output and not output[-1].strip():
        output.pop()
    return "\n".join(output) + "\n"


# ---------------------------------------------------------------- HTTP 细节


class _SplitTimeoutConnection(http.client.HTTPConnection):
    """连接用短超时，读到数据后用长超时（urllib 只有一个 timeout 参数）。"""

    connect_timeout = CONNECT_TIMEOUT

    def connect(self):
        read_timeout = self.timeout
        self.timeout = self.connect_timeout
        try:
            super().connect()
        finally:
            self.timeout = read_timeout
        if self.sock is not None and read_timeout is not None:
            self.sock.settimeout(read_timeout)


class _SplitTimeoutHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SplitTimeoutConnection, req)


_OPENER = urllib.request.build_opener(_SplitTimeoutHandler)


def _error_message(payload: bytes) -> str:
    text = (payload or b"").decode("utf-8", "replace").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text[:300] or "未知错误"
    if isinstance(data, dict):
        message = str(data.get("message") or "").strip()
        detail = str(data.get("Exception") or data.get("detail") or "").strip()
        if message and detail and detail != message:
            return "%s：%s" % (message, detail)
        return message or detail or text[:300]
    return text[:300]


# ---------------------------------------------------------------- 引擎


class TTSEngine:
    """管理 api_v2.py 子进程并提供 /tts 调用。"""

    def __init__(self, cfg, log: "Callable[[str], None] | None" = None):
        self._cfg = cfg
        self._log_sink = log
        self._proc = None
        self._log_handle = None
        self._version = ""
        self._device = ""
        self._yaml_path = ""
        self._lock = threading.RLock()
        self._recent: list[str] = []
        self._io_lock = threading.Lock()
        self._applied_weights: "tuple[str, str] | None" = None
        self._prewarmed = False
        self._stop_requested = False
        self._prewarm_ref: dict = {}
        self._last_ref: dict = {}
        self._gpu_logged = False
        self.log_path = ""

    # ------------------------------------------------------------ 基础属性

    @property
    def host(self) -> str:
        return HOST

    @property
    def port(self) -> int:
        try:
            return int(self._cfg.get("port", 9880) or 9880)
        except (TypeError, ValueError):
            return 9880

    @property
    def version(self) -> str:
        return self._version

    @property
    def device(self) -> str:
        return self._device

    @property
    def yaml_path(self) -> str:
        return self._yaml_path

    @property
    def pid(self) -> int:
        return int(self._proc.pid) if self._proc is not None else 0

    def _log(self, message: str) -> None:
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), message)
        self._recent.append(line)
        del self._recent[:-200]
        if callable(self._log_sink):
            try:
                self._log_sink(message)
            except Exception:
                pass

    def _tail_log(self, count: int = 20) -> str:
        if not self.log_path:
            return "（尚未生成日志文件）"
        try:
            text = Path(self.log_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return "（无法读取日志 %s：%s）" % (self.log_path, exc)
        rows = [row for row in text.splitlines() if row.strip()]
        return "\n".join(rows[-count:]) if rows else "（日志为空）"

    # ------------------------------------------------------------ 进程状态

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def health(self) -> bool:
        url = "http://%s:%d/docs" % (self.host, self.port)
        try:
            with urllib.request.urlopen(url, timeout=DOCS_TIMEOUT) as response:
                return int(getattr(response, "status", 200) or 200) < 500
        except urllib.error.HTTPError as exc:
            return int(getattr(exc, "code", 500) or 500) < 500
        except (urllib.error.URLError, OSError, ValueError):
            return False

    @staticmethod
    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((HOST, port)) == 0

    def _wait_port_free(self, timeout: float) -> bool:
        deadline = time.time() + max(timeout, 0.0)
        while time.time() < deadline:
            if not self._port_in_use(self.port):
                return True
            time.sleep(0.2)
        return not self._port_in_use(self.port)

    def _port_open(self) -> bool:
        return self._port_in_use(self.port)

    def _probe_ready(self) -> bool:
        """TCP 能连上说明 uvicorn 已 listen（api_v2.py 在 uvicorn.run 之前就把管线加载完了），
        再补一次 /docs 确认真的能响应 HTTP。"""
        if not self._port_open():
            return False
        return self.health()

    def _wait_ready(self, timeout: float) -> bool:
        deadline = time.time() + max(timeout, 0.0)
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if self._probe_ready():
                return True
            time.sleep(0.5)
        return self._probe_ready()

    # ------------------------------------------------------------ 路径与 yaml

    def _require_root(self):
        root = str(self._cfg.get("sovits_root", "") or "").strip()
        if not root:
            raise EngineError("未设置 GPT-SoVITS 根目录，请先在『设置』页选择或点『自动检测』")
        root_path = Path(root)
        if not root_path.is_dir():
            raise EngineError("GPT-SoVITS 根目录不存在：%s" % root)
        if not (root_path / "api_v2.py").is_file():
            raise EngineError("该目录不是 GPT-SoVITS 根目录（缺少 api_v2.py）：%s" % root)
        runtime_python = ""
        for candidate in (root_path / "runtime" / "python.exe", root_path / "runtime" / "bin" / "python"):
            if candidate.is_file():
                runtime_python = str(candidate)
                break
        if not runtime_python:
            raise EngineError("缺少内置 Python（runtime\\python.exe）：%s" % root)
        return root_path, runtime_python

    def _write_runtime_yaml(self, root_path: Path, version: str, device: str) -> Path:
        base_yaml = root_path / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        base_text = ""
        if base_yaml.is_file():
            try:
                base_text = base_yaml.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                self._log("读取基础 yaml 失败（将使用内置模板）：%s" % exc)
                base_text = ""
        is_half = bool(self._cfg.get("is_half", True))
        try:
            text = build_yaml_text(base_text, version, device, is_half)
        except ValueError as exc:
            raise EngineError(str(exc))
        target = Path(self._cfg.runtime_dir) / ("tts_infer_%s.yaml" % version)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, target)
        self._yaml_path = str(target)
        self._log("已生成后端配置：%s" % target)
        return target

    def _restore_yaml(self) -> None:
        """后端会通过 save_configs() 把 custom 段回写进 -c 指定的 yaml（实测），
        所以每次停止后都用基础 yaml 重新生成一份干净副本，保证下次启动不被污染。"""
        version, device = self._version, self._device
        if not version or not device:
            return
        try:
            root_path, _runtime_python = self._require_root()
            self._write_runtime_yaml(root_path, version, device)
        except Exception as exc:
            try:
                self._log("刷新后端配置失败（已忽略）：%s" % exc)
            except Exception:
                pass

    # ------------------------------------------------------------ 启动 / 停止

    def ensure_running(self, version: str, device: str, timeout: float = 240.0) -> None:
        version = str(version or self._cfg.get("version") or "v2ProPlus").strip()
        device = str(device or self._cfg.get("device") or "cuda").strip().lower()
        if device not in ("cuda", "cpu"):
            device = "cuda"

        with self._lock:
            self._stop_requested = False
            root_path, runtime_python = self._require_root()

            # 上次崩溃/被强杀可能留下占着端口的后端进程，先自愈清理
            if not self.is_running() and self._port_in_use(self.port):
                self._clear_stale_backend()

            if self.is_running() and self._version == version and self._device == device:
                if self.health():
                    self._log("后端已在运行：http://%s:%d（%s/%s）" % (self.host, self.port, version, device))
                    return
                self._log("后端进程存在但尚未就绪，继续等待…")
                if self._wait_ready(timeout):
                    return
                raise EngineError("后端已启动但 %.0f 秒内未就绪。日志：%s" % (timeout, self.log_path))

            if self._proc is not None or self.is_running():
                self._log("版本/设备变化（%s/%s → %s/%s），重启后端" % (self._version or "未知", self._device or "未知", version, device))
            self.stop()

            yaml_path = self._write_runtime_yaml(root_path, version, device)
            if self._port_in_use(self.port):
                self._clear_stale_backend()

            log_path = Path(self._cfg.logs_dir) / ("api_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
            self.log_path = str(log_path)
            command = [
                runtime_python,
                "api_v2.py",
                "-a",
                self.host,
                "-p",
                str(self.port),
                "-c",
                str(yaml_path),
            ]
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            self._log("正在启动后端（%s/%s）：%s" % (version, device, " ".join(command)))
            try:
                handle = open(log_path, "wb")
            except OSError as exc:
                raise EngineError("无法创建后端日志文件 %s：%s" % (log_path, exc))
            self._log_handle = handle
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(root_path),
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW,
                    env=env,
                )
            except OSError as exc:
                self._close_log_handle()
                raise EngineError("无法启动后端进程：%s" % exc)
            self._proc = process
            self._version = version
            self._device = device
            self._applied_weights = None
            self._prewarmed = False
            self._register_atexit()

            started = time.time()
            while time.time() - started < float(timeout):
                code = process.poll()
                if code is not None:
                    tail = self._tail_log(20)
                    self.stop()
                    raise EngineError(
                        "后端进程提前退出（退出码 %s）。日志：%s\n%s" % (code, self.log_path, tail)
                    )
                if self._stop_requested:
                    self.stop()
                    raise EngineError("已取消后端启动（用户点了停止）")
                if self._probe_ready():
                    self._log("后端就绪，耗时 %.1f 秒：http://%s:%d" % (time.time() - started, self.host, self.port))
                    self._log_gpu_memory()
                    self._prewarm()
                    return
                time.sleep(0.5)

            tail = self._tail_log(20)
            self.stop()
            raise EngineError(
                "后端启动超时（%.0f 秒）。日志：%s\n%s" % (float(timeout), self.log_path, tail)
            )

    def stop(self, wait_timeout: float = 2.0) -> None:
        """停止后端。拿不到引擎锁（例如另一线程正在启动后端）时不会死等：
        置取消标志让启动线程尽快放弃，最多等 wait_timeout 秒，避免把界面线程卡住（AppHang）。"""
        self._stop_requested = True
        acquired = self._lock.acquire(timeout=max(0.0, float(wait_timeout)))
        if not acquired:
            self._log("后端正在启动/切换中，已请求取消，稍后自动收尾")
            return
        try:
            process = self._proc
            self._proc = None
            if process is not None:
                try:
                    if process.poll() is None:
                        self._log("正在停止后端进程（pid=%s）" % process.pid)
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            self._log("后端 5 秒内未退出，强制结束")
                            process.kill()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                pass
                except OSError:
                    pass
            self._close_log_handle()
            self._restore_yaml()
            self._applied_weights = None
            self._version = ""
            self._device = ""
            if process is not None and not self._wait_port_free(3.0):
                self._log("注意：端口 %d 仍被占用" % self.port)
        finally:
            self._stop_requested = False
            self._lock.release()

    def _close_log_handle(self) -> None:
        handle, self._log_handle = self._log_handle, None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError:
                pass

    def _register_atexit(self) -> None:
        if getattr(self, "_atexit_done", False):
            return
        self._atexit_done = True
        try:
            atexit.register(self._atexit_stop)
        except Exception:
            pass

    def _atexit_stop(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.stop()
        return False

    # ------------------------------------------------------------ 自愈与加速

    def _python_processes(self) -> "dict[int, str] | None":
        """列出 python.exe 进程及其命令行；拿不到返回 None。"""
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not result.stdout:
            return None
        text = result.stdout.decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return None
        info: dict = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("ProcessId") or 0)
            except (TypeError, ValueError):
                continue
            if pid:
                info[pid] = str(item.get("CommandLine") or "")
        return info

    @staticmethod
    def _cmdline_uses_port(command_line: str, port: int) -> bool:
        text = str(command_line or "")
        return re.search(r"(^|\s)-p\s*%d(\s|$)" % int(port), text) is not None

    def _pid_listening(self, port: int) -> int:
        """用 netstat 找监听该端口的 pid（CIM 拿不到时兜底）。"""
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 0
        text = result.stdout.decode("utf-8", "replace")
        suffix = ":%d" % int(port)
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 5 or "LISTEN" not in line.upper():
                continue
            if not parts[1].endswith(suffix):
                continue
            try:
                return int(parts[-1])
            except (TypeError, ValueError):
                continue
        return 0

    def _port_owner(self, port: int) -> "tuple[int, str] | None":
        """返回占用端口的 (pid, 命令行)；识别不出返回 None。"""
        info = self._python_processes() or {}
        for pid, command_line in info.items():
            if "api_v2.py" not in str(command_line).lower():
                continue
            if self._cmdline_uses_port(command_line, port):
                return pid, str(command_line)
        pid = self._pid_listening(port)
        if pid:
            return pid, info.get(pid, "")
        return None

    def _is_our_backend(self, command_line: str) -> bool:
        """命令行里带本应用 data\\runtime\\tts_infer_*.yaml 的就是我们自己上次起的。"""
        marker = str(Path(self._cfg.data_dir) / "runtime").replace("\\", "/").lower()
        text = str(command_line or "").replace("\\", "/").lower()
        return "api_v2.py" in text and marker in text

    @staticmethod
    def _kill_pid(pid: int) -> None:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/F", "/T"],
                capture_output=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _clear_stale_backend(self) -> None:
        """端口被占时判断归属：我方残留就结束它，别人的后端/其它程序就报中文错。"""
        if not self._port_in_use(self.port):
            return
        owner = self._port_owner(self.port)
        if owner is None:
            raise EngineError(
                "端口 %d 已被其他程序占用，请在『设置』页改用其他端口" % self.port
            )
        pid, command_line = owner
        lowered = str(command_line).lower()
        if "api_v2.py" not in lowered:
            raise EngineError(
                "端口 %d 已被其他程序占用（pid=%d），请在『设置』页改用其他端口" % (self.port, pid)
            )
        if not self._is_our_backend(command_line):
            raise EngineError(
                "端口 %d 已被另一个 GPT-SoVITS 后端占用（pid=%d），请先关掉那个实例，"
                "或在『设置』页改用其他端口" % (self.port, pid)
            )
        self._log("发现上次残留的后端进程（pid=%d），已结束" % pid)
        self._kill_pid(pid)
        if not self._wait_port_free(10.0):
            raise EngineError(
                "端口 %d 仍被残留进程（pid=%d）占用，请手动结束后重试" % (self.port, pid)
            )

    def _log_gpu_memory(self) -> None:
        """启动后记一行显存；空闲过少时给出中文警告。"""
        if self._gpu_logged:
            return
        self._gpu_logged = True
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"],
                capture_output=True,
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            return
        if result.returncode != 0 or not result.stdout:
            return
        text = result.stdout.decode("utf-8", "replace").strip()
        if not text:
            return
        first = text.splitlines()[0].strip()
        self._log("显存：%s" % first)
        free = 0
        for field in first.split(","):
            number = re.search(r"(\d+)\s*MiB", field)
            if number:
                free = int(number.group(1))
        try:
            threshold = int(self._cfg.get("min_free_vram_mb", 2500) or 2500)
        except (TypeError, ValueError):
            threshold = 2500
        if free and free < threshold:
            self._log(
                "警告：显存仅剩 %d MiB（总 %s），后端推理至少需要约 %d MiB，"
                "合成会显著变慢甚至超时；建议先关闭游戏/浏览器等占显存的程序"
                % (free, first.split(",")[1].strip() if "," in first else "未知", VRAM_NEEDED_MB)
            )

    def prewarm_ref(self, ref_audio_path: str = "", prompt_text: str = "", prompt_lang: str = "", text_lang: str = "") -> None:
        """登记预热用的参考音频；下一次 ensure_running 成功后会自动预热。"""
        if not str(ref_audio_path or "").strip():
            return
        self._prewarm_ref = {
            "ref_audio_path": str(ref_audio_path),
            "prompt_text": str(prompt_text or ""),
            "prompt_lang": str(prompt_lang or "zh"),
            "text_lang": str(text_lang or "zh"),
        }

    def _prewarm(self) -> None:
        """后台跑一次极短合成，把 CUDA kernel / 模型预热掉。"""
        if self._prewarmed:
            return
        reference = self._prewarm_ref or self._last_ref
        if not reference:
            self._log("暂不预热：还没有可用的参考音频（首次合成时会自动预热）")
            return
        self._prewarmed = True

        def worker():
            started = time.time()
            try:
                self.synthesize(
                    text="嗯",
                    text_lang=reference.get("text_lang", "zh"),
                    ref_audio_path=reference.get("ref_audio_path", ""),
                    prompt_text=reference.get("prompt_text", ""),
                    prompt_lang=reference.get("prompt_lang", "zh"),
                    timeout=60.0,
                    warmup=True,
                )
            except Exception as exc:
                self._log("预热失败（已忽略）：%s" % exc)
                return
            self._log("后端已预热（%.1f 秒）" % (time.time() - started))

        threading.Thread(target=worker, name="cala-prewarm", daemon=True).start()

    def _recover(self) -> None:
        """后端无响应后的自愈：重启并复原上次的版本/设备/权重。"""
        version, device = self._version, self._device
        weights = self._applied_weights or ("", "")
        if not version or not device:
            raise EngineTimeoutError(
                "后端已退出，且缺少可恢复的启动参数（版本/设备）。日志：%s" % self.log_path
            )
        try:
            self.stop()
            self.ensure_running(version, device, timeout=240.0)
        except EngineError as exc:
            raise EngineTimeoutError("自动重启后端失败：%s（日志：%s）" % (exc, self.log_path))
        if any(weights):
            try:
                self.apply_weights(gpt_weights=weights[0], sovits_weights=weights[1])
            except EngineError as exc:
                raise EngineTimeoutError(
                    "后端已重启但重新应用权重失败：%s（日志：%s）" % (exc, self.log_path)
                )

    def _with_recovery(self, label: str, call):
        """执行请求；连接中断/超时就重启后端并重试一次，仍失败抛 EngineTimeoutError。"""
        try:
            return call()
        except EngineConnectionError as exc:
            self._log("%s无响应（%s），正在重启后端…" % (label, exc))
            self._recover()
            self._log("已恢复并重试%s…" % label)
            try:
                return call()
            except EngineConnectionError as exc2:
                raise EngineTimeoutError(
                    "后端连续无响应：可能是显存不足或被其他程序占用。"
                    "已自动重启后端一次仍失败，请关闭占显存的程序后重试。（%s；日志：%s）"
                    % (exc2, self.log_path)
                )

    def _adaptive_timeout(self, text: str) -> float:
        return float(min(180.0, max(45.0, 20.0 + 4.0 * len(str(text or "")))))

    # ------------------------------------------------------------ HTTP 调用

    def _api(self, method: str, path: str, *, body=None, params: dict | None = None, timeout: float = 30.0, retry: int = 0):
        """retry>0 时对"连接瞬时中断"重试（只用于幂等调用，如切换权重）。"""
        attempts = max(0, int(retry)) + 1
        for index in range(attempts):
            try:
                return self._api_once(method, path, body=body, params=params, timeout=timeout)
            except EngineConnectionError as exc:
                if index + 1 >= attempts:
                    raise
                self._log("连接后端失败（%s），0.5 秒后重试（第 %d 次）" % (exc, index + 1))
                time.sleep(0.5)
        raise EngineError("与后端通信失败：重试次数已用尽")

    def _api_once(self, method: str, path: str, *, body=None, params: dict | None = None, timeout: float = 30.0):
        url = "http://%s:%d%s" % (self.host, self.port, path)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = None
        headers = {"Accept": "application/json, audio/wav,*/*"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _OPENER.open(request, timeout=float(timeout)) as response:
                headers = getattr(response, "headers", None)
                content_type = str(headers.get("Content-Type", "") if headers else "")
                return int(getattr(response, "status", 200) or 200), response.read(), content_type
        except urllib.error.HTTPError as exc:
            payload = b""
            try:
                payload = exc.read() or b""
            except Exception:
                payload = b""
            headers = getattr(exc, "headers", None)
            content_type = str(headers.get("Content-Type", "") if headers else "")
            return int(exc.code), payload, content_type
        except socket.timeout:
            raise EngineConnectionError(
                "后端响应超时（%.0f 秒），任务可能过大，可调小分段或加大超时" % float(timeout)
            )
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise EngineConnectionError("无法连接后端 http://%s:%d：%s" % (self.host, self.port, reason))
        except ConnectionError as exc:
            raise EngineConnectionError("后端连接中断：%s" % exc)
        except OSError as exc:
            raise EngineConnectionError("与后端通信失败：%s" % exc)

    def apply_weights(self, gpt_weights: str = "", sovits_weights: str = "") -> None:
        """必须先 SoVITS 再 GPT：后端是从 sovits 权重推断当前 version 的，顺序反了版本会错。
        两个都为空时直接返回（不报错）。"""
        gpt_weights = str(gpt_weights or "")
        sovits_weights = str(sovits_weights or "")
        if not gpt_weights and not sovits_weights:
            if self._applied_weights not in (None, ("", "")):
                self._log(
                    "档案未指定权重，后端仍保留上次权重：%s；如需恢复底模请重启后端"
                    % (self._applied_weights,)
                )
            return
        if not self.is_running():
            raise EngineError("后端未运行，无法切换权重，请先启动后端")
        key = (gpt_weights, sovits_weights)
        with self._io_lock:
            if self._weights_already_applied(key):
                self._log(
                    "权重未变化，跳过切换：gpt=%s / sovits=%s"
                    % (gpt_weights or "底模", sovits_weights or "底模")
                )
                return
            self._with_recovery(
                "切换权重",
                lambda: self._apply_weights_once(gpt_weights, sovits_weights),
            )
            self._applied_weights = key

    def _apply_weights_once(self, gpt_weights: str, sovits_weights: str) -> None:
        """一次完整的热切换：先 SoVITS（版本由它推断）再 GPT。"""
        if sovits_weights:
            code, payload, _content_type = self._api(
                "GET", "/set_sovits_weights", params={"weights_path": sovits_weights}, timeout=180.0, retry=1
            )
            if code >= 400:
                raise EngineError("切换 SoVITS 权重失败：%s" % _error_message(payload))
            self._log("已切换 SoVITS 权重：%s" % sovits_weights)
            self._adopt_version_from_response(payload)
        if gpt_weights:
            code, payload, _content_type = self._api(
                "GET", "/set_gpt_weights", params={"weights_path": gpt_weights}, timeout=180.0, retry=1
            )
            if code >= 400:
                raise EngineError("切换 GPT 权重失败：%s" % _error_message(payload))
            self._log("已切换 GPT 权重：%s" % gpt_weights)

    def _weights_already_applied(self, key: tuple) -> bool:
        """批量生成时每行都会调 apply_weights；命中缓存就不必重复两次热切换（0.6~2.1 秒/次）。
        刚启动的进程等于该版本底模，所以 None 视同 ("", "")。"""
        applied = self._applied_weights
        if applied is None:
            applied = ("", "")
        return key == applied

    def _adopt_version_from_response(self, payload: bytes) -> None:
        """后端目前只回 {"message":"success"}；能拿到 version 就采纳，拿不到就保留原值。"""
        try:
            data = json.loads((payload or b"").decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(data, dict):
            return
        version = data.get("version")
        configs = data.get("configs")
        if not version and isinstance(configs, dict):
            version = configs.get("version")
        if isinstance(version, str) and version.strip() and version.strip() != self._version:
            self._version = version.strip()
            self._log("后端当前版本更新为：%s" % self._version)

    def synthesize(
        self,
        *,
        text: str,
        text_lang: str,
        ref_audio_path: str,
        prompt_text: str,
        prompt_lang: str,
        speed_factor: float = 1.0,
        top_k: int = 15,
        top_p: float = 1.0,
        temperature: float = 1.0,
        sample_steps: int = 32,
        timeout: float | None = None,
        fragment_interval: float | None = None,
        warmup: bool = False,
    ) -> bytes:
        if not str(text or "").strip():
            raise EngineError("台词为空，无法合成")
        reference = str(ref_audio_path or "").strip()
        if not reference:
            raise EngineError("未指定参考音频，请先在人声档案里选择参考音频")
        if not Path(reference).is_file():
            raise EngineError("参考音频不存在：%s" % reference)
        if not self.is_running():
            raise EngineError("后端未运行，请先启动后端")

        # 记住这份档案，供后台预热 / 自愈重启后复用
        warmup_source = {
            "ref_audio_path": reference,
            "prompt_text": str(prompt_text or ""),
            "prompt_lang": str(prompt_lang or "zh"),
            "text_lang": str(text_lang or "zh"),
        }
        if not warmup:
            self._last_ref = warmup_source
            if not self._prewarmed:
                self._prewarmed = True
                self._warmup_inline(warmup_source)

        effective_timeout = float(timeout) if timeout is not None else self._adaptive_timeout(text)
        if fragment_interval is None:
            try:
                fragment_interval = float(self._cfg.get("fragment_interval", 0.1))
            except (TypeError, ValueError):
                fragment_interval = 0.1

        payload_body = {
            "text": str(text),
            "text_lang": str(text_lang or "zh"),
            "ref_audio_path": reference,
            "prompt_text": str(prompt_text or ""),
            "prompt_lang": str(prompt_lang or "zh"),
            "text_split_method": "cut5",
            "media_type": "wav",
            "streaming_mode": False,
            "speed_factor": float(speed_factor),
            "top_k": int(top_k),
            "top_p": float(top_p),
            "temperature": float(temperature),
            "sample_steps": int(sample_steps),
            "parallel_infer": True,
            "fragment_interval": float(fragment_interval),
            "repetition_penalty": 1.35,
            "batch_size": 1,
            "split_bucket": True,
            "seed": -1,
        }
        return self._with_recovery("合成", lambda: self._post_tts(payload_body, effective_timeout))

    def _post_tts(self, body: dict, timeout: float) -> bytes:
        """服务端共享 prompt_cache，合成必须串行；错误响应是 JSON 而不是 wav，先看 Content-Type。"""
        with self._io_lock:
            code, payload, content_type = self._api("POST", "/tts", body=body, timeout=timeout)
        lower_type = (content_type or "").lower()
        if code >= 400 or "json" in lower_type:
            raise EngineError("合成失败：%s" % _error_message(payload))
        if not payload:
            raise EngineError("合成失败：后端返回空音频")
        if payload[:4] != b"RIFF":
            raise EngineError("合成失败：后端返回的不是 wav 数据（前 16 字节 %r）" % payload[:16])
        return bytes(payload)

    def _warmup_inline(self, reference: dict) -> None:
        """首次合成前先跑一句"嗯"把 CUDA kernel 预热掉；失败只记日志。"""
        started = time.time()
        try:
            self.synthesize(
                text="嗯",
                text_lang=reference.get("text_lang", "zh"),
                ref_audio_path=reference.get("ref_audio_path", ""),
                prompt_text=reference.get("prompt_text", ""),
                prompt_lang=reference.get("prompt_lang", "zh"),
                timeout=60.0,
                warmup=True,
            )
        except Exception as exc:
            self._log("首次预热失败（已忽略）：%s" % exc)
            return
        self._log("后端已预热（%.1f 秒）" % (time.time() - started))

    def synthesize_long(
        self,
        *,
        text: str,
        text_lang: str,
        ref_audio_path: str,
        prompt_text: str,
        prompt_lang: str,
        speed_factor: float = 1.0,
        top_k: int = 15,
        top_p: float = 1.0,
        temperature: float = 1.0,
        sample_steps: int = 32,
        timeout: float | None = None,
        fragment_interval: float | None = None,
        max_chars: int | None = None,
    ) -> bytes:
        """长文本自动分段合成再拼接成一个 wav 字节串（每段各自超时与自愈）。"""
        if max_chars is None:
            try:
                max_chars = int(self._cfg.get("max_chars_per_request", 48) or 48)
            except (TypeError, ValueError):
                max_chars = 48
        segments = split_for_tts(text, int(max_chars))
        common = dict(
            text_lang=text_lang,
            ref_audio_path=ref_audio_path,
            prompt_text=prompt_text,
            prompt_lang=prompt_lang,
            speed_factor=speed_factor,
            top_k=top_k,
            top_p=top_p,
            temperature=temperature,
            sample_steps=sample_steps,
            timeout=timeout,
            fragment_interval=fragment_interval,
        )
        if len(segments) <= 1:
            return self.synthesize(text=segments[0] if segments else str(text or ""), **common)

        from .audio import AudioError, merge_wavs
        from .paths import resolve_ffmpeg

        ffmpeg = resolve_ffmpeg(self._cfg)
        temp_files: list[str] = []
        merged_path = ""
        try:
            for index, segment in enumerate(segments, 1):
                self._log("长文本分段合成 %d/%d（%d 字）" % (index, len(segments), len(segment)))
                audio = self.synthesize(text=segment, **common)
                handle, temp_path = tempfile.mkstemp(prefix="cala_seg_%02d_" % index, suffix=".wav")
                with os.fdopen(handle, "wb") as fh:
                    fh.write(audio)
                temp_files.append(temp_path)
            handle, merged_path = tempfile.mkstemp(prefix="cala_merged_", suffix=".wav")
            os.close(handle)
            try:
                merge_wavs(temp_files, merged_path, ffmpeg)
            except AudioError as exc:
                raise EngineError("长文本分段合成后的合并失败：%s" % exc)
            return Path(merged_path).read_bytes()
        finally:
            for item in temp_files:
                try:
                    os.unlink(item)
                except OSError:
                    pass
            if merged_path:
                try:
                    os.unlink(merged_path)
                except OSError:
                    pass
