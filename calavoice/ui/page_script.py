"""台词生成页：表格 + 批量生成。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from calavoice import launcher
from calavoice.profiles import VoiceProfile, load_profiles
from calavoice.script_model import ScriptRow, load_rows, save_rows
from calavoice.ui.widgets import LANG_ITEMS, make_button, select_combo, setup_table
from calavoice.ui.worker import (
    STATUS_DONE,
    STATUS_FAIL,
    STATUS_RUNNING,
    STATUS_SKIP,
    STATUS_WAIT,
    GenerateWorker,
    PreviewWorker,
    friendly_error,
    material_sound_dir,
    resolve_tool,
)

COLUMNS = ["#", "台词", "人声档案", "语言", "输出名", "状态"]
COLUMN_WIDTHS = {0: 46, 2: 170, 3: 124, 4: 190, 5: 130}

STATUS_COLORS = {
    STATUS_DONE: "#2e9e4f",
    STATUS_FAIL: "#d64545",
    STATUS_RUNNING: "#d9a11a",
    STATUS_SKIP: "#8a8f98",
}


class ScriptPage(QWidget):
    """台词列表与批量生成。"""

    log = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, cfg, engine, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.rows: list[ScriptRow] = []
        self.worker: GenerateWorker | None = None
        self.preview: PreviewWorker | None = None
        self._profile_names: list[str] = []
        self._loading = False
        self._busy = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)
        # 每行开始时间（idx -> monotonic），用于在“状态”列实时显示已耗时
        self._row_started: dict[int, float] = {}
        self._batch_started: float = 0.0
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick_rows)
        self._timeout_hinted = False
        self._build_ui()
        self.reload_profiles()
        self.reload_rows()

    # ---------- 构建界面 ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_add = make_button("新增行", icon_name="plus", tooltip="在末尾添加一条台词")
        self.btn_del = make_button("删除行", icon_name="trash", tooltip="删除选中的台词行")
        self.btn_up = make_button("上移", icon_name="arrow_up")
        self.btn_down = make_button("下移", icon_name="arrow_down")
        self.btn_import = make_button("导入台词txt", icon_name="import_txt", tooltip="导入文本文件，每行一条台词")
        self.btn_generate = make_button("批量生成", icon_name="play", primary=True, tooltip="生成所有已填写的台词")
        self.btn_stop = make_button(
            "停止", icon_name="stop", danger=True, tooltip="停止批量生成并关闭后端进程"
        )
        for button in (
            self.btn_add,
            self.btn_del,
            self.btn_up,
            self.btn_down,
            self.btn_import,
            self.btn_generate,
            self.btn_stop,
        ):
            bar.addWidget(button)
        bar.addSpacing(12)
        bar.addWidget(QLabel("默认人声档案："))
        self.default_combo = QComboBox(self)
        self.default_combo.setMinimumWidth(180)
        bar.addWidget(self.default_combo)
        bar.addStretch(1)
        root.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        setup_table(self.table, COLUMN_WIDTHS, stretch_column=1)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.btn_preview = make_button("试听选中", icon_name="volume", tooltip="合成并播放选中行")
        self.btn_output_dir = make_button("打开输出目录", icon_name="folder")
        bottom.addWidget(self.btn_preview)
        bottom.addWidget(self.btn_output_dir)
        bottom.addStretch(1)
        self.hint = QLabel("提示：双击单元格可编辑；语言留「跟随档案」即使用档案设置。", self)
        self.hint.setObjectName("HintLabel")
        bottom.addWidget(self.hint)
        root.addLayout(bottom)

        self.btn_add.clicked.connect(self.add_row)
        self.btn_del.clicked.connect(self.delete_rows)
        self.btn_up.clicked.connect(lambda: self.move_row(-1))
        self.btn_down.clicked.connect(lambda: self.move_row(1))
        self.btn_import.clicked.connect(self.import_txt)
        self.btn_generate.clicked.connect(self.start_generate)
        self.btn_stop.clicked.connect(self.stop_generate)
        self.btn_preview.clicked.connect(self.preview_selected)
        self.btn_output_dir.clicked.connect(self.open_output_dir)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

    # ---------- 人声档案 ----------

    def reload_profiles(self) -> None:
        try:
            self._profile_names = [p.name for p in load_profiles(self.cfg)]
        except Exception as exc:
            self._profile_names = []
            self.log.emit("读取人声档案失败：%s" % friendly_error(exc))
        current_default = self.default_combo.currentData() if self.default_combo.count() else ""
        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItem("（未选择）", "")
        for name in self._profile_names:
            self.default_combo.addItem(name, name)
        select_combo(self.default_combo, current_default or "")
        self.default_combo.blockSignals(False)
        for table_row in range(self.table.rowCount()):
            combo = self.table.cellWidget(table_row, 2)
            if isinstance(combo, QComboBox):
                keep = self.rows[table_row].profile if table_row < len(self.rows) else ""
                self._fill_profile_combo(combo, keep)

    def _fill_profile_combo(self, combo: QComboBox, keep: str = "") -> None:
        if keep and keep not in self._profile_names:
            # 缓存可能过期（档案是这之后新建/改名的）：现读一次再判断，避免误标「缺失」
            try:
                fresh = [p.name for p in load_profiles(self.cfg)]
            except Exception:
                fresh = []
            if fresh != self._profile_names:
                self._profile_names = fresh
                self._refill_default_combo()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("（用默认档案）", "")
        for name in self._profile_names:
            combo.addItem(name, name)
        if keep and keep not in self._profile_names:
            combo.addItem("%s（缺失）" % keep, keep)
        select_combo(combo, keep)
        combo.blockSignals(False)

    def _refill_default_combo(self) -> None:
        """档案列表刷新后同步「默认人声档案」下拉，保持当前选择。"""
        current = self.default_combo.currentData() if self.default_combo.count() else ""
        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItem("（未选择）", "")
        for name in self._profile_names:
            self.default_combo.addItem(name, name)
        select_combo(self.default_combo, str(current or ""))
        self.default_combo.blockSignals(False)

    def current_profile(self) -> str:
        return str(self.default_combo.currentData() or "")

    def profile_by_name(self, name: str) -> VoiceProfile | None:
        if not name:
            return None
        try:
            for profile in load_profiles(self.cfg):
                if profile.name == name:
                    return profile
        except Exception as exc:
            self.log.emit("读取人声档案失败：%s" % friendly_error(exc))
        return None

    # ---------- 台词行 ----------

    def reload_rows(self) -> None:
        try:
            rows = list(load_rows(self.cfg) or [])
        except Exception as exc:
            rows = []
            self.log.emit("读取台词列表失败：%s" % friendly_error(exc))
        if not rows:
            rows = [ScriptRow(idx=1)]
        self.rows = []
        for position, row in enumerate(rows, start=1):
            row.idx = position
            if not row.status:
                row.status = STATUS_WAIT
            self.rows.append(row)
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        self._loading = True
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            self.table.setItem(row_index, 0, self._number_item(row.idx))
            self.table.setItem(row_index, 1, QTableWidgetItem(row.text))

            profile_combo = QComboBox(self.table)
            self._fill_profile_combo(profile_combo, row.profile or "")
            profile_combo.currentIndexChanged.connect(
                lambda _index, target=row_index: self._on_combo_changed(target, 2)
            )
            self.table.setCellWidget(row_index, 2, profile_combo)

            lang_combo = QComboBox(self.table)
            for label, code in LANG_ITEMS:
                lang_combo.addItem(label, code)
            select_combo(lang_combo, row.text_lang or "")
            lang_combo.currentIndexChanged.connect(
                lambda _index, target=row_index: self._on_combo_changed(target, 3)
            )
            self.table.setCellWidget(row_index, 3, lang_combo)

            self.table.setItem(row_index, 4, QTableWidgetItem(row.out_name))
            self.table.setItem(row_index, 5, self._status_item(row.status, row.message))
        self._loading = False
        self._update_buttons()

    @staticmethod
    def _number_item(idx: int) -> QTableWidgetItem:
        item = QTableWidgetItem(str(idx))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    @staticmethod
    def _status_item(status: str, message: str) -> QTableWidgetItem:
        item = QTableWidgetItem(status or STATUS_WAIT)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        color = STATUS_COLORS.get(status or STATUS_WAIT)
        if color:
            item.setForeground(QColor(color))
        item.setToolTip(message or "")
        return item

    def _on_combo_changed(self, row_index: int, column: int) -> None:
        if self._loading or row_index >= len(self.rows):
            return
        combo = self.table.cellWidget(row_index, column)
        if not isinstance(combo, QComboBox):
            return
        value = str(combo.currentData() or "")
        row = self.rows[row_index]
        if column == 2:
            row.profile = value
        elif column == 3:
            row.text_lang = value
        self._touch_save()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row_index = item.row()
        if row_index >= len(self.rows):
            return
        if item.column() == 1:
            self.rows[row_index].text = item.text()
        elif item.column() == 4:
            self.rows[row_index].out_name = item.text()
        else:
            return
        self._touch_save()

    def _sync_from_table(self) -> None:
        for row_index in range(min(self.table.rowCount(), len(self.rows))):
            text_item = self.table.item(row_index, 1)
            if text_item is not None:
                self.rows[row_index].text = text_item.text()
            name_item = self.table.item(row_index, 4)
            if name_item is not None:
                self.rows[row_index].out_name = name_item.text()
            profile_combo = self.table.cellWidget(row_index, 2)
            if isinstance(profile_combo, QComboBox):
                self.rows[row_index].profile = str(profile_combo.currentData() or "")
            lang_combo = self.table.cellWidget(row_index, 3)
            if isinstance(lang_combo, QComboBox):
                self.rows[row_index].text_lang = str(lang_combo.currentData() or "")

    def _touch_save(self) -> None:
        self._save_timer.start()

    def save(self) -> None:
        try:
            save_rows(self.cfg, self.rows)
        except Exception as exc:
            self.log.emit("保存台词列表失败：%s" % friendly_error(exc))

    def _renumber(self) -> None:
        for position, row in enumerate(self.rows, start=1):
            row.idx = position

    # ---------- 行编辑 ----------

    def add_row(self) -> None:
        self._sync_from_table()
        self.rows.append(ScriptRow(idx=len(self.rows) + 1))
        self._rebuild_table()
        self.table.selectRow(len(self.rows) - 1)
        self._touch_save()

    def delete_rows(self) -> None:
        selected = self._selected_row_indexes()
        if not selected:
            QMessageBox.information(self, "请选择台词", "请先在表格里选中要删除的行。")
            return
        self._sync_from_table()
        for row_index in sorted(selected, reverse=True):
            if 0 <= row_index < len(self.rows):
                del self.rows[row_index]
        if not self.rows:
            self.rows.append(ScriptRow(idx=1))
        self._renumber()
        self._rebuild_table()
        self._touch_save()

    def move_row(self, delta: int) -> None:
        selected = self._selected_row_indexes()
        if len(selected) != 1:
            QMessageBox.information(self, "请选择一行", "请先选中单独一行再上移或下移。")
            return
        source = selected[0]
        target = source + delta
        if target < 0 or target >= len(self.rows):
            return
        self._sync_from_table()
        self.rows[source], self.rows[target] = self.rows[target], self.rows[source]
        self._renumber()
        self._rebuild_table()
        self.table.selectRow(target)
        self._touch_save()

    def _selected_row_indexes(self) -> list:
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def selected_row(self) -> ScriptRow | None:
        selected = self._selected_row_indexes()
        if not selected:
            return None
        row_index = selected[0]
        if 0 <= row_index < len(self.rows):
            return self.rows[row_index]
        return None

    # ---------- 导入 ----------

    def import_txt(self) -> None:
        start = str(self.cfg.get("material_root") or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "导入台词文本", start, "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            content = _read_text_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "无法读取文件", friendly_error(exc))
            return
        lines = [line.strip() for line in content.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            QMessageBox.information(self, "没有内容", "该文件没有可导入的台词（需要每行一条）。")
            return
        self._sync_from_table()
        if len(self.rows) == 1 and not self.rows[0].text.strip():
            self.rows = []
        start_idx = len(self.rows)
        for offset, line in enumerate(lines):
            self.rows.append(ScriptRow(idx=start_idx + offset + 1, text=line))
        self._rebuild_table()
        self.save()
        self.log.emit("已导入 %d 条台词（来自 %s）" % (len(lines), os.path.basename(path)))

    # ---------- 生成 ----------

    def start_generate(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.log.emit("批量生成正在进行中，请先停止或等待结束。")
            return
        self._sync_from_table()
        self.save()
        pending = [row for row in self.rows if row.enabled and row.text.strip()]
        if not pending:
            QMessageBox.information(self, "无法生成", "没有可生成的台词，请先填写台词内容。")
            return
        if not str(self.cfg.get("material_root") or "").strip():
            QMessageBox.warning(
                self,
                "缺少素材目录",
                "还没有设置 CalaPlayer 素材目录。\n请先到「设置」页选择素材目录，再开始生成。",
            )
            return
        try:
            material_sound_dir(self.cfg)
            resolve_tool(self.cfg, "ffmpeg")
            resolve_tool(self.cfg, "ffprobe")
        except Exception as exc:
            QMessageBox.warning(self, "环境未就绪", friendly_error(exc))
            return

        for row in pending:
            row.status = STATUS_WAIT
            row.message = ""
        self._timeout_hinted = False
        self._row_started.clear()
        self._batch_started = 0.0
        self._refresh_status_column()

        self.worker = GenerateWorker(
            self.cfg, self.engine, pending, default_profile=self.current_profile(), parent=self
        )
        self.worker.row_status.connect(self._on_row_status)
        self.worker.row_done.connect(self._on_row_done)
        self.worker.finished_all.connect(self._on_finished_all)
        self.worker.log.connect(self.log.emit)
        self.worker.finished.connect(self._on_worker_finished)
        self._set_busy(True)
        self.hint.setText("正在生成，共 %d 条…" % len(pending))
        self.log.emit("开始批量生成，共 %d 条台词" % len(pending))
        self.worker.start()

    def stop_generate(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.log.emit("正在停止：将立刻结束后端进程，未生成的行标记为已跳过…")
            self.hint.setText("正在停止…")
            self.worker.stop()
        else:
            self.log.emit("当前没有正在进行的生成任务。")

    def _row_by_idx(self, idx: int):
        for position, row in enumerate(self.rows):
            if row.idx == idx:
                return position, row
        return None, None

    def _refresh_status_column(self) -> None:
        self._loading = True
        for row_index, row in enumerate(self.rows):
            if row_index < self.table.rowCount():
                self.table.setItem(row_index, 5, self._status_item(row.status, row.message))
        self._loading = False

    def _apply_row_state(self, idx: int, status: str, message: str, label: str = "") -> None:
        position, row = self._row_by_idx(idx)
        if row is None:
            return
        row.status = status
        row.message = message
        if position < self.table.rowCount():
            self._loading = True
            item = self._status_item(status, message)
            if label:
                item.setText(label)
            self.table.setItem(position, 5, item)
            self._loading = False

    def _on_row_status(self, idx: int, status: str, message: str) -> None:
        if status == STATUS_RUNNING:
            self._row_started.setdefault(idx, time.monotonic())
            if not self._tick_timer.isActive():
                self._batch_started = self._batch_started or time.monotonic()
                self._tick_timer.start()
        elif status in (STATUS_DONE, STATUS_FAIL, STATUS_SKIP):
            started = self._row_started.pop(idx, 0.0)
            elapsed = (time.monotonic() - started) if started else 0.0
            label = self._elapsed_label(status, message, elapsed)
            self._apply_row_state(idx, status, message, label=label)
            if status == STATUS_FAIL and not self._timeout_hinted:
                hint = self._timeout_hint(message)
                if hint:
                    self._timeout_hinted = True
                    self.log.emit(hint)
            if not self._row_started:
                self._tick_timer.stop()
            return
        self._apply_row_state(idx, status, message)

    def _on_row_done(self, idx: int, path: str) -> None:
        started = self._row_started.pop(idx, 0.0)
        elapsed = (time.monotonic() - started) if started else 0.0
        self._apply_row_state(idx, STATUS_DONE, path, label="完成 %.1fs" % elapsed)
        self.log.emit("已导出：%s（%.1f 秒）" % (path, elapsed))
        if not self._row_started:
            self._tick_timer.stop()

    @staticmethod
    def _elapsed_label(status: str, message: str, elapsed: float) -> str:
        if status == STATUS_DONE:
            return "完成 %.1fs" % elapsed
        if status == STATUS_SKIP:
            return STATUS_SKIP
        brief = (message or "").strip().replace("\n", " ")
        if len(brief) > 18:
            brief = brief[:18] + "…"
        head = ("失败 %.0fs" % elapsed) if elapsed > 0 else "失败"
        return head + ((" · " + brief) if brief else "")

    @staticmethod
    def _timeout_hint(message: str) -> str:
        """卡死类失败给一次可行动提示（每批只提示一次）。"""
        text = message or ""
        if not any(key in text for key in ("无响应", "超时", "显存", "重启")):
            return ""
        return (
            "检测到后端卡死：已自动重启后端。若频繁发生，请关闭占用显卡的程序"
            "（当前可用显存见 data\\logs 里的后端日志），或到设置页把默认设备改成 CPU。"
        )

    def _tick_rows(self) -> None:
        """每秒刷新正在生成的行，让它显示“生成中… 12s”。"""
        if not self._row_started:
            self._tick_timer.stop()
            return
        now = time.monotonic()
        for idx, started in list(self._row_started.items()):
            position, row = self._row_by_idx(idx)
            if row is None or position >= self.table.rowCount():
                continue
            elapsed = now - started
            self._loading = True
            item = self._status_item(STATUS_RUNNING, "生成中… %.1f 秒" % elapsed)
            item.setText("生成中… %.0fs" % elapsed)
            self.table.setItem(position, 5, item)
            self._loading = False

    def _on_finished_all(self, summary: str) -> None:
        total = (time.monotonic() - self._batch_started) if self._batch_started else 0.0
        self._batch_started = 0.0
        self._tick_timer.stop()
        self._row_started.clear()
        line = "%s（总耗时 %.1f 秒）" % (summary, total) if total else summary
        self.log.emit(line)
        self.hint.setText(line)
        self.save()

    def _on_worker_finished(self) -> None:
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_generate.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.btn_add.setEnabled(not busy)
        self.btn_del.setEnabled(not busy)
        self.btn_import.setEnabled(not busy)
        self.btn_up.setEnabled(not busy)
        self.btn_down.setEnabled(not busy)
        self.busy_changed.emit(busy)
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = bool(self._selected_row_indexes())
        self.btn_preview.setEnabled(has_selection and not self._busy)
        self.btn_stop.setEnabled(self._busy)

    def is_busy(self) -> bool:
        return self._busy

    # ---------- 试听 / 目录 ----------

    def preview_selected(self) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "请选择一行", "请先在表格里选中要试听的台词。")
            return
        self._sync_from_table()
        text = (row.text or "").strip()
        if not text:
            QMessageBox.information(self, "台词为空", "选中的台词内容为空，无法试听。")
            return
        profile = self.profile_by_name(row.profile or self.current_profile())
        if profile is None:
            QMessageBox.warning(
                self, "缺少人声档案", "请先在「人声档案」页创建档案，并在工具栏里选好默认人声档案。"
            )
            return
        if self.preview is not None and self.preview.isRunning():
            self.log.emit("试听音频还在生成中，请稍候。")
            return
        self.preview = PreviewWorker(
            self.cfg, self.engine, text, profile, out_name="preview_row.wav", parent=self
        )
        self.preview.log.connect(self.log.emit)
        self.preview.done.connect(self._on_preview_done)
        self.hint.setText("正在生成试听音频…")
        self.preview.start()

    def _on_preview_done(self, ok: bool, payload: str) -> None:
        self.hint.setText("就绪")
        if not ok:
            QMessageBox.warning(self, "试听失败", payload)
            self.log.emit("试听失败：%s" % payload)
            return
        self.log.emit("试听文件：%s" % payload)
        try:
            if hasattr(os, "startfile"):
                os.startfile(payload)  # 交给系统默认播放器
            else:
                launcher.open_folder(str(Path(payload).parent))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "无法播放",
                "试听文件已生成，但无法自动播放：\n%s\n\n文件位置：%s" % (friendly_error(exc), payload),
            )

    def open_output_dir(self) -> None:
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


def _read_text_file(path: str) -> str:
    """按常见中文编码尝试读取文本文件。"""
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16", "big5"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                return handle.read()
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError as exc:
        raise ValueError("无法读取文件：%s" % path) from exc