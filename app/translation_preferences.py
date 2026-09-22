from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QDialogButtonBox


class TranslationPreferences(QDialog):
    def __init__(self, glossary='', prompt='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('翻译术语与风格')
        self.resize(620, 510)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('术语表：每行填写“原文 = 译文”，统一人名、称呼和专有名词。'))
        self.glossary = QPlainTextEdit(glossary)
        self.glossary.setPlaceholderText('田中 = 田中\n先輩 = 前辈\nAPI = 接口')
        layout.addWidget(self.glossary)
        layout.addWidget(QLabel('翻译风格提示词'))
        self.prompt = QPlainTextEdit(prompt)
        self.prompt.setPlaceholderText('例如：使用自然口语，保留人物语气，减少书面语；不要额外解释台词。')
        layout.addWidget(self.prompt)
        note = QLabel('应用于本地和在线翻译，随工程保存；在线模式会把这些文字发送给所配置的 API。\n已完成译文需选中重译后才会采用新要求。每项最多 6000 字符。')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return self.glossary.toPlainText().strip()[:6000], self.prompt.toPlainText().strip()[:6000]
