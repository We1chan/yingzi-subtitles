import html
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QTimer, QProcess, QSizeF, QLockFile
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap, QShortcut, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QSpinBox, QLineEdit, QFileDialog,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QSlider,
    QSplitter, QGraphicsView, QGraphicsScene, QGraphicsTextItem, QAbstractItemView,
    QPlainTextEdit, QDialog, QStyle)

from .config import ROOT, CACHE, OUTPUT, LOGS, readiness
from .media import audio_tracks, CREATE_NO_WINDOW
from .subtitles import Cue, timestamp, parse_time, validate, render, atomic_write, load_project, save_project, subtitle_text


STYLE = '''
QWidget { color: #243344; font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; font-size: 13px; }
QMainWindow, #workspace { background: #F3F5F7; }
#sidebar { background: #12232B; }
#sidebar QLabel { color: #98B0B9; background: transparent; }
#sidebar #brand { color: #F4FCFA; font-size: 27px; font-weight: 700; }
#sidebar #nav { color: #D0FFF1; background: #24443F; border-radius: 9px; padding: 13px 15px; font-weight: 600; }
#sidebar QPushButton { color: #B8C9CF; background: transparent; border: 1px solid #38515A; text-align: left; padding: 10px; }
#sidebar QPushButton:hover { background: #29414B; }
#title { font-size: 26px; font-weight: 700; color: #152C35; }
#muted { color: #7B8995; font-size: 12px; }
#section { font-size: 15px; font-weight: 600; }
#card { background: white; border: 1px solid #E2E8EB; border-radius: 12px; }
#fileName { font-size: 14px; font-weight: 600; }
#badge { color: #237361; background: #E3F4EE; padding: 5px 11px; border-radius: 10px; font-size: 11px; }
QPushButton { background: white; border: 1px solid #D7E0E5; border-radius: 7px; padding: 8px 14px; font-weight: 500; }
QPushButton:hover { background: #EDF5F2; border-color: #8EBEAE; }
QPushButton:pressed { background: #DAEEE7; }
QPushButton:disabled { color: #A7B3BA; background: #EBEFF2; border-color: #E2E8EB; }
#primary { color: white; background: #137B66; border: none; font-weight: 600; padding: 12px 18px; }
#primary:hover { background: #106753; }
#primary:disabled { color: #90AAA4; background: #DFEAE7; }
#danger { color: #B85752; border-color: #E8CFCA; }
QComboBox, QSpinBox, QLineEdit { background: #F8FAFB; border: 1px solid #DFE6EA; border-radius: 6px; padding: 7px 9px; min-height: 18px; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #DCEEE8; selection-color: #153E32; }
QProgressBar { background: #E8EEED; border: none; border-radius: 3px; height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #1A987D; border-radius: 3px; }
QTableWidget { background: white; alternate-background-color: #F8FAFB; border: none; gridline-color: #EDF1F3; selection-background-color: #E0F0EA; selection-color: #173F32; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #EDF1F3; }
QHeaderView::section { background: #F8FAFB; border: none; border-bottom: 1px solid #E5EAEE; color: #82919C; font-size: 12px; padding: 9px 8px; }
QScrollBar:vertical { background: #F3F5F6; width: 8px; border: none; }
QScrollBar::handle:vertical { background: #CCD9DE; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSlider::groove:horizontal { background: #DDE5E9; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #1A8A72; }
QSlider::handle:horizontal { background: #137B66; width: 12px; margin: -4px 0; border-radius: 6px; }
QSplitter::handle { background: transparent; height: 12px; }
QPlainTextEdit { background: #17252F; color: #CFDFE5; border: none; font-family: Consolas; font-size: 12px; }
'''


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
    painter.setBrush(QColor('#137B66'))
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


class VideoView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.scene_object = QGraphicsScene(self)
        self.setScene(self.scene_object)
        self.setSceneRect(0, 0, 1280, 720)
        self.setBackgroundBrush(QColor('#13232E'))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.video_item = QGraphicsVideoItem()
        self.video_item.setSize(QSizeF(1280, 720))
        self.scene_object.addItem(self.video_item)
        self.empty = QGraphicsTextItem()
        self.empty.setTextWidth(1100)
        self.empty.setPos(90, 240)
        self.empty.setHtml('<div align="center" style="color:#EDF8F5; font-size:42px; font-family:Microsoft YaHei;">让每一句，都有字幕</div><br><div align="center" style="color:#819CA9; font-size:22px;">拖入视频，或点击「选择视频」开始</div>')
        self.scene_object.addItem(self.empty)
        self.caption = QGraphicsTextItem()
        self.caption.setTextWidth(1140)
        self.caption.setZValue(10)
        self.scene_object.addItem(self.caption)
        self.setMinimumSize(380, 210)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def set_caption(self, text):
        if not text:
            self.caption.setHtml('')
            return
        lines = text.splitlines()
        content = '<br>'.join(html.escape(line) for line in lines)
        self.caption.setHtml(f'<div align="center" style="font-family:Microsoft YaHei; font-size:30px; font-weight:600; color:white; background-color:#142027;">{content}</div>')
        self.caption.setPos(70, 684 - self.caption.boundingRect().height())


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.video = None
        self.project = None
        self.project_status = 'complete'
        self.output_dir = OUTPUT
        self.cues = []
        self.worker = None
        self.log_handle = None
        self.buffer = b''
        self.terminal_event = False
        self.loading_table = False
        self.active_cue = -1
        self.setWindowTitle('映字 · 本地双语字幕工作台')
        self.setWindowIcon(make_icon())
        self.resize(1340, 920)
        self.setMinimumSize(1080, 780)
        self.setAcceptDrops(True)
        self.build_ui()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.preview.video_item)
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

    def build_ui(self):
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(172)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 30, 18, 24)
        side.setSpacing(14)
        side.addWidget(label('映字', 'brand'))
        side.addWidget(label('YINGZI  /  LOCAL', 'muted'))
        side.addSpacing(32)
        side.addWidget(label('  字幕工作台', 'nav'))
        side.addWidget(label('  视频 · 原文 · 译文'))
        side.addStretch()
        side.addWidget(button('打开字幕工程', self.open_project))
        side.addWidget(button('打开输出文件夹', self.open_output))
        side.addWidget(button('查看运行日志', self.show_logs))
        side.addSpacing(20)
        side.addWidget(label('本地识别 · 本地翻译'))
        side.addWidget(label('视频与音频保留在本机', 'muted'))
        layout.addWidget(sidebar)
        work = QWidget()
        work.setObjectName('workspace')
        main = QVBoxLayout(work)
        main.setContentsMargins(26, 22, 26, 18)
        main.setSpacing(16)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label('把视频，变成双语字幕。', 'title'))
        titles.addWidget(label('选择视频，生成字幕，再逐句校对。', 'muted'))
        header.addLayout(titles)
        header.addStretch()
        badge = label('●  完全本地处理', 'badge')
        badge.setFixedHeight(28)
        header.addWidget(badge, alignment=Qt.AlignVCenter)
        main.addLayout(header)
        self.splitter = QSplitter(Qt.Vertical)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(16)
        video_card = card()
        video_layout = QVBoxLayout(video_card)
        video_layout.setContentsMargins(12, 12, 12, 12)
        title_row = QHBoxLayout()
        self.file_name = label('尚未选择视频', 'fileName')
        self.file_name.setMinimumWidth(100)
        title_row.addWidget(self.file_name, 1)
        self.choose = button('选择视频', self.choose_video)
        title_row.addWidget(self.choose)
        video_layout.addLayout(title_row)
        self.preview = VideoView()
        video_layout.addWidget(self.preview, 1)
        controls = QHBoxLayout()
        self.play = button('播放', self.toggle_play)
        self.play.setFixedWidth(60)
        self.seek = QSlider(Qt.Horizontal)
        self.time_label = label('00:00 / 00:00', 'muted')
        controls.addWidget(self.play)
        controls.addWidget(self.seek, 1)
        controls.addWidget(self.time_label)
        video_layout.addLayout(controls)
        top_layout.addWidget(video_card, 1)
        settings = card()
        settings.setFixedWidth(286)
        config = QVBoxLayout(settings)
        config.setContentsMargins(19, 18, 19, 18)
        config.setSpacing(9)
        config.addWidget(label('生成设置', 'section'))
        config.addSpacing(4)
        config.addWidget(label('视频语言', 'muted'))
        self.language = QComboBox()
        for title, value in [('自动识别（中文 / 日语）', ''), ('日语', 'Japanese'), ('中文', 'Chinese')]:
            self.language.addItem(title, value)
        config.addWidget(self.language)
        config.addWidget(label('字幕内容', 'muted'))
        self.mode = QComboBox()
        self.mode.addItem('日文原文 + 简体中文译文', True)
        self.mode.addItem('仅识别原文，不翻译', False)
        self.mode.currentIndexChanged.connect(lambda: self.start_button.setText('生成双语字幕' if self.mode.currentData() else '生成原文字幕'))
        config.addWidget(self.mode)
        config.addWidget(label('音轨', 'muted'))
        self.track = QComboBox()
        self.track.addItem('第 1 音轨', 0)
        self.track.currentIndexChanged.connect(self.change_track)
        config.addWidget(self.track)
        row = QHBoxLayout()
        self.device = QComboBox()
        self.device.addItem('自动使用 GPU', 'auto')
        self.device.addItem('CPU 兼容', 'cpu')
        self.max_chars = QSpinBox()
        self.max_chars.setRange(16, 50)
        self.max_chars.setValue(30)
        self.max_chars.setSuffix(' 字 / 条')
        row.addWidget(self.device, 1)
        row.addWidget(self.max_chars)
        config.addLayout(row)
        self.context = QLineEdit()
        self.context.setPlaceholderText('可选：人名、地名、专业词汇')
        config.addWidget(self.context)
        config.addStretch()
        self.model_status = label('', 'muted')
        self.model_status.setWordWrap(True)
        config.addWidget(self.model_status)
        self.start_button = button('生成双语字幕', self.start_job, 'primary')
        config.addWidget(self.start_button)
        self.cancel = button('取消处理', self.cancel_job, 'danger')
        self.cancel.hide()
        config.addWidget(self.cancel)
        top_layout.addWidget(settings)
        self.splitter.addWidget(top)
        bottom = card()
        table_layout = QVBoxLayout(bottom)
        table_layout.setContentsMargins(16, 14, 16, 12)
        row = QHBoxLayout()
        self.cue_count = label('字幕校对  ·  0 条', 'section')
        row.addWidget(self.cue_count)
        row.addStretch()
        self.retry = button('继续翻译', self.retry_translation)
        self.retry.setEnabled(False)
        row.addWidget(self.retry)
        self.export_mode = QComboBox()
        for name, value in [('双语字幕', 'bilingual'), ('原文字幕', 'original'), ('中文字幕', 'translated')]:
            self.export_mode.addItem(name, value)
        self.export_mode.currentIndexChanged.connect(lambda: self.position_changed(self.player.position()) if hasattr(self, 'player') else None)
        row.addWidget(self.export_mode)
        self.export_button = button('导出 SRT / VTT', self.export)
        self.export_button.setEnabled(False)
        row.addWidget(self.export_button)
        table_layout.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['#', '开始时间', '结束时间', '原文', '中文译文'])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setWordWrap(True)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        for column, width in [(0, 40), (1, 116), (2, 116)]:
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemChanged.connect(self.edit_cue)
        self.table.cellClicked.connect(self.seek_cue)
        table_layout.addWidget(self.table, 1)
        table_layout.addWidget(label('单击字幕回到对应画面；双击修改文字或时间。修改自动保存到工程，导出时生成新字幕文件。', 'muted'))
        self.splitter.addWidget(bottom)
        self.splitter.setSizes([440, 320])
        main.addWidget(self.splitter, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        main.addWidget(self.progress_bar)
        footer = QHBoxLayout()
        self.status = label('准备就绪 · 选择一个视频开始', 'muted')
        self.status.setWordWrap(True)
        footer.addWidget(self.status, 1)
        self.percent = label('', 'muted')
        footer.addWidget(self.percent)
        main.addLayout(footer)
        layout.addWidget(work, 1)
        self.setCentralWidget(root)

    def busy(self):
        return self.worker is not None and self.worker.state() != QProcess.NotRunning

    def notify(self, text, error=False):
        self.status.setText(text)
        self.status.setStyleSheet('color: #B54B43;' if error else '')

    def refresh_models(self):
        state = readiness()
        count = sum(state.values())
        self.model_status.setText('●  本地模型已就绪' if count == 4 else f'模型准备中：{count} / 4 项就绪')
        self.model_status.setToolTip('\n'.join(f'{name}：' + ('已就绪' if ready else '待下载') for name, ready in state.items()))
        self.start_button.setEnabled(self.video is not None and not self.busy() and state['语音识别'] and state['时间轴对齐'])

    def choose_video(self):
        if self.busy():
            return
        filename, _ = QFileDialog.getOpenFileName(self, '选择视频或音频', str(self.video.parent if self.video else ROOT), '视频和音频 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.flv *.wmv *.mp3 *.wav *.m4a *.flac *.aac);;所有文件 (*)')
        if filename:
            self.load_video(Path(filename))

    def load_video(self, path, clear=True):
        if self.busy():
            return
        self.player.stop()
        self.video = Path(path).resolve()
        self.file_name.setText(self.fontMetrics().elidedText(self.video.name, Qt.ElideMiddle, 380))
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
            self.fill_table()
        self.progress_bar.setValue(0)
        self.percent.setText('')
        self.notify('视频已载入 · 选择语言后即可生成字幕')
        self.refresh_models()

    def change_track(self, index):
        if hasattr(self, 'player') and index >= 0:
            self.player.setActiveAudioTrack(index)

    def toggle_play(self):
        if self.video:
            self.player.pause() if self.player.playbackState() == QMediaPlayer.PlayingState else self.player.play()

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
        index = bisect.bisect_right([cue.start for cue in self.cues], seconds) - 1
        current = self.cues[index] if 0 <= index < len(self.cues) and seconds < self.cues[index].end else None
        self.preview.set_caption(subtitle_text(current, self.export_mode.currentData()) if current else '')
        if current and index != self.active_cue and self.table.state() != QAbstractItemView.EditingState:
            self.table.selectRow(index)
            self.active_cue = index

    def seek_cue(self, row, column):
        if row < len(self.cues):
            self.player.setPosition(round(self.cues[row].start * 1000))

    def fill_table(self):
        self.loading_table = True
        self.table.setRowCount(len(self.cues))
        for row, cue in enumerate(self.cues):
            for column, text in enumerate([str(row + 1), timestamp(cue.start), timestamp(cue.end), cue.text, cue.translation]):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column < 3:
                    item.setForeground(QColor('#82939F'))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 58)
        self.loading_table = False
        self.cue_count.setText(f'字幕校对  ·  {len(self.cues)} 条')
        self.export_button.setEnabled(bool(self.cues) and not self.busy())
        self.retry.setEnabled(bool(self.project) and self.project_status != 'aligning' and any(c.language == 'Japanese' and not c.translation for c in self.cues) and not self.busy())
        self.active_cue = -1

    def edit_cue(self, item):
        if self.loading_table or item.row() >= len(self.cues):
            return
        cue = self.cues[item.row()]
        column = item.column()
        attribute = {1: 'start', 2: 'end', 3: 'text', 4: 'translation'}.get(column)
        if not attribute:
            return
        old = getattr(cue, attribute)
        try:
            value = parse_time(item.text()) if column in (1, 2) else item.text().strip()
            setattr(cue, attribute, value)
            validate(self.cues)
            if column == 3 and value != old:
                # Existing translations must not silently appear current after source edits.
                cue.translation = ''
            self.save_edits()
        except Exception as exc:
            setattr(cue, attribute, old)
            self.notify(str(exc), error=True)
        self.fill_table()
        self.position_changed(self.player.position())

    def save_edits(self):
        if self.project and self.cues:
            save_project(self.project, self.video, self.cues, self.current_options(), status='aligning' if self.project_status == 'aligning' else 'edited')
            self.notify('字幕工程已保存 · 导出文件请点击「导出 SRT / VTT」')

    def current_options(self):
        return {'language': self.language.currentData(), 'bilingual': self.mode.currentData(), 'track': self.track.currentData() or 0, 'device': self.device.currentData(), 'max_chars': self.max_chars.value(), 'context': self.context.text().strip()}

    def start_job(self):
        if not self.video or self.busy():
            return
        folder = OUTPUT / f'{self.video.stem[:45]}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:4]}'
        job = {**self.current_options(), 'video': str(self.video), 'output': str(folder)}
        self.cues, self.project = [], None
        self.fill_table()
        self.launch(job)

    def retry_translation(self):
        if self.project and self.project_status != 'aligning' and not self.busy():
            self.save_edits()
            job = {**self.current_options(), 'video': str(self.video), 'output': str(self.project.parent), 'resume_project': str(self.project), 'bilingual': True}
            self.launch(job)

    def set_busy(self, busy):
        for widget in (self.choose, self.language, self.mode, self.track, self.device, self.max_chars, self.context, self.table, self.export_button, self.retry):
            widget.setEnabled(not busy)
        self.cancel.setVisible(busy)
        self.start_button.setEnabled(not busy and self.video is not None)
        self.refresh_models()

    def launch(self, job):
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
        self.notify('正在启动本地处理任务…')
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
                self.project_status = 'translating'
            elif kind == 'done':
                self.terminal_event = True
                self.project = Path(event['project'])
                self.project_status = 'complete'
                _, self.cues = load_project(self.project)
                self.fill_table()
                self.notify(f'已生成 {event["count"]} 条字幕 · 用时 {event["seconds"]:.0f} 秒 · 已保存原文、中文和双语 SRT')
            elif kind == 'error':
                self.terminal_event = True
                self.notify(event['message'], error=True)
                self.recover_checkpoint()

    def read_errors(self):
        content = bytes(self.worker.readAllStandardError())
        if self.log_handle:
            self.log_handle.write(content)
            self.log_handle.flush()

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.terminal_event = True
            self.notify('处理程序无法启动，请运行 setup.cmd 修复环境。', error=True)
            self.set_busy(False)

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
            self.notify('处理意外结束，已保留可用结果。请打开输出文件夹查看 run.log。', error=True)
            self.recover_checkpoint()
        self.set_busy(False)
        self.fill_table()

    def cancel_job(self):
        if self.busy():
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
        self.fill_table()
        self.notify(f'已打开字幕工程 · {len(cues)} 条字幕')
        self.refresh_models()

    def export(self):
        if not self.cues:
            return
        if self.project_status == 'aligning':
            QMessageBox.information(self, '视频处理尚未完成', '此工程只包含部分字幕，请重新生成字幕后再导出完整视频字幕。')
            return
        mode = self.export_mode.currentData()
        incomplete = mode != 'original' and any(c.language == 'Japanese' and not c.translation for c in self.cues)
        if incomplete:
            QMessageBox.information(self, '译文尚未完成', '部分日语字幕没有中文译文，请点击“继续翻译”，或选择“原文字幕”导出。')
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
        path = self.output_dir / 'run.log'
        if not path.exists():
            path = LOGS / 'download.log'
        content.setPlainText(path.read_text(encoding='utf-8', errors='replace')[-40000:] if path.exists() else '暂无日志。')
        layout.addWidget(content)
        dialog.exec()

    def dragEnterEvent(self, event):
        if not self.busy() and event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.busy():
            path = Path(event.mimeData().urls()[0].toLocalFile())
            if path.is_file():
                self.load_saved_project(path) if path.name.endswith('.yingzi.json') else self.load_video(path)

    def closeEvent(self, event):
        if self.busy():
            answer = QMessageBox.question(self, '正在处理视频', '关闭应用会取消当前处理。已经完成的字幕会保留。确定关闭？')
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_job()
            self.worker.waitForFinished(5000)
        self.player.stop()
        event.accept()


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
            QTimer.singleShot(200, lambda: window.load_saved_project(path) if path.name.endswith('.yingzi.json') else window.load_video(path))
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
