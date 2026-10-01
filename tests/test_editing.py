import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.editing import merge_cues, split_cue, quality_issues
from app.gui import Studio, initialize_fonts
from app.subtitles import Cue, save_project, load_project


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    return app


def test_merge_split_edit_history_and_persistence(application, tmp_path):
    project = tmp_path / 'project.yingziproj'
    cues = [Cue(1, 2, 'Hello', '你好', 'English'), Cue(2, 4, 'world.', '世界。', 'English')]
    save_project(project, tmp_path / 'missing.mp4', cues)
    window = Studio()
    window.load_saved_project(project)
    window.apply_cue_edit(merge_cues(window.cues, [0, 1]))
    assert window.cues[0].text == 'Hello world.' and window.cues[0].translation == ''
    window.undo_edit()
    assert window.cues == cues
    window.redo_edit()
    assert len(window.cues) == 1
    window.apply_cue_edit(split_cue(window.cues, 0, 6, 2.2))
    assert [cue.text for cue in window.cues] == ['Hello', 'world.']
    assert window.cues[0].end == window.cues[1].start == 2.2
    window.table.item(1, 4).setText('世界！')
    window.undo_edit()
    assert not window.cues[1].translation
    assert load_project(project)[1] == window.cues
    window.translation_glossary, window.translation_prompt = 'world = 世界', '使用自然口语'
    window.save_edits()
    window.load_saved_project(project)
    assert window.translation_glossary == 'world = 世界' and window.translation_prompt == '使用自然口语'
    assert not window.history.undo_stack
    window.close()


def test_quality_filter_and_selected_retranslation_do_not_change_other_rows(application, tmp_path, monkeypatch):
    project = tmp_path / 'project.yingziproj'
    cues = [Cue(0, 1, 'Hello', '你好', 'English'), Cue(1, 1.3, 'Different long sentence', '你好', 'English'),
            Cue(2, 4, 'Retry', language='English', translation_error='需要重试')]
    save_project(project, tmp_path / 'missing.mp4', cues)
    window = Studio()
    window.load_saved_project(project)
    assert '显示时间短于' in '\n'.join(quality_issues(cues[1], cues[0]))
    assert '与上一条译文相同' in '\n'.join(quality_issues(cues[1], cues[0]))
    window.quality_filter.setCurrentIndex(1)
    assert window.table.isRowHidden(0) and not window.table.isRowHidden(2)
    window.table.selectRow(2)
    jobs = []
    monkeypatch.setattr(window, 'launch', jobs.append)
    window.retranslate_selected()
    assert jobs[0]['selected_ids'] == [3]
    assert load_project(project)[1][0].translation == '你好'
    window.close()


def test_waveform_mouse_drag_changes_timing_and_undo_restores(application, tmp_path):
    window = Studio()
    window.session.video = tmp_path / 'missing.mp4'
    window.cues = [Cue(1, 3, 'Hello', language='English'), Cue(3.5, 5, 'Next', language='English')]
    window.fill_table()
    window.show()
    application.processEvents()
    window.seek_cue(0, 0)
    waveform = window.waveform
    start = QPoint(round(waveform.time_to_x(3)), waveform.height() // 2)
    stop = QPoint(round(waveform.time_to_x(3.4)), waveform.height() // 2)
    QTest.mousePress(waveform, Qt.LeftButton, pos=start)
    QTest.mouseMove(waveform, stop)
    QTest.mouseRelease(waveform, Qt.LeftButton, pos=stop)
    assert window.cues[0].end == pytest.approx(3.4, abs=.03)
    assert window.cues[0].end <= window.cues[1].start
    window.undo_edit()
    assert window.cues[0].end == 3
    window.close()
