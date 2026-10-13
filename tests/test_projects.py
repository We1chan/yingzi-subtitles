"""Persist editing sessions and keep named projects through worker checkpoints."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import worker
from app.caption_style import CaptionStyle
from app.gui import Studio, initialize_fonts
from app.pipeline import cache_ready
from app.subtitle_import import input_kind
from app.subtitles import Cue, load_project, project_workspace, save_project


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    return app


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    monkeypatch.setattr('app.project_session.OUTPUT', tmp_path / 'output')
    monkeypatch.setattr('app.gui.audio_tracks', lambda path: ['默认音轨'])
    studio = Studio()
    studio.model_timer.stop()
    yield studio
    studio.style_save_timer.stop()
    studio.close()


def test_import_media_creates_draft_and_autosaves_generation_settings(window, tmp_path):
    video = tmp_path / '尚未生成.mp4'
    video.touch()
    window.load_video(video)
    project = window.session.project
    assert input_kind(project) == 'project' and project.suffix == '.yingziproj'
    assert load_project(project)[0]['status'] == 'draft'
    window.language.setCurrentIndex(window.language.findData('English'))
    window.max_chars.setValue(42)
    window.context.setText('工程里的词汇提示')
    window.mode.setCurrentIndex(window.mode.findData(False))
    for _ in range(40):
        QTest.qWait(50)
        options = load_project(project)[0]['options']
        if options['context'] == '工程里的词汇提示':
            break
    assert options['language'] == 'English' and options['max_chars'] == 42
    assert options['context'] == '工程里的词汇提示' and options['bilingual'] is False
    window.close()
    window.load_saved_project(project)
    assert window.context.text() == options['context'] and window.cues == []


def test_roundtrip_restores_subtitles_style_encoding_and_editor_state(window, tmp_path):
    project = tmp_path / '完整工程.yingziproj'
    cues = [Cue(1, 3, 'Hello', '你好', 'English'), Cue(4, 6, 'Next', '下一句', 'English')]
    save_project(project, tmp_path / 'missing.mp4', cues)
    window.load_saved_project(project)
    style = CaptionStyle(font_size=44, color='#12AB34', x=40, y=80)
    window.style_changed(style)
    window.export_settings = {'codec': 'hevc', 'encoder': 'cpu', 'rate_mode': 'bitrate',
                              'video_bitrate': 2500, 'audio_mode': 'aac', 'audio_bitrate': 192}
    window.translation_glossary = 'Hello = 你好'
    window.export_mode.setCurrentIndex(window.export_mode.findData('translated'))
    window.last_export_target = str(tmp_path / '成片.mp4')
    window.go_to_step(1)
    window.preview_toggle.setChecked(True)
    window.subtitle_search.setText('Next')
    window.table.selectRow(1)
    window._restore_position = 4500
    window.save_current_project()
    before = project.read_bytes()
    window.load_saved_project(project)
    assert project.read_bytes() == before  # Opening must not autosave halfway through restoring.
    assert window.cues == cues and window.preview.caption_style.to_dict() == CaptionStyle.from_dict(style.to_dict()).to_dict()
    assert window.export_settings['codec'] == 'hevc' and window.export_settings['video_bitrate'] == 2500
    assert window.translation_glossary == 'Hello = 你好'
    assert window.export_mode.currentData() == 'translated'
    assert window.settings_tabs.currentIndex() == 1 and window.preview_toggle.isChecked()
    assert window.subtitle_search.text() == 'Next' and window.selected_rows() == [1]
    assert window._restore_position == 4500 and window.last_export_target.endswith('成片.mp4')


def test_switching_media_flushes_pending_options_before_new_draft(window, tmp_path):
    first, second = tmp_path / 'first.mp4', tmp_path / 'second.mp4'
    first.touch()
    second.touch()
    window.load_video(first)
    old_project = window.session.project
    window.context.setText('即将关闭时刚刚填写')
    window.load_video(second)
    assert window.session.project != old_project
    assert load_project(old_project)[0]['options']['context'] == '即将关闭时刚刚填写'
    assert load_project(old_project)[0]['video'] == str(first)


def test_save_as_preserves_workspace_and_worker_updates_named_file(window, tmp_path, monkeypatch):
    original = tmp_path / 'old.yingziproj'
    workspace = tmp_path / '缓存'
    workspace.mkdir()
    save_project(original, tmp_path / 'missing.mp4', [Cue(0, 2, '字幕')], workspace=workspace)
    window.load_saved_project(original)
    monkeypatch.setattr('app.gui.QInputDialog.getText', lambda *args, **kwargs: ('另存 工程 #1', True))
    window.save_project_as()
    target = window.session.project
    assert window.library.contains(target) and project_workspace(target) != workspace
    assert project_workspace(target).parent == target.parent
    original_bytes = original.read_bytes()
    window.cues[0].text = '继续校对'
    window.save_edits()
    assert original.read_bytes() == original_bytes
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    worker.process({'video': str(window.session.video), 'output': str(project_workspace(target)), 'resume_project': str(target), 'bilingual': False})
    assert load_project(target)[1][0].text == '继续校对'
    assert load_project(target)[0]['editor']['step'] == 1
    assert not (workspace / 'subtitles.yingziproj').exists()
    assert original.read_bytes() == original_bytes


def test_save_as_cancel_and_failure_keep_active_project(window, tmp_path, monkeypatch):
    project = tmp_path / 'active.yingziproj'
    save_project(project, tmp_path / 'missing.mp4', [Cue(0, 2, '保留')])
    window.load_saved_project(project)
    monkeypatch.setattr('app.gui.QInputDialog.getText', lambda *args, **kwargs: ('', False))
    window.save_project_as()
    assert window.session.project == project
    monkeypatch.setattr('app.gui.QInputDialog.getText', lambda *args, **kwargs: ('failed', True))
    warnings = []
    monkeypatch.setattr('app.gui.QMessageBox.warning', lambda *args: warnings.append(args[2]))
    with monkeypatch.context() as patch:
        patch.setattr('app.project_session.save_project', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')))
        window.save_project_as()
    assert window.session.project == project and window.cues[0].text == '保留'
    assert warnings and not window.library.scan()


def test_bundle_move_resolves_relative_media_and_workspace(tmp_path):
    folder = tmp_path / 'old'
    folder.mkdir()
    media = folder / 'media.mp4'
    media.touch()
    workspace = folder / 'cache'
    (workspace / '.pipeline').mkdir(parents=True)
    (workspace / '.pipeline' / 'state.json').write_text(json.dumps({'fingerprint': {'video': str(media)}, 'aligned_complete': True}))
    project = folder / '字幕.yingziproj'
    save_project(project, media, [Cue(0, 1, 'Hello')], workspace=workspace)
    moved = tmp_path / 'new'
    shutil.move(str(folder), moved)
    data, _ = load_project(moved / project.name)
    assert data['video'] == str(moved / media.name)
    assert project_workspace(moved / project.name) == moved / 'cache'
    assert cache_ready(moved / project.name, complete=True)


def test_relink_media_preserves_cues_style_encoding_and_selection(window, tmp_path, monkeypatch):
    project = tmp_path / 'media-moved.yingziproj'
    style = CaptionStyle.from_dict(CaptionStyle(font_size=48).to_dict()).to_dict()
    cues = [Cue(0, 1, '保留字幕')]
    save_project(project, tmp_path / 'missing.mp4', cues, {'subtitle_style': style, 'video_export': {'codec': 'hevc'}}, editor={'position_ms': 500})
    window.load_saved_project(project)
    window.table.selectRow(0)
    video = tmp_path / '找到的原视频.mp4'
    video.touch()
    monkeypatch.setattr('app.gui.QFileDialog.getOpenFileName', lambda *args: (str(video), ''))
    window.relink_media()
    data, loaded = load_project(project)
    assert loaded == cues and window.cues == cues
    assert data['video'] == str(video)
    assert data['options']['subtitle_style'] == style and data['options']['video_export']['codec'] == 'hevc'
    assert data['editor']['position_ms'] == 500
    assert window.selected_rows() == [0] and data['editor']['selected_rows'] == [0]


@pytest.mark.parametrize('invalid', [[], {'version': 1, 'video': 'missing.mp4', 'cues': [], 'options': []},
    {'version': 1, 'video': 'missing.mp4', 'cues': [{'start': 0, 'end': 1, 'text': 'bad', 'language': []}]},
    {'version': 1, 'video': 'missing.mp4', 'cues': [{'start': 'wrong', 'end': 1, 'text': 'bad'}]}])
def test_corrupt_project_keeps_open_session(window, tmp_path, invalid):
    active = tmp_path / 'active.yingziproj'
    save_project(active, tmp_path / 'missing.mp4', [Cue(0, 1, '保留')])
    window.load_saved_project(active)
    broken = tmp_path / 'broken.yingziproj'
    broken.write_text(json.dumps(invalid), encoding='utf-8')
    assert not window.open_input_file(broken)
    assert window.session.project == active and window.cues[0].text == '保留'


def test_worker_running_cannot_be_overwritten_by_ui_autosave(window, tmp_path, monkeypatch):
    project = tmp_path / 'partial.yingziproj'
    save_project(project, tmp_path / 'missing.mp4', [], status='recognizing')
    window.load_saved_project(project)
    before = project.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(window, 'busy', lambda: True)
        window.cues = [Cue(0, 1, 'stale UI checkpoint')]
        window.save_edits()
    assert project.read_bytes() == before and load_project(project)[0]['status'] == 'recognizing'


def test_fresh_generation_keeps_named_project_and_uses_new_cache(window, tmp_path, monkeypatch):
    video = tmp_path / 'video.mp4'
    video.touch()
    project = tmp_path / 'named.yingziproj'
    workspace = tmp_path / 'existing-cache'
    (workspace / '.pipeline').mkdir(parents=True)
    save_project(project, video, [Cue(0, 1, '旧字幕')], workspace=workspace)
    window.load_saved_project(project)
    jobs = []
    monkeypatch.setattr(window, 'launch', jobs.append)
    window.start_job()
    assert window.session.project == project and window.session.output_dir != workspace
    assert project_workspace(project) == window.session.output_dir
    assert load_project(project)[0]['status'] == 'recognizing'
    assert window.cues == [] and len(jobs) == 1


def test_real_player_restores_saved_playhead_after_media_load(window, tmp_path):
    from app.media import run_ffmpeg
    video = tmp_path / '实际视频.mp4'
    run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=4',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', video])
    project = tmp_path / 'playhead.yingziproj'
    save_project(project, video, [Cue(2, 3, '定位字幕')], {'export_mode': 'original'}, editor={'position_ms': 2500})
    window.load_saved_project(project)
    for _ in range(60):
        QTest.qWait(50)
        if window._restore_position is None:
            break
    assert window._restore_position is None
    assert window.player.position() == 2500
    assert window.seek.value() == 2500 and window.preview.caption.toPlainText() == '定位字幕'
    window.close()
    QTest.qWait(500)
    assert load_project(project)[0]['editor']['position_ms'] == 2500


def test_second_app_instance_opens_project_in_existing_window(tmp_path):
    first, second = tmp_path / '工程 1.yingziproj', tmp_path / '工程 #2.yingziproj'
    for project in (first, second):
        save_project(project, tmp_path / 'missing.mp4', [Cue(0, 1, project.stem)])
    script = '''
import json, sys
from pathlib import Path
from PySide6.QtCore import QTimer
from app import gui
root = Path(sys.argv[1])
project = sys.argv[2]
gui.ROOT = root
gui.CACHE = root
gui.PROJECTS = root / 'projects'
events = root / 'opened.json'
class ProbeStudio(gui.Studio):
    def load_saved_project(self, filename):
        super().load_saved_project(filename)
        opened = json.loads(events.read_text(encoding='utf-8')) if events.exists() else []
        opened.append(str(self.session.project))
        events.write_text(json.dumps(opened), encoding='utf-8')
        if len(opened) == 2:
            QTimer.singleShot(200, self.close)
gui.Studio = ProbeStudio
sys.argv = ['main.py', project]
sys.exit(gui.main())
'''
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
    command = [sys.executable, '-c', script, str(tmp_path)]
    running = subprocess.Popen([*command, str(first)], cwd=Path(__file__).resolve().parents[1],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        events = tmp_path / 'opened.json'
        deadline = time.monotonic() + 10
        while not events.exists() and time.monotonic() < deadline and running.poll() is None:
            time.sleep(.05)
        assert events.exists(), 'The first application did not load its project.'
        forwarded = subprocess.run([*command, str(second)], cwd=Path(__file__).resolve().parents[1],
                                   env=env, capture_output=True, timeout=10)
        assert forwarded.returncode == 0, forwarded.stderr.decode('utf-8', errors='replace')
        _, errors = running.communicate(timeout=10)
        assert running.returncode == 0, errors.decode('utf-8', errors='replace')
        opened = [Path(path) for path in json.loads(events.read_text(encoding='utf-8'))]
        assert [path.stem for path in opened] == [first.stem, second.stem]
        assert all(path.is_relative_to(tmp_path / 'projects') for path in opened)
        assert load_project(opened[1])[0]['editor']['step'] == 1
    finally:
        if running.poll() is None:
            running.kill()
            running.communicate(timeout=5)
