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
    project = tmp_path / '字幕.yingziproj'
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
    project = tmp_path / 'english.yingziproj'
    selection = {'asr_model': str(tmp_path / 'asr06'), 'translation_model': str(tmp_path / 'chat.gguf')}
    save_project(project, str(tmp_path / 'missing.mp4'), [Cue(1, 2, 'Hello.', '你好。', 'English')], {'language': 'English', 'max_chars': 68, **selection})
    window = Studio()
    window.load_saved_project(project)
    assert window.language.currentData() == 'English'
    assert window.max_chars.value() == 68
    assert window.current_options()['segmentation_mode'] == 'fixed'
    from app.config import ASR_PATH
    assert window.current_options()['asr_model'] == str(ASR_PATH.resolve())
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
    project = tmp_path / 'smart.yingziproj'
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


def test_model_dialog_fixes_qwen_and_remembers_translation_paths(app, tmp_path, monkeypatch):
    from app.model_dialog import ModelDialog
    from app.config import ASR_PATH
    custom = tmp_path / '手选模型.gguf'
    custom.write_bytes(b'GGUF' + b'\x00' * 32)
    dialog = ModelDialog({'asr_model': str(tmp_path / 'retired-asr')})
    assert 'asr_model' not in dialog.selectors
    monkeypatch.setattr('app.model_dialog.QFileDialog.getOpenFileName', lambda *args: (str(custom), ''))
    dialog.browse('translation_model')
    assert dialog.selection()['translation_model'] == str(custom.resolve())
    assert dialog.selection()['asr_model'] == str(ASR_PATH.resolve())
    assert dialog.details['translation_model'].text().endswith('已就绪')
    dialog.close()


def test_model_download_process_updates_log_and_unlocks_selection(app, monkeypatch):
    import sys
    from PySide6.QtCore import QProcess, QEventLoop, QTimer
    from PySide6.QtWidgets import QDialogButtonBox
    from app.model_dialog import ModelDialog
    class DownloadProcess(QProcess):
        def start(self, program, args):
            assert args[-1] == 'asr'
            super().start(sys.executable, ['-u', '-c', "import sys; sys.stdout.reconfigure(encoding='utf-8'); print('下载测试完成')"])
    monkeypatch.setattr('app.model_dialog.QProcess', DownloadProcess)
    dialog = ModelDialog({})
    monkeypatch.setattr(dialog, 'download_tasks', lambda: ['asr'])
    dialog.download_missing()
    assert not dialog.selectors['align_model'].isEnabled()
    loop = QEventLoop()
    dialog.process.finished.connect(loop.quit)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(5000)
    loop.exec()
    assert not dialog.downloading()
    assert '下载测试完成' in dialog.log.toPlainText()
    assert dialog.selectors['align_model'].isEnabled()
    assert dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
    dialog.close()


@pytest.mark.parametrize('language', ['', 'Chinese', 'Japanese', 'English'])
def test_fixed_qwen_supports_auto_and_all_three_languages(app, tmp_path, monkeypatch, language):
    from app.config import ASR_PATH
    window = Studio()
    monkeypatch.setattr('app.gui.readiness', lambda options: {'语音识别': True, '时间轴对齐': True, '字幕翻译': True, '翻译引擎': True})
    window.session.video = tmp_path / 'video.mp4'
    window.session.video.touch()
    window.model_selection['asr_model'] = str(tmp_path / 'retired-asr')
    window.language.setCurrentIndex(window.language.findData(language))
    window.refresh_models()
    assert window.start_button.isEnabled()
    assert window.model_selection['asr_model'] == str(ASR_PATH.resolve())
    assert window.context.isEnabled()
    window.close()
