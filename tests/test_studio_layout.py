"""Behavior checks for the redesigned workflow, filtering and action gating."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import Qt, QItemSelectionModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.gui import Studio, STYLE, initialize_fonts
from app.subtitles import Cue, save_project


@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    app.setStyleSheet(STYLE)
    return app


@pytest.fixture
def window(app):
    window = Studio()
    window.model_timer.stop()
    yield window
    window.session.project = None
    window.close()


def test_empty_workspace_navigation_and_disclosure(app, window, monkeypatch):
    window.resize(1100, 760)
    window.show()
    app.processEvents()
    assert window.size().width() == 1100 and window.size().height() == 760
    assert window.preview_stack.currentWidget() is window.welcome
    assert not window.start_button.isEnabled()
    assert not window.import_srt_button.isEnabled()
    assert not window.export_button.isEnabled()
    assert window.advanced_settings.isHidden()
    calls = []
    monkeypatch.setattr(window, 'choose_video', lambda: calls.append('media'))
    QTest.mouseClick(window.welcome_import, Qt.LeftButton)
    assert calls == ['media']
    window.advanced_toggle.click()
    assert not window.advanced_settings.isHidden()
    window.step_buttons[1].click()
    assert window.settings_tabs.currentWidget() is window.style_panel
    assert window.preview_stack.currentWidget() is window.preview
    app.processEvents()
    editor_height = window.splitter.widget(1).height()
    window.preview_toggle.click()
    app.processEvents()
    assert window.preview_stack.isHidden()
    assert window.splitter.widget(1).height() > editor_height
    window.step_buttons[2].click()
    assert not window.preview_toggle.isChecked()
    assert not window.preview_stack.isHidden()
    window.settings_tabs.setCurrentIndex(2)
    assert window.step_buttons[2].isChecked()
    assert window.cancel.isHidden()


def test_search_combines_with_quality_and_preserves_underlying_row_ids(window, tmp_path, monkeypatch):
    project = tmp_path / 'subtitles.yingziproj'
    cues = [Cue(0, 2, 'Hello WORLD', '你好世界', 'English'),
            Cue(3, 5, 'Another world', language='English'),
            Cue(6, 8, 'Last line', '最后一行', 'English')]
    save_project(project, tmp_path / 'missing.mp4', cues)
    window.load_saved_project(project)
    window.subtitle_search.setText('WORLD')
    assert [window.table.isRowHidden(i) for i in range(3)] == [False, False, True]
    window.quality_filter.setCurrentIndex(1)
    assert [window.table.isRowHidden(i) for i in range(3)] == [True, False, True]
    window.table.selectRow(1)
    assert window.split_button.isEnabled() and not window.merge_button.isEnabled()
    jobs = []
    monkeypatch.setattr(window, 'launch', jobs.append)
    window.retranslate_button.click()
    assert jobs[0]['selected_ids'] == [2]
    window.subtitle_search.setText('no matching text')
    assert window.match_count.text() == '0 / 3 条'
    assert not window.retranslate_button.isEnabled()
    assert window.cues == cues
    window.quality_filter.setCurrentIndex(0)
    window.subtitle_search.setText('最后')
    assert not window.table.isRowHidden(2)


def test_contextual_actions_require_compatible_selection_and_lock_while_busy(window, tmp_path):
    project = tmp_path / 'subtitles.yingziproj'
    save_project(project, tmp_path / 'missing.mp4', [Cue(i * 2, i * 2 + 1, str(i)) for i in range(3)])
    window.load_saved_project(project)
    window.table.clearSelection()
    assert not window.split_button.isEnabled() and not window.merge_button.isEnabled()
    select = window.table.selectionModel()
    flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
    select.select(window.table.model().index(0, 0), flags)
    assert window.split_button.isEnabled()
    select.select(window.table.model().index(2, 0), flags)
    assert not window.merge_button.isEnabled()
    select.select(window.table.model().index(1, 0), flags)
    assert window.merge_button.isEnabled() and not window.split_button.isEnabled()
    window.set_busy(True)
    assert not window.start_button.isEnabled()
    assert not window.merge_button.isEnabled()
    assert not window.retranslate_button.isEnabled()
    assert not window.open_project_button.isEnabled()
    assert not window.cancel.isHidden()
    window.settings_tabs.setCurrentIndex(2)
    assert not window.cancel.isHidden()
    window.set_busy(False)
    assert window.merge_button.isEnabled()
    assert window.cancel.isHidden()


def test_export_readiness_tracks_content_translation_and_missing_media(window, tmp_path):
    window.session.video = tmp_path / 'missing.mp4'
    window.cues = [Cue(0, 2, 'Hello', language='English')]
    window.fill_table()
    assert not window.export_button.isEnabled()
    assert '译文待补全' in window.export_summary.text()
    window.export_mode.setCurrentIndex(window.export_mode.findData('original'))
    assert window.export_button.isEnabled() and not window.export_video_button.isEnabled()
    assert '原媒体缺失' in window.export_summary.text()
    window.session.video.touch()
    window.refresh_workspace()
    assert window.export_video_button.isEnabled()
    window.session.status = 'aligning'
    window.fill_table()
    assert not window.export_button.isEnabled() and not window.export_video_button.isEnabled()


def test_subtitle_import_button_opens_review_and_new_project_clears_search(window, tmp_path, monkeypatch):
    monkeypatch.setattr('app.project_session.OUTPUT', tmp_path / 'output')
    window.session.video = tmp_path / 'video.mp4'
    window.session.video.touch()
    srt = tmp_path / 'existing.srt'
    srt.write_text('1\n00:00:01,000 --> 00:00:03,000\n已有字幕', encoding='utf-8')
    monkeypatch.setattr('app.gui.QFileDialog.getOpenFileName', lambda *args: (str(srt), ''))
    window.refresh_workspace()
    window.import_srt_button.click()
    assert len(window.cues) == 1 and window.step_buttons[1].isChecked()
    assert window.export_mode.currentData() == 'original'
    window.subtitle_search.setText('no match')
    window.load_saved_project(window.session.project)
    assert window.subtitle_search.text() == '' and not window.table.isRowHidden(0)


def test_landing_page_gives_the_drop_zone_the_whole_column_and_explains_disabled_start(app, window):
    window.show()
    app.processEvents()
    assert window.settings_tabs.tabBar().isHidden()
    assert window.editor_card.isHidden() and window.preview_heading.isHidden()
    assert not window.start_button.isEnabled() and '导入视频或音频' in window.start_hint.text()
    window.step_buttons[1].click()
    assert not window.editor_card.isHidden() and not window.preview_heading.isHidden()
    assert window.subtitle_search.isHidden() and window.quality_filter.isHidden()
    assert window.import_srt_button.isHidden() and window.retry.isHidden()
    assert window.progress_bar.isHidden()


def test_review_rows_are_compact_and_problem_statuses_stand_out(app, window, tmp_path):
    project = tmp_path / 'subtitles.yingziproj'
    cues = [Cue(0, 2, 'Fine.', '好。', 'English'), Cue(3, 5, 'Pending.', language='English'),
            Cue(6, 8, 'Failed.', language='English', translation_error='timeout'), Cue(9, 9.3, 'Short.', '短。', 'English')]
    save_project(project, tmp_path / 'missing.mp4', cues)
    window.resize(1440, 940)
    window.show()
    window.load_saved_project(project)
    app.processEvents()
    assert window.table.rowHeight(0) < 50
    statuses = [window.table.item(row, 5) for row in range(4)]
    assert [item.text() for item in statuses] == ['正常', '待翻译', '待重试', '需检查']
    assert len({item.foreground().color().name() for item in statuses}) == 4
    assert not statuses[0].font().bold() and all(item.font().bold() for item in statuses[1:])
    assert not window.retry.isHidden() and window.retry.isEnabled()
    assert window.import_srt_button.isHidden()


def test_match_count_shows_only_while_filtering(window, tmp_path):
    project = tmp_path / 'subtitles.yingziproj'
    save_project(project, tmp_path / 'missing.mp4', [Cue(0, 1, 'One', '一', 'English'), Cue(2, 3, 'Two', '二', 'English')])
    window.load_saved_project(project)
    assert window.match_count.isHidden()
    window.subtitle_search.setText('One')
    assert not window.match_count.isHidden() and window.match_count.text() == '1 / 2 条'
    window.subtitle_search.clear()
    assert window.match_count.isHidden()


def test_keyboard_shortcuts_and_table_menu_mirror_toolbar(app, window, tmp_path, monkeypatch):
    project = tmp_path / 'subtitles.yingziproj'
    save_project(project, tmp_path / 'missing.mp4', [Cue(i * 2, i * 2 + 1, f'Line {i}', '译文', 'English') for i in range(3)])
    window.show()
    window.activateWindow()
    QTest.qWaitForWindowActive(window)
    window.load_saved_project(project)
    plays = []
    monkeypatch.setattr(window, 'toggle_play', lambda: plays.append(True))
    window.table.setFocus()
    QTest.keyClick(window.table, Qt.Key_Space)
    assert plays == [True]
    QTest.keyClick(window, Qt.Key_F, Qt.ControlModifier)
    assert window.subtitle_search.hasFocus()
    window.subtitle_search.setText('Line')
    QTest.keyClick(window.subtitle_search, Qt.Key_Space)
    assert plays == [True] and window.subtitle_search.text() == 'Line '
    window.table.selectRow(1)
    menu = window.table_menu()
    actions = {action.text(): action.isEnabled() for action in menu.actions() if action.text()}
    assert actions['拆分'] and not actions['合并'] and actions['重译所选']
    assert '从此条播放' not in actions  # The source media is missing.
    menu.deleteLater()


def test_network_badge_distinguishes_local_and_online_translation(window):
    def use(backend):
        # Avoid translation_backend_changed, which would rewrite the local settings file.
        window.translation_backend.blockSignals(True)
        window.translation_backend.setCurrentIndex(window.translation_backend.findData(backend))
        window.translation_backend.blockSignals(False)
        window.refresh_models()
    use('local')
    assert window.network_badge.objectName() == 'localBadge'
    use('online')
    assert window.network_badge.objectName() == 'onlineBadge' and '在线 API' in window.network_badge.text()
    window.mode.setCurrentIndex(window.mode.findData(False))
    assert window.network_badge.objectName() == 'localBadge'


def test_generation_options_hide_unused_translation_controls(window):
    window.mode.setCurrentIndex(window.mode.findData(False))
    assert window.translation_group.isHidden()
    assert window.start_button.text() == '生成原文字幕'
    window.mode.setCurrentIndex(window.mode.findData(True))
    assert not window.translation_group.isHidden()
    assert window.start_button.text() == '生成双语字幕'
