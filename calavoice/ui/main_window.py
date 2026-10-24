"""主窗口：玻璃背景 + 双工具条 + 三页标签 + 状态栏 + 运行日志。"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QWidget,
)

from calavoice import launcher, paths
from calavoice.tts_engine import TTSEngine
from calavoice.ui.page_profiles import ProfilesPage
from calavoice.ui.page_script import ScriptPage
from calavoice.ui.page_settings import SettingsPage
from calavoice.ui.theme import GlassBackground, app_icon, avatar_pixmap, title_font
from calavoice.ui.widgets import LogView, StatusLight, make_button
from calavoice.ui.worker import (
    CheckWorker,
    TaskWorker,
    friendly_error,
    material_sound_dir,
)

BLOCKING_KEYS = {"sovits_root", "runtime_python", "api_v2", "ffmpeg", "ffprobe"}
STATUS_POLL_INTERVAL = 3000
NOT_CONFIGURED_HINT = "未配置，去设置页选择或自动检测"
# 后台预启动后端所需的最低空闲显存（MiB）；低于它就不预启动，免得把显存挤爆
PRESTART_MIN_VRAM_MB = 1900

CALA_TARGETS = (
    ("calaplayer_exe", "游戏 CalaPlayer", "btn_cala_game", "▶ 打开游戏 CalaPlayer"),
    ("calaplayer_builder_exe", "打包工具", "btn_cala_builder", "🛠 打开打包工具"),
)


class MainWindow(QMainWindow):
    """CalaVoiceStudio 主窗口。"""

    log_signal = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("CalaVoiceStudio · 卡拉语音工作室")
        self.setWindowIcon(app_icon())
        self.resize(1200, 820)
        self.setMinimumSize(1000, 660)

        self._env_items: list = []
        self._check_worker: CheckWorker | None = None
        self._status_worker: TaskWorker | None = None
        self._backend_worker: TaskWorker | None = None
        self._last_progress_log = 0.0
        self._first_show = True

        self.engine = TTSEngine(self.cfg, log=self._engine_log)
        self.log_signal.connect(self.append_log)
        self._glass = GlassBackground(self)

        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()
        self._build_log_dock()
        self._wire_pages()

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_about_to_quit)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_POLL_INTERVAL)
        self._status_timer.timeout.connect(self._poll_backend)
        self._apply_backend_state({"running": False, "healthy": False, "port": self._port()})
        self._refresh_calaplayer_buttons()
        version_text = ""
        try:
            import calavoice as _cv

            caps = getattr(_cv, "capabilities", None)
            enabled = sum(1 for ok in (caps() or {}).values() if ok) if callable(caps) else 0
            version_text = " · core v%s（%d 项能力）" % (getattr(_cv, "__version__", "?"), enabled)
        except Exception:
            version_text = ""
        self.append_log("界面已就绪%s。建议先「自动检测」环境，再创建人声档案。" % version_text)

    # ---------- 玻璃背景 ----------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        self._glass.paint(painter)
        painter.end()
        super().paintEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._glass.schedule()

    # ---------- 构建 ----------

    def _build_toolbar(self) -> None:
        bar = QToolBar("主工具条", self)
        bar.setObjectName("MainToolBar")
        bar.setMovable(False)
        bar.setIconSize(QSize(18, 18))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

        avatar = QLabel(self)
        pixmap = avatar_pixmap(24)
        if not pixmap.isNull():
            avatar.setPixmap(pixmap)
        avatar.setFixedSize(24, 24)
        avatar.setToolTip("CalaVoiceStudio")
        bar.addWidget(avatar)

        title = QLabel("CalaVoiceStudio 卡拉语音工作室", self)
        title.setObjectName("AppTitle")
        title.setFont(title_font(13))
        bar.addWidget(title)
        bar.addSeparator()

        # 两个 CalaPlayer 开关放在第一行（各自独立、各自独立配置路径）
        self.btn_cala_game = make_button("打开游戏 CalaPlayer", icon_name="play")
        self.btn_cala_builder = make_button("打开打包工具", icon_name="computer")
        bar.addWidget(self.btn_cala_game)
        bar.addWidget(self.btn_cala_builder)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        self.btn_start = make_button("启动后端", icon_name="play", primary=True, tooltip="启动 GPT-SoVITS 语音后端")
        self.btn_stop_backend = make_button("停止后端", icon_name="stop", danger=True, tooltip="关闭后端进程")
        bar.addWidget(self.btn_start)
        bar.addWidget(self.btn_stop_backend)

        second = QToolBar("状态与环境检测", self)
        second.setObjectName("StatusToolBar")
        second.setMovable(False)
        second.setIconSize(QSize(18, 18))
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, second)

        self.env_light = StatusLight("环境：未检测", self)
        self.env_light.setMaximumWidth(300)
        self.env_light.setMinimumWidth(110)
        self.backend_light = StatusLight("后端：未启动", self)
        self.backend_light.setMaximumWidth(220)
        self.backend_light.setMinimumWidth(90)
        second.addWidget(self.env_light)
        second.addWidget(self.backend_light)

        spacer2 = QWidget(self)
        spacer2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        second.addWidget(spacer2)

        self.btn_auto_check = make_button(
            "自动检测", icon_name="refresh", tooltip="自动搜索 GPT-SoVITS 与素材目录后检查环境"
        )
        self.btn_manual_check = make_button(
            "手动检测", icon_name="folder_plus", tooltip="只检查现有设置，不搜索磁盘"
        )
        self.btn_material = make_button("打开素材目录", icon_name="folder")
        self.btn_output_dir = make_button("打开输出目录", icon_name="folder")
        self.btn_audition = make_button("打开 AU", icon_name="play")
        second.addWidget(self.btn_auto_check)
        second.addWidget(self.btn_manual_check)
        second.addWidget(self.btn_material)
        second.addWidget(self.btn_output_dir)
        second.addWidget(self.btn_audition)

        self.btn_auto_check.clicked.connect(lambda: self.run_check(True))
        self.btn_manual_check.clicked.connect(lambda: self.run_check(False))
        self.btn_start.clicked.connect(self.start_backend)
        self.btn_stop_backend.clicked.connect(self.stop_backend)
        self.btn_material.clicked.connect(self.open_material_dir)
        self.btn_output_dir.clicked.connect(self.open_output_dir)
        self.btn_audition.clicked.connect(self.open_audition)
        self.btn_cala_game.clicked.connect(lambda: self.launch_calaplayer("calaplayer_exe", "游戏 CalaPlayer"))
        self.btn_cala_builder.clicked.connect(
            lambda: self.launch_calaplayer("calaplayer_builder_exe", "打包工具")
        )

    def _build_tabs(self) -> None:
        self.tabs = QTabWidget(self)
        self.script_page = ScriptPage(self.cfg, self.engine, self)
        self.profiles_page = ProfilesPage(self.cfg, self.engine, self)
        self.settings_page = SettingsPage(self.cfg, self)
        self.tabs.addTab(self.script_page, "台词生成")
        self.tabs.addTab(self.profiles_page, "人声档案")
        self.tabs.addTab(self.settings_page, "设置")
        self.setCentralWidget(self.tabs)
        self.tabs.setCurrentIndex(0)

    def _build_statusbar(self) -> None:
        self.backend_label = QLabel("后端：未启动", self)
        self.last_log_label = QLabel("最近：—", self)
        self.statusBar().addWidget(self.backend_label, 0)
        self.statusBar().addWidget(self.last_log_label, 1)

    def _build_log_dock(self) -> None:
        self.log_dock = QDockWidget("运行日志", self)
        self.log_dock.setObjectName("LogDock")
        self.log_view = LogView(self.log_dock)
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [110], Qt.Orientation.Vertical)

    def _wire_pages(self) -> None:
        self.script_page.log.connect(self.append_log)
        self.script_page.busy_changed.connect(self._on_script_busy)
        self.profiles_page.log.connect(self.append_log)
        self.profiles_page.profiles_changed.connect(self._on_profiles_changed)
        self.settings_page.log.connect(self.append_log)
        self.settings_page.check_requested.connect(self.run_check)
        self.settings_page.settings_saved.connect(self._on_settings_saved)

    # ---------- 日志 ----------

    def _engine_log(self, text: str) -> None:
        """TTSEngine 的日志回调可能来自后台线程，统一转到主线程。"""
        self.log_signal.emit(str(text))

    def append_log(self, text: str) -> None:
        content = str(text or "")
        self.log_view.append_log(content)
        first = content.strip().splitlines()[0] if content.strip() else ""
        if len(first) > 90:
            first = first[:90] + "…"
        self.last_log_label.setText("最近：%s" % (first or "—"))

    # ---------- 环境检测 ----------

    def run_check(self, autodetect: bool = False) -> None:
        if self._check_worker is not None and self._check_worker.isRunning():
            self.append_log("环境检测正在进行中，请稍候。")
            return
        self.btn_auto_check.setEnabled(False)
        self.btn_manual_check.setEnabled(False)
        self.env_light.set_state("idle", "环境：正在检测…")
        self.append_log("开始%s环境检测…" % ("自动" if autodetect else "手动"))
        worker = CheckWorker(self.cfg, autodetect, parent=self)
        worker.progress.connect(self._on_check_progress)
        worker.done.connect(self._on_check_done)
        worker.finished.connect(self._on_check_finished)
        self._check_worker = worker
        worker.start()

    def _on_check_progress(self, text: str) -> None:
        self.env_light.set_state("idle", "环境：%s" % text)
        now = time.monotonic()
        if now - self._last_progress_log > 0.8:
            self._last_progress_log = now
            self.append_log(text)

    def _on_check_done(self, result) -> None:
        data = result if isinstance(result, dict) else {}
        if data.get("error"):
            self.append_log("环境检测出错：%s" % data["error"])
        applied = data.get("applied") or {}
        if applied:
            self.settings_page.reload()
            for key, value in applied.items():
                self.append_log("已自动填入 %s：%s" % (key, value))
        items = list(data.get("items") or [])
        self._env_items = items
        self.settings_page.show_check_items(items)
        self._apply_env_state(items)
        ok_count = len([item for item in items if getattr(item, "ok", False)])
        self.append_log("环境检测完成：%d 项通过，%d 项未通过。" % (ok_count, len(items) - ok_count))

    def _on_check_finished(self) -> None:
        self.btn_auto_check.setEnabled(not self._script_busy)
        self.btn_manual_check.setEnabled(not self._script_busy)

    @property
    def _script_busy(self) -> bool:
        return self.script_page.is_busy()

    def _apply_env_state(self, items) -> None:
        items = list(items or [])
        if not items:
            self.env_light.set_state("idle", "环境：未检测")
            return
        failed = [item for item in items if not getattr(item, "ok", True)]
        if not failed:
            self.env_light.set_state("ok", "环境：就绪（%d 项全部通过）" % len(items))
            return
        blocking = [item for item in failed if getattr(item, "key", "") in BLOCKING_KEYS]
        names = "、".join(getattr(item, "label", "") or getattr(item, "key", "") for item in failed[:3])
        if blocking:
            self.env_light.set_state("bad", "环境：%d 项未通过（%s）" % (len(failed), names))
        else:
            self.env_light.set_state("warn", "环境：可用，%d 项待完善（%s）" % (len(failed), names))

    # ---------- 后端 ----------

    def _port(self) -> int:
        try:
            return int(self.cfg.get("port") or 9880)
        except (TypeError, ValueError):
            return 9880

    def start_backend(self) -> None:
        if self._backend_worker is not None and self._backend_worker.isRunning():
            self.append_log("后端启动任务正在进行中，请稍候。")
            return
        version = str(self.cfg.get("version") or "v2ProPlus")
        device = str(self.cfg.get("device") or "cuda")
        wait_hint = "后端：启动中（首次约 40~60 秒，请稍候）"
        self.backend_light.set_state("warn", wait_hint)
        self.backend_label.setText(wait_hint)
        self.btn_start.setEnabled(False)
        self.append_log(
            "正在启动后端（版本 %s / 设备 %s）：首次启动约需 40~60 秒，界面不会卡死，可继续编辑台词。"
            % (version, device)
        )
        worker = TaskWorker(self.engine.ensure_running, version, device, parent=self)
        worker.done.connect(self._on_backend_started)
        worker.failed.connect(self._on_backend_failed)
        worker.finished.connect(lambda: self._refresh_backend_ui())
        self._backend_worker = worker
        worker.start()

    def _on_backend_started(self, _result) -> None:
        self.append_log("后端已就绪：127.0.0.1:%d" % self._port())

    def _on_backend_failed(self, message: str) -> None:
        self.append_log("后端启动失败：%s" % message)
        self.backend_light.set_state("bad", "后端：启动失败")
        QMessageBox.warning(self, "后端启动失败", message)

    def stop_backend(self) -> None:
        try:
            self.engine.stop()
        except Exception as exc:
            self.append_log("停止后端时出错：%s" % friendly_error(exc))
        self.append_log("已停止后端进程。")
        self._refresh_backend_ui()

    def _poll_backend(self) -> None:
        if self._status_worker is not None and self._status_worker.isRunning():
            return
        # 批量生成期间只查“进程在不在”，不做 HTTP 探活（既不抢推理请求，也不会把“忙”误判成“无响应”）
        skip_health = bool(self._script_busy)
        worker = TaskWorker(lambda: self._probe_backend_state(skip_health), parent=self)
        worker.done.connect(self._apply_backend_state)
        worker.failed.connect(lambda message: self.append_log("后端状态查询失败：%s" % message))
        self._status_worker = worker
        worker.start()

    def _probe_backend_state(self, skip_health: bool = False) -> dict:
        running = False
        healthy = False
        try:
            running = bool(self.engine.is_running())
            if running and not skip_health:
                healthy = bool(self.engine.health())
        except Exception:
            running = False
            healthy = False
        starting = self._backend_worker is not None and self._backend_worker.isRunning()
        return {
            "running": running,
            "healthy": healthy,
            "busy": bool(skip_health),
            "starting": bool(starting),
            "port": self._port(),
        }

    def _apply_backend_state(self, state) -> None:
        data = state if isinstance(state, dict) else {}
        running = bool(data.get("running"))
        healthy = bool(data.get("healthy"))
        port = int(data.get("port") or self._port())
        if healthy:
            text = "后端：就绪 127.0.0.1:%d" % port
            self.backend_light.set_state("ok", text)
            self.backend_label.setText(text)
        elif running and not healthy and data.get("starting"):
            text = "后端：启动中…（约 30 秒，期间可以继续写台词）"
            self.backend_light.set_state("idle", text)
            self.backend_label.setText(text)
        elif running and data.get("busy"):
            text = "后端：就绪（生成中）127.0.0.1:%d" % port
            self.backend_light.set_state("ok", text)
            self.backend_label.setText(text)
        elif running:
            text = "后端：无响应（可能显存不足，正在自动重启…）"
            self.backend_light.set_state("warn", text)
            self.backend_label.setText(text)
        else:
            text = "后端：未启动"
            self.backend_light.set_state("idle", text)
            self.backend_label.setText(text)
        busy = self._backend_worker is not None and self._backend_worker.isRunning()
        self.btn_start.setEnabled(not busy and not self._script_busy)
        self.btn_stop_backend.setEnabled(running)

    def _refresh_backend_ui(self) -> None:
        self._poll_backend()

    # ---------- CalaPlayer / 目录 ----------

    def _calaplayer_path(self, key: str) -> str:
        return str(self.cfg.get(key) or "").strip()

    def _refresh_calaplayer_buttons(self) -> None:
        for key, label, attr, _text in CALA_TARGETS:
            button = getattr(self, attr, None)
            if button is None:
                continue
            exe = self._calaplayer_path(key)
            ready = bool(exe) and os.path.isfile(exe)
            button.setEnabled(ready)
            button.setToolTip("启动%s：%s" % (label, exe) if ready else NOT_CONFIGURED_HINT)
        self._refresh_tool_buttons()

    def _refresh_tool_buttons(self) -> None:
        """刷新「打开输出目录」「打开 AU」两个按钮的状态与提示。"""
        out_dir = ""
        try:
            out_dir = str(getattr(paths, "resolve_output_dir", lambda _cfg: "")(self.cfg) or "")
        except Exception:
            out_dir = ""
        if out_dir:
            self.btn_output_dir.setEnabled(True)
            self.btn_output_dir.setToolTip("打开输出目录：%s" % out_dir)
        else:
            self.btn_output_dir.setEnabled(False)
            self.btn_output_dir.setToolTip("未设置素材目录或输出目录，去设置页选择或自动检测")

        audition = ""
        try:
            audition = str(getattr(paths, "resolve_audition_exe", lambda _cfg: "")(self.cfg) or "")
        except Exception:
            audition = ""
        if audition:
            self.btn_audition.setEnabled(True)
            self.btn_audition.setToolTip("打开 Adobe Audition：%s" % audition)
        else:
            self.btn_audition.setEnabled(False)
            self.btn_audition.setToolTip("未找到 Adobe Audition，去设置页手动选择或自动检测")

    def open_output_dir(self) -> None:
        ensure = getattr(paths, "ensure_output_dir", None)
        try:
            target = str(ensure(self.cfg)) if callable(ensure) else str(material_sound_dir(self.cfg))
        except Exception as exc:
            QMessageBox.warning(self, "无法确定输出目录", friendly_error(exc))
            return
        try:
            launcher.open_folder(target)
        except Exception as exc:
            QMessageBox.warning(self, "无法打开目录", friendly_error(exc))
            return
        self.append_log("已打开输出目录：%s" % target)

    def open_audition(self) -> None:
        resolve = getattr(paths, "resolve_audition_exe", None)
        exe = ""
        try:
            exe = str(resolve(self.cfg) or "") if callable(resolve) else ""
        except Exception:
            exe = ""
        if not exe:
            QMessageBox.warning(
                self, "未找到 Audition", "没有找到 Adobe Audition。\n请到「设置」页手动选择 Adobe Audition.exe，或点「自动检测」。"
            )
            self._refresh_tool_buttons()
            return
        try:
            pid = launcher.launch_exe(exe)
        except Exception as exc:
            QMessageBox.warning(self, "启动失败", friendly_error(exc))
            return
        self.append_log("已启动 Adobe Audition（进程 %s）：%s" % (pid, exe))

    def launch_calaplayer(self, key: str, label: str) -> None:
        exe = self._calaplayer_path(key)
        if not exe:
            QMessageBox.warning(self, "未配置", "%s 还没有配置，请到「设置」页选择或点「自动检测」。" % label)
            return
        if not os.path.isfile(exe):
            QMessageBox.warning(self, "文件不存在", "找不到%s：\n%s" % (label, exe))
            self._refresh_calaplayer_buttons()
            return
        try:
            pid = launcher.launch_exe(exe)
        except Exception as exc:
            QMessageBox.warning(self, "启动失败", friendly_error(exc))
            return
        self.append_log("已启动%s（进程 %s）：%s" % (label, pid, exe))

    def open_material_dir(self) -> None:
        try:
            target = material_sound_dir(self.cfg)
        except Exception as exc:
            QMessageBox.warning(self, "缺少素材目录", friendly_error(exc))
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
            launcher.open_folder(str(target))
        except Exception as exc:
            QMessageBox.warning(self, "无法打开目录", friendly_error(exc))

    def _on_profiles_changed(self) -> None:
        self.script_page.reload_profiles()

    def _on_settings_saved(self) -> None:
        self.script_page.reload_profiles()
        self._refresh_calaplayer_buttons()
        self._poll_backend()

    def _on_script_busy(self, busy: bool) -> None:
        self.btn_auto_check.setEnabled(not busy)
        self.btn_manual_check.setEnabled(not busy)
        self.btn_start.setEnabled(not busy)
        self._status_timer.setInterval(15000 if busy else STATUS_POLL_INTERVAL)
        if busy:
            self.append_log("批量生成进行中：已暂停后端探活（避免与推理抢请求），生成结束后自动恢复。")

    # ---------- 生命周期 ----------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            self._status_timer.start()
            QTimer.singleShot(120, self._startup_check)

    def _startup_check(self) -> None:
        """启动自检：只做存在性检查（毫秒级）。

        仅当关键路径尚未配置（换电脑 / 换位置 / 首启）时才全盘自动探测，
        避免每次启动都扫一遍几十万个目录。
        """
        unconfigured = not (self.cfg.get("sovits_root") and self.cfg.get("material_root"))
        self.run_check(bool(unconfigured))
        QTimer.singleShot(800, self._maybe_prestart_backend)

    # ---------- 后端预启动（把 30 秒冷启动藏到用户写台词的时间里） ----------

    def _maybe_prestart_backend(self) -> None:
        if not bool(self.cfg.get("auto_start_backend", True)):
            return
        if self._script_busy:
            return
        try:
            if self.engine.is_running():
                self.append_log("后端已在运行，跳过后台预启动。")
                self._refresh_backend_ui()
                return
        except Exception:
            pass

        free_mb = self._free_vram_mb()
        if free_mb is not None and free_mb < PRESTART_MIN_VRAM_MB:
            self.append_log(
                "跳过后台预启动：可用显存仅 %d MiB（后端推理至少需要约 %d MiB）。"
                "建议先关闭占显存的程序，再点「启动后端」。" % (free_mb, PRESTART_MIN_VRAM_MB)
            )
            return

        version = str(self.cfg.get("version") or "v2ProPlus")
        device = str(self.cfg.get("device") or "cuda")
        profile = None
        try:
            name = self.script_page.current_profile()
            profile = self.script_page.profile_by_name(name) if name else None
        except Exception:
            profile = None
        if profile is not None:
            version = str(profile.version or version)
            device = str(profile.device or device)
            register = getattr(self.engine, "prewarm_ref", None)
            if callable(register):
                try:
                    register(
                        profile.ref_audio,
                        str(profile.prompt_text or ""),
                        str(profile.prompt_lang or "zh"),
                        str(profile.text_lang or "zh"),
                    )
                except Exception:
                    pass

        if self._backend_worker is not None and self._backend_worker.isRunning():
            return
        self.append_log(
            "后台预启动后端（%s / %s）…约需 30 秒 + 预热 10 秒，期间可以正常编辑台词。"
            % (version, device)
        )
        worker = TaskWorker(lambda: self.engine.ensure_running(version, device), parent=self)
        worker.done.connect(self._on_prestart_done)
        worker.failed.connect(self._on_prestart_failed)
        self._backend_worker = worker
        worker.start()

    def _on_prestart_done(self, _result=None) -> None:
        self.append_log("后端已在后台就绪，可以直接生成。")
        self._refresh_backend_ui()

    def _on_prestart_failed(self, message: str) -> None:
        self.append_log("后台预启动失败（不影响使用，生成时会再试一次）：%s" % message)

    @staticmethod
    def _free_vram_mb():
        """读 nvidia-smi 的空闲显存（MiB）；没有 nvidia-smi 或读不到返回 None。"""
        try:
            import subprocess

            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                return None
            first = (result.stdout or "").strip().splitlines()[0].strip()
            return int(float(first))
        except Exception:
            return None

    def _on_about_to_quit(self) -> None:
        self._status_timer.stop()
        workers = [
            self._check_worker,
            self._status_worker,
            self._backend_worker,
            self.script_page.worker,
            self.script_page.preview,
            self.profiles_page.preview,
            self.profiles_page._discover_worker,
        ]
        for worker in workers:
            try:
                if worker is not None and worker.isRunning():
                    worker.wait(1500)
            except Exception:
                pass
        try:
            self.engine.stop()
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._on_about_to_quit()
        super().closeEvent(event)