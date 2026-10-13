import dataclasses
import hashlib
import copy
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QTimer, QSizeF, QLockFile, Signal, QPointF, QEvent, QItemSelectionModel
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QShortcut, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QLineEdit, QFileDialog, QMessageBox, QTableWidgetItem,
    QGraphicsView, QGraphicsScene, QGraphicsTextItem, QAbstractItemView,
    QPlainTextEdit, QDialog, QGraphicsPixmapItem, QGraphicsItem,
    QFormLayout, QDialogButtonBox, QCheckBox, QMenu, QStackedWidget, QInputDialog, QAbstractItemDelegate)

from .config import ROOT, CACHE, PROJECTS, LOGS, readiness, load_model_selection, save_model_selection, model_paths
from .project_library import ProjectLibrary
from .project_home import ProjectHome, NewProjectDialog
from .project_session import ProjectSession
from .job_runner import JobRunner
from .media import audio_tracks
from .subtitles import timestamp, parse_time, validate, render, atomic_write, load_project, subtitle_text, needs_translation, clean_line, project_workspace
from .caption_style import CaptionStyle, render_caption, caption_position
from .api_settings import save_translation_backend, load_api_settings, api_issue
from .widgets import ComboBox as QComboBox, DoubleSpinBox
from .pipeline import cache_ready
from .editing import EditHistory, merge_cues, split_cue, quality_issues
from .preview_audio import PreviewAudio, PreviewAudioDialog
from .subtitle_import import input_kind, read_srt, MEDIA_FILTER, AUDIO_SUFFIXES
from .workflow import next_step


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
        self._restore_position = None
        self.last_export_target = ''
        self.library = ProjectLibrary(PROJECTS)
        self.jobs = JobRunner(self)
        self.jobs.event.connect(self.handle_event)
        self.jobs.ended.connect(self.job_ended)
        self.jobs.failedToStart.connect(self.job_failed_to_start)
        self.session = ProjectSession(self.library)
        # Looked up on each call so a replaced busy() (tests, future guards) still applies.
        self.session.worker_owns = lambda: self.busy()
        self.cues = []
        self.cue_starts = []
        self.history = EditHistory()
        self.translation_glossary = ''
        self.translation_prompt = ''
        self.active_online_translation = False
        self.export_settings = {}
        self.loading_table = False
        self.active_cue = -1
        self.loop_row = None
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
        self.style_save_timer.timeout.connect(self.autosave_project)
        self.player.positionChanged.connect(self.position_changed)
        self.player.positionChanged.connect(self.schedule_position_save)
        self.player.durationChanged.connect(lambda value: self.seek.setRange(0, value))
        self.player.mediaStatusChanged.connect(self.restore_playback_position)
        self.player.playbackStateChanged.connect(lambda state: self.play.setText('暂停' if state == QMediaPlayer.PlayingState else '播放'))
        self.player.errorOccurred.connect(lambda error, text: self.notify(f'预览提示：{text}。可继续尝试生成字幕。', error=True))
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.refresh_models()
        self.model_timer = QTimer(self)
        self.model_timer.timeout.connect(self.refresh_models)
        self.model_timer.start(10000)
        QShortcut(QKeySequence.Save, self, activated=self.save_current_project)
        QShortcut(QKeySequence('Ctrl+Shift+S'), self, activated=self.save_project_as)
        QShortcut(QKeySequence('Ctrl+Shift+O'), self, activated=self.open_project)
        QShortcut(QKeySequence('Ctrl+N'), self, activated=self.new_project)
        QShortcut(QKeySequence('Ctrl+O'), self, activated=self.choose_video)
        QShortcut(QKeySequence.Undo, self, activated=self.undo_edit)
        QShortcut(QKeySequence.Redo, self, activated=self.redo_edit)
        QShortcut(QKeySequence.Find, self, activated=self.focus_search)
        QShortcut(QKeySequence('Alt+Up'), self, activated=lambda: self.navigate_cue(-1))
        QShortcut(QKeySequence('Alt+Down'), self, activated=lambda: self.navigate_cue(1))
        QShortcut(QKeySequence('Alt+Left'), self, activated=lambda: self.skip_preview(-2))
        QShortcut(QKeySequence('Alt+Right'), self, activated=lambda: self.skip_preview(2))
        QShortcut(QKeySequence('Ctrl+Return'), self, activated=self.review_and_next)
        QShortcut(QKeySequence('Ctrl+Enter'), self, activated=self.review_and_next)
        # Space plays only from the list or picture; text fields and cell editors keep it.
        for widget in (self.table, self.preview):
            QShortcut(QKeySequence(Qt.Key_Space), widget, activated=lambda: self.toggle_play(),
                      context=Qt.WidgetWithChildrenShortcut)
        # Wrapped rows are refit after column widths settle, not on every resize step.
        self.row_fit_timer = QTimer(self)
        self.row_fit_timer.setSingleShot(True)
        self.row_fit_timer.timeout.connect(self.table.resizeRowsToContents)
        self.table.horizontalHeader().sectionResized.connect(lambda *args: self.row_fit_timer.start(80))
        for control in (self.language, self.mode, self.translation_backend, self.track, self.device,
                        self.max_chars, self.export_mode, self.quality_filter):
            control.currentIndexChanged.connect(self.schedule_project_save)
        self.settings_tabs.currentChanged.connect(self.schedule_project_save)
        self.context.textChanged.connect(self.schedule_project_save)
        self.subtitle_search.textChanged.connect(self.schedule_project_save)
        self.table.itemSelectionChanged.connect(self.schedule_project_save)
        self.preview_toggle.toggled.connect(self.schedule_project_save)
        self.player.playbackStateChanged.connect(self.schedule_project_save)
        self.player.playbackRateChanged.connect(self.schedule_project_save)

    def build_ui(self):
        build_studio(self, VideoView)
        editor = self.takeCentralWidget()
        self.project_home = ProjectHome(self.library, self)
        self.screen_stack = QStackedWidget(self)
        self.screen_stack.addWidget(self.project_home)
        self.screen_stack.addWidget(editor)
        self.screen_stack.setCurrentIndex(1)
        self.setCentralWidget(self.screen_stack)
        self.project_home.openRequested.connect(self.open_library_project)
        self.project_home.newRequested.connect(self.new_project)
        self.project_home.mediaRequested.connect(self.choose_video)
        self.project_home.importRequested.connect(self.import_project_file)
        self.project_home.renameRequested.connect(self.rename_library_project)
        self.project_home.trashRequested.connect(self.trash_library_project)
        self.project_home.restoreRequested.connect(self.restore_library_project)
        self.project_home.resumeRequested.connect(self.show_project_editor)

    def show_project_editor(self):
        self.project_home.thumbnails.stop()
        self.screen_stack.setCurrentIndex(1)
        self.refresh_workspace()

    def show_project_library(self):
        if self.busy():
            return
        try:
            self.player.pause()
            self.style_save_timer.stop()
            self.save_edits()
            self.project_home.resume.setVisible(bool(self.session.project))
            self.screen_stack.setCurrentIndex(0)
            self.project_home.refresh()
            self.setWindowTitle('映字 · 工程库')
        except (OSError, ValueError) as exc:
            self.notify(f'无法保存当前工程：{exc}', error=True)

    def new_project(self):
        if self.busy():
            return
        dialog = NewProjectDialog(self.library.root, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            media = Path(dialog.media.text()) if dialog.media.text() else None
            if media and (not media.is_file() or input_kind(media) != 'media'):
                raise ValueError('请选择有效的视频或音频文件。')
            self.style_save_timer.stop()
            self.save_edits()
            path = self.library.create(dialog.name.text(), media, options=self.current_options(), editor={'step': 0})
            self.load_saved_project(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '无法新建工程', str(exc))

    def open_library_project(self, path):
        if not self.busy():
            try:
                self.load_saved_project(path)
            except (OSError, ValueError, TypeError) as exc:
                self.project_home.notice.setText(f'无法打开工程：{exc}')

    def import_project_file(self):
        if self.busy():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '导入映字工程', str(self.library.root), '映字工程 (*.yingziproj)')
        if filename:
            self.open_input_file(Path(filename))

    def rename_library_project(self, path):
        if self.busy():
            return
        info = self.library.info(path)
        name, accepted = QInputDialog.getText(self, '重命名工程', '工程名称', text=info.name)
        if not accepted:
            return
        try:
            self.style_save_timer.stop()
            self.save_edits()
            self.project_home.thumbnails.stop()
            target = self.library.rename(path, name)
            if self.session.is_open(path):
                self.session.project = target
                self.refresh_workspace()
            self.project_home.refresh()
            self.project_home.notice.setText(f'已重命名为「{name.strip()}」')
        except (OSError, ValueError) as exc:
            self.project_home.notice.setText(f'重命名失败：{exc}')

    def trash_library_project(self, path):
        if self.busy():
            return
        name = self.library.info(path).name
        if QMessageBox.question(self, '移至回收站', f'将「{name}」移至工程回收站？\n字幕、缓存及工程目录内的文件会一起移入，可以随时恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        active = self.session.is_open(path)
        suspended = False
        try:
            self.style_save_timer.stop()
            self.save_edits()
            self.project_home.thumbnails.stop()
            # Release Windows media handles before moving a folder containing the source.
            folder = self.library.folder(path)
            if self.session.video and self.session.video.resolve().is_relative_to(folder):
                self.waveform.clear()
                self.player.stop()
                self.player.setSource(QUrl())
                suspended = True
            self.library.trash(path)
            if active:
                self.session.project, self.session.video, self.cues = None, None, []
                self.player.stop()
                self.player.setSource(QUrl())
                self._restore_position = None
                self.history = EditHistory()
                self.session.status = 'complete'
                self.fill_table()
                self.file_name.setText('新建字幕工程')
                self.project_home.resume.hide()
            self.project_home.refresh()
            self.project_home.notice.setText(f'「{name}」已移至回收站，需要时可恢复。')
        except (OSError, ValueError) as exc:
            if suspended and self.session.project:
                self.load_saved_project(self.session.project)
            self.project_home.notice.setText(f'无法移除工程：{exc}')

    def restore_library_project(self, path):
        if self.busy():
            return
        try:
            target = self.library.restore(path)
            self.project_home.show_trash(False)
            self.project_home.notice.setText(f'已恢复「{self.library.info(target).name}」')
        except (OSError, ValueError) as exc:
            self.project_home.notice.setText(f'无法恢复工程：{exc}')

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

    def toggle_edit_tools(self, expanded):
        self.editor_tools.setVisible(expanded and bool(self.cues))
        self.edit_tools_toggle.setText('收起修改工具' if expanded else '更多修改工具')

    def toggle_preview(self, collapsed):
        if collapsed:
            self._preview_sizes = self.splitter.sizes()
        self.preview_stack.setVisible(not collapsed)
        self.playback_controls.setVisible(not collapsed and bool(self.session.video))
        self.preview_toggle.setText('展开画面' if collapsed else '收起画面')
        self.splitter.setSizes([60, self.splitter.height() - 60] if collapsed else getattr(self, '_preview_sizes', [390, 330]))

    def choose_subtitles(self):
        if self.busy():
            return
        if not self.session.video or not self.session.video.is_file():
            self.notify('请先导入视频或音频，再导入 SRT 字幕。')
            return
        filename, _ = QFileDialog.getOpenFileName(self, '导入 SRT 字幕', str(self.session.video.parent), 'SRT 字幕 (*.srt)')
        if filename and self.open_input_file(Path(filename)):
            self.go_to_step(1)

    def export_content_changed(self):
        self.refresh_caption()
        self.refresh_workspace()

    def refresh_workspace(self):
        """Present the next useful action without changing processing state."""
        busy = self.busy() or getattr(self, '_ui_busy', False)
        media = bool(self.session.video)
        available = media and self.session.video.is_file()
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
        self.edit_toolbar.setVisible(has_cues)
        self.empty_import_srt.setVisible(available and not has_cues)
        self.empty_import_srt.setEnabled(available and not busy)
        self.welcome_import.setEnabled(not busy)
        self.open_project_button.setEnabled(not busy)
        self.save_project_button.setEnabled(bool(self.session.video or self.session.project) and not busy)
        self.save_as_action.setEnabled(bool(self.session.video or self.session.project) and not busy)
        self.relink_action.setEnabled(bool(self.session.video) and not busy)
        self.history_action.setEnabled(bool(self.session.project) and not busy)
        self.proof_toolbar.setVisible(has_cues)
        self.setWindowTitle('映字 · 工程库' if self.screen_stack.currentIndex() == 0 else f'{self.session.project.name} · 映字' if self.session.project else '映字 · 双语字幕工作台')
        if self.session.project:
            self.file_name.setText(self.library.metadata(self.session.project).get('name') or self.session.project.stem)
            self.file_name.setToolTip(f'工程：{self.session.project}\n媒体：{self.session.video or "尚未导入"}')
        self.save_project_button.setToolTip(('工程：' + str(self.session.project) + '\n自动保存字幕和编辑进度 · Ctrl+S · 第 3 步保存可播放或分享的成品') if self.session.project else '保存字幕和编辑进度 · Ctrl+S')
        bilingual = self.translation_required()
        self.table.setColumnHidden(4, not (bilingual and any(c.language in ('Japanese', 'English') or c.translation for c in self.cues)))
        self.translation_group.setVisible(bilingual)
        self.chinese_note.setVisible(self.language.currentData() == 'Chinese')
        self.api_button.setVisible(self.translation_backend.currentData() == 'online')
        setup = getattr(self, '_translation_setup_needed', False)
        self.start_button.setText('先设置中文翻译' if setup else '重新生成字幕' if has_cues else
                                 '生成中文字幕' if self.language.currentData() == 'Chinese' else '开始生成字幕')
        self.edit_tools_toggle.setVisible(has_cues)
        self.editor_tools.setVisible(has_cues and self.edit_tools_toggle.isChecked())
        pending = sum(needs_translation(cue) for cue in self.cues)
        incomplete = self.session.status in ('recognizing', 'aligning')
        export_ready = has_cues and not incomplete and (self.export_mode.currentData() == 'original' or not pending)
        self.export_button.setEnabled(export_ready and not busy)
        video = available and self.session.video.suffix.lower() not in AUDIO_SUFFIXES
        self.export_video_button.setEnabled(export_ready and video and not busy)
        self.export_content.setText('当前内容：' + self.export_mode.currentText())
        self.export_content_choice.blockSignals(True)
        self.export_content_choice.setCurrentIndex(self.export_content_choice.findData(self.export_mode.currentData()))
        self.export_content_choice.blockSignals(False)
        self.export_content_choice.setEnabled(not busy)
        if busy:
            hint = '正在导出视频，可在下方取消' if self.jobs.kind == 'export' else '正在处理，下方显示实时进度'
        elif not media:
            hint = '先导入一个视频或音频'
        elif not available:
            hint = '原媒体缺失 · 字幕仍可编辑和导出'
        elif incomplete:
            hint = '任务尚未完成 · 点击继续处理'
        elif not has_cues:
            hint = '设置已就绪后，点击「生成字幕」'
        elif pending and (bilingual or self.export_mode.currentData() != 'original'):
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
        elif media and not video:
            summary += '\n当前是音频文件，请保存字幕文件。'
        self.export_summary.setText(summary)
        self.start_button.setToolTip('请先导入媒体' if not media else self.model_status.text())
        self._guide = next_step(step=self.settings_tabs.currentIndex(), busy=busy,
            exporting=self.jobs.kind == 'export', media=media, available=available, video=video,
            count=len(self.cues), pending=pending if bilingual or self.export_mode.currentData() != 'original' else 0,
            incomplete=incomplete,
            resumable=bool(self.session.project) and incomplete and cache_ready(self.session.project),
            ready=getattr(self, '_generation_ready', False),
            translation_setup=setup, online=self.translation_backend.currentData() == 'online',
            original=self.export_mode.currentData() == 'original')
        self.guide_title.setText(self._guide.title)
        self.guide_hint.setText(self._guide.hint)
        self.guide_button.setText(self._guide.label)
        self.guide_button.setEnabled(bool(self._guide.action))
        self.guide_alternative.setText(self._guide.alternative_label)
        self.guide_alternative.setVisible(bool(self._guide.alternative_label))
        self.guide_alternative.setEnabled(not busy)
        self.update_editor_actions()

    def run_guided_action(self, alternative=False):
        if self.busy() or getattr(self, '_ui_busy', False):
            return
        self.refresh_workspace()
        action = self._guide.alternative if alternative else self._guide.action
        if action == 'original_generate':
            self.mode.setCurrentIndex(self.mode.findData(False))
            self.export_mode.setCurrentIndex(self.export_mode.findData('original'))
            self.start_job()
        elif action == 'original_export':
            self.export_mode.setCurrentIndex(self.export_mode.findData('original'))
            self.go_to_step(2)
        elif action == 'language':
            self.go_to_step(0)
            self.language.setFocus()
            self.language.showPopup()
        else:
            callback = {'import': self.choose_video, 'relink': self.relink_media,
                        'models': self.choose_models, 'translation': self.setup_translation,
                        'generate': self.start_with_guidance, 'resume': self.continue_with_guidance,
                        'review': lambda: self.go_to_step(1), 'export_step': lambda: self.go_to_step(2),
                        'video_export': self.export_video, 'export': self.export}.get(action)
            if callback:
                callback()

    def translation_required(self):
        return bool(self.mode.currentData()) and self.language.currentData() != 'Chinese'

    def generation_content_changed(self):
        if not self.session.loading:
            self.export_mode.setCurrentIndex(self.export_mode.findData('bilingual' if self.mode.currentData() else 'original'))
        self.refresh_models()
        if self.table.rowCount() == len(self.cues):
            for row in range(len(self.cues)):
                self.update_status_cell(row)
            self.filter_rows()

    def setup_translation(self):
        if self.translation_backend.currentData() == 'online':
            self.configure_api()
        else:
            self.choose_models()

    def start_with_guidance(self):
        if not self.session.video or self.busy() or getattr(self, '_ui_busy', False):
            return
        if getattr(self, '_translation_setup_needed', False):
            self.setup_translation()
            return
        self.start_job()

    def continue_with_guidance(self):
        if self.busy() or getattr(self, '_ui_busy', False):
            return
        if self.session.status not in ('recognizing', 'aligning') and any(needs_translation(c) for c in self.cues):
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(self.mode.findData(True))
            self.mode.blockSignals(False)
            self.refresh_models()
            if self._translation_setup_needed:
                self.setup_translation()
                return
        self.retry_translation()

    def update_editor_actions(self):
        if self.loading_table:
            return
        rows = self.selected_rows()
        ready = not (self.busy() or getattr(self, '_ui_busy', False)) and self.session.status not in ('recognizing', 'aligning')
        consecutive = len(rows) >= 2 and rows == list(range(rows[0], rows[-1] + 1))
        self.merge_button.setEnabled(ready and consecutive)
        self.split_button.setEnabled(ready and len(rows) == 1)
        self.retranslate_button.setEnabled(ready and bool(rows) and bool(self.session.project))
        self.undo_button.setEnabled(ready and bool(self.history.undo_stack))
        self.redo_button.setEnabled(ready and bool(self.history.redo_stack))
        for control in (self.previous_cue, self.next_cue, self.review_button, self.review_next_button):
            control.setEnabled(ready and bool(rows))
        self.review_button.setText('取消已校对' if rows and all(self.cues[row].reviewed for row in rows) else '标记已校对')
        self.loop_current.setEnabled(ready and bool(rows) and bool(self.session.video and self.session.video.is_file()))
        self.auto_advance.setEnabled(ready and bool(self.cues))
        self.selection_label.setText(f'已选 {len(rows)} 条 · 右键查看可用操作' if rows
                                     else '单击定位 · 双击编辑 · 空格播放 · 修改自动保存')

    def busy(self):
        return self.jobs.busy()

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

    def schedule_project_save(self, *args):
        if self.session.can_save():
            self.style_save_timer.start(400)

    def schedule_position_save(self, *args):
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            self.schedule_project_save()

    def autosave_project(self):
        try:
            self.save_edits()
        except (OSError, ValueError) as exc:
            self.notify(f'工程自动保存失败：{exc}。请使用「另存工程」选择可写位置。', error=True)

    def restore_playback_position(self, status):
        if self._restore_position is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            position, self._restore_position = self._restore_position, None
            self.player.setPosition(min(position, self.player.duration()) if self.player.duration() > 0 else position)

    def notify(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet('color: #B54B43;' if error else '')

    def refresh_models(self):
        self.model_selection = {key: str(path) for key, path in model_paths(self.model_selection).items()}
        online = self.translation_backend.currentData() == 'online'
        state = readiness({**self.model_selection, 'translation_backend': self.translation_backend.currentData()})
        if not self.translation_required():
            state = {name: ready for name, ready in state.items() if name not in ('字幕翻译', '翻译引擎')}
        count = sum(state.values())
        name = Path(self.model_selection['asr_model']).name
        self.model_status.setText('●  识别功能已准备好' if count == len(state) else f'需要准备功能：{count} / {len(state)} 项就绪')
        if online and self.translation_required():
            settings = load_api_settings()
            self.model_status.setText('●  识别与翻译已准备好' if count == len(state) else '中文翻译尚未设置好')
        self.translation_note.setText('识别在本机完成；翻译使用你设置的在线服务。' if online else '翻译功能下载好后，可在本机离线使用。')
        uses_api = online and (self.translation_required() or self.active_online_translation)
        self.network_badge.setText('●  字幕文本将发送至在线 API' if uses_api else '●  完全本地处理')
        badge = 'onlineBadge' if uses_api else 'localBadge'
        if self.network_badge.objectName() != badge:
            self.network_badge.setObjectName(badge)
            self.network_badge.style().unpolish(self.network_badge)
            self.network_badge.style().polish(self.network_badge)
        self.api_button.setEnabled(not self.busy())
        self.model_status.setToolTip(name + '\n' + '\n'.join(f'{name}：' + ('已就绪' if ready else '待准备') for name, ready in state.items()))
        if online and self.translation_required():
            self.model_status.setToolTip(settings['base_url'] + f'\n在线翻译：每组最多 {settings["batch_size"]} 条字幕，可在 API 设置中调整。\n' + self.model_status.toolTip())
        busy = self.busy() or getattr(self, '_ui_busy', False)
        media = self.session.video is not None and self.session.video.is_file()
        models = state['语音识别'] and state['时间轴对齐']
        self._generation_ready = models
        self._translation_setup_needed = self.translation_required() and not (state.get('字幕翻译', True) and state.get('翻译引擎', True))
        self.start_button.setEnabled(media and not busy and models)
        # A disabled primary action states the one thing still missing.
        if busy:
            reason = ''
        elif self.session.video is None:
            reason = '导入视频或音频后即可开始生成'
        elif not media:
            reason = '原媒体已移动或删除，恢复后才能重新生成'
        elif self._translation_setup_needed:
            reason = '中文翻译尚未准备好。先完成设置，或点击上方「先生成原文字幕」。'
        elif self.start_button.isEnabled():
            reason = ''
        else:
            reason = '识别或时间轴模型未就绪 · 点击「管理模型」下载或选择'
        self.start_hint.setText(reason)
        self.context.setPlaceholderText('可选：人名、地名、专业词汇')
        self.context.setEnabled(not self.busy())
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
            self.save_edits()
            self.refresh_models()
            self.notify('模型选择已保存，将用于下一次生成或继续翻译')

    def choose_video(self):
        if self.busy():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '选择视频或音频', str(self.session.video.parent if self.session.video else ROOT), MEDIA_FILTER)
        if filename:
            self.open_input_file(Path(filename))

    def load_video(self, path, clear=True):
        if self.busy():
            return
        if input_kind(path) != 'media':
            raise ValueError('这不是支持的视频或音频文件。SRT 字幕请在载入视频后拖入。')
        attach_to_draft = clear and self.session.project is not None and self.session.video is None and not self.cues and self.session.status == 'draft'
        self.flush_style_edits()
        if clear:
            self.save_edits()
        self.player.stop()
        self._restore_position = None
        self.session.video = Path(path).resolve()
        self.file_name.setText(self.session.video.name)
        self.file_name.setToolTip(str(self.session.video))
        self.preview.empty.hide()
        self.preview.set_caption('')
        self.player.setSource(QUrl.fromLocalFile(str(self.session.video)))
        self.track.blockSignals(True)
        self.track.clear()
        try:
            tracks = audio_tracks(self.session.video)
        except Exception:
            tracks = []
        for i, text in enumerate(tracks or ['默认音轨']):
            self.track.addItem(f'第 {i + 1} 音轨 · ' + text.split('Audio:')[-1].strip()[:24], i)
            self.track.setItemData(i, text, Qt.ToolTipRole)
        self.track.blockSignals(False)
        if clear:
            self.export_settings = {}
            self.cues = []
            if not attach_to_draft:
                self.session.project = None
            self.history = EditHistory()
            self.session.status = 'draft'
            self.last_export_target = ''
            self.preview.set_video_size(QSizeF(1280, 720))
            self.fill_table()
            self.subtitle_search.clear()
            self.quality_filter.setCurrentIndex(0)
            self.go_to_step(0)
            if not attach_to_draft:
                self.create_project()
            self.save_edits()
        self.progress_bar.setValue(0)
        self.percent.setText('')
        self.notify('视频已载入 · 可生成字幕，或拖入 SRT 导入字幕')
        self.refresh_models()

    def import_srt(self, path):
        if self.busy():
            raise ValueError('请先结束当前处理，再导入字幕。')
        if input_kind(path) != 'srt':
            raise ValueError('请选择 .srt 字幕文件。')
        if not self.session.video or not self.session.video.is_file() or input_kind(self.session.video) != 'media':
            raise ValueError('请先载入视频，再拖入 SRT 字幕。')
        # Parse and save the new project before replacing any in-memory subtitles.
        cues = read_srt(path)
        self.flush_style_edits()
        self.save_edits()
        options = {**self.current_options(), 'export_mode': 'original'}
        project = self.library.create(f'{self.session.video.stem[:80]} · 导入字幕', self.session.video, cues, options, status='edited',
                     editor={**self.editor_state(), 'step': 1, 'selected_rows': [], 'search': '', 'quality_filter': 0})
        folder = project_workspace(project)
        self.history.record(self.cues, cues)
        self.cues, self.session.project, self.session.output_dir = cues, project, folder
        self.session.status = 'edited'
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
                self.load_saved_project(self.library.adopt(path))
            elif kind == 'srt':
                self.import_srt(path)
            elif kind == 'media':
                self.load_video(path)
            else:
                raise ValueError('不支持此文件类型，请选择视频、音频、SRT 字幕或 .yingziproj 工程。')
        except Exception as exc:
            self.notify(f'无法打开拖入的文件：{exc}', error=True)
            return False
        self.screen_stack.setCurrentIndex(1)
        return True

    def change_track(self, index):
        if hasattr(self, 'player') and index >= 0:
            self.player.setActiveAudioTrack(index)

    def toggle_play(self):
        if self.session.video:
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
        if (self.loop_current.isChecked() and self.loop_row is not None and 0 <= self.loop_row < len(self.cues)
                and self.player.playbackState() == QMediaPlayer.PlayingState):
            cue = self.cues[self.loop_row]
            if milliseconds >= round(cue.end * 1000) or milliseconds < round(cue.start * 1000):
                self.player.setPosition(round(cue.start * 1000))
                return
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
        if 0 <= row < len(self.cues):
            self.loop_row = row
            self.player.setPosition(round(self.cues[row].start * 1000))
            self.waveform_row = row
            self.waveform.show()
            self.waveform.load(self.session.video, self.cues[row], self.track.currentData() or 0,
                self.cues[row - 1].end if row else 0, self.cues[row + 1].start if row + 1 < len(self.cues) else None)

    def change_waveform_range(self, start, end):
        index = getattr(self, 'waveform_row', -1)
        if not 0 <= index < len(self.cues):
            return
        result = list(self.cues)
        result[index] = dataclasses.replace(self.cues[index], start=start, end=end, reviewed=False)
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
        if self.busy() or self.session.status in ('recognizing', 'aligning'):
            return
        validate(result)
        previous = self.cues
        self.cues = result
        try:
            self.save_edits()
        except (OSError, ValueError) as exc:
            self.cues = previous
            self.notify(f'无法保存此次编辑：{exc}', error=True)
            return False
        self.history.record(previous, result)
        self.refresh_changed_cues(previous)
        self.refresh_caption()
        return True

    def undo_edit(self):
        if not self.busy():
            result = self.history.undo(self.cues)
            if result is not None:
                previous = self.cues
                self.cues = result
                try:
                    self.save_edits()
                except (OSError, ValueError) as exc:
                    self.cues = previous
                    self.history.undo_stack.append(self.history.redo_stack.pop())
                    self.notify(f'无法保存此次撤销：{exc}', error=True)
                    return
                self.refresh_changed_cues(previous)
                self.refresh_caption()

    def redo_edit(self):
        if not self.busy():
            result = self.history.redo(self.cues)
            if result is not None:
                previous = self.cues
                self.cues = result
                try:
                    self.save_edits()
                except (OSError, ValueError) as exc:
                    self.cues = previous
                    self.history.redo_stack.append(self.history.undo_stack.pop())
                    self.notify(f'无法保存此次重做：{exc}', error=True)
                    return
                self.refresh_changed_cues(previous)
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
        if not rows or not self.session.project or self.busy():
            self.notify('请选择需要重新翻译的字幕。')
            return
        self.save_edits()
        self.session.snapshot('重译所选前')
        self.launch({**self.current_options(), 'video': str(self.session.video), 'output': str(self.session.output_dir),
                     'resume_project': str(self.session.project), 'bilingual': True, 'selected_ids': [i + 1 for i in rows]})

    def resegment(self):
        if not self.session.project or not cache_ready(self.session.project, complete=True) or self.busy():
            self.notify('此工程尚无完整对齐缓存，需要先生成字幕。')
            return
        self.save_edits()
        self.session.snapshot('重新分段前')
        self.launch({**self.current_options(), 'video': str(self.session.video), 'output': str(self.session.output_dir),
                     'resume_project': str(self.session.project), 'resegment_only': True})

    STATUS_COLORS = {'待重试': '#B54B43', '待翻译': '#A86412', '需检查': '#C24B20', '正常': '#ADA59E'}

    def cue_issues(self, row):
        issues = quality_issues(self.cues[row], self.cues[row - 1] if row else None)
        if not self.translation_required() and self.export_mode.currentData() == 'original':
            issues = [issue for issue in issues if issue != '待翻译' and not issue.startswith('待重试：')]
        return issues

    def update_status_cell(self, row):
        cue = self.cues[row]
        issues = self.cue_issues(row)
        translation_expected = self.translation_required() or self.export_mode.currentData() != 'original'
        text = '待重试' if cue.translation_error and translation_expected else '待翻译' if needs_translation(cue) and translation_expected else '需检查' if issues else '正常'
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
        review = QTableWidgetItem('已校对' if cue.reviewed else '未校对')
        review.setFlags(review.flags() & ~Qt.ItemIsEditable)
        review.setForeground(QColor('#39745C' if cue.reviewed else '#ADA59E'))
        self.table.setItem(row, 6, review)

    def filter_rows(self):
        mode = self.quality_filter.currentIndex()
        query = self.subtitle_search.text().strip().casefold()
        visible = 0
        for row, cue in enumerate(self.cues):
            issues = self.cue_issues(row)
            hidden = ((mode == 1 and not needs_translation(cue)) or (mode == 2 and not issues)
                      or (mode == 3 and cue.reviewed) or (mode == 4 and not cue.reviewed)
                      or bool(query and query not in (cue.text + '\n' + cue.translation).casefold()))
            self.table.setRowHidden(row, hidden)
            visible += not hidden
        self.match_count.setText(f'{visible} / {len(self.cues)} 条' if self.cues else '')
        self.match_count.setVisible(bool(self.cues) and (bool(query) or mode != 0))
        self.update_editor_actions()

    def focus_search(self):
        if self.screen_stack.currentIndex() == 0:
            self.project_home.search.setFocus()
            self.project_home.search.selectAll()
            return
        if self.cues and self.subtitle_search.isVisible():
            self.subtitle_search.setFocus()
            self.subtitle_search.selectAll()

    def table_menu(self):
        menu = QMenu(self)
        rows = self.selected_rows()
        if len(rows) == 1 and self.session.video and self.session.video.is_file():
            menu.addAction('从此条播放', lambda: self.play_from(rows[0]))
            menu.addSeparator()
        for control in (self.split_button, self.merge_button, self.retranslate_button):
            menu.addAction(control.text(), control.click).setEnabled(control.isEnabled())
        menu.addAction(self.review_button.text(), self.mark_reviewed).setEnabled(self.review_button.isEnabled())
        menu.addAction('确认并下一条', self.review_and_next).setEnabled(self.review_next_button.isEnabled())
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

    def commit_cell_edit(self):
        editor = QApplication.focusWidget()
        self._cell_commit_ok = True
        if self.table.state() == QAbstractItemView.EditingState and editor and self.table.isAncestorOf(editor):
            self._committing_navigation = True
            try:
                self.table.itemDelegate().commitData.emit(editor)
                self.table.itemDelegate().closeEditor.emit(editor, QAbstractItemDelegate.NoHint)
            finally:
                self._committing_navigation = False
        return self._cell_commit_ok

    def navigate_cue(self, direction, anchor=None):
        if self.busy() or not self.cues:
            return
        rows = self.selected_rows()
        if anchor is None:
            anchor = rows[0] if rows else (-1 if direction > 0 else len(self.cues))
        if not self.commit_cell_edit():
            return
        for row in range(anchor + direction, len(self.cues) if direction > 0 else -1, direction):
            if not self.table.isRowHidden(row):
                self.table.clearSelection()
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 3))
                self.seek_cue(row, 0)
                return row
        self.notify('已到最后一条可见字幕。' if direction > 0 else '已到第一条可见字幕。')

    def skip_preview(self, seconds):
        if self.session.video and not self.busy():
            position = max(0, self.player.position() + round(seconds * 1000))
            if self.player.duration() > 0:
                position = min(position, self.player.duration())
            self.player.setPosition(position)

    def toggle_cue_loop(self, enabled):
        if not enabled:
            self.loop_row = None
            return
        rows = self.selected_rows()
        if rows and not self.busy() and self.session.video and self.session.video.is_file():
            self.seek_cue(rows[0], 0)
            self.preview_audio.refresh()
            self.player.play()
        else:
            self.loop_current.setChecked(False)

    def mark_reviewed(self):
        rows = self.selected_rows()
        if rows:
            self.set_reviewed(rows, not all(self.cues[row].reviewed for row in rows))

    def set_reviewed(self, rows, value):
        if self.busy() or self.session.status in ('recognizing', 'aligning'):
            return
        result = list(self.cues)
        for row in rows:
            result[row] = dataclasses.replace(result[row], reviewed=value)
        return self.apply_cue_edit(result)

    def review_and_next(self):
        if self.busy():
            return
        rows = self.selected_rows()
        if len(rows) != 1:
            return
        row = rows[0]
        if not self.commit_cell_edit():
            return
        if self.set_reviewed([row], True):
            self.navigate_cue(1, anchor=row)

    def fill_table(self):
        self.cue_starts = [cue.start for cue in self.cues]
        self.waveform.clear()
        self.waveform.hide()
        self.waveform_row = -1
        self.loop_row = None
        self.loop_current.setChecked(False)
        self.active_cue = -1
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
        self.refresh_table_actions()

    def refresh_table_actions(self):
        self.cue_count.setText(f'字幕  ·  {len(self.cues)} 条' if self.cues else '字幕列表')
        self.export_button.setEnabled(bool(self.cues) and not self.busy())
        self.export_video_button.setEnabled(bool(self.cues) and bool(self.session.video) and self.session.video.is_file() and not self.busy())
        incomplete = self.session.status in ('recognizing', 'aligning')
        self.retry.setText('继续处理' if incomplete else '继续翻译')
        resumable = bool(self.session.project) and (cache_ready(self.session.project) if incomplete else any(needs_translation(c) for c in self.cues))
        self.retry.setEnabled(resumable and not self.busy())
        self.retry.setVisible(resumable)
        self.undo_button.setEnabled(bool(self.history.undo_stack) and not self.busy())
        self.redo_button.setEnabled(bool(self.history.redo_stack) and not self.busy())
        for control in (self.retranslate_button, self.merge_button, self.split_button):
            control.setEnabled(bool(self.cues) and not self.busy() and not incomplete)
        self.resegment_button.setEnabled(bool(self.session.project) and cache_ready(self.session.project, complete=True) and not self.busy())
        self.refresh_workspace()

    def edit_cue(self, item):
        if self.loading_table or item.row() >= len(self.cues):
            return
        row = item.row()
        cue = self.cues[row]
        column = item.column()
        attribute = {1: 'start', 2: 'end', 3: 'text', 4: 'translation'}.get(column)
        if not attribute:
            return
        old = getattr(cue, attribute)
        changed = False
        try:
            value = parse_time(item.text()) if column in (1, 2) else item.text().strip()
            if value == old:
                return
            updated = dataclasses.replace(cue, **{attribute: value}, reviewed=False)
            if column == 3 and value != old:
                # Existing translations must not silently appear current after source edits.
                updated.translation = ''
            if column in (3, 4):
                updated.translation_error = ''
            # Only this cue and its neighbours can acquire invalid timing or overlap.
            validate(self.cues[max(0, row - 1):row] + [updated] + self.cues[row + 1:row + 2])
            self.cues[row] = updated
            self.save_edits()
            self.history.record_change(row, [cue], [updated])
            changed = True
        except Exception as exc:
            self.cues[row] = cue
            self._cell_commit_ok = False
            self.notify(str(exc), error=True)
        self.refresh_cue_rows([row])
        self.position_changed(self.player.position())
        if changed and self.auto_advance.isChecked() and not getattr(self, '_committing_navigation', False):
            QTimer.singleShot(0, lambda: self.navigate_cue(1, anchor=row))

    def refresh_changed_cues(self, previous):
        if len(previous) != len(self.cues):
            self.fill_table()
        else:
            self.refresh_cue_rows([row for row, (old, new) in enumerate(zip(previous, self.cues)) if old != new])

    def refresh_cue_rows(self, rows):
        affected = {row for row in rows if 0 <= row < len(self.cues)}
        affected.update(row + 1 for row in list(affected) if row + 1 < len(self.cues))
        self.loading_table = True
        try:
            for row in sorted(affected):
                cue = self.cues[row]
                self.cue_starts[row] = cue.start
                for column, value in enumerate([timestamp(cue.start), timestamp(cue.end), cue.text, cue.translation], 1):
                    self.table.item(row, column).setText(value)
                self.update_status_cell(row)
                self.table.resizeRowToContents(row)
        finally:
            self.loading_table = False
        waveform_row = getattr(self, 'waveform_row', -1)
        if 0 <= waveform_row < len(self.cues) and self.waveform.interval:
            cue = self.cues[waveform_row]
            lower = self.cues[waveform_row - 1].end if waveform_row else 0
            upper = self.cues[waveform_row + 1].start if waveform_row + 1 < len(self.cues) else None
            if not self.waveform.update_range(cue.start, cue.end, lower, upper):
                self.waveform.load(self.session.video, cue, self.track.currentData() or 0, lower, upper)
        self.filter_rows()
        self.refresh_table_actions()

    def save_edits(self):
        if self.session.can_save():
            self.session.save(self.cues, self.current_options(), self.editor_state())

    def editor_state(self):
        return {'position_ms': self._restore_position if self._restore_position is not None else self.player.position(),
                'selected_rows': self.selected_rows(), 'step': self.settings_tabs.currentIndex(),
                'preview_collapsed': self.preview_toggle.isChecked(), 'search': self.subtitle_search.text(),
                'quality_filter': self.quality_filter.currentIndex(), 'last_export_target': self.last_export_target,
                'playback_rate': self.playback_speed.currentData(), 'auto_advance': self.auto_advance.isChecked()}

    def create_project(self):
        self.session.create(self.current_options())

    def save_current_project(self):
        if self.busy() or not (self.session.video or self.session.project):
            return
        try:
            if not self.session.project:
                self.create_project()
            self.style_save_timer.stop()
            self.save_edits()
            self.refresh_workspace()
            self.notify(f'工程已保存：{self.session.project}')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '无法保存工程', str(exc))

    def save_project_as(self):
        if self.busy() or not self.session.project:
            return
        name, accepted = QInputDialog.getText(self, '另存工程副本', '副本名称', text=f'{self.library.info(self.session.project).name} · 副本')
        if not accepted:
            return
        try:
            self.style_save_timer.stop()
            self.save_edits()
            target = self.session.clone(name)
            self.refresh_workspace()
            self.notify(f'工程已另存：{target} · 后续编辑自动保存到此文件')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '无法另存工程', str(exc))

    def show_project_versions(self):
        if self.busy() or not self.session.project:
            return
        from .version_dialog import VersionDialog
        from .project_versions import restore_version
        try:
            self.flush_style_edits()
            self.save_edits()
            dialog = VersionDialog(self.session.project, self)
            if dialog.exec() != QDialog.Accepted or dialog.selected_version() is None:
                return
            if QMessageBox.question(self, '恢复历史版本', '恢复所选版本？当前内容会先保留为新的历史版本。',
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.style_save_timer.stop()
            project = self.session.project
            restore_version(project, dialog.selected_version())
            self.load_saved_project(project)
            self.notify('已恢复历史版本；恢复前的内容仍可从「工程历史版本」找回。')
        except (OSError, ValueError, TypeError) as exc:
            self.notify(f'无法恢复历史版本：{exc}', error=True)

    def relink_media(self):
        if self.busy() or not self.session.video:
            return
        filename, _ = QFileDialog.getOpenFileName(self, '重新关联工程的原媒体', str(self.session.video.parent), MEDIA_FILTER)
        if not filename:
            return
        try:
            track = self.track.currentData() or 0
            position = self._restore_position if self._restore_position is not None else self.player.position()
            selected = self.selected_rows()
            self.load_video(filename, clear=False)
            self.track.setCurrentIndex(max(0, self.track.findData(track)))
            self._restore_position = position
            self.restore_playback_position(self.player.mediaStatus())
            self.fill_table()
            for row in selected:
                self.table.selectionModel().select(self.table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.save_edits()
            self.notify('已重新关联媒体 · 字幕、样式与编码设置已保留')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '无法关联媒体', str(exc))

    def current_options(self):
        characters = self.max_chars.value()
        return {'language': self.language.currentData(), 'bilingual': self.mode.currentData(), 'translation_backend': self.translation_backend.currentData(), 'track': self.track.currentData() or 0, 'device': self.device.currentData(), 'segmentation_mode': 'smart' if characters is None else 'fixed', 'max_chars': characters or (68 if self.language.currentData() == 'English' else 30), 'context': self.context.text().strip(), 'translation_glossary': self.translation_glossary, 'translation_prompt': self.translation_prompt, 'subtitle_style': self.preview.caption_style.to_dict(), 'export_mode': self.export_mode.currentData(), 'video_export': self.export_settings.copy(), **self.model_selection}

    def start_job(self):
        if not self.session.video or self.busy():
            return
        if not self.session.project:
            self.create_project()
        self.save_edits()
        self.session.snapshot('重新生成前')
        job = {**self.current_options(), 'video': str(self.session.video), 'output': str(self.session.fresh_workspace())}
        self.cues = []
        self.session.status = 'recognizing'
        self.save_edits()
        self.history = EditHistory()
        self.fill_table()
        self.launch(job)

    def retry_translation(self):
        if self.session.project and not self.busy() and (self.session.status not in ('recognizing', 'aligning') or cache_ready(self.session.project)):
            self.save_edits()
            job = {**self.current_options(), 'video': str(self.session.video), 'output': str(self.session.output_dir), 'resume_project': str(self.session.project)}
            if self.session.status not in ('recognizing', 'aligning'):
                job['bilingual'] = True
            self.launch(job)

    def set_busy(self, busy):
        self._ui_busy = busy
        for widget in (self.choose, self.language, self.mode, self.translation_backend, self.api_button, self.track, self.device, self.max_chars, self.context, self.model_button, self.table, self.export_button, self.export_video_button, self.export_mode, self.style_panel, self.retry):
            widget.setEnabled(not busy)
        for control in self.editor_buttons + [self.translation_preferences_button]:
            control.setEnabled(not busy)
        self.waveform.setEnabled(not busy)
        self.playback_speed.setEnabled(not busy)
        self.preview.caption.setFlag(QGraphicsItem.ItemIsMovable, not busy)
        self.cancel.setVisible(busy)
        self.cancel.setText('取消导出' if self.jobs.kind == 'export' else '取消处理')
        self.start_button.setEnabled(not busy and self.session.video is not None)
        self.refresh_models()

    def launch(self, job):
        if job.get('resume_project'):
            self.history.remember(self.cues)
        self.active_online_translation = job.get('translation_backend') == 'online' and job.get('bilingual', True)
        self._job_translation = job.get('bilingual', True) and job.get('language') != 'Chinese' and not job.get('resegment_only')
        self.player.pause()
        self.style_save_timer.stop()
        job['project_path'] = str(self.session.project)
        job['editor_state'] = self.editor_state()
        self.session.output_dir = Path(job['output'])
        self.jobs.start_processing(job, self.session.output_dir)
        self.set_busy(True)
        self.notify('正在启动字幕处理任务…')
        self.metrics_label.setText('')
        self.progress_bar.setValue(0)

    def handle_event(self, event):
        kind = event.get('type')
        if kind == 'progress':
            self.progress_bar.setValue(max(self.progress_bar.value(), event['percent']))
            self.percent.setText(f'{self.progress_bar.value()}%')
            percent = event['percent']
            message = ('正在保存带字幕的视频…' if self.jobs.kind == 'export' else
                       '正在读取视频里的声音…' if percent < 15 else
                       '正在听写视频中的说话内容…' if percent < 54 else
                       '正在让字幕跟上说话时间…' if percent < 75 else
                       '正在翻译成中文…' if getattr(self, '_job_translation', self.translation_required()) else '正在保存字幕…')
            self.status.setToolTip(event['message'])
            self.notify(message)
        elif kind == 'checkpoint':
            self.cues = self.session.checkpoint(event['project'], event.get('status', 'translating'))
            self.fill_table()
        elif kind == 'translation_update':
            self.loading_table = True
            try:
                for update in event['updates']:
                    row = update['id'] - 1
                    if 0 <= row < len(self.cues):
                        self.cues[row].translation = update['translation']
                        self.cues[row].translation_error = update['error']
                        self.cues[row].reviewed = False
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
            eta = event.get('eta')
            self.metrics_label.setText(f'已翻译 {event["completed"]} 条 · 待重试 {event["failed"]} 条 · 已用 {event["elapsed"]} 秒' +
                                       (f' · 预计还需 {eta} 秒' if eta is not None else ''))
            self.metrics_label.setToolTip(f'请求 {event["requests"]} 次 · Token {event.get("tokens", "服务未提供")}')
        elif kind == 'api_diagnostic':
            response = event.get('error_message') or event.get('finish_reason') or event.get('status') or '连接失败'
            retry = f' · {event["retry_in_seconds"]:g} 秒后重试' if 'retry_in_seconds' in event else ''
            self.metrics_label.setText('翻译服务暂时没有返回结果' + retry if retry else '正在等待翻译服务返回结果…')
            self.metrics_label.setToolTip(f'请求 {event["requests"]} 次 · 最近请求 {event["seconds"]:.1f} 秒 · 返回状态 {response}')
        elif kind == 'done':
            self.cues = self.session.checkpoint(event['project'], event.get('status', 'complete'))
            self.fill_table()
            if self.cues:
                self.go_to_step(1)
            self.notify(f'还有 {event["failed"]} 条中文字幕待补全，点击「补全中文字幕」继续。' if event.get('failed') else
                        f'已生成 {event["count"]} 条字幕，用时 {event["seconds"]:.0f} 秒。工程已自动保存，可试听、改错，再保存成品。')
        elif kind == 'error':
            self.notify(event['message'], error=True)
            if self.jobs.kind != 'export':
                self.recover_checkpoint()
        elif kind == 'export_done':
            self.notify(f'视频已导出：{event["target"]}')
        elif kind == 'export_cancelled':
            self.notify('已取消视频导出。')

    def job_failed_to_start(self):
        self.active_online_translation = False
        self.history.finish(self.cues)
        self.notify('处理程序无法启动，请运行 setup.cmd 修复环境。', error=True)
        self.set_busy(False)
        self.fill_table()

    def recover_checkpoint(self):
        cues = self.session.recover()
        if cues is not None:
            self.cues = cues
            self.fill_table()

    def job_ended(self, unexpected):
        if unexpected:
            name = 'video-export.log' if self.jobs.kind == 'export' else 'run.log'
            self.notify(f'处理意外结束，请打开输出文件夹查看 {name}。', error=True)
            if self.jobs.kind != 'export':
                self.recover_checkpoint()
        self.active_online_translation = False
        self.history.finish(self.cues)
        self.set_busy(False)
        self.fill_table()

    def cancel_job(self):
        if self.busy():
            if self.jobs.cancel():
                self.notify('正在取消视频导出…')
                return
            self.notify('已取消处理，已完成的字幕工程保留在输出文件夹。')
            self.recover_checkpoint()

    def open_project(self):
        self.show_project_library()

    def load_saved_project(self, filename):
        if self.busy():
            return
        data, cues = load_project(filename)
        options = data.get('options', {})
        editor = data.get('editor', {})
        # Validate values before replacing the open project or its player.
        style = CaptionStyle.from_dict(options.get('subtitle_style'))
        characters = int(options.get('max_chars') or (68 if options.get('language') == 'English' else 30))
        position = max(0, int(editor.get('position_ms', 0)))
        step = max(0, min(2, int(editor.get('step', 1 if cues else 0))))
        quality_filter = max(0, min(4, int(editor.get('quality_filter', 0))))
        rows = editor.get('selected_rows', [])
        if not isinstance(rows, list) or any(not isinstance(row, int) for row in rows):
            raise ValueError('工程中的字幕选择记录无效。')
        self.style_save_timer.stop()
        if self.session.project and Path(filename).resolve() != self.session.project.resolve():
            self.save_edits()
        self.session.loading = True
        self.session.closed = False
        if not self.model_timer.isActive():
            self.model_timer.start(10000)
        try:
            self.restore_project(data, cues, filename, options, editor, style, characters, position, step, quality_filter, rows)
        finally:
            self.session.loading = False

    def restore_project(self, data, cues, filename, options, editor, style, characters, position, step, quality_filter, rows):
        path = Path(data['video']) if data['video'] else None
        if path and path.is_file():
            self.load_video(path, clear=False)
        else:
            self.player.stop()
            self.player.setSource(QUrl())
            self.session.video = path
            self.file_name.setText('原视频已移动，可继续编辑字幕' if path else Path(filename).stem)
            self.file_name.setToolTip(str(path) if path else str(filename))
            self.track.clear()
            self.track.addItem('原媒体缺失' if path else '默认音轨', options.get('track', 0))
        self.session.opened(filename, data)
        self.loop_current.setChecked(False)
        speed = self.playback_speed.findData(editor.get('playback_rate', 1.))
        self.playback_speed.setCurrentIndex(speed if speed >= 0 else 2)
        self.auto_advance.setChecked(bool(editor.get('auto_advance', False)))
        self.cues = cues
        self.export_settings = options.get('video_export', {}).copy() if isinstance(options.get('video_export'), dict) else {}
        if not isinstance(self.export_settings, dict):
            self.export_settings = {}
        self.translation_backend.blockSignals(True)
        self.translation_backend.setCurrentIndex(self.translation_backend.findData('online' if options.get('translation_backend') == 'online' else 'local'))
        self.translation_backend.blockSignals(False)
        self.style_panel.set_style(style)
        self.preview.set_style(style)
        default_export = 'original' if options.get('bilingual') is False else 'bilingual'
        self.export_mode.setCurrentIndex(max(0, self.export_mode.findData(options.get('export_mode', default_export))))
        for combo, key in [(self.language, 'language'), (self.mode, 'bilingual'), (self.track, 'track'), (self.device, 'device')]:
            index = combo.findData(options.get(key))
            if index >= 0:
                combo.setCurrentIndex(index)
        if options.get('segmentation_mode') == 'smart':
            self.max_chars.setCurrentIndex(0)
        else:
            self.max_chars.setValue(characters)
        self.context.setText(str(options.get('context', '')))
        self.translation_glossary = str(options.get('translation_glossary', ''))[:6000]
        self.translation_prompt = str(options.get('translation_prompt', ''))[:6000]
        self.history = EditHistory()
        self.model_selection = {key: str(path) for key, path in model_paths({**load_model_selection(), **options}).items()}
        self.subtitle_search.clear()
        self.quality_filter.setCurrentIndex(0)
        self.fill_table()
        self.go_to_step(step)
        self.preview_toggle.setChecked(bool(editor.get('preview_collapsed', False)) and step == 1)
        self.subtitle_search.setText(str(editor.get('search', '')))
        self.quality_filter.setCurrentIndex(quality_filter)
        self.table.clearSelection()
        for row in rows:
            if 0 <= row < len(cues) and not self.table.isRowHidden(row):
                self.table.selectionModel().select(self.table.model().index(row, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.last_export_target = str(editor.get('last_export_target', ''))
        self._restore_position = position
        if path and path.is_file():
            self.restore_playback_position(self.player.mediaStatus())
        self.position_changed(position)
        self.refresh_models()
        self.screen_stack.setCurrentIndex(1)
        self.refresh_workspace()
        self.notify(f'已打开工程 · {len(cues)} 条字幕' + (' · 可以导入视频或音频' if path is None else ' · 原媒体缺失，请在「更多」中重新关联' if not path.is_file() else ' · 可以继续编辑'))

    def export(self):
        if not self.cues:
            return
        if self.session.status in ('recognizing', 'aligning'):
            QMessageBox.information(self, '视频处理尚未完成', '此工程只包含部分字幕，请重新生成字幕后再导出完整视频字幕。')
            return
        mode = self.export_mode.currentData()
        incomplete = mode != 'original' and any(needs_translation(c) for c in self.cues)
        if incomplete:
            QMessageBox.information(self, '译文尚未完成', '部分日语或英语字幕没有中文译文，请点击“继续翻译”，或选择“原文字幕”导出。')
            return
        stem = self.session.video.stem if self.session.video else 'subtitles'
        filename, selection = QFileDialog.getSaveFileName(self, '导出字幕', str(self.export_folder() / f'{stem}.{mode}.srt'), 'SubRip 字幕 (*.srt);;WebVTT 字幕 (*.vtt)')
        if filename:
            try:
                target = Path(filename)
                if not target.suffix:
                    target = target.with_suffix('.vtt' if '*.vtt' in selection else '.srt')
                format = 'vtt' if target.suffix.lower() == '.vtt' else 'srt'
                atomic_write(target, render(self.cues, mode, format))
                self.notify(f'已导出：{target}')
            except Exception as exc:
                QMessageBox.warning(self, '导出失败', str(exc))

    def can_export_video(self):
        if self.busy() or not self.cues:
            return False
        if self.session.status in ('recognizing', 'aligning'):
            QMessageBox.information(self, '时间轴尚未完成', '请重新生成字幕，完成时间轴后再导出视频。')
            return False
        if not self.session.video or not self.session.video.is_file():
            QMessageBox.information(self, '找不到原视频', '请将原视频放回工程记录的位置后再导出。')
            return False
        if self.session.video.suffix.lower() in AUDIO_SUFFIXES:
            QMessageBox.information(self, '当前是音频文件', '音频文件可以导出字幕文件。请点击「保存字幕文件」。')
            return False
        if self.export_mode.currentData() != 'original' and any(needs_translation(cue) for cue in self.cues):
            QMessageBox.information(self, '译文尚未完成', '请继续翻译，或选择“原文字幕”后导出视频。')
            return False
        return True

    def export_video(self):
        if not self.can_export_video():
            return
        from .export_dialog import ExportDialog
        dialog = ExportDialog(self.session.video, self.export_mode.currentText(), self.export_settings,
                              self.track.currentData() or 0, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.export_settings = dialog.settings()
        self.save_edits()
        filename, _ = QFileDialog.getSaveFileName(self, '导出字幕视频', self.last_export_target or str(self.export_folder() / f'{self.session.video.stem}.字幕版.mp4'), 'MP4 视频 (*.mp4)')
        if not filename:
            return
        target = Path(filename)
        if not target.suffix:
            target = target.with_suffix('.mp4')
        try:
            self.start_video_export(target, dialog.settings())
        except Exception as exc:
            QMessageBox.warning(self, '无法导出视频', str(exc))

    def start_video_export(self, target, options=None):
        if not self.can_export_video():
            return
        target = Path(target).resolve()
        if target == self.session.video.resolve() or (target.exists() and target.samefile(self.session.video)):
            raise ValueError('请使用不同的文件名，不能覆盖原视频。')
        if target.suffix.lower() != '.mp4':
            raise ValueError('请使用 .mp4 文件扩展名。')
        validate(self.cues)
        from .export_settings import normalize_settings
        self.export_settings = normalize_settings({'track': self.track.currentData() or 0,
                                                   **(options if options is not None else self.export_settings)})
        self.style_save_timer.stop()
        self.last_export_target = str(target)
        self.save_edits()
        self.player.pause()
        job = {**self.export_settings, 'video': str(self.session.video), 'target': str(target),
               'mode': self.export_mode.currentData(), 'track': self.track.currentData() or 0,
               'subtitle_style': self.preview.caption_style.to_dict(),
               'cues': [dataclasses.asdict(cue) for cue in self.cues]}
        self.jobs.start_export(job, target, self.session.output_dir)
        self.set_busy(True)
        self.progress_bar.setValue(0)
        self.percent.setText('0%')
        self.notify('正在准备字幕视频导出…')

    def export_folder(self):
        folder = self.library.folder(self.session.project) / 'exports' if self.session.project and self.library.contains(self.session.project) else self.session.output_dir
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def open_output(self):
        from PySide6.QtGui import QDesktopServices
        self.session.output_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.session.output_dir)))

    def open_exports(self):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.export_folder())))

    def open_project_folder(self):
        from PySide6.QtGui import QDesktopServices
        folder = self.library.folder(self.session.project) if self.session.project and self.library.contains(self.session.project) else self.library.root
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_logs(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('运行日志')
        dialog.resize(920, 550)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit()
        content.setReadOnly(True)
        path = self.session.output_dir / ('video-export.log' if self.jobs.kind == 'export' else 'run.log')
        if not path.exists():
            path = LOGS / 'download.log'
        content.setPlainText(path.read_text(encoding='utf-8', errors='replace')[-40000:] if path.exists() else '暂无日志。')
        diagnostic = self.session.output_dir / 'api-diagnostics.jsonl'
        if self.jobs.kind != 'export' and diagnostic.exists():
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
            if input_kind(path) == 'srt' and (not self.session.video or not self.session.video.is_file() or input_kind(self.session.video) != 'media'):
                self.notify('请先载入视频，再拖入 SRT 字幕。')
                return
            event.setDropAction(Qt.CopyAction)
            event.accept()
        elif not self.busy() and event.mimeData().urls():
            self.notify('支持拖入视频、音频、SRT 字幕或 .yingziproj 工程；SRT 需先载入视频。')

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
        if self.session.closed:
            event.accept()
            return
        if self.busy():
            answer = QMessageBox.question(self, '正在处理视频', '关闭应用会取消当前处理。已经完成的字幕会保留。确定关闭？')
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_job()
            if not self.jobs.wait(5000):
                self.notify('正在结束后台进程，请稍后再关闭窗口。')
                event.ignore()
                return
        self.style_save_timer.stop()
        try:
            self.save_edits()
        except (OSError, ValueError) as exc:
            self.notify(f'工程保存失败：{exc}', error=True)
            event.ignore()
            return
        self.session.closed = True
        self.project_home.thumbnails.stop()
        self.model_timer.stop()
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
    server_name = 'yingzi-' + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    if not lock.tryLock(100):
        client = QLocalSocket()
        client.connectToServer(server_name)
        if client.waitForConnected(1000):
            payload = {'path': str(Path(sys.argv[1]).resolve()) if len(sys.argv) > 1 else ''}
            client.write((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
            client.waitForBytesWritten(1000)
            client.disconnectFromServer()
            return 0
        QMessageBox.information(None, '映字已启动', '映字已经在运行，请切换到现有窗口。')
        return 0
    window = Studio()
    window.show_project_library()
    QLocalServer.removeServer(server_name)
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.UserAccessOption)
    server.listen(server_name)
    def receive_open_request():
        client = server.nextPendingConnection()
        client.setParent(server)
        received = bytearray()
        def read_request():
            received.extend(bytes(client.readAll()))
            if len(received) > 65536:
                client.abort()
                return
            if b'\n' not in received:
                return
            try:
                request = json.loads(received.split(b'\n', 1)[0])
                if window.busy():
                    window.notify('正在处理，请结束当前任务后再打开工程。')
                elif request.get('path'):
                    window.open_input_file(Path(request['path']))
                window.showNormal()
                window.raise_()
                window.activateWindow()
            except (ValueError, TypeError, AttributeError):
                pass
            client.disconnectFromServer()
        client.readyRead.connect(read_request)
        client.disconnected.connect(client.deleteLater)
        read_request()
    server.newConnection.connect(receive_open_request)
    window.show()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if path.is_file():
            QTimer.singleShot(200, lambda: window.open_input_file(path))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
