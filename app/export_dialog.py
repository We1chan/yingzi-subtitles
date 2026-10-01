"""Video/audio settings with an asynchronous source probe and a live size estimate."""
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFormLayout,
                               QDialogButtonBox, QWidget, QScrollArea)

from .widgets import ComboBox, SpinBox
from .media import ffmpeg
from .export_settings import (DEFAULTS, normalize_settings, parse_media_info, video_bitrate,
                              estimated_size, format_size, audio_plan)


def note(text, name='muted'):
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.PlainText)
    return label


class ExportDialog(QDialog):
    def __init__(self, video, content, options=None, current_track=0, parent=None):
        super().__init__(parent)
        self.video = Path(video)
        self.info = None
        self.controls = {}
        self.setWindowTitle('合并字幕并导出视频')
        self.resize(700, 730)
        self.setMinimumSize(620, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(note('导出字幕视频', 'panelTitle'))
        layout.addWidget(note(f'{content} · MP4\n字幕按预览样式烧录，视频需要重新编码。'))
        self.source_label = note('正在读取源文件码率与音轨…', 'notice')
        layout.addWidget(self.source_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName('panelBody')
        columns = QHBoxLayout(body)
        columns.setContentsMargins(4, 8, 4, 8)
        columns.setSpacing(22)
        video_box, audio_box = QVBoxLayout(), QVBoxLayout()
        columns.addLayout(video_box, 1)
        columns.addLayout(audio_box, 1)
        video_box.addWidget(note('视频', 'section'))
        audio_box.addWidget(note('音频', 'section'))
        self.video_form, self.audio_form = QFormLayout(), QFormLayout()
        for form, box in ((self.video_form, video_box), (self.audio_form, audio_box)):
            form.setRowWrapPolicy(QFormLayout.WrapAllRows)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(8)
            box.addLayout(form)
        choices = [
            ('codec', '视频编码', [('H.264 · 兼容性优先', 'h264'), ('H.265 / HEVC · 压缩效率优先', 'hevc')]),
            ('encoder', '编码设备', [('自动选择显卡 / CPU', 'auto'), ('NVIDIA 显卡', 'nvidia'), ('CPU 兼容', 'cpu')]),
            ('resolution', '分辨率', [('原始分辨率', 'original'), ('最高 1080p', '1080'), ('最高 720p', '720')]),
            ('rate_mode', '码率控制', [('跟随源文件 · 控制体积', 'source'), ('自定义平均码率', 'bitrate'), ('恒定画质 · 体积不固定', 'quality')]),
            ('crf', '画质', [('高画质 · 18', 18), ('均衡 · 23', 23), ('较小文件 · 28', 28)]),
            ('preset', '编码速度', [('快速', 'fast'), ('均衡', 'medium'), ('较慢 · 压缩更充分', 'slow')]),
        ]
        for key, title, items in choices:
            self.add_choice(self.video_form, key, title, items)
        self.bitrate = SpinBox()
        self.bitrate.setRange(1, 200000)
        self.bitrate.setSingleStep(500)
        self.bitrate.setSuffix(' kbps')
        self.controls['video_bitrate'] = self.bitrate
        self.video_form.insertRow(4, '视频平均码率', self.bitrate)
        self.add_choice(self.audio_form, 'audio_mode', '音频编码', [
            ('自动 · 兼容音轨直接复制', 'auto'), ('直接复制 · 不重新编码', 'copy'),
            ('AAC · 重新编码', 'aac'), ('无音频', 'none')])
        self.add_choice(self.audio_form, 'audio_track', '保留音轨', [('全部音轨', 'all')])
        self.add_choice(self.audio_form, 'audio_bitrate', 'AAC 码率（每条音轨）',
                        [(f'{rate} kbps', rate) for rate in (64, 96, 128, 160, 192, 256, 320)])
        self.add_choice(self.audio_form, 'sample_rate', 'AAC 采样率', [('保持原采样率', 0), ('44.1 kHz', 44100), ('48 kHz', 48000)])
        self.add_choice(self.audio_form, 'channels', 'AAC 声道', [('保持原声道', 0), ('单声道', 1), ('立体声', 2)])
        self.audio_note = note('自动模式只对不兼容 MP4 的音轨转为 AAC；复制的音轨保持原参数。')
        audio_box.addWidget(self.audio_note)
        audio_box.addStretch()
        video_box.addWidget(note('H.265 需要播放器支持。显卡不可用时自动回退同一格式的 CPU 编码。'))
        video_box.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.estimate_label = note('', 'notice')
        layout.addWidget(self.estimate_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.Save)
        self.save_button.setText('选择保存位置…')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.save_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        try:
            self.initial = normalize_settings(options)
        except (ValueError, TypeError):
            self.initial = DEFAULTS.copy()
        self.current_track = current_track
        for key, control in self.controls.items():
            if key == 'video_bitrate':
                control.setValue(self.initial[key])
                control.valueChanged.connect(self.refresh)
            else:
                control.setCurrentIndex(max(0, control.findData(self.initial[key])))
                control.currentIndexChanged.connect(self.refresh)
        self.refresh()
        self.probe = QProcess(self)
        self.probe.finished.connect(self.probe_finished)
        self.probe.errorOccurred.connect(self.probe_error)
        self.probe_timer = QTimer(self)
        self.probe_timer.setSingleShot(True)
        self.probe_timer.timeout.connect(self.probe_timeout)
        self.probe.start(ffmpeg(), ['-hide_banner', '-nostdin', '-i', str(self.video)])
        self.probe_timer.start(20000)
        self.finished.connect(self.stop_probe)

    def add_choice(self, form, key, title, items):
        control = ComboBox()
        for name, value in items:
            control.addItem(name, value)
        form.addRow(title, control)
        self.controls[key] = control

    def settings(self):
        return normalize_settings({key: control.value() if key == 'video_bitrate' else control.currentData()
                                   for key, control in self.controls.items()})

    def probe_finished(self, *args):
        self.probe_timer.stop()
        try:
            self.set_source_info(parse_media_info(bytes(self.probe.readAllStandardError()).decode('utf-8', errors='replace'), self.video.stat().st_size))
        except OSError:
            self.probe_error()

    def set_source_info(self, info):
        self.info = info
        video = info['video']
        if not video:
            self.source_label.setText('无法读取视频信息，请确认文件存在且包含可解码的视频画面。')
        else:
            rate = f"{video['bitrate']:g} kbps" if video['bitrate'] else '未标注（按文件体积估算）'
            self.source_label.setText(f"源文件 {format_size(info['size_bytes'])} · {info['duration'] / 60:.1f} 分钟\n{video['codec'].upper()} · 视频码率 {rate} · {len(info['audio'])} 条音轨")
        track = self.controls['audio_track']
        track.blockSignals(True)
        track.clear()
        track.addItem('全部音轨', 'all')
        for index, stream in enumerate(info['audio']):
            current = ' · 当前' if index == self.current_track else ''
            track.addItem(f"第 {index + 1} 音轨 · {stream['codec'].upper()}{current}", index)
            track.setItemData(track.count() - 1, stream['description'], Qt.ToolTipRole)
        track.setCurrentIndex(max(0, track.findData(self.initial['audio_track'])))
        track.blockSignals(False)
        self.refresh()

    def probe_error(self, *args):
        self.probe_timer.stop()
        self.source_label.setText('读取源文件失败，请关闭后重新选择可读取的视频。')
        self.save_button.setEnabled(False)

    def probe_timeout(self):
        self.stop_probe()
        self.probe_error()

    def stop_probe(self, *args):
        self.probe_timer.stop()
        if self.probe.state() != QProcess.NotRunning:
            self.probe.kill()
            self.probe.waitForFinished(1000)

    def refresh(self, *args):
        settings = self.settings()
        self.video_form.setRowVisible(self.bitrate, settings['rate_mode'] == 'bitrate')
        self.video_form.setRowVisible(self.controls['crf'], settings['rate_mode'] == 'quality')
        mode = settings['audio_mode']
        audio_enabled = mode != 'none' and (self.info is None or bool(self.info['audio']))
        self.controls['audio_track'].setEnabled(audio_enabled)
        transcode = mode in ('auto', 'aac') and audio_enabled
        for key in ('audio_bitrate', 'sample_rate', 'channels'):
            self.controls[key].setEnabled(transcode)
        if self.info is None:
            self.estimate_label.setText('读取源文件后显示预计体积。')
            return
        self.save_button.setEnabled(bool(self.info['video']))
        try:
            plan = audio_plan(settings, self.info)
            transcode = any(codec == 'aac' for _, codec in plan)
            for key in ('audio_bitrate', 'sample_rate', 'channels'):
                self.controls[key].setEnabled(transcode)
            copied = sum(codec == 'copy' for _, codec in plan)
            self.audio_note.setText(f'输出 {len(plan)} 条音轨：{copied} 条直接复制，{len(plan) - copied} 条 AAC 转码。\n复制保留原参数；AAC 设置按每条转码音轨生效。' if plan else '导出的视频不包含音频。')
            rate = video_bitrate(settings, self.info)
            size = estimated_size(settings, self.info)
            if settings['rate_mode'] == 'quality':
                message = '恒定画质不限制体积，成片可能明显大于源文件。'
            else:
                message = f'目标视频码率 {rate} kbps · '
                message += f'预计 {format_size(size)}' if size is not None else '音轨码率或时长未知，无法预估总体积'
                message += '\n按平均码率估算，实际体积会随画面、音轨时长及封装开销浮动。'
                if size is not None and size > self.info['size_bytes'] * 1.2:
                    message += '\n当前设置预计大于源文件，可降低视频或音频码率。'
            if settings['rate_mode'] == 'source':
                message += '\n跟随源码率不保证相同画质；压缩过低时可提高自定义码率。'
            self.estimate_label.setText(message)
        except ValueError as exc:
            self.estimate_label.setText(str(exc))
            self.save_button.setEnabled(False)
