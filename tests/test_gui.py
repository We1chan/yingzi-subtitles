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
