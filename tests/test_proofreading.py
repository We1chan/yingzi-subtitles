import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialog, QMessageBox

from app.gui import Studio
from app.subtitles import Cue, load_project, save_project
from app.editing import EditHistory, merge_cues


@pytest.fixture(scope='module')
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def studio(application, tmp_path):
    project = tmp_path / 'proof.yingziproj'
    save_project(project, tmp_path / 'missing.mp4', [Cue(i * 3, i * 3 + 2, f'Line {i}', f'译文{i}', 'English') for i in range(4)])
    window = Studio()
    window.load_saved_project(project)
    yield window
    window.close()


def test_single_edit_and_undo_preserve_unaffected_items_waveform_and_selection(studio, monkeypatch):
    studio.table.selectRow(2)
    studio.seek_cue(2, 0)
    untouched = studio.table.item(0, 3)
    studio.cues[2].reviewed = True
    monkeypatch.setattr(studio, 'fill_table', lambda: pytest.fail('A single edit rebuilt the entire table.'))
    studio.table.item(2, 4).setText('校对后的译文')
    assert studio.table.item(0, 3) is untouched and studio.waveform_row == 2
    assert not studio.cues[2].reviewed
    change = studio.history.undo_stack[-1]
    assert change.index == 2 and len(change.before) == len(change.after) == 1
    assert load_project(studio.session.project)[1][2].translation == '校对后的译文'
    studio.undo_edit()
    assert studio.cues[2].translation == '译文2' and studio.cues[2].reviewed
    studio.redo_edit()
    assert studio.cues[2].translation == '校对后的译文' and studio.table.item(0, 3) is untouched


def test_invalid_timing_rolls_back_without_recording_an_edit(studio):
    studio.table.item(1, 1).setText('00:00:01,500')
    assert studio.cues[1].start == 3 and not studio.history.undo_stack
    assert studio.table.item(1, 1).text() == '00:00:03,000'
    assert load_project(studio.session.project)[1][1].start == 3


def test_timing_edits_update_waveform_handles_and_neighbour_limits_without_reloading(studio):
    studio.table.selectRow(1)
    studio.seek_cue(1, 0)
    peaks = studio.waveform.peaks = [.2, .5, .1]
    generation = studio.waveform.generation
    studio.table.item(1, 1).setText('00:00:03,200')
    assert studio.waveform.interval == [3.2, 5] and studio.waveform.peaks is peaks
    studio.undo_edit()
    assert studio.waveform.interval == [3, 5] and studio.waveform.generation == generation
    studio.table.item(0, 2).setText('00:00:02,500')
    studio.table.item(2, 1).setText('00:00:06,500')
    assert studio.waveform.lower == 2.5 and studio.waveform.upper == 6.5
    assert studio.waveform.peaks is peaks


def test_splice_history_keeps_only_affected_cues_for_a_large_project():
    cues = [Cue(i, i + .5, f'Line {i}') for i in range(10000)]
    merged = merge_cues(cues, [5000, 5001])
    history = EditHistory()
    history.record(cues, merged)
    change = history.undo_stack[0]
    assert len(change.before) == 2 and len(change.after) == 1
    restored = history.undo(merged)
    assert restored == cues and restored[0] is cues[0]
    assert history.redo(restored) == merged


def test_review_filter_confirmation_and_persistence(studio):
    studio.quality_filter.setCurrentIndex(3)
    studio.table.selectRow(1)
    studio.review_and_next()
    assert studio.cues[1].reviewed and studio.table.isRowHidden(1)
    assert studio.selected_rows() == [2]
    project = studio.session.project
    studio.save_edits()
    studio.load_saved_project(project)
    assert studio.cues[1].reviewed and studio.quality_filter.currentIndex() == 3
    studio.quality_filter.setCurrentIndex(4)
    assert not studio.table.isRowHidden(1) and studio.table.isRowHidden(2)


def test_navigation_skips_hidden_rows_and_auto_advance_uses_edited_row(studio):
    studio.cues[0].text = studio.cues[2].text = 'Hide'
    studio.fill_table()
    studio.subtitle_search.setText('Line')
    studio.table.selectRow(1)
    assert studio.navigate_cue(1) == 3
    assert studio.navigate_cue(-1) == 1
    studio.auto_advance.setChecked(True)
    studio.table.item(1, 4).setText('已编辑')
    QTest.qWait(30)
    assert studio.selected_rows() == [3]


def test_ctrl_enter_commits_cell_before_reviewing_and_advancing(studio, application):
    studio.show()
    studio.activateWindow()
    studio.table.selectRow(1)
    studio.table.editItem(studio.table.item(1, 4))
    QTest.qWait(30)
    editor = application.focusWidget()
    assert studio.table.state() == QAbstractItemView.EditingState
    editor.setText('快捷键提交的译文')
    QTest.keyClick(editor, Qt.Key_Return, Qt.ControlModifier)
    QTest.qWait(30)
    assert studio.cues[1].translation == '快捷键提交的译文' and studio.cues[1].reviewed
    assert studio.selected_rows() == [2]


def test_failed_cell_commit_does_not_mark_reviewed_or_advance(studio, application):
    studio.show()
    studio.activateWindow()
    studio.table.selectRow(1)
    studio.table.editItem(studio.table.item(1, 1))
    QTest.qWait(30)
    editor = application.focusWidget()
    editor.setText('00:00:01,500')  # Overlaps the previous cue.
    QTest.keyClick(editor, Qt.Key_Return, Qt.ControlModifier)
    QTest.qWait(30)
    assert studio.cues[1].start == 3 and not studio.cues[1].reviewed
    assert studio.selected_rows() == [1] and not studio.history.undo_stack


@pytest.mark.parametrize('redo', [False, True])
def test_failed_undo_or_redo_preserves_content_and_history(studio, monkeypatch, redo):
    studio.table.item(1, 4).setText('修改后的译文')
    if redo:
        studio.undo_edit()
    before = load_project(studio.session.project)[1]
    stacks = (list(studio.history.undo_stack), list(studio.history.redo_stack))
    monkeypatch.setattr(studio, 'save_edits', lambda: (_ for _ in ()).throw(OSError('disk full')))
    (studio.redo_edit if redo else studio.undo_edit)()
    assert studio.cues == before and load_project(studio.session.project)[1] == before
    assert (studio.history.undo_stack, studio.history.redo_stack) == stacks
    assert studio.table.item(1, 4).text() == before[1].translation


@pytest.mark.parametrize('confirmed', [False, True])
def test_history_dialog_restore_and_cancel_keep_expected_project_content(studio, monkeypatch, confirmed):
    from app.project_versions import create_version, list_versions
    from app.version_dialog import VersionDialog
    saved = create_version(studio.session.project, '人工修改前', force=True)
    original = list(studio.cues)
    studio.table.item(1, 4).setText('恢复前的译文')
    current = list(studio.cues)
    def choose_saved(dialog):
        index = next(i for i, version in enumerate(dialog.versions) if version.path == saved.path)
        dialog.list.setCurrentRow(index)
        assert dialog.restore.isEnabled() and '恢复前的译文' in dialog.details.toPlainText()
        return QDialog.Accepted
    monkeypatch.setattr(VersionDialog, 'exec', choose_saved)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes if confirmed else QMessageBox.No)
    project = studio.session.project
    studio.show_project_versions()
    expected = original if confirmed else current
    assert studio.session.project == project and studio.cues == expected
    assert load_project(project)[1] == expected and studio.table.item(1, 4).text() == expected[1].translation
    if confirmed:
        latest = list_versions(project)[0]
        assert latest.reason == '恢复历史版本前' and load_project(latest.path)[1] == current


def test_loop_boundary_skip_and_speed_persistence(studio, tmp_path, monkeypatch):
    video = tmp_path / 'available.mp4'
    video.touch()
    studio.session.video = video
    monkeypatch.setattr(studio.waveform, 'load', lambda *args: None)
    positions = []
    monkeypatch.setattr(studio.player, 'setPosition', positions.append)
    monkeypatch.setattr(studio.player, 'play', lambda: None)
    monkeypatch.setattr(studio.player, 'playbackState', lambda: QMediaPlayer.PlayingState)
    studio.table.selectRow(1)
    studio.loop_current.setChecked(True)
    studio.position_changed(5100)
    assert positions[-1] == 3000 and studio.loop_row == 1
    studio.loop_current.setChecked(False)
    studio.playback_speed.setCurrentIndex(studio.playback_speed.findData(1.5))
    studio.save_edits()
    assert load_project(studio.session.project)[0]['editor']['playback_rate'] == 1.5


def test_old_projects_default_to_unreviewed_and_bad_flags_are_rejected(tmp_path):
    import json
    project = tmp_path / 'old.yingziproj'
    save_project(project, tmp_path / 'missing.mp4', [Cue(0, 1, 'Hello')])
    data = json.loads(project.read_text(encoding='utf-8'))
    data['cues'][0].pop('reviewed')
    project.write_text(json.dumps(data), encoding='utf-8')
    assert load_project(project)[1][0].reviewed is False
    data['cues'][0]['reviewed'] = 'true'
    project.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='校对标记'):
        load_project(project)
