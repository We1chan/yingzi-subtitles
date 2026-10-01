import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from app.gui import Studio, initialize_fonts
from app.media import run_ffmpeg
from app.subtitles import Cue, save_project, load_project, render


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    return app


def send_drag(application, target, mime, actions=Qt.CopyAction | Qt.MoveAction):
    events = [
        QDragEnterEvent(QPoint(20, 20), actions, mime, Qt.LeftButton, Qt.ShiftModifier),
        QDragMoveEvent(QPoint(30, 25), actions, mime, Qt.LeftButton, Qt.ShiftModifier),
        QDropEvent(QPointF(30, 25), actions, mime, Qt.LeftButton, Qt.ShiftModifier),
    ]
    for event in events:
        application.sendEvent(target, event)
    return events


@pytest.mark.parametrize('area', ['window', 'preview', 'table', 'settings', 'context'])
def test_file_drop_reaches_loader_once_from_every_main_area(application, tmp_path, monkeypatch, area):
    video = tmp_path / '中文 空格 #1.MP4'
    video.touch()
    window = Studio()
    window.show()
    application.processEvents()
    targets = {'window': window, 'preview': window.preview.viewport(), 'table': window.table.viewport(),
               'settings': window.settings_tabs.currentWidget(), 'context': window.context}
    calls = []
    monkeypatch.setattr(window, 'load_video', calls.append)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(video))])
    try:
        events = send_drag(application, targets[area], mime)
        assert all(event.isAccepted() for event in events)
        assert all(event.dropAction() == Qt.CopyAction for event in events)
        assert calls == [video]
        assert window.context.text() == ''
        assert video.is_file()
    finally:
        window.close()


@pytest.mark.parametrize('invalid', ['busy', 'directory', 'missing', 'remote', 'empty'])
def test_unusable_drop_keeps_current_project(application, tmp_path, monkeypatch, invalid):
    video = tmp_path / 'video.mp4'
    video.touch()
    window = Studio()
    window.session.video = tmp_path / 'current.mp4'
    original_cues = [Cue(1, 2, 'Keep this subtitle.', language='English')]
    window.cues = original_cues
    window.show()
    application.processEvents()
    calls = []
    monkeypatch.setattr(window, 'load_video', calls.append)
    mime = QMimeData()
    urls = {'busy': [QUrl.fromLocalFile(str(video))], 'directory': [QUrl.fromLocalFile(str(tmp_path))],
            'missing': [QUrl.fromLocalFile(str(tmp_path / 'gone.mp4'))],
            'remote': [QUrl('https://example.com/video.mp4')], 'empty': []}
    mime.setUrls(urls[invalid])
    with monkeypatch.context() as patch:
        if invalid == 'busy':
            patch.setattr(window, 'busy', lambda: True)
        events = send_drag(application, window.preview.viewport(), mime)
        assert not any(event.isAccepted() for event in events)
        assert calls == [] and window.cues is original_cues
        assert window.session.video == tmp_path / 'current.mp4'
    window.close()


def test_project_drop_and_invalid_project_report(application, tmp_path):
    video = tmp_path / 'missing.mp4'
    project = tmp_path / '字幕.YINGZIPROJ'
    cues = [Cue(1, 2, 'Hello.', '你好。', 'English')]
    save_project(project, video, cues)
    window = Studio()
    window.show()
    application.processEvents()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(project))])
    assert all(event.isAccepted() for event in send_drag(application, window.preview.viewport(), mime))
    imported = window.session.project
    assert window.library.contains(imported) and project.exists() and window.cues == cues
    broken = tmp_path / 'broken.yingziproj'
    broken.write_text('{', encoding='utf-8')
    mime.setUrls([QUrl.fromLocalFile(str(broken))])
    events = send_drag(application, window.preview.viewport(), mime)
    assert not events[-1].isAccepted()
    assert '无法打开拖入的文件' in window.status.text()
    assert window.session.project == imported and window.cues == cues
    window.close()


def test_dropped_video_loads_player_and_audio_track(application, tmp_path):
    video = tmp_path / '拖入 视频 #1.mp4'
    run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=1',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(video)])
    window = Studio()
    window.show()
    application.processEvents()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(video))])
    assert all(event.isAccepted() for event in send_drag(application, window.preview.viewport(), mime))
    assert window.session.video == video.resolve()
    assert Path(window.player.source().toLocalFile()) == video.resolve()
    assert window.track.count() == 1 and 'aac' in window.track.itemText(0)
    assert not window.preview.empty.isVisible()
    assert window.start_button.isEnabled()
    assert window.player.error() == QMediaPlayer.NoError
    assert video.is_file()
    window.close()


@pytest.mark.parametrize('suffix', ['.txt', '.png', '.exe', '.json', '.ass', '.srt.exe'])
def test_unsupported_file_drop_never_replaces_video(application, tmp_path, monkeypatch, suffix):
    path = tmp_path / ('unsupported' + suffix)
    path.write_text('data', encoding='utf-8')
    window = Studio()
    window.session.video = tmp_path / 'current.mp4'
    window.show()
    application.processEvents()
    calls = []
    monkeypatch.setattr(window, 'load_video', calls.append)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    assert not any(e.isAccepted() for e in send_drag(application, window.preview.viewport(), mime))
    assert not calls and window.session.video.name == 'current.mp4'
    window.close()


def test_srt_requires_loaded_video_and_invalid_import_preserves_project(application, tmp_path, monkeypatch):
    monkeypatch.setattr('app.project_session.OUTPUT', tmp_path / 'output')
    srt = tmp_path / 'captions.srt'
    srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nHello', encoding='utf-8')
    window = Studio()
    window.show()
    application.processEvents()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(srt))])
    assert not any(e.isAccepted() for e in send_drag(application, window.preview.viewport(), mime))
    assert window.session.video is None and not window.cues and '先载入视频' in window.status.text()
    video = tmp_path / 'current.mp4'
    video.touch()
    window.session.video = video
    cues = [Cue(1, 2, 'Original', '原字幕', 'English')]
    project = tmp_path / 'original.yingziproj'
    save_project(project, video, cues)
    window.session.project, window.cues = project, cues
    before = project.read_bytes()
    srt.write_text('not an srt file', encoding='utf-8')
    events = send_drag(application, window.preview.viewport(), mime)
    assert not events[-1].isAccepted()
    assert window.cues == cues and window.session.project == project and window.session.video == video
    assert project.read_bytes() == before and not (tmp_path / 'output').exists()
    window.close()


@pytest.mark.parametrize('area', ['preview', 'table', 'context'])
def test_srt_drop_import_save_undo_and_existing_source_protection(application, tmp_path, monkeypatch, area):
    monkeypatch.setattr('app.project_session.OUTPUT', tmp_path / 'output')
    video = tmp_path / 'current.mp4'
    video.touch()
    srt = tmp_path / '多行 字幕.SRT'
    source = '1\n00:00:01,000 --> 00:00:03,000\n第一行\n第二行\n\n2\n00:00:04,000 --> 00:00:05,000\n下一条'
    srt.write_text(source, encoding='utf-8')
    window = Studio()
    window.session.video = video
    window.language.setCurrentIndex(window.language.findData('Japanese'))
    window.show()
    application.processEvents()
    monkeypatch.setattr(window, 'load_video', lambda *args: pytest.fail('SRT must not enter the media loader'))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(srt))])
    target = {'preview': window.preview.viewport(), 'table': window.table.viewport(), 'context': window.context}[area]
    assert all(e.isAccepted() for e in send_drag(application, target, mime))
    assert window.session.video == video and window.table.rowCount() == 2
    assert window.cues[0].text == '第一行\n第二行'
    assert all(c.language == 'Chinese' for c in window.cues)
    assert window.context.text() == '' and window.export_mode.currentData() == 'original'
    window.position_changed(1500)
    assert window.preview.caption.toPlainText() == '第一行\n第二行'
    assert window.preview.caption_original_lines == 2
    assert window.export_video_button.isEnabled()
    assert load_project(window.session.project)[1] == window.cues
    assert '第一行\n第二行' in render(window.cues, 'original')
    window.undo_edit()
    assert window.cues == [] and load_project(window.session.project)[1] == []
    window.redo_edit()
    assert len(window.cues) == 2
    window.table.item(0, 3).setText('已校对的文字')
    assert load_project(window.session.project)[1][0].text == '已校对的文字'
    assert srt.read_text(encoding='utf-8') == source
    window.close()


def test_srt_import_save_failure_does_not_replace_existing_cues(application, tmp_path, monkeypatch):
    window = Studio()
    video = tmp_path / 'current.mp4'
    video.touch()
    window.session.video = video
    window.cues = [Cue(1, 2, 'Existing')]
    srt = tmp_path / 'new.srt'
    srt.write_text('1\n00:00:01,000 --> 00:00:02,000\nNew', encoding='utf-8')
    with monkeypatch.context() as patch:
        patch.setattr('app.project_library.save_project', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')))
        assert not window.open_input_file(srt)
    assert window.cues[0].text == 'Existing' and window.session.project is None
    window.close()
