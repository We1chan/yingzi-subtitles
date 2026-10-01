"""Choose and compare an open project's local history before restoring it."""
from datetime import datetime

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QListWidget, QPlainTextEdit, QDialogButtonBox
from PySide6.QtCore import Qt

from .project_versions import list_versions, compare_version


class VersionDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle('工程历史版本')
        self.resize(650, 500)
        box = QVBoxLayout(self)
        hint = QLabel('自动保存每隔 5 分钟保留修改前的版本，重要操作前另存版本；最多保留 30 个。')
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.versions = list_versions(project)
        self.list = QListWidget()
        self.list.setObjectName('versionList')
        for version in self.versions:
            self.list.addItem(f'{datetime.fromtimestamp(version.created):%Y-%m-%d %H:%M:%S}  ·  {version.reason}  ·  {version.cue_count} 条')
        box.addWidget(self.list, 1)
        self.details = QPlainTextEdit()
        self.details.setObjectName('versionDetails')
        self.details.setReadOnly(True)
        box.addWidget(self.details, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.restore = buttons.button(QDialogButtonBox.Ok)
        self.restore.setText('恢复此版本')
        buttons.button(QDialogButtonBox.Cancel).setText('关闭')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.list.currentRowChanged.connect(self.show_comparison)
        self.restore.setEnabled(False)
        if self.versions:
            self.list.setCurrentRow(0)
        else:
            self.details.setPlainText('此工程尚无历史版本。开始编辑或重新生成后会自动保留。')

    def selected_version(self):
        index = self.list.currentRow()
        return self.versions[index].path if 0 <= index < len(self.versions) else None

    def show_comparison(self, index):
        self.restore.setEnabled(False)
        if not 0 <= index < len(self.versions):
            self.details.clear()
            return
        try:
            self.details.setPlainText(compare_version(self.project, self.versions[index].path))
            self.restore.setEnabled(True)
        except (OSError, ValueError, TypeError) as exc:
            self.details.setPlainText(f'无法读取此版本：{exc}')
