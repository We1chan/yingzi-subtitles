import dataclasses
import copy
import json
import os
import subprocess
import sys
import time
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QTimer, QProcess, QSizeF, QLockFile, Signal, QPointF, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QShortcut, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QLineEdit, QFileDialog, QMessageBox, QTableWidgetItem,
    QGraphicsView, QGraphicsScene, QGraphicsTextItem, QAbstractItemView,
    QPlainTextEdit, QDialog, QGraphicsPixmapItem, QGraphicsItem,
    QFormLayout, QDialogButtonBox, QCheckBox, QMenu)

from .config import ROOT, CACHE, OUTPUT, LOGS, readiness, load_model_selection, save_model_selection, MODEL_DEFAULTS, asr_language_issue, asr_backend
from .media import audio_tracks, CREATE_NO_WINDOW
from .subtitles import timestamp, parse_time, validate, render, atomic_write, load_project, save_project, subtitle_text, needs_translation, clean_line
from .caption_style import CaptionStyle, render_caption, caption_position
from .api_settings import save_translation_backend, load_api_settings, api_issue
from .widgets import ComboBox as QComboBox, DoubleSpinBox
from .pipeline import cache_ready
from .editing import EditHistory, merge_cues, split_cue, quality_issues
from .preview_audio import PreviewAudio, PreviewAudioDialog
from .subtitle_import import input_kind, read_srt, MEDIA_FILTER


from .theme import STYLE
from .studio_layout import build_studio


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, slot=None, name=None):
    widget = QPushButton(text)
    widget.setCursor(Qt.PointingHandCursor)
    if slot:
        widget.clicked.connect(slot)
    if name:
        widget.setObjectName(name)
    return widget


def card():
    widget = QFrame()
    widget.setObjectName('card')
    return widget


def make_icon():
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#FF5522'))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, 124, 124, 28, 28)
    painter.setPen(QColor('white'))
    painter.setFont(QFont('Microsoft YaHei UI', 57, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, '字')
    painter.end()
    return QIcon(pixmap)


def initialize_fonts(app):
    # Qt's offscreen plugin has no system font discovery on some Windows builds.
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
        path = fonts / name
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('Microsoft YaHei', 10))


class DraggableCaption(QGraphicsPixmapItem):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.text = ''
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.setCursor(Qt.OpenHandCursor)

    def toPlainText(self):
        return self.text

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            bounds = self.view.sceneRect()
            return QPointF(max(0, min(bounds.width() - self.boundingRect().width(), value.x())),
                           max(0, min(bounds.height() - self.boundingRect().height(), value.y())))
        if change == QGraphicsItem.ItemPositionHasChanged and not self.view.updating_caption:
            self.view.caption_moved()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self.setCursor(Qt.ClosedHandCursor)
        self.view.dragStarted.emit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)


class VideoView(QGraphicsView):
    styleMoved = Signal(object)
    dragStarted = Signal()

    def __init__(self):
        super().__init__()
        self.caption_style = CaptionStyle()
        self.caption_text = ''
        self.caption_mode = 'bilingual'
        self.caption_original_lines = 1
        self.updating_caption = False
        self.scene_object = QGraphicsScene(self)
        self.setScene(self.scene_object)
        self.setSceneRect(0, 0, 1280, 720)
        self.setBackgroundBrush(QColor('#242526'))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setSize(QSizeF(1280, 720))
        self.video_item.nativeSizeChanged.connect(lambda: self.update_frame_geometry(self.video_item.videoSink().videoFrame()))
        self.video_item.videoSink().videoFrameChanged.connect(self.update_frame_geometry)
        self.scene_object.addItem(self.video_item)
        self.empty = QGraphicsTextItem()
        self.empty.setTextWidth(1100)
        self.empty.setPos(90, 240)
        self.empty.setHtml('<div align="center" style="color:#FFF3EC; font-size:42px; font-family:Microsoft YaHei;">让每一句，都有字幕</div><br><div align="center" style="color:#B69C8E; font-size:22px;">拖入视频，或点击「选择视频」开始</div>')
        self.scene_object.addItem(self.empty)
        self.caption = DraggableCaption(self)
        self.caption.setZValue(10)
        self.scene_object.addItem(self.caption)
        self.setMinimumSize(380, 100)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def set_video_size(self, size):
        if size.width() <= 0 or size.height() <= 0:
            return
        if (round(size.width()), round(size.height())) == (round(self.sceneRect().width()), round(self.sceneRect().height())):
            return
        self.setSceneRect(0, 0, round(size.width()), round(size.height()))
        self.video_item.setSize(size)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self.redraw_caption()

    def update_frame_geometry(self, frame):
        if not frame.isValid():
            return
        size = self.video_item.nativeSize()
        angle = frame.rotation().value + frame.surfaceFormat().rotation().value
        if angle % 180:
            size = QSizeF(size.height(), size.width())
        self.set_video_size(size)

    def set_caption(self, text, mode='bilingual', original_lines=1):
        if (text, mode, original_lines) != (self.caption_text, self.caption_mode, self.caption_original_lines):
            self.caption_text, self.caption_mode = text, mode
            self.caption_original_lines = original_lines
            self.redraw_caption()

    def set_style(self, style):
        if style.to_dict() != self.caption_style.to_dict():
            self.caption_style = CaptionStyle.from_dict(style.to_dict())
            self.redraw_caption()

    def redraw_caption(self):
        self.updating_caption = True
        try:
            width, height = round(self.sceneRect().width()), round(self.sceneRect().height())
            tile = render_caption(self.caption_text, self.caption_style, width, height,
                                  self.caption_mode == 'translated', self.caption_original_lines)
            self.caption.setPixmap(QPixmap.fromImage(tile))
            self.caption.text = self.caption_text
            self.caption.setVisible(bool(self.caption_text))
            x, y = caption_position(self.caption_style, tile.width(), tile.height(), width, height)
            self.caption.setPos(round(x), round(y))
        finally:
            self.updating_caption = False

    def caption_moved(self):
        bounds = self.caption.boundingRect()
        self.caption_style.x = 100 * (self.caption.x() + bounds.width() / 2) / self.sceneRect().width()
        self.caption_style.y = 100 * (self.caption.y() + bounds.height()) / self.sceneRect().height()
        self.styleMoved.emit(self.caption_style)


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.video = None
        self.project = None
        self.project_status = 'complete'
        self.output_dir = OUTPUT
        self.cues = []
        self.cue_starts = []
        self.history = EditHistory()
        self.translation_glossary = ''
        self.translation_prompt = ''
        self.worker = None
        self.active_online_translation = False
        self.job_kind = 'recognition'
        self.export_work = None
        self.log_handle = None
        self.buffer = b''
        self.terminal_event = False
        self.loading_table = False
        self.active_cue = -1
        self.model_selection = load_model_selection()
        self.setWindowTitle('映字 · 双语字幕工作台')
        self.setWindowIcon(make_icon())
        self.resize(1440, 940)
        self.setMinimumSize(1100, 760)
        self.setAcceptDrops(True)
        self.build_ui()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.preview_audio = PreviewAudio(self.audio, self)
        self.preview_audio.changed.connect(self.refresh_audio_button)
        self.refresh_audio_button()
        self.player.setVideoOutput(self.preview.video_item)
        self.preview.dragStarted.connect(self.player.pause)
        self.preview.styleMoved.connect(self.style_moved)
        self.style_save_timer = QTimer(self)
        self.style_save_timer.setSingleShot(True)
        self.style_save_timer.timeout.connect(self.save_edits)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda value: self.seek.setRange(0, value))
        self.player.playbackStateChanged.connect(lambda state: self.play.setText('暂停' if state == QMediaPlayer.PlayingState else '播放'))
        self.player.errorOccurred.connect(lambda error, text: self.notify(f'预览提示：{text}。可继续尝试生成字幕。', error=True))
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.refresh_models()
        self.model_timer = QTimer(self)
        self.model_timer.timeout.connect(self.refresh_models)
        self.model_timer.start(10000)
        QShortcut(QKeySequence.Save, self, activated=self.save_edits)
        QShortcut(QKeySequence('Ctrl+O'), self, activated=self.choose_video)
        QShortcut(QKeySequence.Undo, self, activated=self.undo_edit)
        QShortcut(QKeySequence.Redo, self, activated=self.redo_edit)
        QShortcut(QKeySequence.Find, self, activated=self.focus_search)
        # Space plays only from the list or picture; text fields and cell editors keep it.
        for widget in (self.table, self.preview):
            QShortcut(QKeySequence(Qt.Key_Space), widget, activated=lambda: self.toggle_play(),
                      context=Qt.WidgetWithChildrenShortcut)
        # Wrapped rows are refit after column widths settle, not on every resize step.
        self.row_fit_timer = QTimer(self)
        self.row_fit_timer.setSingleShot(True)
        self.row_fit_timer.timeout.connect(self.table.resizeRowsToContents)
        self.table.horizontalHeader().sectionResized.connect(lambda *args: self.row_fit_timer.start(80))

    def build_ui(self):
        build_studio(self, VideoView)

    def go_to_step(self, index):
        if index != 1:
            self.preview_toggle.setChecked(False)
        self.settings_tabs.setCurrentIndex(index)
        self.step_buttons[index].setChecked(True)
        height = max(400, self.splitter.height())
        ratio = .43 if index == 1 else .56
        if not self.preview_toggle.isChecked():
            self.splitter.setSizes([round(height * ratio), round(height * (1 - ratio))])
        if index == 1 and self.cues:
            self.table.setFocus()

    def inspector_changed(self, index):
        self.step_buttons[index].setChecked(True)
        self.refresh_workspace()
        self.refresh_caption()

    def toggle_advanced(self, expanded):
        self.advanced_settings.setVisible(expanded)
        self.advanced_toggle.setText('－  收起高级设置' if expanded else '＋  高级设置')

    def toggle_preview(self, collapsed):
        if collapsed:
            self._preview_sizes = self.splitter.sizes()
        self.preview_stack.setVisible(not collapsed)
        self.playback_controls.setVisible(not collapsed and bool(self.video))
        self.preview_toggle.setText('展开画面' if collapsed else '收起画面')
        self.splitter.setSizes([60, self.splitter.height() - 60] if collapsed else getattr(self, '_preview_sizes', [390, 330]))

    def choose_subtitles(self):
        if self.busy():
            return
        if not self.video or not self.video.is_file():
            self.notify('请先导入视频或音频，再导入 SRT 字幕。')
            return
        filename, _ = QFileDialog.getOpenFileName(self, '导入 SRT 字幕', str(self.video.parent), 'SRT 字幕 (*.srt)')
        if filename and self.open_input_file(Path(filename)):
            self.go_to_step(1)

    def export_content_changed(self):
        self.refresh_caption()
        self.refresh_workspace()

    def refresh_workspace(self):
        """Present the next useful action without changing processing state."""
        busy = self.busy() or getattr(self, '_ui_busy', False)
        media = bool(self.video)
        available = media and self.video.is_file()
        has_cues = bool(self.cues)
        styling = self.settings_tabs.currentIndex() == 1
        self.preview_stack.setCurrentIndex(1 if media or styling else 0)
        # Before anything is loaded, the drop zone gets the whole column.
        landing = not media and not has_cues and self.settings_tabs.currentIndex() == 0
        self.editor_card.setVisible(not landing)
        self.preview_heading.setVisible(not landing)
        self.progress_bar.setVisible(busy or self.progress_bar.value() > 0)
        if not available:
            self.preview.empty.show()
            title = '原媒体不可用' if media else '字幕样式预览'
            hint = '仍可校对和导出字幕文件' if media else '拖动字幕调整位置 · 效果应用于全部字幕'
            self.preview.empty.setHtml(f'<div align="center" style="font-family:Microsoft YaHei; color:#A5A19E; font-size:32px;">{title}</div><br><div align="center" style="font-family:Microsoft YaHei; color:#807D7A; font-size:20px;">{hint}</div>')
        self.playback_controls.setVisible(media and not self.preview_toggle.isChecked())
        self.play.setEnabled(available)
        self.seek.setEnabled(available)
        self.editor_stack.setCurrentIndex(1 if has_cues else 0)
        for control in self.editor_buttons:
            control.setVisible(has_cues)
        self.selection_label.setVisible(has_cues)
        for control in (self.subtitle_search, self.quality_filter):
            control.setEnabled(has_cues)
            control.setVisible(has_cues)
        self.import_srt_button.setEnabled(available and not busy)
        self.import_srt_button.setVisible(available)
        self.welcome_import.setEnabled(not busy)
        self.open_project_button.setEnabled(not busy)
        bilingual = bool(self.mode.currentData())
        self.translation_group.setVisible(bilingual)
        self.api_button.setVisible(self.translation_backend.currentData() == 'online')
        self.start_button.setText('生成双语字幕' if bilingual else '生成原文字幕')
        pending = sum(needs_translation(cue) for cue in self.cues)
        incomplete = self.project_status in ('recognizing', 'aligning')
        export_ready = has_cues and not incomplete and (self.export_mode.currentData() == 'original' or not pending)
        self.export_button.setEnabled(export_ready and not busy)
        self.export_video_button.setEnabled(export_ready and available and not busy)
        self.export_content.setText('当前内容：' + self.export_mode.currentText())
        if busy:
            hint = '正在导出视频，可在下方取消' if self.job_kind == 'export' else '正在处理，下方显示实时进度'
        elif not media:
            hint = '先导入一个视频或音频'
        elif not available:
            hint = '原媒体缺失 · 字幕仍可编辑和导出'
        elif incomplete:
            hint = '任务尚未完成 · 点击继续处理'
        elif not has_cues:
            hint = '设置已就绪后，点击「生成字幕」'
        elif pending:
            hint = f'{pending} 条译文待补全 · 可筛选查看'
        else:
            hint = f'{len(self.cues)} 条字幕 · 校对后即可导出'
        self.workflow_hint.setText(hint)
        if not has_cues:
            summary = '生成或导入字幕后，即可在这里导出。'
        elif incomplete:
            summary = '时间轴尚未完成，请先继续处理。'
        elif pending and self.export_mode.currentData() != 'original':
            summary = f'共 {len(self.cues)} 条字幕，{pending} 条译文待补全。请先补全翻译，或切换为原文字幕导出。'
        else:
            summary = f'共 {len(self.cues)} 条字幕 · 可导出当前内容。\n建议先检查文字和时间轴。'
        if media and not available:
            summary += '\n原媒体缺失，暂时无法导出视频。'
        self.export_summary.setText(summary)
        self.start_button.setToolTip('请先导入媒体' if not media else self.model_status.text())
        self.update_editor_actions()

    def update_editor_actions(self):
        if self.loading_table:
            return
        rows = self.selected_rows()
        ready = not (self.busy() or getattr(self, '_ui_busy', False)) and self.project_status not in ('recognizing', 'aligning')
        consecutive = len(rows) >= 2 and rows == list(range(rows[0], rows[-1] + 1))
        self.merge_button.setEnabled(ready and consecutive)
        self.split_button.setEnabled(ready and len(rows) == 1)
        self.retranslate_button.setEnabled(ready and bool(rows) and bool(self.project))
        self.undo_button.setEnabled(ready and bool(self.history.undo_stack))
        self.redo_button.setEnabled(ready and bool(self.history.redo_stack))
        self.selection_label.setText(f'已选 {len(rows)} 条 · 右键查看可用操作' if rows
                                     else '单击定位 · 双击编辑 · 空格播放 · 修改自动保存')

    def busy(self):
        return self.worker is not None and self.worker.state() != QProcess.NotRunning

    def refresh_caption(self, *args):
        if hasattr(self, 'player'):
            self.position_changed(self.player.position())

    def style_changed(self, style):
        self.preview.set_style(style)
        self.refresh_caption()
        if hasattr(self, 'style_save_timer'):
            self.style_save_timer.start(400)

    def style_moved(self, style):
        self.style_panel.set_style(style)
        self.style_save_timer.start(400)

    def flush_style_edits(self):
        if self.style_save_timer.isActive():
            self.style_save_timer.stop()
            self.save_edits()

    def notify(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet('color: #B54B43;' if error else '')

    def refresh_models(self):
        online = self.translation_backend.currentData() == 'online'
        state = readiness({**self.model_selection, 'translation_backend': self.translation_backend.currentData()})
        count = sum(state.values())
        name = Path(self.model_selection['asr_model']).name
        issue = asr_language_issue(self.model_selection['asr_model'], self.language.currentData())
        self.model_status.setText(f'{name}\n' + (issue or ('●  本地模型已就绪' if count == len(state) else f'模型准备中：{count} / {len(state)} 项就绪')))
        if online:
            settings = load_api_settings()
            self.model_status.setText((issue or name) + '\n在线翻译：' + (api_issue() or settings['model']))
        self.translation_note.setText('本地识别 · 在线翻译' if online else '本地识别 · 本地翻译')
        uses_api = online and (self.mode.currentData() or self.active_online_translation)
        self.network_badge.setText('●  字幕文本将发送至在线 API' if uses_api else '●  完全本地处理')
        badge = 'onlineBadge' if uses_api else 'localBadge'
        if self.network_badge.objectName() != badge:
            self.network_badge.setObjectName(badge)
            self.network_badge.style().unpolish(self.network_badge)
            self.network_badge.style().polish(self.network_badge)
        self.api_button.setEnabled(not self.busy())
        self.model_status.setToolTip('\n'.join(f'{name}：' + ('已就绪' if ready else '待下载') for name, ready in state.items()))
        if online:
            self.model_status.setToolTip(settings['base_url'] + f'\n在线翻译：每组最多 {settings["batch_size"]} 条字幕，可在 API 设置中调整。\n' + self.model_status.toolTip())
        busy = self.busy() or getattr(self, '_ui_busy', False)
        media = self.video is not None and self.video.is_file()
        models = state['语音识别'] and state['时间轴对齐'] and state.get('扩展识别引擎', True)
        self.start_button.setEnabled(media and not busy and models and not issue)
        # A disabled primary action states the one thing still missing.
        if busy or self.start_button.isEnabled():
            reason = ''
        elif self.video is None:
            reason = '导入视频或音频后即可开始生成'
        elif not media:
            reason = '原媒体已移动或删除，恢复后才能重新生成'
        elif issue:
            reason = '请更改「媒体语言」，或在「管理模型」中切换识别模型'
        else:
            reason = '识别或时间轴模型未就绪 · 点击「管理模型」下载或选择'
        self.start_hint.setText(reason)
        self.context.setPlaceholderText('可选：人名、地名、专业词汇' if asr_backend(self.model_selection['asr_model']) == 'qwen' else '当前识别模型不支持术语提示')
        self.context.setEnabled(not self.busy() and asr_backend(self.model_selection['asr_model']) == 'qwen')
        self.refresh_workspace()

    def translation_backend_changed(self):
        backend = self.translation_backend.currentData()
        try:
            save_translation_backend(backend)
            self.save_edits()
        except OSError:
            self.notify('无法保存翻译方式，请检查设置文件权限。', error=True)
        self.refresh_models()

    def configure_api(self):
        if self.busy():
            return
        from .api_dialog import ApiDialog
        dialog = ApiDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.translation_backend.blockSignals(True)
            self.translation_backend.setCurrentIndex(self.translation_backend.findData('online'))
            self.translation_backend.blockSignals(False)
            self.translation_backend_changed()
            self.notify('在线翻译设置已保存，可生成双语字幕或继续翻译。')

    def language_changed(self):
        if self.language.currentData() == 'English' and self.max_chars.value() == 30:
            self.max_chars.setValue(68)
        elif self.language.currentData() != 'English' and self.max_chars.value() == 68:
            self.max_chars.setValue(30)
        self.refresh_models()

    def choose_models(self):
        if self.busy():
            return
        from .model_dialog import ModelDialog
        dialog = ModelDialog(self.model_selection, self)
        if dialog.exec() == QDialog.Accepted:
            selection = dialog.selection()
            try:
                save_model_selection(selection)
            except OSError as exc:
                QMessageBox.warning(self, '无法保存模型设置', str(exc))
                return
            self.model_selection = selection
            self.refresh_models()
            self.notify('模型选择已保存，将用于下一次生成或继续翻译')

    def choose_video(self):
        if self.busy():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '选择视频或音频', str(self.video.parent if self.video else ROOT), MEDIA_FILTER)
        if filename:
            self.open_input_file(Path(filename))

    def load_video(self, path, clear=True):
        if self.busy():
            return
        if input_kind(path) != 'media':
            raise ValueError('这不是支持的视频或音频文件。SRT 字幕请在载入视频后拖入。')
        self.flush_style_edits()
        self.player.stop()
        self.video = Path(path).resolve()
        self.file_name.setText(self.video.name)
        self.file_name.setToolTip(str(self.video))
        self.preview.empty.hide()
        self.preview.set_caption('')
        self.player.setSource(QUrl.fromLocalFile(str(self.video)))
        self.track.blockSignals(True)
        self.track.clear()
        try:
            tracks = audio_tracks(self.video)
        except Exception:
            tracks = []
        for i, text in enumerate(tracks or ['默认音轨']):
            self.track.addItem(f'第 {i + 1} 音轨 · ' + text.split('Audio:')[-1].strip()[:24], i)
            self.track.setItemData(i, text, Qt.ToolTipRole)
        self.track.blockSignals(False)
        if clear:
            self.cues, self.project = [], None
            self.history = EditHistory()
            self.project_status = 'complete'
            self.preview.set_video_size(QSizeF(1280, 720))
            self.fill_table()
            self.subtitle_search.clear()
            self.quality_filter.setCurrentIndex(0)
            self.go_to_step(0)
        self.progress_bar.setValue(0)
        self.percent.setText('')
        self.notify('视频已载入 · 可生成字幕，或拖入 SRT 导入字幕')
        self.refresh_models()

    def import_srt(self, path):
        if self.busy():
            raise ValueError('请先结束当前处理，再导入字幕。')
        if input_kind(path) != 'srt':
            raise ValueError('请选择 .srt 字幕文件。')
        if not self.video or not self.video.is_file() or input_kind(self.video) != 'media':
            raise ValueError('请先载入视频，再拖入 SRT 字幕。')
        # Parse and save the new project before replacing any in-memory subtitles.
        cues = read_srt(path)
        self.flush_style_edits()
        folder = OUTPUT / f'{self.video.stem[:40]}_导入字幕_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:4]}'
        project = folder / 'subtitles.yingzi.json'
        options = {**self.current_options(), 'export_mode': 'original'}
        save_project(project, self.video, cues, options, status='edited')
        self.history.remember(self.cues)
        self.cues, self.project, self.output_dir = cues, project, folder
        self.project_status = 'edited'
        self.subtitle_search.clear()
        self.export_mode.blockSignals(True)
        self.export_mode.setCurrentIndex(self.export_mode.findData('original'))
        self.export_mode.blockSignals(False)
        self.quality_filter.blockSignals(True)
        self.quality_filter.setCurrentIndex(0)
        self.quality_filter.blockSignals(False)
        self.fill_table()
        self.refresh_caption()
        self.notify(f'已导入 {len(cues)} 条 SRT 字幕 · 保留完整文本与换行 · 已另存为字幕工程，可撤销或导出视频')
        self.go_to_step(1)

    def open_input_file(self, path):
        if self.busy():
            return False
        try:
            kind = input_kind(path)
            if kind == 'project':
                self.load_saved_project(path)
            elif kind == 'srt':
                self.import_srt(path)
            elif kind == 'media':
                self.load_video(path)
            else:
                raise ValueError('不支持此文件类型，请选择视频、音频、SRT 字幕或 .yingzi.json 工程。')
        except Exception as exc:
            self.notify(f'无法打开拖入的文件：{exc}', error=True)
            return False
        return True

    def change_track(self, index):
        if hasattr(self, 'player') and index >= 0:
            self.player.setActiveAudioTrack(index)

    def toggle_play(self):
        if self.video:
            if self.player.playbackState() == QMediaPlayer.PlayingState:
                self.player.pause()
            else:
                self.preview_audio.refresh()
                self.player.play()

    def configure_preview_audio(self):
        self.preview_audio.refresh()
        dialog = PreviewAudioDialog(self.preview_audio, self)
        dialog.exec()
        dialog.deleteLater()

    def refresh_audio_button(self):
        volume = round(self.audio.volume() * 100)
        silent = self.audio.isMuted() or volume == 0
        self.sound_button.setText('已静音…' if silent else '声音…')
        self.sound_button.setToolTip(f'声音输出：{self.preview_audio.description()}\n预览音量：{volume}%' + (' · 已静音' if silent else ''))

    def position_changed(self, milliseconds):
        if not self.seek.isSliderDown():
            self.seek.setValue(milliseconds)
        def mmss(ms):
            seconds = max(0, ms // 1000)
            return f'{seconds // 60:02}:{seconds % 60:02}'
        self.time_label.setText(f'{mmss(milliseconds)} / {mmss(self.player.duration())}')
        seconds = milliseconds / 1000
        # Binary search keeps preview responsive for long movies.
        import bisect
        index = bisect.bisect_right(self.cue_starts, seconds) - 1
        current = self.cues[index] if 0 <= index < len(self.cues) and seconds < self.cues[index].end else None
        mode = self.export_mode.currentData()
        text = subtitle_text(current, mode) if current else ''
        if not text and self.settings_tabs.currentIndex() == 1 and self.style_panel.sample.isChecked():
            text = {'bilingual': 'Every voice deserves to be heard.\n让每一句，都有字幕。',
                    'original': 'Every voice deserves to be heard.', 'translated': '让每一句，都有字幕。'}[mode]
        original_lines = len(clean_line(current.text).splitlines()) if current else (len(text.splitlines()) if mode == 'original' else 1)
        self.preview.set_caption(text, mode, original_lines)
        if current and index != self.active_cue and self.table.state() != QAbstractItemView.EditingState:
            if len(self.table.selectionModel().selectedRows()) <= 1:
                self.table.selectRow(index)
            self.active_cue = index

    def seek_cue(self, row, column):
        if row < len(self.cues):
            self.player.setPosition(round(self.cues[row].start * 1000))
            self.waveform_row = row
            self.waveform.show()
            self.waveform.load(self.video, self.cues[row], self.track.currentData() or 0,
                self.cues[row - 1].end if row else 0, self.cues[row + 1].start if row + 1 < len(self.cues) else None)

    def change_waveform_range(self, start, end):
        index = getattr(self, 'waveform_row', -1)
        if not 0 <= index < len(self.cues):
            return
        result = copy.deepcopy(self.cues)
        result[index].start, result[index].end = start, end
        try:
            self.apply_cue_edit(result)
            self.table.selectRow(index)
        except ValueError as exc:
            self.notify(str(exc), error=True)
        self.seek_cue(index, 0)

    def configure_translation(self):
        from .translation_preferences import TranslationPreferences
        dialog = TranslationPreferences(self.translation_glossary, self.translation_prompt, self)
        if dialog.exec() == QDialog.Accepted:
            self.translation_glossary, self.translation_prompt = dialog.values()
            self.save_edits()

    def selected_rows(self):
        return sorted({item.row() for item in self.table.selectionModel().selectedRows() if not self.table.isRowHidden(item.row())})

    def apply_cue_edit(self, result):
        if self.busy() or self.project_status in ('recognizing', 'aligning'):
            return
        validate(result)
        self.history.remember(self.cues)
        self.cues = result
        self.save_edits()
        self.fill_table()
        self.refresh_caption()

    def undo_edit(self):
        if not self.busy():
            result = self.history.undo(self.cues)
            if result is not None:
                self.cues = result
                self.save_edits()
                self.fill_table()
                self.refresh_caption()

    def redo_edit(self):
        if not self.busy():
            result = self.history.redo(self.cues)
            if result is not None:
                self.cues = result
                self.save_edits()
                self.fill_table()
                self.refresh_caption()

    def merge_selected(self):
        try:
            self.apply_cue_edit(merge_cues(self.cues, self.selected_rows()))
        except ValueError as exc:
            self.notify(str(exc), error=True)

    def split_selected(self):
        rows = self.selected_rows()
        if len(rows) != 1:
            self.notify('请选择一条字幕进行拆分。')
            return
        index = rows[0]
        cue = self.cues[index]
        if len(cue.text) < 2 or cue.end - cue.start <= .1:
            self.notify('这条字幕过短，无法继续拆分。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('拆分字幕')
        dialog.resize(580, 240)
        form = QFormLayout(dialog)
        source = QLineEdit(cue.text)
        source.setReadOnly(True)
        source.setCursorPosition(len(cue.text) // 2)
        form.addRow('点击文字选择断点', source)
        at = DoubleSpinBox()
        at.setDecimals(3)
        at.setRange(cue.start + .05, cue.end - .05)
        position = self.player.position() / 1000
        at.setValue(position if cue.start < position < cue.end else (cue.start + cue.end) / 2)
        at.setSuffix(' 秒')
        form.addRow('拆分时间', at)
        preview = QLabel()
        preview.setTextFormat(Qt.PlainText)
        preview.setWordWrap(True)
        def update_preview():
            point = source.cursorPosition()
            preview.setText(cue.text[:point] + '\n────────\n' + cue.text[point:])
        source.cursorPositionChanged.connect(update_preview)
        update_preview()
        form.addRow('拆分预览', preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.apply_cue_edit(split_cue(self.cues, index, source.cursorPosition(), at.value()))
            except ValueError as exc:
                self.notify(str(exc), error=True)

    def retranslate_selected(self):
        rows = self.selected_rows()
        if not rows or not self.project or self.busy():
            self.notify('请选择需要重新翻译的字幕。')
            return
        self.save_edits()
        self.launch({**self.current_options(), 'video': str(self.video), 'output': str(self.project.parent),
                     'resume_project': str(self.project), 'bilingual': True, 'selected_ids': [i + 1 for i in rows]})

    def resegment(self):
        if not self.project or not cache_ready(self.project, complete=True) or self.busy():
            self.notify('此工程尚无完整对齐缓存，需要先生成字幕。')
            return
        self.save_edits()
        self.launch({**self.current_options(), 'video': str(self.video), 'output': str(self.project.parent),
                     'resume_project': str(self.project), 'resegment_only': True})

    STATUS_COLORS = {'待重试': '#B54B43', '待翻译': '#A86412', '需检查': '#C24B20', '正常': '#ADA59E'}

    def update_status_cell(self, row):
        cue = self.cues[row]
        issues = quality_issues(cue, self.cues[row - 1] if row else None)
        text = '待重试' if cue.translation_error else '待翻译' if needs_translation(cue) else '需检查' if issues else '正常'
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setToolTip('\n'.join(issues) or '未发现规则检查问题；译文含义仍需人工校对。')
        # Muted "正常" lets rows that need attention stand out while scanning.
        item.setForeground(QColor(self.STATUS_COLORS[text]))
        if text != '正常':
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        self.table.setItem(row, 5, item)

    def filter_rows(self):
        mode = self.quality_filter.currentIndex()
        query = self.subtitle_search.text().strip().casefold()
        visible = 0
        for row, cue in enumerate(self.cues):
            issues = quality_issues(cue, self.cues[row - 1] if row else None)
            hidden = ((mode == 1 and not needs_translation(cue)) or (mode == 2 and not issues)
                      or bool(query and query not in (cue.text + '\n' + cue.translation).casefold()))
            self.table.setRowHidden(row, hidden)
            visible += not hidden
        self.match_count.setText(f'{visible} / {len(self.cues)} 条' if self.cues else '')
        self.match_count.setVisible(bool(self.cues) and (bool(query) or mode != 0))
        self.update_editor_actions()

    def focus_search(self):
        if self.cues and self.subtitle_search.isVisible():
            self.subtitle_search.setFocus()
            self.subtitle_search.selectAll()

    def table_menu(self):
        menu = QMenu(self)
        rows = self.selected_rows()
        if len(rows) == 1 and self.video and self.video.is_file():
            menu.addAction('从此条播放', lambda: self.play_from(rows[0]))
            menu.addSeparator()
        for control in (self.split_button, self.merge_button, self.retranslate_button):
            menu.addAction(control.text(), control.click).setEnabled(control.isEnabled())
        menu.addSeparator()
        for control in (self.undo_button, self.redo_button):
            menu.addAction(control.text(), control.click).setEnabled(control.isEnabled())
        return menu

    def show_table_menu(self, point):
        if self.cues and self.table.indexAt(point).isValid():
            menu = self.table_menu()
            menu.exec(self.table.viewport().mapToGlobal(point))
            menu.deleteLater()

    def play_from(self, row):
        self.seek_cue(row, 0)
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self.toggle_play()

    def fill_table(self):
        self.cue_starts = [cue.start for cue in self.cues]
        self.waveform.clear()
        self.waveform.hide()
        self.waveform_row = -1
        self.loading_table = True
        self.table.setRowCount(len(self.cues))
        for row, cue in enumerate(self.cues):
            for column, text in enumerate([str(row + 1), timestamp(cue.start), timestamp(cue.end), cue.text, cue.translation]):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column < 3:
                    item.setForeground(QColor('#8C7B71'))
                self.table.setItem(row, column, item)
            self.update_status_cell(row)
        # Fit wrapped text instead of a fixed tall row, so more subtitles fit on screen.
        self.table.resizeRowsToContents()
        self.loading_table = False
        self.filter_rows()
        self.cue_count.setText(f'字幕  ·  {len(self.cues)} 条' if self.cues else '字幕列表')
        self.export_button.setEnabled(bool(self.cues) and not self.busy())
        self.export_video_button.setEnabled(bool(self.cues) and bool(self.video) and self.video.is_file() and not self.busy())
        incomplete = self.project_status in ('recognizing', 'aligning')
        self.retry.setText('继续处理' if incomplete else '继续翻译')
        resumable = bool(self.project) and (cache_ready(self.project) if incomplete else any(needs_translation(c) for c in self.cues))
        self.retry.setEnabled(resumable and not self.busy())
        self.retry.setVisible(resumable)
        self.active_cue = -1
        self.undo_button.setEnabled(bool(self.history.undo_stack) and not self.busy())
        self.redo_button.setEnabled(bool(self.history.redo_stack) and not self.busy())
        for control in (self.retranslate_button, self.merge_button, self.split_button):
            control.setEnabled(bool(self.cues) and not self.busy() and not incomplete)
        self.resegment_button.setEnabled(bool(self.project) and cache_ready(self.project, complete=True) and not self.busy())
        self.refresh_workspace()

    def edit_cue(self, item):
        if self.loading_table or item.row() >= len(self.cues):
            return
        cue = self.cues[item.row()]
        column = item.column()
        attribute = {1: 'start', 2: 'end', 3: 'text', 4: 'translation'}.get(column)
        if not attribute:
            return
        old = getattr(cue, attribute)
        before = copy.deepcopy(self.cues)
        try:
            value = parse_time(item.text()) if column in (1, 2) else item.text().strip()
            setattr(cue, attribute, value)
            validate(self.cues)
            if column == 3 and value != old:
                # Existing translations must not silently appear current after source edits.
                cue.translation = ''
            if column in (3, 4):
                cue.translation_error = ''
            self.save_edits()
            if value != old:
                self.history.remember(before)
        except Exception as exc:
            self.cues = before
            self.notify(str(exc), error=True)
        self.fill_table()
        self.position_changed(self.player.position())

    def save_edits(self):
        if self.project:
            save_project(self.project, self.video, self.cues, self.current_options(), status=self.project_status if self.project_status in ('recognizing', 'aligning') else 'edited')
            if not self.busy():
                self.notify('字幕与样式已保存 · 可导出字幕文件或合成视频')

    def current_options(self):
        characters = self.max_chars.value()
        return {'language': self.language.currentData(), 'bilingual': self.mode.currentData(), 'translation_backend': self.translation_backend.currentData(), 'track': self.track.currentData() or 0, 'device': self.device.currentData(), 'segmentation_mode': 'smart' if characters is None else 'fixed', 'max_chars': characters or (68 if self.language.currentData() == 'English' else 30), 'context': self.context.text().strip(), 'translation_glossary': self.translation_glossary, 'translation_prompt': self.translation_prompt, 'subtitle_style': self.preview.caption_style.to_dict(), 'export_mode': self.export_mode.currentData(), **self.model_selection}

    def start_job(self):
        if not self.video or self.busy():
            return
        issue = asr_language_issue(self.model_selection['asr_model'], self.language.currentData())
        if issue:
            self.notify(issue, error=True)
            return
        folder = OUTPUT / f'{self.video.stem[:45]}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:4]}'
        job = {**self.current_options(), 'video': str(self.video), 'output': str(folder)}
        self.cues, self.project = [], None
        self.history = EditHistory()
        self.fill_table()
        self.launch(job)

    def retry_translation(self):
        if self.project and not self.busy() and (self.project_status not in ('recognizing', 'aligning') or cache_ready(self.project)):
            self.save_edits()
            job = {**self.current_options(), 'video': str(self.video), 'output': str(self.project.parent), 'resume_project': str(self.project)}
            if self.project_status not in ('recognizing', 'aligning'):
                job['bilingual'] = True
            self.launch(job)

    def set_busy(self, busy):
        self._ui_busy = busy
        for widget in (self.choose, self.language, self.mode, self.translation_backend, self.api_button, self.track, self.device, self.max_chars, self.context, self.model_button, self.table, self.export_button, self.export_video_button, self.export_mode, self.style_panel, self.retry):
            widget.setEnabled(not busy)
        for control in self.editor_buttons + [self.translation_preferences_button]:
            control.setEnabled(not busy)
        self.waveform.setEnabled(not busy)
        self.preview.caption.setFlag(QGraphicsItem.ItemIsMovable, not busy)
        self.cancel.setVisible(busy)
        self.cancel.setText('取消导出' if self.job_kind == 'export' else '取消处理')
        self.start_button.setEnabled(not busy and self.video is not None)
        self.refresh_models()

    def launch(self, job):
        if job.get('resume_project'):
            self.history.remember(self.cues)
        self.job_kind = 'recognition'
        self.active_online_translation = job.get('translation_backend') == 'online' and job.get('bilingual', True)
        self.player.pause()
        self.output_dir = Path(job['output'])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        job_path = self.output_dir / 'job.json'
        atomic_write(job_path, json.dumps(job, ensure_ascii=False, indent=2))
        self.log_handle = (self.output_dir / 'run.log').open('ab')
        self.worker = QProcess(self)
        self.worker.setWorkingDirectory(str(ROOT))
        self.worker.setProgram(str(ROOT / '.venv' / 'Scripts' / 'python.exe'))
        self.worker.setArguments(['-u', '-m', 'app.worker', '--job', str(job_path)])
        self.buffer = b''
        self.terminal_event = False
        self.worker.readyReadStandardOutput.connect(self.read_events)
        self.worker.readyReadStandardError.connect(self.read_errors)
        self.worker.finished.connect(self.finished)
        self.worker.errorOccurred.connect(self.process_error)
        self.worker.start()
        self.set_busy(True)
        self.notify('正在启动字幕处理任务…')
        self.metrics_label.setText('')
        self.progress_bar.setValue(0)

    def read_events(self):
        self.buffer += bytes(self.worker.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            kind = event.get('type')
            if kind == 'progress':
                self.progress_bar.setValue(max(self.progress_bar.value(), event['percent']))
                self.percent.setText(f'{self.progress_bar.value()}%')
                self.notify(event['message'])
            elif kind == 'checkpoint':
                self.project = Path(event['project'])
                self.project_status = event.get('status', 'translating')
                _, self.cues = load_project(self.project)
                self.fill_table()
            elif kind == 'translation_update':
                self.loading_table = True
                try:
                    for update in event['updates']:
                        row = update['id'] - 1
                        if 0 <= row < len(self.cues):
                            self.cues[row].translation = update['translation']
                            self.cues[row].translation_error = update['error']
                            self.table.item(row, 4).setText(update['translation'])
                            self.table.resizeRowToContents(row)
                            self.update_status_cell(row)
                            if row + 1 < len(self.cues):
                                self.update_status_cell(row + 1)
                finally:
                    self.loading_table = False
                self.filter_rows()
                self.refresh_workspace()
                self.refresh_caption()
                tokens = event.get('tokens')
                eta = event.get('eta')
                self.metrics_label.setText(f'成功 {event["completed"]} · 待重试 {event["failed"]} · 请求 {event["requests"]} 次 · Token {tokens if tokens is not None else "服务未提供"} · 已用 {event["elapsed"]} 秒 · 预计剩余 {eta if eta is not None else "—"} 秒')
            elif kind == 'api_diagnostic':
                response = event.get('error_message') or event.get('finish_reason') or event.get('status') or '连接失败'
                retry = f' · {event["retry_in_seconds"]:g} 秒后重试' if 'retry_in_seconds' in event else ''
                self.metrics_label.setText(f'本轮请求 {event["requests"]} 次 · Token {event["tokens"] if event["tokens"] is not None else "服务未提供"} · 最近请求 {event["seconds"]:.1f} 秒 · 返回状态 {response}{retry}')
            elif kind == 'done':
                self.terminal_event = True
                self.project = Path(event['project'])
                self.project_status = event.get('status', 'complete')
                _, self.cues = load_project(self.project)
                self.fill_table()
                if self.cues:
                    self.go_to_step(1)
                self.notify(f'本轮结束 · {event["failed"]} 条待重试，点击“继续翻译”补全' if event.get('failed') else f'已生成 {event["count"]} 条字幕 · 用时 {event["seconds"]:.0f} 秒 · 已保存原文、中文和双语 SRT')
            elif kind == 'error':
                self.terminal_event = True
                self.notify(event['message'], error=True)
                if self.job_kind != 'export':
                    self.recover_checkpoint()
            elif kind == 'export_done':
                self.terminal_event = True
                self.notify(f'视频已导出：{event["target"]}')
            elif kind == 'export_cancelled':
                self.terminal_event = True
                self.notify('已取消视频导出。')

    def read_errors(self):
        content = bytes(self.worker.readAllStandardError())
        if self.log_handle:
            self.log_handle.write(content)
            self.log_handle.flush()

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.active_online_translation = False
            self.terminal_event = True
            self.notify('处理程序无法启动，请运行 setup.cmd 修复环境。', error=True)
            if self.job_kind == 'export':
                self.cleanup_export()
            self.set_busy(False)
            self.fill_table()

    def recover_checkpoint(self):
        candidate = self.output_dir / 'subtitles.yingzi.json'
        if candidate.exists():
            try:
                data, self.cues = load_project(candidate)
                self.project = candidate
                self.project_status = data.get('status', 'complete')
                self.fill_table()
            except Exception:
                pass

    def finished(self, code, status):
        self.read_events()
        self.read_errors()
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        if not self.terminal_event:
            name = 'video-export.log' if self.job_kind == 'export' else 'run.log'
            self.notify(f'处理意外结束，请打开输出文件夹查看 {name}。', error=True)
            if self.job_kind != 'export':
                self.recover_checkpoint()
        if self.job_kind == 'export':
            self.cleanup_export()
        self.active_online_translation = False
        self.set_busy(False)
        self.fill_table()

    def cancel_job(self):
        if self.busy():
            if self.job_kind == 'export' and self.export_work:
                (Path(self.export_work.name) / 'cancel').touch()
                self.notify('正在取消视频导出…')
                return
            self.terminal_event = True
            # Only terminate this application-owned worker and its child model process.
            if self.worker.processId():
                subprocess.run(['taskkill', '/PID', str(self.worker.processId()), '/T', '/F'], capture_output=True, creationflags=CREATE_NO_WINDOW)
            else:
                self.worker.kill()
            self.notify('已取消处理，已完成的字幕工程保留在输出文件夹。')
            self.recover_checkpoint()

    def open_project(self):
        if self.busy():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '打开字幕工程', str(OUTPUT), '映字工程 (*.yingzi.json);;JSON (*.json)')
        if filename:
            try:
                self.load_saved_project(filename)
            except Exception as exc:
                QMessageBox.warning(self, '无法打开工程', str(exc))

    def load_saved_project(self, filename):
        self.flush_style_edits()
        data, cues = load_project(filename)
        path = Path(data['video'])
        if path.is_file():
            self.load_video(path, clear=False)
        else:
            self.player.stop()
            self.player.setSource(QUrl())
            self.video = path
            self.file_name.setText('原视频已移动，可继续编辑字幕')
        self.project, self.cues = Path(filename), cues
        self.project_status = data.get('status', 'complete')
        self.output_dir = self.project.parent
        options = data.get('options', {})
        self.translation_backend.blockSignals(True)
        self.translation_backend.setCurrentIndex(self.translation_backend.findData('online' if options.get('translation_backend') == 'online' else 'local'))
        self.translation_backend.blockSignals(False)
        style = CaptionStyle.from_dict(options.get('subtitle_style'))
        self.style_panel.set_style(style)
        self.preview.set_style(style)
        self.export_mode.setCurrentIndex(max(0, self.export_mode.findData(options.get('export_mode', 'bilingual'))))
        for combo, key in [(self.language, 'language'), (self.mode, 'bilingual'), (self.track, 'track'), (self.device, 'device')]:
            index = combo.findData(options.get(key))
            if index >= 0:
                combo.setCurrentIndex(index)
        if options.get('segmentation_mode') == 'smart':
            self.max_chars.setCurrentIndex(0)
        else:
            self.max_chars.setValue(int(options.get('max_chars') or (68 if self.language.currentData() == 'English' else 30)))
        self.context.setText(options.get('context', ''))
        self.translation_glossary = str(options.get('translation_glossary', ''))[:6000]
        self.translation_prompt = str(options.get('translation_prompt', ''))[:6000]
        self.history = EditHistory()
        self.model_selection.update({key: value for key, value in options.items() if key in MODEL_DEFAULTS and isinstance(value, str) and value.strip()})
        self.subtitle_search.clear()
        self.quality_filter.setCurrentIndex(0)
        self.fill_table()
        self.refresh_caption()
        self.notify(f'已打开字幕工程 · {len(cues)} 条字幕')
        self.refresh_models()
        self.go_to_step(1 if self.cues else 0)

    def export(self):
        if not self.cues:
            return
        if self.project_status in ('recognizing', 'aligning'):
            QMessageBox.information(self, '视频处理尚未完成', '此工程只包含部分字幕，请重新生成字幕后再导出完整视频字幕。')
            return
        mode = self.export_mode.currentData()
        incomplete = mode != 'original' and any(needs_translation(c) for c in self.cues)
        if incomplete:
            QMessageBox.information(self, '译文尚未完成', '部分日语或英语字幕没有中文译文，请点击“继续翻译”，或选择“原文字幕”导出。')
            return
        stem = self.video.stem if self.video else 'subtitles'
        filename, selection = QFileDialog.getSaveFileName(self, '导出字幕', str(self.output_dir / f'{stem}.{mode}.srt'), 'SubRip 字幕 (*.srt);;WebVTT 字幕 (*.vtt)')
        if filename:
            try:
                target = Path(filename)
                format = 'vtt' if target.suffix.lower() == '.vtt' else 'srt'
                atomic_write(target, render(self.cues, mode, format))
                self.notify(f'已导出：{target}')
            except Exception as exc:
                QMessageBox.warning(self, '导出失败', str(exc))

    def can_export_video(self):
        if self.busy() or not self.cues:
            return False
        if self.project_status in ('recognizing', 'aligning'):
            QMessageBox.information(self, '时间轴尚未完成', '请重新生成字幕，完成时间轴后再导出视频。')
            return False
        if not self.video or not self.video.is_file():
            QMessageBox.information(self, '找不到原视频', '请将原视频放回工程记录的位置后再导出。')
            return False
        if self.export_mode.currentData() != 'original' and any(needs_translation(cue) for cue in self.cues):
            QMessageBox.information(self, '译文尚未完成', '请继续翻译，或选择“原文字幕”后导出视频。')
            return False
        return True

    def export_video(self):
        if not self.can_export_video():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('合并字幕并导出视频')
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        summary = label(f'字幕内容：{self.export_mode.currentText()}\n字幕将按预览样式直接绘制到视频画面中。')
        summary.setWordWrap(True)
        layout.addWidget(summary)
        form = QFormLayout()
        controls = {}
        for key, title, choices in [
            ('encoder', '编码方式', [('自动选择显卡 / CPU', 'auto'), ('NVIDIA 显卡（不可用时回退 CPU）', 'nvidia'), ('CPU 兼容', 'cpu')]),
            ('resolution', '分辨率', [('原始分辨率', 'original'), ('最高 1080p', '1080'), ('最高 720p', '720')]),
            ('crf', '画质', [('高画质', 18), ('均衡', 23), ('较小文件', 28)]),
            ('preset', '编码速度', [('快速', 'fast'), ('均衡', 'medium'), ('更慢 / 文件更小', 'slow')])]:
            combo = QComboBox()
            for name, value in choices:
                combo.addItem(name, value)
            form.addRow(title, combo)
            controls[key] = combo
        audio = QCheckBox('保留全部音轨（取消勾选则仅导出当前音轨）')
        audio.setChecked(True)
        layout.addLayout(form)
        layout.addWidget(audio)
        note = label('输出 MP4 · H.264 视频 / AAC 音频\n开始后可查看进度或取消导出。', 'muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('选择保存位置…')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        filename, _ = QFileDialog.getSaveFileName(self, '导出字幕视频', str(self.output_dir / f'{self.video.stem}.字幕版.mp4'), 'MP4 视频 (*.mp4)')
        if not filename:
            return
        target = Path(filename)
        if not target.suffix:
            target = target.with_suffix('.mp4')
        try:
            self.start_video_export(target, {**{key: control.currentData() for key, control in controls.items()}, 'all_audio': audio.isChecked()})
        except Exception as exc:
            QMessageBox.warning(self, '无法导出视频', str(exc))

    def start_video_export(self, target, options=None):
        if not self.can_export_video():
            return
        target = Path(target).resolve()
        if target == self.video.resolve() or (target.exists() and target.samefile(self.video)):
            raise ValueError('请使用不同的文件名，不能覆盖原视频。')
        if target.suffix.lower() != '.mp4':
            raise ValueError('请使用 .mp4 文件扩展名。')
        validate(self.cues)
        self.style_save_timer.stop()
        self.save_edits()
        self.player.pause()
        self.export_parent = target.parent
        self.export_work = tempfile.TemporaryDirectory(prefix='.yingzi-export-', dir=target.parent)
        work = Path(self.export_work.name)
        try:
            job = {**(options or {}), 'video': str(self.video), 'target': str(target),
                   'mode': self.export_mode.currentData(), 'track': self.track.currentData() or 0,
                   'subtitle_style': self.preview.caption_style.to_dict(),
                   'cues': [dataclasses.asdict(cue) for cue in self.cues]}
            job_path = work / 'job.json'
            atomic_write(job_path, json.dumps(job, ensure_ascii=False, indent=2))
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.log_handle = (self.output_dir / 'video-export.log').open('ab')
        except Exception:
            self.cleanup_export()
            raise
        self.job_kind = 'export'
        self.worker = QProcess(self)
        self.worker.setWorkingDirectory(str(ROOT))
        self.worker.setProgram(str(ROOT / '.venv' / 'Scripts' / 'python.exe'))
        self.worker.setArguments(['-u', '-m', 'app.video_export', '--job', str(job_path)])
        self.buffer = b''
        self.terminal_event = False
        self.worker.readyReadStandardOutput.connect(self.read_events)
        self.worker.readyReadStandardError.connect(self.read_errors)
        self.worker.finished.connect(self.finished)
        self.worker.errorOccurred.connect(self.process_error)
        self.worker.start()
        self.set_busy(True)
        self.progress_bar.setValue(0)
        self.percent.setText('0%')
        self.notify('正在准备字幕视频导出…')

    def cleanup_export(self):
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        if self.export_work:
            work = Path(self.export_work.name).resolve()
            if work.parent != self.export_parent or not work.name.startswith('.yingzi-export-'):
                raise ValueError('导出临时目录与预期位置不一致。')
            log = work / 'ffmpeg.log'
            if log.exists():
                with (self.output_dir / 'video-export.log').open('ab') as saved:
                    saved.write(log.read_bytes())
            self.export_work.cleanup()
            self.export_work = None

    def open_output(self):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def show_logs(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('运行日志')
        dialog.resize(920, 550)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        path = self.output_dir / ('video-export.log' if self.job_kind == 'export' else 'run.log')
        if not path.exists():
            path = LOGS / 'download.log'
        content.setPlainText(path.read_text(encoding='utf-8', errors='replace')[-40000:] if path.exists() else '暂无日志。')
        diagnostic = self.output_dir / 'api-diagnostics.jsonl'
        if self.job_kind != 'export' and diagnostic.exists():
            content.appendPlainText('\n—— API 请求诊断（不含密钥及思考文本）——\n' + diagnostic.read_text(encoding='utf-8')[-30000:])
        layout.addWidget(content)
        dialog.exec()

    def showEvent(self, event):
        super().showEvent(event)
        # Route file drops before graphics views or text editors consume them.
        # Ordinary mouse drags (including caption positioning) are unaffected.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if (event.type() in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop)
                and isinstance(watched, QWidget) and watched.window() is self
                and event.mimeData().hasUrls()):
            if event.type() == QEvent.Drop:
                self.dropEvent(event)
            else:
                self.dragEnterEvent(event)
            return True
        return super().eventFilter(watched, event)

    def dropped_file(self, event):
        if self.busy() or not (event.possibleActions() & Qt.CopyAction):
            return None
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.is_file() and input_kind(path):
                    return path
        return None

    def dragEnterEvent(self, event):
        event.ignore()
        path = self.dropped_file(event)
        if path is not None:
            if input_kind(path) == 'srt' and (not self.video or not self.video.is_file() or input_kind(self.video) != 'media'):
                self.notify('请先载入视频，再拖入 SRT 字幕。')
                return
            event.setDropAction(Qt.CopyAction)
            event.accept()
        elif not self.busy() and event.mimeData().urls():
            self.notify('支持拖入视频、音频、SRT 字幕或 .yingzi.json 工程；SRT 需先载入视频。')

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        event.ignore()
        path = self.dropped_file(event)
        if path is None:
            return
        if not self.open_input_file(path):
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def closeEvent(self, event):
        if self.busy():
            answer = QMessageBox.question(self, '正在处理视频', '关闭应用会取消当前处理。已经完成的字幕会保留。确定关闭？')
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_job()
            if not self.worker.waitForFinished(5000):
                self.notify('正在结束后台进程，请稍后再关闭窗口。')
                event.ignore()
                return
        self.style_save_timer.stop()
        try:
            self.save_edits()
        except OSError as exc:
            self.notify(f'工程保存失败：{exc}', error=True)
            event.ignore()
            return
        self.player.stop()
        event.accept()
        self.waveform.clear()
        QApplication.instance().removeEventFilter(self)


def main():
    app = QApplication(sys.argv)
    initialize_fonts(app)
    app.setApplicationName('映字')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    lock = QLockFile(str(CACHE / 'yingzi.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, '映字已启动', '映字已经在运行，请切换到现有窗口。')
        return 0
    window = Studio()
    window.show()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.is_file():
            QTimer.singleShot(200, lambda: window.open_input_file(path))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
