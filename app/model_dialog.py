"""Local model selection and optional downloads, independent of inference jobs."""
import codecs
import subprocess
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QFileDialog, QPlainTextEdit, QDialogButtonBox)

from .config import (ROOT, MODELS, MODEL_DEFAULTS, ASR_PRESETS, model_paths,
    model_issue, server_path)
from .media import CREATE_NO_WINDOW


class ModelDialog(QDialog):
    def __init__(self, selection, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择本地模型')
        self.resize(730, 580)
        self.process = None
        self.selectors, self.details, self.browse_buttons = {}, {}, []
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        intro = QLabel('识别支持 Qwen3-ASR 1.7B / 0.6B 及兼容模型；翻译支持 llama.cpp 可加载的聊天型 GGUF。\n模型在本机运行。可浏览本地文件，或下载下方选中的内置模型。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        paths = model_paths(selection)
        for key, title in [('asr_model', '语音识别模型'), ('align_model', '时间轴对齐模型'), ('translation_model', '日语 / 英语 → 简体中文翻译模型')]:
            layout.addWidget(QLabel(title))
            row = QHBoxLayout()
            combo = QComboBox()
            combo.setMinimumWidth(300)
            candidates = [MODEL_DEFAULTS[key]]
            if key == 'asr_model':
                candidates.extend(MODELS / name for name in ASR_PRESETS)
            if key == 'translation_model':
                candidates.extend(sorted(MODELS.glob('*.gguf')))
            else:
                candidates.extend(path for path in sorted(MODELS.iterdir()) if path.is_dir() and not model_issue(path, key))
            candidates.append(paths[key])
            self.selectors[key] = combo
            for candidate in candidates:
                self.add_path(key, candidate)
            combo.setCurrentIndex(combo.findData(str(paths[key])))
            row.addWidget(combo, 1)
            browse = QPushButton('浏览文件…' if key == 'translation_model' else '浏览目录…')
            browse.clicked.connect(lambda checked=False, kind=key: self.browse(kind))
            row.addWidget(browse)
            self.browse_buttons.append(browse)
            layout.addLayout(row)
            detail = QLabel()
            detail.setWordWrap(True)
            detail.setStyleSheet('font-size: 12px;')
            self.details[key] = detail
            layout.addWidget(detail)
            combo.currentIndexChanged.connect(self.refresh)
        self.download = QPushButton('下载缺失的内置模型')
        self.download.clicked.connect(self.download_missing)
        layout.addWidget(self.download)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.setPlaceholderText('首次下载需要联网。GGUF 模型还需适配当前 llama.cpp 引擎；建议先用短片验证。')
        layout.addWidget(self.log)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('保存选择')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.refresh()

    def add_path(self, key, path):
        path = Path(path).resolve()
        combo = self.selectors[key]
        value = str(path)
        if combo.findData(value) < 0:
            combo.addItem(path.name, value)
            combo.setItemData(combo.count() - 1, value, Qt.ToolTipRole)
        return combo.findData(value)

    def selection(self):
        return {key: combo.currentData() for key, combo in self.selectors.items()}

    def browse(self, key):
        if key == 'translation_model':
            filename, _ = QFileDialog.getOpenFileName(self, '选择聊天型 GGUF 翻译模型', str(MODELS), 'GGUF 模型 (*.gguf)')
        else:
            filename = QFileDialog.getExistingDirectory(self, '选择 Qwen3 模型目录', str(MODELS))
        if filename:
            self.selectors[key].setCurrentIndex(self.add_path(key, filename))
            self.refresh()

    def download_tasks(self):
        tasks = []
        for key, path in model_paths(self.selection()).items():
            if not model_issue(path, key):
                continue
            if key == 'asr_model':
                for name, task in ASR_PRESETS.items():
                    if path == (MODELS / name).resolve():
                        tasks.append(task)
            elif path == MODEL_DEFAULTS[key].resolve():
                tasks.append('align' if key == 'align_model' else 'translate')
        if not server_path().is_file():
            tasks.append('runtime')
        return tasks

    def refresh(self):
        for key, path in model_paths(self.selection()).items():
            issue = model_issue(path, key)
            self.details[key].setText(f'{path}\n' + (issue or '已就绪'))
            self.details[key].setStyleSheet('font-size: 12px; color: ' + ('#B54B43;' if issue else '#237361;'))
        self.download.setEnabled(bool(self.download_tasks()) and not self.downloading())

    def downloading(self):
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def download_missing(self):
        tasks = self.download_tasks()
        if not tasks or self.downloading():
            return
        self.log.setPlainText('开始下载：' + ', '.join(tasks) + '\n文件保存在 models/；中断后可以继续下载。')
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_download)
        self.process.finished.connect(self.download_finished)
        self.process.errorOccurred.connect(self.download_error)
        self.set_downloading(True)
        self.process.start(str(ROOT / '.venv/Scripts/python.exe'), ['-u', str(ROOT / 'tools/download_assets.py'), *tasks])

    def set_downloading(self, active):
        for widget in [*self.selectors.values(), *self.browse_buttons, self.download, self.buttons.button(QDialogButtonBox.Save)]:
            widget.setEnabled(not active)
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消下载并关闭' if active else '取消')

    def read_download(self):
        text = self.decoder.decode(bytes(self.process.readAllStandardOutput()))
        if text:
            cursor = self.log.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(text)
            self.log.setTextCursor(cursor)
            self.log.ensureCursorVisible()

    def download_error(self, error):
        if error == QProcess.FailedToStart:
            self.log.appendPlainText('下载程序无法启动，请运行 setup.cmd 修复 Python 环境。')
            self.set_downloading(False)
            self.refresh()

    def download_finished(self, code, status):
        self.read_download()
        self.log.appendPlainText('\n下载完成，可以保存选择。' if code == 0 else '\n下载未完成，可重新下载以续传。请检查上方日志。')
        self.set_downloading(False)
        self.refresh()

    def stop_download(self):
        if self.downloading():
            if self.process.processId():
                subprocess.run(['taskkill', '/PID', str(self.process.processId()), '/T', '/F'], capture_output=True, creationflags=CREATE_NO_WINDOW)
            else:
                self.process.kill()
            self.process.waitForFinished(5000)

    def reject(self):
        self.stop_download()
        super().reject()

    def closeEvent(self, event):
        self.stop_download()
        super().closeEvent(event)
