import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtWidgets import QApplication

from app.gui import Studio, STYLE, initialize_fonts
from app.subtitles import Cue, load_project, save_project


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    initialize_fonts(application)
    application.setStyleSheet(STYLE)
    yield application


def test_editing_source_invalidates_translation_and_saves(app, tmp_path):
    project = tmp_path / '字幕.yingzi.json'
    save_project(project, str(tmp_path / 'missing.mp4'), [Cue(1, 2, 'こんにちは。', '你好。', 'Japanese')])
    window = Studio()
    window.load_saved_project(project)
    window.table.item(0, 3).setText('こんばんは。')
    _, cues = load_project(project)
    assert cues[0].text == 'こんばんは。'
    assert cues[0].translation == ''
    assert window.retry.isEnabled()
    window.table.item(0, 1).setText('00:00:03,000')
    assert window.cues[0].start == 1
    window.close()


def test_preview_escapes_caption_markup(app):
    window = Studio()
    window.cues = [Cue(1, 2, '<font>こんにちは', '你好', 'Japanese')]
    window.fill_table()
    window.position_changed(1500)
    assert '<font>こんにちは' in window.preview.caption.toPlainText()
    window.position_changed(2200)
    assert window.preview.caption.toPlainText() == ''
    window.close()


def test_english_project_restores_models_and_invalidates_edited_translation(app, tmp_path, monkeypatch):
    project = tmp_path / 'english.yingzi.json'
    selection = {'asr_model': str(tmp_path / 'asr06'), 'translation_model': str(tmp_path / 'chat.gguf')}
    save_project(project, str(tmp_path / 'missing.mp4'), [Cue(1, 2, 'Hello.', '你好。', 'English')], {'language': 'English', 'max_chars': 68, **selection})
    window = Studio()
    window.load_saved_project(project)
    assert window.language.currentData() == 'English'
    assert window.max_chars.value() == 68
    assert window.current_options()['segmentation_mode'] == 'fixed'
    assert window.current_options()['asr_model'] == selection['asr_model']
    window.table.item(0, 3).setText('Good morning.')
    assert window.retry.isEnabled()
    notices = []
    monkeypatch.setattr('app.gui.QMessageBox.information', lambda *args: notices.append(args[2]))
    window.export()
    assert notices and '英语' in notices[0]
    jobs = []
    monkeypatch.setattr(window, 'launch', jobs.append)
    window.retry_translation()
    assert jobs[0]['translation_model'] == selection['translation_model']
    assert jobs[0]['language'] == 'English'
    _, cues = load_project(project)
    assert cues[0].text == 'Good morning.' and not cues[0].translation
    window.close()


def test_smart_segmentation_default_language_change_and_project_roundtrip(app, tmp_path):
    window = Studio()
    assert window.max_chars.currentText() == '智能分割'
    window.language.setCurrentIndex(window.language.findData('English'))
    options = window.current_options()
    assert options['segmentation_mode'] == 'smart' and options['max_chars'] == 68
    project = tmp_path / 'smart.yingzi.json'
    cues = [Cue(1, 3, 'Hello world.', language='English')]
    save_project(project, tmp_path / 'missing.mp4', cues, options)
    window.max_chars.setValue(40)
    window.load_saved_project(project)
    assert window.max_chars.currentText() == '智能分割'
    assert window.cues == cues
    window.max_chars.setValue(40)
    assert window.current_options()['segmentation_mode'] == 'fixed'
    assert window.current_options()['max_chars'] == 40
    window.close()


def test_model_dialog_selects_presets_and_remembers_browsed_paths(app, tmp_path, monkeypatch):
    from app.model_dialog import ModelDialog
    from app.config import MODELS
    custom = tmp_path / '手选模型.gguf'
    custom.write_bytes(b'GGUF' + b'\x00' * 32)
    dialog = ModelDialog({})
    combo = dialog.selectors['asr_model']
    index = combo.findData(str((MODELS / 'Qwen3-ASR-0.6B').resolve()))
    assert index >= 0
    combo.setCurrentIndex(index)
    monkeypatch.setattr('app.model_dialog.QFileDialog.getOpenFileName', lambda *args: (str(custom), ''))
    dialog.browse('translation_model')
    assert dialog.selection()['translation_model'] == str(custom.resolve())
    assert dialog.selection()['asr_model'].endswith('Qwen3-ASR-0.6B')
    assert dialog.details['translation_model'].text().endswith('已就绪')
    dialog.close()


def test_model_download_process_updates_log_and_unlocks_selection(app, monkeypatch):
    import sys
    from PySide6.QtCore import QProcess, QEventLoop, QTimer
    from PySide6.QtWidgets import QDialogButtonBox
    from app.model_dialog import ModelDialog
    class DownloadProcess(QProcess):
        def start(self, program, args):
            assert args[-1] == 'asr-small'
            super().start(sys.executable, ['-u', '-c', "import sys; sys.stdout.reconfigure(encoding='utf-8'); print('下载测试完成')"])
    monkeypatch.setattr('app.model_dialog.QProcess', DownloadProcess)
    dialog = ModelDialog({})
    monkeypatch.setattr(dialog, 'download_tasks', lambda: ['asr-small'])
    dialog.download_missing()
    assert not dialog.selectors['asr_model'].isEnabled()
    loop = QEventLoop()
    dialog.process.finished.connect(loop.quit)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(5000)
    loop.exec()
    assert not dialog.downloading()
    assert '下载测试完成' in dialog.log.toPlainText()
    assert dialog.selectors['asr_model'].isEnabled()
    assert dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
    dialog.close()


def test_new_models_are_selectable_and_wrong_language_blocks_generation(app, tmp_path, monkeypatch):
    from app.config import MODELS
    from app.model_dialog import ModelDialog
    dialog = ModelDialog({})
    for name in ['cohere-transcribe-03-2026', 'parakeet-tdt-0.6b-v2-onnx']:
        assert dialog.selectors['asr_model'].findData(str((MODELS / name).resolve())) >= 0
    dialog.close()
    window = Studio()
    monkeypatch.setattr('app.gui.readiness', lambda options: {'语音识别': True, '时间轴对齐': True, '字幕翻译': True, '翻译引擎': True, '扩展识别引擎': True})
    window.video = tmp_path / 'video.mp4'
    window.model_selection['asr_model'] = str(MODELS / 'parakeet-tdt-0.6b-v2-onnx')
    window.language.setCurrentIndex(window.language.findData('Japanese'))
    window.refresh_models()
    assert not window.start_button.isEnabled()
    assert '仅识别英语' in window.model_status.text()
    window.language.setCurrentIndex(window.language.findData('English'))
    assert window.start_button.isEnabled()
    window.model_selection['asr_model'] = str(MODELS / 'cohere-transcribe-03-2026')
    window.language.setCurrentIndex(0)
    assert not window.start_button.isEnabled()
    window.close()
