"""人声档案页：左侧档案列表，右侧表单。"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from calavoice.profiles import (
    VoiceProfile,
    delete_profile,
    discover_weights,
    load_profiles,
    upsert_profile,
)
from calavoice.ui.widgets import (
    DEVICE_ITEMS,
    LANG_ITEMS,
    VERSION_ITEMS,
    PathRow,
    fill_combo,
    make_button,
    select_combo,
)
from calavoice.ui.worker import PreviewWorker, TaskWorker, friendly_error

TEST_SENTENCE = "你好，这是一段人声档案的试听，用来确认音色是否合适。"
AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac")


class ProfilesPage(QWidget):
    """人声档案的增删改与试听。"""

    log = Signal(str)
    profiles_changed = Signal()

    def __init__(self, cfg, engine, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.profiles: list[VoiceProfile] = []
        self.preview: PreviewWorker | None = None
        self._discover_worker: TaskWorker | None = None
        self._loading = False
        self._discovered = False
        self._build_ui()
        self.reload()

    # ---------- 界面 ----------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(QLabel("档案列表", self))
        self.list = QListWidget(self)
        self.list.setMinimumWidth(210)
        self.list.setMaximumWidth(260)
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list, 1)
        left_buttons = QHBoxLayout()
        left_buttons.setSpacing(6)
        self.btn_new = make_button("新增", icon_name="plus")
        self.btn_delete = make_button("删除", icon_name="trash")
        left_buttons.addWidget(self.btn_new)
        left_buttons.addWidget(self.btn_delete)
        left.addLayout(left_buttons)
        root.addLayout(left, 0)

        box = QGroupBox("档案详情", self)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.name_edit = QLineEdit(box)
        self.name_edit.setPlaceholderText("例如：旁白A")
        form.addRow("档案名称", self.name_edit)

        self.ref_audio = PathRow(
            mode="file",
            file_filter="音频文件 (*.wav *.mp3 *.flac *.m4a *.ogg);;所有文件 (*.*)",
            with_combo=True,
            placeholder="3~10 秒单人干声",
            parent=box,
        )
        form.addRow("参考音频", self.ref_audio)

        self.prompt_text = QPlainTextEdit(box)
        self.prompt_text.setPlaceholderText("参考音频里说的内容（必填，需与音频逐字一致）")
        self.prompt_text.setMinimumHeight(64)
        self.prompt_text.setMaximumHeight(96)
        form.addRow("参考文本", self.prompt_text)

        self.prompt_lang = _lang_combo(box)
        form.addRow("参考语言", self.prompt_lang)

        self.text_lang = _lang_combo(box)
        form.addRow("合成语言", self.text_lang)

        self.version = QComboBox(box)
        for item in VERSION_ITEMS:
            self.version.addItem(item, item)
        form.addRow("模型版本", self.version)

        self.device = QComboBox(box)
        for label, value in DEVICE_ITEMS:
            self.device.addItem(label, value)
        form.addRow("运算设备", self.device)

        self.gpt_weights = PathRow(
            mode="file",
            file_filter="GPT 权重 (*.ckpt);;所有文件 (*.*)",
            with_combo=True,
            placeholder="留空=使用该版本底模",
            parent=box,
        )
        form.addRow("GPT 权重", self.gpt_weights)

        self.sovits_weights = PathRow(
            mode="file",
            file_filter="SoVITS 权重 (*.pth);;所有文件 (*.*)",
            with_combo=True,
            placeholder="留空=使用该版本底模",
            parent=box,
        )
        form.addRow("SoVITS 权重", self.sovits_weights)

        self.speed = QDoubleSpinBox(box)
        self.speed.setRange(0.5, 2.0)
        self.speed.setSingleStep(0.05)
        self.speed.setDecimals(2)
        self.speed.setSuffix(" 倍")
        self.speed.setValue(1.0)
        form.addRow("语速", self.speed)

        self.note = QLineEdit(box)
        self.note.setPlaceholderText("备注（可选）")
        form.addRow("备注", self.note)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(box, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_save = make_button("保存", icon_name="save", primary=True)
        self.btn_test = make_button("用测试句试听", icon_name="volume")
        self.btn_scan = make_button("扫描权重", icon_name="refresh", tooltip="在 GPT-SoVITS 目录里查找 ckpt/pth 权重")
        buttons.addWidget(self.btn_save)
        buttons.addWidget(self.btn_test)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_scan)
        right.addLayout(buttons)
        root.addLayout(right, 1)

        self.btn_new.clicked.connect(self.new_profile)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_save.clicked.connect(self.save_profile)
        self.btn_test.clicked.connect(self.test_play)
        self.btn_scan.clicked.connect(self.scan_weights)
        self.ref_audio.changed.connect(self._refresh_ref_choices)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._discovered:
            self._discovered = True
            self.scan_weights(silent=True)

    # ---------- 数据 ----------

    def reload(self, keep: str = "") -> None:
        try:
            self.profiles = list(load_profiles(self.cfg) or [])
        except Exception as exc:
            self.profiles = []
            self.log.emit("读取人声档案失败：%s" % friendly_error(exc))
        target = keep or self._selected_name()
        self._refresh_list(target)
        if not self.profiles:
            self._clear_form()

    def _refresh_list(self, keep: str = "") -> None:
        self._loading = True
        self.list.clear()
        for profile in self.profiles:
            item = QListWidgetItem(profile.name)
            item.setData(Qt.ItemDataRole.UserRole, profile.name)
            item.setToolTip(profile.ref_audio or "（未设置参考音频）")
            self.list.addItem(item)
        self._loading = False
        if not self.profiles:
            return
        index = 0
        for position, profile in enumerate(self.profiles):
            if profile.name == keep:
                index = position
                break
        self.list.setCurrentRow(index)

    def _selected_name(self) -> str:
        item = self.list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _profile_by_name(self, name: str) -> VoiceProfile | None:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        return None

    def _on_select(self, current, _previous=None) -> None:
        if self._loading or current is None:
            return
        profile = self._profile_by_name(str(current.data(Qt.ItemDataRole.UserRole) or ""))
        if profile is not None:
            self._load_form(profile)

    def _load_form(self, profile: VoiceProfile) -> None:
        self._loading = True
        self.name_edit.setText(profile.name)
        self.ref_audio.set_text(profile.ref_audio)
        self.prompt_text.setPlainText(profile.prompt_text)
        select_combo(self.prompt_lang, profile.prompt_lang or "zh")
        select_combo(self.text_lang, profile.text_lang or "zh")
        select_combo(self.version, profile.version or "v2ProPlus")
        select_combo(self.device, profile.device or "cuda")
        self.gpt_weights.set_text(profile.gpt_weights)
        self.sovits_weights.set_text(profile.sovits_weights)
        self.speed.setValue(float(profile.speed or 1.0))
        self.note.setText(profile.note)
        self._loading = False
        self._refresh_ref_choices()

    def _clear_form(self) -> None:
        self._loading = True
        self.name_edit.setText("")
        self.ref_audio.set_text("")
        self.prompt_text.setPlainText("")
        select_combo(self.prompt_lang, "zh")
        select_combo(self.text_lang, "zh")
        select_combo(self.version, str(self.cfg.get("version") or "v2ProPlus"))
        select_combo(self.device, str(self.cfg.get("device") or "cuda"))
        self.gpt_weights.set_text("")
        self.sovits_weights.set_text("")
        self.speed.setValue(1.0)
        self.note.setText("")
        self._loading = False

    def _collect_form(self) -> VoiceProfile | None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "缺少名称", "请先填写档案名称。")
            return None
        return VoiceProfile(
            name=name,
            ref_audio=self.ref_audio.text(),
            prompt_text=self.prompt_text.toPlainText().strip(),
            prompt_lang=str(self.prompt_lang.currentData() or "").strip() or "zh",
            text_lang=str(self.text_lang.currentData() or "").strip() or "zh",
            version=str(self.version.currentData() or "v2ProPlus"),
            device=str(self.device.currentData() or "cuda"),
            gpt_weights=self.gpt_weights.text(),
            sovits_weights=self.sovits_weights.text(),
            speed=float(self.speed.value()),
            note=self.note.text().strip(),
        )

    # ---------- 操作 ----------

    def new_profile(self) -> None:
        self._clear_form()
        self.list.clearSelection()
        self.name_edit.setText("新档案%d" % (len(self.profiles) + 1))
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self.log.emit("已新建空白档案，填好后点「保存」。")

    def save_profile(self) -> None:
        profile = self._collect_form()
        if profile is None:
            return
        if not profile.ref_audio:
            QMessageBox.warning(self, "缺少参考音频", "请先为档案选择参考音频（3~10 秒单人干声）。")
            return
        if not os.path.isfile(profile.ref_audio):
            QMessageBox.warning(self, "参考音频不存在", "找不到参考音频：\n%s" % profile.ref_audio)
            return
        if not profile.prompt_text:
            keep = QMessageBox.question(
                self,
                "参考文本为空",
                "参考文本为空会让音色还原变差，仍要保存吗？\n（后端要求参考语言必须有值，已按「%s」保存）"
                % profile.prompt_lang,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if keep != QMessageBox.StandardButton.Yes:
                return
        try:
            upsert_profile(self.cfg, profile)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", friendly_error(exc))
            return
        self.reload(keep=profile.name)
        self.log.emit("人声档案已保存：%s" % profile.name)
        self.profiles_changed.emit()

    def delete_selected(self) -> None:
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "请选择档案", "请先在左侧选中要删除的档案。")
            return
        confirm = QMessageBox.question(
            self, "删除确认", "确定删除人声档案「%s」吗？" % name, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            ok = delete_profile(self.cfg, name)
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", friendly_error(exc))
            return
        if not ok:
            QMessageBox.information(self, "未删除", "没有找到档案「%s」。" % name)
        self.reload()
        self.log.emit("已删除人声档案：%s" % name)
        self.profiles_changed.emit()

    def test_play(self) -> None:
        profile = self._collect_form()
        if profile is None:
            return
        if not profile.ref_audio or not os.path.isfile(profile.ref_audio):
            QMessageBox.warning(self, "缺少参考音频", "请先选择有效的参考音频再试听。")
            return
        if self.preview is not None and self.preview.isRunning():
            self.log.emit("试听音频还在生成中，请稍候。")
            return
        self.preview = PreviewWorker(
            self.cfg, self.engine, TEST_SENTENCE, profile, out_name="preview_profile.wav", parent=self
        )
        self.preview.log.connect(self.log.emit)
        self.preview.done.connect(self._on_preview_done)
        self.log.emit("正在用测试句生成试听（档案：%s）…" % profile.name)
        self.preview.start()

    def _on_preview_done(self, ok: bool, payload: str) -> None:
        if not ok:
            QMessageBox.warning(self, "试听失败", payload)
            self.log.emit("试听失败：%s" % payload)
            return
        self.log.emit("试听文件：%s" % payload)
        try:
            if hasattr(os, "startfile"):
                os.startfile(payload)
            else:
                from calavoice import launcher

                launcher.open_folder(str(Path(payload).parent))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "无法播放",
                "试听文件已生成，但无法自动播放：\n%s\n\n文件位置：%s" % (friendly_error(exc), payload),
            )

    # ---------- 权重扫描 ----------

    def scan_weights(self, silent: bool = False) -> None:
        if self._discover_worker is not None and self._discover_worker.isRunning():
            return
        self._discover_worker = TaskWorker(
            lambda progress: discover_weights(self.cfg, progress=progress),
            pass_progress=True,
            parent=self,
        )
        if not silent:
            self._discover_worker.progress.connect(self.log.emit)
        self._discover_worker.done.connect(lambda result: self._apply_weights(result, silent))
        self._discover_worker.failed.connect(lambda message: self._on_scan_failed(message, silent))
        self._discover_worker.start()

    def _apply_weights(self, result, silent: bool) -> None:
        data = result if isinstance(result, dict) else {}
        gpt = [str(item) for item in (data.get("gpt") or [])]
        sovits = [str(item) for item in (data.get("sovits") or [])]
        self.gpt_weights.set_choices(gpt)
        self.sovits_weights.set_choices(sovits)
        if not silent:
            self.log.emit("权重扫描完成：GPT %d 个，SoVITS %d 个" % (len(gpt), len(sovits)))

    def _on_scan_failed(self, message: str, silent: bool) -> None:
        self.log.emit("权重扫描失败：%s" % message)
        if not silent:
            QMessageBox.warning(self, "扫描失败", message)

    def _refresh_ref_choices(self) -> None:
        current = self.ref_audio.text()
        folder = str(Path(current).parent) if current else str(self.cfg.get("sovits_root") or "")
        choices: list[str] = []
        if folder and os.path.isdir(folder):
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                names = []
            for name in names:
                if name.lower().endswith(AUDIO_SUFFIXES):
                    choices.append(os.path.join(folder, name))
        self.ref_audio.set_choices(choices[:200])


def _lang_combo(parent) -> QComboBox:
    combo = QComboBox(parent)
    fill_combo(combo, LANG_ITEMS[1:])
    return combo