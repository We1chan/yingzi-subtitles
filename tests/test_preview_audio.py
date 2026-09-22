import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal, QEventLoop, QTimer
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioFormat, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QApplication

from app.gui import Studio, initialize_fonts
from app.media import run_ffmpeg
from app.preview_audio import PreviewAudio, PreviewAudioDialog


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    return app


class Device:
    def __init__(self, name=''):
        self.name = name
    def id(self):
        return self.name.encode()
    def description(self):
        return self.name


class Devices(QObject):
    audioOutputsChanged = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.outputs = [Device('Headset A'), Device('Headset B')]
        self.default = self.outputs[0]
    def audioOutputs(self):
        return self.outputs
    def defaultAudioOutput(self):
        return self.default


class Output(QObject):
    deviceChanged = Signal()
    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)
    def __init__(self):
        super().__init__()
        self.current, self.level, self.muted = Device('Headset A'), .7, False
    def device(self):
        return self.current
    def setDevice(self, device):
        self.current = device
        self.deviceChanged.emit()
    def volume(self):
        return self.level
    def setVolume(self, volume):
        self.level = volume
        self.volumeChanged.emit(volume)
    def isMuted(self):
        return self.muted
    def setMuted(self, muted):
        self.muted = muted
        self.mutedChanged.emit(muted)


@pytest.fixture
def controller(application, monkeypatch):
    monkeypatch.setattr('app.preview_audio.QMediaDevices', Devices)
    output = Output()
    controller = PreviewAudio(output)
    yield controller


def test_default_output_follows_windows_change_without_resetting_volume(controller):
    output = controller.output
    output.setVolume(.23)
    output.setMuted(True)
    controller.devices.default = controller.devices.outputs[1]
    controller.devices.audioOutputsChanged.emit()
    assert output.device().description() == 'Headset B'
    assert controller.selected_id == b''
    assert output.volume() == .23 and output.isMuted()


def test_explicit_output_is_kept_until_unplugged_then_falls_back(controller):
    controller.select(b'Headset B')
    controller.devices.audioOutputsChanged.emit()
    assert controller.output.device().description() == 'Headset B'
    controller.devices.outputs = [controller.devices.default]
    controller.devices.audioOutputsChanged.emit()
    assert controller.output.device().description() == 'Headset A'
    assert controller.selected_id == b''
    controller.devices.outputs = []
    controller.devices.default = Device()
    controller.devices.audioOutputsChanged.emit()
    assert '未检测到' in controller.description()


def test_sound_dialog_controls_output_volume_mute_and_updates_device_list(controller):
    dialog = PreviewAudioDialog(controller)
    assert dialog.output_device.currentIndex() == 0
    dialog.output_device.setCurrentIndex(dialog.output_device.findText('Headset B'))
    assert controller.output.device().description() == 'Headset B'
    assert dialog.output_device.currentText() == 'Headset B'
    dialog.volume.setValue(40)
    assert controller.output.volume() == .4 and dialog.volume_label.text() == '40%'
    dialog.muted.setChecked(True)
    assert controller.output.isMuted()
    dialog.output_device.setCurrentIndex(0)
    controller.devices.default = controller.devices.outputs[1]
    controller.devices.audioOutputsChanged.emit()
    assert 'Headset B' in dialog.status.text() and 'Headset B' in dialog.output_device.currentText()
    assert dialog.volume.value() == 40 and dialog.muted.isChecked()
    dialog.close()
    dialog.deleteLater()


def test_real_preview_decodes_sound_and_uses_default_device(application, tmp_path):
    video = tmp_path / '带声音的预览.mp4'
    run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=2',
                '-f', 'lavfi', '-i', 'sine=frequency=500:duration=2', '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', video])
    window = Studio()
    window.audio.setVolume(0)  # Inspect decoded samples without making the test audible.
    assert '已静音' in window.sound_button.text()
    output = QAudioBufferOutput(window)
    window.player.setAudioBufferOutput(output)
    peaks = []
    loop = QEventLoop()
    def receive(buffer):
        if buffer.isValid() and buffer.format().sampleFormat() == QAudioFormat.Float:
            values = np.frombuffer(buffer.constData(), dtype=np.float32, count=buffer.sampleCount())
            peaks.append(float(np.max(np.abs(values))))
            if len(peaks) >= 5:
                loop.quit()
    output.audioBufferReceived.connect(receive)
    def loaded(status):
        if status == QMediaPlayer.LoadedMedia:
            window.toggle_play()
    window.player.mediaStatusChanged.connect(loaded)
    window.load_video(video)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(5000)
    try:
        loop.exec()
        assert len(peaks) >= 5 and max(peaks) > .01
        assert window.player.hasAudio() and window.player.activeAudioTrack() == 0
        assert window.player.error() == QMediaPlayer.NoError
        assert window.audio.device().id() == QMediaDevices.defaultAudioOutput().id()
        assert window.audio.volume() == 0  # Playback/device refresh must respect explicit mute volume.
    finally:
        timer.stop()
        window.close()
