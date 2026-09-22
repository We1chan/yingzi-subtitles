"""Keep preview audio on the chosen output as Windows devices change."""
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel,
                              QHBoxLayout, QCheckBox, QDialogButtonBox)

from .widgets import ComboBox, Slider


class PreviewAudio(QObject):
    changed = Signal()

    def __init__(self, output, parent=None):
        super().__init__(parent)
        self.output = output
        self.selected_id = b''  # Empty means follow the system default, including later changes.
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self.refresh)
        output.deviceChanged.connect(self.output_changed)
        output.volumeChanged.connect(self.output_changed)
        output.mutedChanged.connect(self.output_changed)
        self.refresh()

    def output_changed(self, *args):
        self.changed.emit()

    def refresh(self):
        outputs = self.devices.audioOutputs()
        device = next((device for device in outputs if bytes(device.id()) == self.selected_id), None)
        if device is None:
            self.selected_id = b''
            device = self.devices.defaultAudioOutput()
        if bytes(self.output.device().id()) != bytes(device.id()):
            self.output.setDevice(device)
        self.changed.emit()

    def select(self, device_id):
        self.selected_id = bytes(device_id or b'')
        self.refresh()

    def description(self):
        return self.output.device().description() or '未检测到可用的声音输出设备'


class PreviewAudioDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle('预览声音')
        self.resize(610, 235)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.output_device = ComboBox()
        self.output_device.currentIndexChanged.connect(self.select_device)
        form.addRow('声音输出', self.output_device)
        volume_row = QHBoxLayout()
        self.volume = Slider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.valueChanged.connect(lambda value: controller.output.setVolume(value / 100))
        self.volume_label = QLabel()
        self.volume_label.setMinimumWidth(42)
        volume_row.addWidget(self.volume, 1)
        volume_row.addWidget(self.volume_label)
        form.addRow('预览音量', volume_row)
        self.muted = QCheckBox('静音')
        self.muted.toggled.connect(controller.output.setMuted)
        form.addRow('', self.muted)
        layout.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        controller.changed.connect(self.refresh)
        self.refresh()

    def select_device(self, index):
        if index >= 0:
            self.controller.select(bytes.fromhex(self.output_device.currentData()))

    def refresh(self):
        controller = self.controller
        self.output_device.blockSignals(True)
        self.output_device.clear()
        default = controller.devices.defaultAudioOutput().description() or '暂无设备'
        self.output_device.addItem('跟随系统默认 · ' + default, '')
        for device in controller.devices.audioOutputs():
            self.output_device.addItem(device.description(), bytes(device.id()).hex())
        self.output_device.setCurrentIndex(max(0, self.output_device.findData(controller.selected_id.hex())))
        self.output_device.blockSignals(False)
        volume = round(controller.output.volume() * 100)
        self.volume.blockSignals(True)
        self.volume.setValue(volume)
        self.volume.blockSignals(False)
        self.volume_label.setText(f'{volume}%')
        self.muted.blockSignals(True)
        self.muted.setChecked(controller.output.isMuted())
        self.muted.blockSignals(False)
        self.status.setText('当前输出：' + controller.description())
