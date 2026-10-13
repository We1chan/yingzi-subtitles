"""Exercise beginner actions against actual project, recovery and export behavior."""
import json
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtWidgets import QApplication

from app.gui import Studio, STYLE, initialize_fonts
from app.subtitles import Cue, load_project, save_project


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    app.setStyleSheet(STYLE)
    return app


@pytest.fixture
def window(application, monkeypatch, tmp_path):
    monkeypatch.setattr('app.config.SETTINGS', tmp_path / 'settings.json')
    monkeypatch.setattr('app.gui.audio_tracks', lambda *args: ['默认音轨'])
    monkeypatch.setattr('app.gui.readiness', lambda *args: {
        '语音识别': True, '时间轴对齐': True, '字幕翻译': True, '翻译引擎': True})
    window = Studio()
    window.model_timer.stop()
    window.resize(1100, 760)
    window.show()
    yield window
    window.session.project = None
    window.close()


def load_sample(window, tmp_path, cues=(), status='complete', suffix='.mp4'):
    media = tmp_path / ('示例视频' + suffix)
    media.touch()
    project = window.library.create('示例', media, list(cues),
        options={'language': 'English', 'segmentation_mode': 'smart'}, status=status)
    window.load_saved_project(project)
    return project, media


def test_home_quick_start_creates_a_named_saved_project_without_a_name_dialog(window, tmp_path, monkeypatch):
    media = tmp_path / '第一次加字幕.mp4'
    media.touch()
    window.show_project_library()
    monkeypatch.setattr('app.gui.QFileDialog.getOpenFileName', lambda *args: (str(media), ''))
    monkeypatch.setattr('app.gui.NewProjectDialog', lambda *args: pytest.fail('Unexpected naming dialog'))
    window.project_home.quick_start.click()
    assert window.screen_stack.currentIndex() == 1 and window.session.video == media
    assert len(window.library.scan()) == 1
    data, cues = load_project(window.session.project)
    assert data['video'] == str(media) and not cues
    assert window.library.info(window.session.project).name == media.stem
    starts = []
    monkeypatch.setattr(window, 'start_job', lambda: starts.append(window.current_options()))
    window.guide_button.click()
    assert len(starts) == 1 and starts[0]['segmentation_mode'] == 'smart'


def test_guided_review_and_export_keep_subtitles_and_expose_optional_tools(window, tmp_path):
    project, _ = load_sample(window, tmp_path, [Cue(0, 2, 'Hello.', '你好。', 'English')])
    original = project.read_bytes()
    window.go_to_step(0)
    assert window._guide.action == 'review'
    window.guide_button.click()
    assert window.settings_tabs.currentIndex() == 1 and window.editor_tools.isHidden()
    window.edit_tools_toggle.click()
    assert not window.editor_tools.isHidden() and window.merge_button.isVisible()
    window.edit_tools_toggle.click()
    window.guide_button.click()
    assert window.settings_tabs.currentIndex() == 2 and window._guide.action == 'video_export'
    assert window.cues[0].translation == '你好。' and project.read_bytes() == original


def test_missing_translation_setup_opens_settings_before_processing_and_can_generate_original(window, tmp_path, monkeypatch):
    load_sample(window, tmp_path)
    monkeypatch.setattr('app.gui.readiness', lambda *args: {
        '语音识别': True, '时间轴对齐': True, '字幕翻译': False, '翻译引擎': False})
    window.refresh_models()
    starts, setups = [], []
    monkeypatch.setattr(window, 'setup_translation', lambda: setups.append(True))
    monkeypatch.setattr(window, 'start_job', lambda: starts.append(window.current_options()))
    window.start_button.click()
    assert setups == [True] and not starts
    window.guide_alternative.click()
    assert len(starts) == 1 and starts[0]['bilingual'] is False
    assert starts[0]['export_mode'] == 'original' and window.translation_group.isHidden()


def test_chinese_does_not_require_foreign_translation_components(window, tmp_path, monkeypatch):
    load_sample(window, tmp_path)
    monkeypatch.setattr('app.gui.readiness', lambda *args: {
        '语音识别': True, '时间轴对齐': True, '字幕翻译': False, '翻译引擎': False})
    window.language.setCurrentIndex(window.language.findData('Chinese'))
    assert window.start_button.isEnabled() and window.start_button.text() == '生成中文字幕'
    assert window.translation_group.isHidden() and not window.chinese_note.isHidden()
    assert window._guide.action == 'generate' and not window._translation_setup_needed


def test_pending_translation_can_resume_or_export_original_without_losing_work(window, tmp_path, monkeypatch):
    project, _ = load_sample(window, tmp_path, [Cue(0, 2, 'Hello.', language='English')])
    before = project.read_bytes()
    resumes = []
    monkeypatch.setattr(window, 'retry_translation', lambda: resumes.append(True))
    window.guide_button.click()
    assert resumes == [True] and window.cues[0].text == 'Hello.'
    window.guide_alternative.click()
    assert window.settings_tabs.currentIndex() == 2 and window.export_mode.currentData() == 'original'
    assert window.export_content_choice.currentData() == 'original' and window.export_button.isEnabled()
    assert project.read_bytes() == before


def test_interrupted_generation_guidance_reuses_the_saved_project(window, tmp_path, monkeypatch):
    project, _ = load_sample(window, tmp_path, status='aligning')
    cache = project.parent / 'work/.pipeline/state.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({'fingerprint': {'video': 'saved'}}), encoding='utf-8')
    window.refresh_table_actions()
    jobs = []
    monkeypatch.setattr(window, 'launch', jobs.append)
    window.guide_button.click()
    assert len(jobs) == 1 and jobs[0]['resume_project'] == str(project)
    assert window.session.project == project and not window.cues


def test_busy_guidance_cannot_start_another_action(window, tmp_path, monkeypatch):
    load_sample(window, tmp_path, [Cue(0, 2, '字幕')])
    window.jobs.kind = 'export'
    window.set_busy(True)
    assert not window.guide_button.isEnabled() and window.guide_alternative.isHidden()
    assert not window.export_content_choice.isEnabled()
    monkeypatch.setattr(window, 'export_video', lambda: pytest.fail('Unexpected second export'))
    window.run_guided_action()
    window.run_guided_action(alternative=True)
    window.set_busy(False)
    assert window.guide_button.isEnabled()


def test_export_content_can_be_changed_here_and_stays_in_sync_with_preview(window, tmp_path):
    load_sample(window, tmp_path, [Cue(0, 2, 'Hello.', language='English')])
    window.go_to_step(2)
    window.export_content_choice.setCurrentIndex(window.export_content_choice.findData('original'))
    assert window.export_mode.currentData() == 'original' and window.export_button.isEnabled()
    window.export_mode.setCurrentIndex(window.export_mode.findData('translated'))
    assert window.export_content_choice.currentData() == 'translated' and not window.export_button.isEnabled()


def test_audio_guidance_offers_a_subtitle_file_instead_of_video_export(window, tmp_path, monkeypatch):
    load_sample(window, tmp_path, [Cue(0, 2, '中文字幕', language='Chinese')], suffix='.wav')
    window.go_to_step(2)
    assert window._guide.action == 'export' and not window.export_video_button.isEnabled()
    exports = []
    monkeypatch.setattr(window, 'export', lambda: exports.append(True))
    window.guide_button.click()
    assert exports == [True]


def test_missing_media_guidance_can_relink_without_regenerating_subtitles(window, tmp_path, monkeypatch):
    _, media = load_sample(window, tmp_path, [Cue(0, 2, '保留字幕', language='Chinese')])
    media.unlink()
    window.refresh_workspace()
    links = []
    monkeypatch.setattr(window, 'relink_media', lambda: links.append(True))
    window.guide_button.click()
    assert links == [True] and window.cues[0].text == '保留字幕'


@pytest.mark.parametrize('selection, extension, header', [
    ('SubRip 字幕 (*.srt)', '.srt', '1\n'), ('WebVTT 字幕 (*.vtt)', '.vtt', 'WEBVTT\n'),
])
def test_subtitle_export_adds_the_selected_extension(window, tmp_path, monkeypatch, selection, extension, header):
    load_sample(window, tmp_path, [Cue(0, 2, '中文字幕', language='Chinese')])
    target = tmp_path / '我的字幕'
    monkeypatch.setattr('app.gui.QFileDialog.getSaveFileName', lambda *args: (str(target), selection))
    window.export()
    assert target.with_suffix(extension).read_text(encoding='utf-8-sig').startswith(header)


def test_original_only_preparation_does_not_download_unneeded_translation_components(window, tmp_path, monkeypatch):
    from app.model_dialog import ModelDialog
    window.mode.setCurrentIndex(window.mode.findData(False))
    monkeypatch.setattr('app.model_dialog.model_issue', lambda *args: '待准备')
    monkeypatch.setattr('app.model_dialog.server_path', lambda: tmp_path / 'missing-server.exe')
    dialog = ModelDialog(window.model_selection, window)
    assert set(dialog.download_tasks()) == {'asr', 'align'}
    dialog.close()


def test_original_only_generation_goes_to_review_and_export_without_requesting_translation(window, tmp_path):
    load_sample(window, tmp_path)
    window.mode.setCurrentIndex(window.mode.findData(False))
    assert window.export_mode.currentData() == 'original'
    window.cues = [Cue(0, 2, 'Hello.', language='English')]
    window.fill_table()
    window.go_to_step(1)
    assert window._guide.action == 'export_step'
    assert window.table.isColumnHidden(4) and window.table.item(0, 5).text() == '正常'
    window.guide_button.click()
    assert window.export_button.isEnabled() and window._guide.action == 'video_export'


def test_original_only_project_without_saved_export_mode_restores_an_exportable_view(window, tmp_path):
    media = tmp_path / 'source.mp4'
    media.touch()
    project = window.library.create('仅原文', media, [Cue(0, 2, 'Hello.', language='English')],
                                    options={'bilingual': False, 'segmentation_mode': 'smart'})
    window.load_saved_project(project)
    assert window.mode.currentData() is False and window.export_mode.currentData() == 'original'
    window.go_to_step(2)
    assert window.export_button.isEnabled() and window._guide.action == 'video_export'


def test_pending_translation_prepares_missing_components_before_trying_to_resume(window, tmp_path, monkeypatch):
    load_sample(window, tmp_path, [Cue(0, 2, 'Hello.', language='English')])
    monkeypatch.setattr('app.gui.readiness', lambda *args: {
        '语音识别': True, '时间轴对齐': True, '字幕翻译': False, '翻译引擎': False})
    window.refresh_models()
    setups = []
    monkeypatch.setattr(window, 'setup_translation', lambda: setups.append(True))
    monkeypatch.setattr(window, 'retry_translation', lambda: pytest.fail('Unexpected resume before translation setup'))
    window.guide_button.click()
    assert setups == [True] and window.cues[0].text == 'Hello.'
