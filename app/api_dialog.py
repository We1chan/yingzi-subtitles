import queue
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QDialogButtonBox, QPushButton, QHBoxLayout, QCheckBox, QScrollArea, QWidget, QFrame)

from .api_settings import (load_api_settings, load_api_key, save_api_settings,
                           clear_api_key, normalize_api, validate_api_key)
from .online_translation import OnlineTranslator
from .widgets import ComboBox as QComboBox, SpinBox as QSpinBox, IntegerChoice


class ApiDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('在线翻译 API')
        self.setMinimumWidth(610)
        self.resize(650, 740)
        self.results = queue.Queue()
        self.testing = False
        options = load_api_settings()
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        intro = QLabel('连接 OpenAI 兼容的 Chat Completions 接口。\n在线翻译发送字幕、相邻上下文及填写的翻译要求；视频识别与时间轴对齐仍在本机完成。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.base_url = QLineEdit(options['base_url'])
        self.base_url.setPlaceholderText('服务商的 Base URL，例如 https://api.example.com/v1')
        form.addRow('API 地址', self.base_url)
        self.model = QLineEdit(options['model'])
        self.model.setPlaceholderText('填写服务商提供的模型 ID')
        form.addRow('模型名称', self.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        form.addRow('API 密钥', self.key)
        show = QCheckBox('显示本次输入的密钥')
        show.toggled.connect(lambda visible: self.key.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password))
        form.addRow('', show)
        self.response_format = QComboBox()
        for name, value in [('自动兼容（推荐）', 'auto'), ('JSON Object', 'json_object'),
                            ('JSON Schema', 'json_schema'), ('仅提示词约束 JSON', 'prompt')]:
            self.response_format.addItem(name, value)
        self.response_format.setCurrentIndex(self.response_format.findData(options['response_format']))
        form.addRow('输出模式', self.response_format)
        self.batch_size = IntegerChoice(1, 20, ' 条 / 组')
        self.batch_size.setItemText(0, '逐条翻译（1 条 / 组）')
        self.batch_size.setValue(options['batch_size'])
        self.batch_size.setToolTip('默认每组最多 10 条连续字幕，联合理解上下文后逐条返回译文。\n遇到明显停顿、语言切换或内容过长会提前分组；结果不完整时缩小批次重试。')
        form.addRow('每组字幕', self.batch_size)
        self.concurrency = IntegerChoice(1, 4, ' 个请求')
        self.concurrency.setValue(options['concurrency'])
        form.addRow('同时翻译', self.concurrency)
        self.requests_per_minute = QSpinBox()
        self.requests_per_minute.setRange(0, 600)
        self.requests_per_minute.setSpecialValueText('不主动限速')
        self.requests_per_minute.setValue(options['requests_per_minute'])
        form.addRow('每分钟最多请求', self.requests_per_minute)
        self.thinking_mode = QComboBox()
        for name, value in [('自动（DeepSeek 普通翻译）', 'auto'), ('服务默认', 'default'),
                            ('关闭思考（需接口支持）', 'disabled'), ('开启思考（需接口支持）', 'enabled')]:
            self.thinking_mode.addItem(name, value)
        self.thinking_mode.setCurrentIndex(self.thinking_mode.findData(options['thinking_mode']))
        self.thinking_mode.setToolTip('自动模式仅针对 DeepSeek 官方的 flash / v4 型号关闭思考，其它服务不额外发送参数。\n可切回服务默认，或为支持 thinking 参数的兼容接口手动指定。')
        form.addRow('思考模式', self.thinking_mode)
        self.token_parameter = QComboBox()
        self.token_parameter.addItems(['max_tokens', 'max_completion_tokens'])
        self.token_parameter.setCurrentText(options['token_parameter'])
        form.addRow('长度参数', self.token_parameter)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(256, 32768)
        self.max_tokens.setValue(options['max_tokens'])
        form.addRow('最大输出 Token', self.max_tokens)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 600)
        self.timeout.setValue(options['timeout'])
        self.timeout.setSuffix(' 秒')
        form.addRow('请求超时', self.timeout)
        form_widget = QWidget()
        form_widget.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumHeight(250)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)
        note = QLabel('地址可填写 Base URL 或完整的 /chat/completions 地址。\n密钥使用当前 Windows 账户加密保存，不写入字幕工程。\n“测试翻译”会发送最多三条示例字幕，可能产生少量 API 费用。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        row = QHBoxLayout()
        self.test_button = QPushButton('测试翻译')
        self.test_button.clicked.connect(self.test_connection)
        row.addWidget(self.test_button)
        self.forget_button = QPushButton('清除已保存密钥')
        self.forget_button.clicked.connect(self.forget_key)
        row.addWidget(self.forget_button)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(40)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('保存并使用在线翻译')
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.base_url.textChanged.connect(self.key_hint)
        self.key_hint()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_result)

    def key_hint(self):
        saved = load_api_key({'base_url': self.base_url.text()})
        self.key.setPlaceholderText('此地址已有密钥，留空沿用；输入新密钥可替换' if saved else '请填写此服务的 API 密钥')

    def options(self):
        return normalize_api({'base_url': self.base_url.text(), 'model': self.model.text(),
            'response_format': self.response_format.currentData(), 'timeout': self.timeout.value(),
            'max_tokens': self.max_tokens.value(), 'token_parameter': self.token_parameter.currentText(),
            'batch_size': self.batch_size.value(), 'concurrency': self.concurrency.value(),
            'requests_per_minute': self.requests_per_minute.value(), 'thinking_mode': self.thinking_mode.currentData()})

    def current_key(self, options):
        return validate_api_key(self.key.text().strip() or load_api_key(options))

    def save(self):
        try:
            options = self.options()
            save_api_settings(options, self.current_key(options))
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self.accept()

    def forget_key(self):
        try:
            clear_api_key()
        except OSError:
            self.status.setText('无法清除密钥，请检查本机设置文件权限。')
            return
        self.key.clear()
        self.key_hint()
        self.status.setText('已清除本机保存的 API 密钥。')

    def test_connection(self):
        if self.testing:
            return
        try:
            options = self.options()
            key = self.current_key(options)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.testing = True
        self.test_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.set_editable(False)
        self.status.setText('正在测试当前配置的翻译与返回格式…')
        # The daemon owns no widgets, so closing the dialog never destroys a live QThread.
        results = self.results

        def run():
            try:
                examples = ['I thought he had already left.', 'But he was waiting outside.', 'He said he wanted to talk to me.']
                rows = [{'id': i + 1, 'text': text, 'language': 'English'}
                        for i, text in enumerate(examples[:min(3, options['batch_size'])])]
                with OnlineTranslator(options, key) as translator:
                    translator.translate(rows)
                results.put(f'连接成功，{len(rows)} 条示例字幕翻译及编号校验通过。')
            except Exception as exc:
                # API errors are already sanitized; never display provider response text.
                results.put(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '连接测试失败，请检查 API 配置。')

        threading.Thread(target=run, daemon=True).start()
        self.timer.start(100)

    def poll_result(self):
        try:
            message = self.results.get_nowait()
        except queue.Empty:
            return
        self.status.setText(message)
        self.testing = False
        self.test_button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(True)
        self.set_editable(True)
        self.timer.stop()

    def set_editable(self, enabled):
        for control in (self.base_url, self.model, self.key, self.response_format,
                        self.token_parameter, self.max_tokens, self.timeout, self.batch_size, self.concurrency,
                        self.requests_per_minute, self.thinking_mode, self.forget_button):
            control.setEnabled(enabled)
