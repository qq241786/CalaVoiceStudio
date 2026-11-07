"""设置页：路径（含双 CalaPlayer）、默认参数与环境检测。"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QGridLayout,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from calavoice import launcher, paths
from calavoice.ui.widgets import (
    DEVICE_ITEMS,
    LANG_ITEMS,
    VERSION_ITEMS,
    PathRow,
    fill_combo,
    make_button,
    select_combo,
    setup_table,
)
from calavoice.ui.worker import TaskWorker, friendly_error


class SettingsPage(QWidget):
    """路径与默认参数的配置页，同时展示环境检测结果。"""

    log = Signal(str)
    check_requested = Signal(bool)
    settings_saved = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._worker: TaskWorker | None = None
        self._build_ui()
        self.reload()

    # ---------- 界面 ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(5)

        path_box = QGroupBox("路径", self)
        path_form = QFormLayout(path_box)
        path_form.setContentsMargins(6, 2, 6, 2)
        path_form.setHorizontalSpacing(10)
        path_form.setVerticalSpacing(3)

        self.sovits_row = PathRow("dir", placeholder="含 api_v2.py 与 runtime\\python.exe 的目录", parent=path_box)
        self.btn_detect_sovits = make_button("自动检测", icon_name="refresh")
        path_form.addRow("GPT-SoVITS 目录", _with_button(self.sovits_row, self.btn_detect_sovits, path_box))

        self.material_row = PathRow("dir", placeholder="CalaPlayer 素材根目录（含 bg 与 Sound）", parent=path_box)
        self.btn_detect_material = make_button("自动检测", icon_name="refresh")
        path_form.addRow("素材目录", _with_button(self.material_row, self.btn_detect_material, path_box))

        self.calaplayer_row = PathRow(
            "file",
            file_filter="可执行文件 (*.exe);;所有文件 (*.*)",
            placeholder="游戏本体的 CalaPlayer.exe（顶栏「打开游戏 CalaPlayer」用）",
            parent=path_box,
        )
        self.btn_detect_game = make_button("自动检测", icon_name="refresh")
        path_form.addRow(
            "游戏 CalaPlayer.exe", _with_button(self.calaplayer_row, self.btn_detect_game, path_box)
        )

        self.builder_row = PathRow(
            "file",
            file_filter="可执行文件 (*.exe);;所有文件 (*.*)",
            placeholder="打包工具 CalaPlayerSrcmBuilderGUI.exe（顶栏「打开打包工具」用）",
            parent=path_box,
        )
        self.btn_detect_builder = make_button("自动检测", icon_name="refresh")
        path_form.addRow(
            "打包工具（SrcmBuilderGUI.exe）", _with_button(self.builder_row, self.btn_detect_builder, path_box)
        )

        self.calabiyau_row = PathRow(
            "file",
            file_filter="可执行文件 (*.exe);;所有文件 (*.*)",
            placeholder="CalabiYauVoice_GUI.exe（或其所在文件夹；顶栏「打开 CalabiYau」用）",
            parent=path_box,
        )
        self.btn_detect_calabiyau = make_button("自动检测", icon_name="refresh")
        path_form.addRow(
            "CalabiYau 语音工具",
            _with_button(self.calabiyau_row, self.btn_detect_calabiyau, path_box),
        )

        self.audition_row = PathRow(
            "file",
            file_filter="可执行文件 (*.exe);;所有文件 (*.*)",
            placeholder="Adobe Audition（留空则自动检测；顶栏「打开 AU」用）",
            parent=path_box,
        )
        self.btn_detect_audition = make_button("自动检测", icon_name="refresh")
        path_form.addRow("AU 程序（Audition）", _with_button(self.audition_row, self.btn_detect_audition, path_box))

        self.output_row = PathRow(
            "dir",
            placeholder="语音输出目录（留空则用 <素材根>\\Sound）",
            parent=path_box,
        )
        self.btn_detect_output = make_button("自动检测", icon_name="refresh")
        path_form.addRow("输出目录", _with_button(self.output_row, self.btn_detect_output, path_box))
        root.addWidget(path_box)

        param_box = QGroupBox("默认合成参数", self)
        param_grid = QGridLayout(param_box)
        param_grid.setContentsMargins(6, 2, 6, 2)
        param_grid.setHorizontalSpacing(10)
        param_grid.setVerticalSpacing(3)
        self.port = QSpinBox(param_box)
        self.port.setRange(1024, 65535)
        self.port.setValue(9880)
        self.version = QComboBox(param_box)
        fill_combo(self.version, VERSION_ITEMS)
        self.device = QComboBox(param_box)
        fill_combo(self.device, DEVICE_ITEMS)
        self.text_lang = QComboBox(param_box)
        fill_combo(self.text_lang, LANG_ITEMS[1:])
        self.max_chars = QSpinBox(param_box)
        self.max_chars.setRange(20, 300)
        self.max_chars.setValue(48)
        self.max_chars.setToolTip("单行台词超过这个字数会自动分段合成再拼接，避免单条过慢或卡死")
        self.fragment_interval = QDoubleSpinBox(param_box)
        self.fragment_interval.setRange(0.0, 1.0)
        self.fragment_interval.setSingleStep(0.05)
        self.fragment_interval.setDecimals(2)
        self.fragment_interval.setValue(0.10)
        self.fragment_interval.setToolTip("分句之间的静音时长（秒），越小越紧凑、总时长越短")
        param_grid.addWidget(QLabel("后端端口", param_box), 0, 0)
        param_grid.addWidget(self.port, 0, 1)
        param_grid.addWidget(QLabel("默认版本", param_box), 0, 2)
        param_grid.addWidget(self.version, 0, 3)
        param_grid.addWidget(QLabel("默认设备", param_box), 1, 0)
        param_grid.addWidget(self.device, 1, 1)
        param_grid.addWidget(QLabel("默认语言", param_box), 1, 2)
        param_grid.addWidget(self.text_lang, 1, 3)
        param_grid.addWidget(QLabel("单行最大字数", param_box), 2, 0)
        param_grid.addWidget(self.max_chars, 2, 1)
        param_grid.addWidget(QLabel("段间静音(秒)", param_box), 2, 2)
        param_grid.addWidget(self.fragment_interval, 2, 3)
        param_grid.setColumnStretch(1, 1)
        param_grid.setColumnStretch(3, 1)
        root.addWidget(param_box)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_save = make_button("保存设置", icon_name="save", primary=True)
        self.btn_make_root = make_button(
            "新建素材目录",
            icon_name="folder_plus",
            tooltip="选一个父目录（例如游戏目录下的 Windows），自动新建 CalaVoiceMaterials 并在其中建 bg / BGM / Sound / Ambient",
        )
        self.btn_make_tree = make_button(
            "补全素材结构", icon_name="folder_plus", tooltip="在当前素材根里补齐 bg / BGM / Sound / Ambient"
        )
        self.btn_open_sovits = make_button("打开 GPT-SoVITS 目录", icon_name="folder")
        self.btn_open_calaplayer = make_button("打开 CalaPlayer 所在目录", icon_name="computer")
        self.btn_detect_tools = make_button(
            "一键检测外部工具",
            icon_name="checklist",
            tooltip="一次把 游戏 CalaPlayer / 打包工具 / CalabiYau / AU 全部自动找出来并保存，不用逐项点自动检测",
        )
        buttons.addWidget(self.btn_save)
        buttons.addWidget(self.btn_make_root)
        buttons.addWidget(self.btn_make_tree)
        buttons.addWidget(self.btn_detect_tools)
        buttons.addWidget(self.btn_open_sovits)
        buttons.addWidget(self.btn_open_calaplayer)
        buttons.addStretch(1)
        root.addLayout(buttons)

        check_box = QGroupBox("环境检测", self)
        check_layout = QVBoxLayout(check_box)
        check_layout.setSpacing(6)
        check_bar = QHBoxLayout()
        self.btn_check = make_button("立即检测", icon_name="refresh")
        self.btn_auto_check = make_button("自动检测并填入路径", icon_name="checklist")
        check_bar.addWidget(self.btn_check)
        check_bar.addWidget(self.btn_auto_check)
        check_bar.addStretch(1)
        self.check_hint = QLabel("尚未检测。点「立即检测」检查运行环境。", check_box)
        self.check_hint.setObjectName("HintLabel")
        check_bar.addWidget(self.check_hint)
        check_layout.addLayout(check_bar)

        self.check_table = QTableWidget(0, 4, check_box)
        self.check_table.setHorizontalHeaderLabels(["状态", "项目", "详情", "建议"])
        setup_table(self.check_table, {0: 66, 1: 190, 2: 440, 3: 300}, stretch_column=2)
        self.check_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.check_table.setMinimumHeight(140)
        self.check_table.setMaximumHeight(160)
        check_layout.addWidget(self.check_table, 1)
        root.addWidget(check_box, 1)

        self.btn_save.clicked.connect(self.save_settings)
        self.btn_make_root.clicked.connect(self.create_material_root)
        self.btn_make_tree.clicked.connect(self.create_material_tree)
        self.btn_open_sovits.clicked.connect(lambda: self._open_dir(self.sovits_row.text(), "GPT-SoVITS 目录"))
        self.btn_open_calaplayer.clicked.connect(
            lambda: self._open_dir(
                str(Path(self.calaplayer_row.text()).parent) if self.calaplayer_row.text() else "",
                "CalaPlayer 所在目录",
            )
        )
        self.btn_detect_sovits.clicked.connect(self.detect_sovits)
        self.btn_detect_material.clicked.connect(self.detect_material)
        self.btn_detect_game.clicked.connect(self.detect_game)
        self.btn_detect_builder.clicked.connect(self.detect_builder)
        self.btn_detect_calabiyau.clicked.connect(self.detect_calabiyau)
        self.btn_detect_audition.clicked.connect(self.detect_audition)
        self.btn_detect_output.clicked.connect(self.detect_output_dir)
        self.btn_detect_tools.clicked.connect(self.detect_all_tools)
        self.btn_check.clicked.connect(lambda: self.check_requested.emit(False))
        self.btn_auto_check.clicked.connect(lambda: self.check_requested.emit(True))

    # ---------- 数据 ----------

    def reload(self) -> None:
        self.sovits_row.set_text(str(self.cfg.get("sovits_root") or ""))
        self.material_row.set_text(str(self.cfg.get("material_root") or ""))
        self.calaplayer_row.set_text(str(self.cfg.get("calaplayer_exe") or ""))
        self.builder_row.set_text(str(self.cfg.get("calaplayer_builder_exe") or ""))
        self.calabiyau_row.set_text(str(self.cfg.get("calabiyau_exe") or ""))
        self.audition_row.set_text(str(self.cfg.get("audition_exe") or ""))
        self.output_row.set_text(str(self.cfg.get("output_dir") or ""))
        try:
            self.port.setValue(int(self.cfg.get("port") or 9880))
        except (TypeError, ValueError):
            self.port.setValue(9880)
        select_combo(self.version, str(self.cfg.get("version") or "v2ProPlus"))
        select_combo(self.device, str(self.cfg.get("device") or "cuda"))
        select_combo(self.text_lang, str(self.cfg.get("text_lang") or "zh"))
        try:
            self.max_chars.setValue(int(self.cfg.get("max_chars_per_request") or 48))
        except (TypeError, ValueError):
            self.max_chars.setValue(48)
        try:
            self.fragment_interval.setValue(float(self.cfg.get("fragment_interval") or 0.1))
        except (TypeError, ValueError):
            self.fragment_interval.setValue(0.1)

    def save_settings(self) -> None:
        try:
            self.cfg.set("sovits_root", self.sovits_row.text())
            self.cfg.set("material_root", self.material_row.text())
            self.cfg.set("calaplayer_exe", self.calaplayer_row.text())
            self.cfg.set("calaplayer_builder_exe", self.builder_row.text())
            self.cfg.set("calabiyau_exe", self.calabiyau_row.text())
            self.cfg.set("audition_exe", self.audition_row.text())
            self.cfg.set("output_dir", self.output_row.text())
            self.cfg.set("port", int(self.port.value()))
            self.cfg.set("version", str(self.version.currentData() or "v2ProPlus"))
            self.cfg.set("device", str(self.device.currentData() or "cuda"))
            self.cfg.set("text_lang", str(self.text_lang.currentData() or "zh"))
            self.cfg.set("max_chars_per_request", int(self.max_chars.value()))
            self.cfg.set("fragment_interval", float(self.fragment_interval.value()))
            self.cfg.save()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", friendly_error(exc))
            return
        self.log.emit("设置已保存。")
        self.settings_saved.emit()

    def create_material_root(self) -> None:
        """先选父目录，自动新建 <父目录>/CalaVoiceMaterials 再建四个子目录。"""
        start = self.material_row.text() or str(Path(self.cfg.get("sovits_root") or "").parent or "")
        parent = QFileDialog.getExistingDirectory(self, "选择父目录（素材文件夹将建在它下面）", start)
        if not parent:
            return
        try:
            root, created = paths.create_material_root(parent)
        except Exception as exc:
            QMessageBox.warning(self, "创建失败", friendly_error(exc))
            return
        self.material_row.set_text(root)
        self.cfg.set("material_root", root)
        self.cfg.set("sound_subdir", "Sound")
        self.cfg.save()
        detail = "、".join(created) if created else "（四个子目录已存在）"
        message = "已创建素材根：\n%s\n本次新建：%s" % (root, detail)
        self.log.emit(message.replace("\n", " "))
        self.settings_saved.emit()
        QMessageBox.information(self, "完成", message)

    def create_material_tree(self) -> None:
        root = self.material_row.text()
        if not root:
            QMessageBox.warning(self, "缺少素材目录", "请先选择 CalaPlayer 素材根目录。")
            return
        try:
            created = paths.ensure_material_tree(root)
        except Exception as exc:
            QMessageBox.warning(self, "创建失败", friendly_error(exc))
            return
        created = list(created or [])
        if created:
            message = "已创建目录：%s" % "、".join(created)
        else:
            message = "素材结构已完整（bg / BGM / Sound / Ambient 都存在），无需创建。"
        self.log.emit(message)
        QMessageBox.information(self, "完成", message)

    def _open_dir(self, path: str, label: str) -> None:
        target = str(path or "").strip()
        if not target:
            QMessageBox.warning(self, "路径为空", "请先设置%s。" % label)
            return
        if not os.path.isdir(target):
            QMessageBox.warning(self, "目录不存在", "找不到目录：\n%s" % target)
            return
        try:
            launcher.open_folder(target)
        except Exception as exc:
            QMessageBox.warning(self, "无法打开目录", friendly_error(exc))

    # ---------- 检测 ----------

    def show_check_items(self, items) -> None:
        items = list(items or [])
        self.check_table.setRowCount(len(items))
        for row_index, item in enumerate(items):
            ok = bool(getattr(item, "ok", False))
            ok_item = QTableWidgetItem("通过" if ok else "未通过")
            ok_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ok_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            ok_item.setForeground(Qt.GlobalColor.green if ok else Qt.GlobalColor.red)
            self.check_table.setItem(row_index, 0, ok_item)
            for column, value in (
                (1, getattr(item, "label", "") or getattr(item, "key", "")),
                (2, getattr(item, "detail", "")),
                (3, getattr(item, "hint", "")),
            ):
                cell = QTableWidgetItem(str(value or ""))
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                cell.setToolTip(str(value or ""))
                self.check_table.setItem(row_index, column, cell)
            self.check_table.resizeRowToContents(row_index)

        failed = [item for item in items if not getattr(item, "ok", True)]
        if not items:
            self.check_hint.setText("尚未检测。")
        elif not failed:
            self.check_hint.setText("全部 %d 项通过。" % len(items))
        else:
            self.check_hint.setText("%d / %d 项未通过。" % (len(failed), len(items)))

    def _roots(self) -> list:
        roots = self.cfg.get("search_roots") or []
        return [str(root) for root in roots if str(root).strip()]

    def _run_detect(self, field: str, work, empty_message: str, auto_first: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.log.emit("上一次检测还没结束，请稍候。")
            return
        self._worker = TaskWorker(work, pass_progress=True, parent=self)
        self._worker.progress.connect(self.log.emit)
        self._worker.done.connect(
            lambda result: self._on_detect_done(field, result, empty_message, auto_first)
        )
        self._worker.failed.connect(self._on_detect_failed)
        self.log.emit("正在自动检测，请稍候…")
        self._worker.start()

    def _on_detect_failed(self, message: str) -> None:
        self.log.emit("自动检测失败：%s" % message)
        QMessageBox.warning(self, "检测失败", message)

    def _on_detect_done(self, field: str, result, empty_message: str, auto_first: bool = False) -> None:
        values = [str(value) for value in (result or []) if str(value).strip()]
        if not values:
            self.log.emit(empty_message)
            QMessageBox.information(self, "未找到", empty_message)
            return
        chosen = values[0]
        if auto_first:
            self._persist_detected(field, chosen)
            self.log.emit("已按优先级选中并保存：%s" % chosen)
            for position, value in enumerate(values[:5], start=1):
                self.log.emit("  候选%d：%s" % (position, value))
            self.check_hint.setText("已选中并保存 %s" % os.path.basename(chosen))
            return
        if len(values) > 1:
            chosen, accepted = QInputDialog.getItem(self, "请选择", "找到多个候选，请选择一个：", values, 0, False)
            if not accepted:
                return
        self._persist_detected(field, chosen)
        self.log.emit("已填入并保存候选路径：%s" % chosen)

    def _set_field(self, field: str, value: str) -> None:
        if field == "sovits_root":
            self.sovits_row.set_text(value)
        elif field == "material_root":
            self.material_row.set_text(value)
        elif field == "calaplayer_exe":
            self.calaplayer_row.set_text(value)
        elif field == "calaplayer_builder_exe":
            self.builder_row.set_text(value)
        elif field == "calabiyau_exe":
            self.calabiyau_row.set_text(value)
        elif field == "audition_exe":
            self.audition_row.set_text(value)
        elif field == "output_dir":
            self.output_row.set_text(value)

    def _field_value(self, key: str) -> str:
        row = {
            "sovits_root": self.sovits_row,
            "material_root": self.material_row,
            "calaplayer_exe": self.calaplayer_row,
            "calaplayer_builder_exe": self.builder_row,
            "calabiyau_exe": self.calabiyau_row,
            "audition_exe": self.audition_row,
            "output_dir": self.output_row,
        }.get(key)
        return row.text() if row is not None else ""

    @staticmethod
    def _field_valid(key: str, value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        target = Path(text)
        try:
            if key in ("calaplayer_exe", "calaplayer_builder_exe", "audition_exe"):
                return target.is_file()
            return target.is_file() or target.is_dir()
        except OSError:
            return False

    def _persist_detected(self, field: str, value: str) -> None:
        """检测/选择成功后立刻写进配置与缓存，下次启动不用再点检测。"""
        self._set_field(field, value)
        try:
            self.cfg.set(field, value)
            paths.remember_detected(self.cfg, field, value)
        except Exception as exc:
            self.log.emit("保存路径失败：%s" % friendly_error(exc))
            return
        self.settings_saved.emit()

    def detect_sovits(self) -> None:
        roots = self._roots()
        self._run_detect(
            "sovits_root",
            lambda progress: paths.find_sovits_roots(roots, progress=progress),
            "没有找到 GPT-SoVITS 目录，请手动选择（需含 api_v2.py 与 runtime\\python.exe）。",
        )

    def detect_material(self) -> None:
        roots = self._roots()
        self._run_detect(
            "material_root",
            lambda progress: paths.find_material_roots(roots, progress=progress),
            "没有找到素材目录，请手动选择（需同时含 bg 与 Sound 子目录）。",
        )

    def detect_game(self) -> None:
        self._detect_calaplayer("calaplayer_exe", "game", "没有找到游戏 CalaPlayer.exe，请手动选择。")

    def detect_builder(self) -> None:
        self._detect_calaplayer(
            "calaplayer_builder_exe", "builder", "没有找到打包工具 CalaPlayerSrcmBuilderGUI.exe，请手动选择。"
        )

    def detect_calabiyau(self) -> None:
        discover = getattr(paths, "discover_calabiyau", None)
        if not callable(discover):
            QMessageBox.information(
                self, "暂不支持自动检测", "当前版本还不支持自动查找 CalabiYauVoice，请手动选择。"
            )
            return
        self._run_detect(
            "calabiyau_exe",
            lambda progress: discover(self.cfg, progress=progress),
            "没有找到 CalabiYauVoice_GUI.exe，请手动选择 exe 或它所在的文件夹。",
            auto_first=True,
        )

    def detect_all_tools(self) -> None:
        """一键把 4 个外部工具路径全部找出来并自动保存（不用逐项点自动检测）。"""
        if self._worker is not None and self._worker.isRunning():
            self.log.emit("上一次检测还没结束，请稍候。")
            return
        discover = getattr(paths, "detect_all", None)
        if not callable(discover):
            QMessageBox.information(self, "暂不支持自动检测", "当前版本还不支持一键检测外部工具。")
            return
        self._worker = TaskWorker(
            lambda progress: discover(self.cfg, progress=progress), pass_progress=True, parent=self
        )
        self._worker.progress.connect(self.log.emit)
        self._worker.done.connect(self._on_detect_tools_done)
        self._worker.failed.connect(self._on_detect_failed)
        self.log.emit("正在一键检测外部工具（游戏 CalaPlayer / 打包工具 / CalabiYau / AU）…")
        self._worker.start()

    def _on_detect_tools_done(self, result) -> None:
        detected = result if isinstance(result, dict) else {}
        tool_keys = list(getattr(paths, "TOOL_KEYS", ()) or ())
        labels = getattr(paths, "TOOL_LABELS", {}) or {}
        applied = 0
        for key in tool_keys:
            values = [str(value) for value in (detected.get(key) or []) if str(value).strip()]
            label = str(labels.get(key) or key)
            if not values:
                self.log.emit("%s：没有找到候选" % label)
                continue
            current = self._field_value(key)
            keep = current if self._field_valid(key, current) else values[0]
            self._persist_detected(key, keep)
            applied += 1
            self.log.emit("%s：已填入 %s（共 %d 个候选）" % (label, keep, len(values)))
            for position, value in enumerate(values[:5], start=1):
                self.log.emit("  候选%d：%s" % (position, value))
        if applied:
            try:
                self.cfg.save()
            except Exception:
                pass
            self.check_hint.setText("已自动填入 %d 项外部工具路径并保存。" % applied)
            self.log.emit("一键检测完成：%d 项已填入并保存，下次启动直接可用。" % applied)
        else:
            self.log.emit("一键检测完成：没有找到可用路径，请手动选择。")

    def detect_audition(self) -> None:
        discover = getattr(paths, "discover_audition", None)
        if not callable(discover):
            QMessageBox.information(self, "暂不支持自动检测", "当前版本还不支持自动查找 Audition，请手动选择。")
            return
        self._run_detect(
            "audition_exe",
            lambda progress: discover(self.cfg, progress=progress),
            "没有找到 Adobe Audition，请手动选择 Adobe Audition.exe。",
            auto_first=True,
        )

    def detect_output_dir(self) -> None:
        discover = getattr(paths, "discover_output_dirs", None)
        if not callable(discover):
            QMessageBox.information(self, "暂不支持自动检测", "当前版本还不支持自动查找输出目录，请手动选择。")
            return
        self._run_detect(
            "output_dir",
            lambda progress: discover(self.cfg, progress=progress),
            "没有找到可用的输出目录，请手动选择（或先设置素材目录）。",
            auto_first=True,
        )

    def _detect_calaplayer(self, field: str, kind: str, empty_message: str) -> None:
        discover = getattr(paths, "discover_calaplayer", None)
        if not callable(discover):
            message = "当前版本的 calavoice.paths 还没有 discover_calaplayer，请先手动选择文件。"
            self.log.emit(message)
            QMessageBox.information(self, "暂不支持自动检测", message)
            return
        self._run_detect(
            field,
            lambda progress: _pick_candidates(discover(self.cfg, progress=progress), kind),
            empty_message,
            auto_first=True,
        )


def _pick_candidates(result, kind: str) -> list:
    """discover_calaplayer 返回 {"game": [...], "builder": [...]}，取出对应列表。"""
    if isinstance(result, dict):
        values = result.get(kind) or []
    elif isinstance(result, (list, tuple)):
        values = result
    else:
        values = []
    return [str(value) for value in values if str(value).strip()]


def _with_button(row: QWidget, button: QWidget, parent: QWidget) -> QWidget:
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(row, 1)
    layout.addWidget(button, 0)
    return container