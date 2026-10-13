import dataclasses
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import numpy as np
import pytest
from PySide6.QtCore import QSizeF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.caption_style import CaptionStyle, render_overlay
from app.gui import Studio, initialize_fonts
from app.media import run_ffmpeg, audio_tracks
from app.subtitles import Cue, save_project, load_project
from app.video_export import encode_video, ExportCancelled, probe_video


def test_gpu_failure_falls_back_to_cpu_without_replacing_target_early(application, tmp_path, monkeypatch):
    from app import video_export
    video = tmp_path / 'input.mp4'
    target = tmp_path / 'result.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=24:d=1', '-c:v', 'libx264', video])
    target.write_bytes(b'previous result')
    calls = []
    original = video_export.run_encoder
    def encode(command, *args):
        encoder = command[command.index('-c:v') + 1]
        calls.append(encoder)
        assert target.read_bytes() == b'previous result'
        if encoder == 'h264_nvenc':
            return 1
        return original(command, *args)
    monkeypatch.setattr(video_export, 'select_encoder', lambda choice, codec: 'h264_nvenc')
    monkeypatch.setattr(video_export, 'run_encoder', encode)
    encode_video({'video': str(video), 'target': str(target), 'encoder': 'auto', 'mode': 'original',
                  'cues': [dataclasses.asdict(Cue(.1, .7, 'Test'))]}, tmp_path)
    assert calls == ['h264_nvenc', 'libx264']
    assert target.read_bytes() != b'previous result'
    assert probe_video(target)[0]['size'] == (320, 180)


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    return app


def rgb(image):
    converted = image.convertToFormat(QImage.Format_RGBA8888)
    return np.frombuffer(converted.bits(), np.uint8).reshape(converted.height(), converted.width(), 4).copy()


def test_style_drag_and_project_restore(application, tmp_path):
    project = tmp_path / 'styled.yingziproj'
    save_project(project, str(tmp_path / 'missing.mp4'), [Cue(0, 2, 'Hello', '你好', 'English')])
    window = Studio()
    window.load_saved_project(project)
    window.show()
    window.settings_tabs.setCurrentIndex(1)
    window.style_panel.controls['font_size'].setValue(52)
    window.style_panel.controls['rotation'].setValue(12)
    window.position_changed(1000)
    application.processEvents()
    caption = window.preview.caption
    start = window.preview.mapFromScene(caption.sceneBoundingRect().center())
    end = start + window.preview.mapFromScene(100, -150) - window.preview.mapFromScene(0, 0)
    QTest.mousePress(window.preview.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(window.preview.viewport(), end, delay=40)
    QTest.mouseRelease(window.preview.viewport(), Qt.LeftButton, pos=end)
    application.processEvents()
    style = window.preview.caption_style.to_dict()
    assert style['x'] > 50 and style['y'] < 95
    assert abs(window.style_panel.controls['x'].value() - style['x']) < .11
    window.save_edits()
    data, _ = load_project(project)
    assert data['options']['subtitle_style'] == style
    window.load_saved_project(project)
    assert window.preview.caption_style.to_dict() == style
    # Portrait frame, wrapped text and rotation must remain inside the actual image.
    window.preview.set_video_size(QSizeF(360, 640))
    window.preview.set_caption('A very long caption to wrap at the video edge.\n字幕位置保持在画面内部。')
    window.preview.caption.setPos(-500, 1000)
    assert window.preview.sceneRect().contains(window.preview.caption.sceneBoundingRect())
    window.close()


def test_encoded_pixels_timing_audio_and_tail(application, tmp_path):
    import imageio_ffmpeg
    video, target = tmp_path / "输入 有空格's.mp4", tmp_path / '成片.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=25:d=2',
                '-f', 'lavfi', '-i', 'sine=frequency=400:duration=0.8',
                '-f', 'lavfi', '-i', 'sine=frequency=800:duration=2',
                '-map', '0:v', '-map', '1:a', '-map', '2:a', '-c:v', 'libx264', '-c:a', 'aac', video])
    original_bytes = video.read_bytes()
    cues = [Cue(.12, .48, '<Hello> {\\N}', '你好', 'English'), Cue(1.04, 1.48, 'Second', '第二句', 'English')]
    style = CaptionStyle(font_size=65, translation_size=55, x=70, y=65, background_opacity=75, rotation=8)
    job = {'video': str(video), 'target': str(target), 'mode': 'bilingual', 'all_audio': True,
           'cues': [dataclasses.asdict(cue) for cue in cues], 'subtitle_style': style.to_dict(),
           'preset': 'ultrafast', 'crf': 18, 'encoder': 'cpu'}
    progress = []
    encode_video(job, tmp_path, lambda *args: progress.append(args))
    assert progress[-1][0] == 100
    assert video.read_bytes() == original_bytes
    assert len(audio_tracks(target)) == 2
    reader = imageio_ffmpeg.read_frames(str(target))
    meta = next(reader)
    frames = [np.frombuffer(frame, np.uint8).reshape(180, 320, 3) for frame in reader]
    assert len(frames) == 50 and meta['size'] == (320, 180)
    # All cue boundaries and silent gaps, including the tail after short audio, are checked.
    for index, frame in enumerate(frames):
        active = any(cue.start <= index / 25 < cue.end for cue in cues)
        assert (frame.max() > 80) == active, f'Incorrect subtitle at {index / 25}s'
    expected = rgb(render_overlay('<Hello> {\\N}\n你好', style, 320, 180))
    expected_rgb = expected[:, :, :3].astype(float) * expected[:, :, 3:] / 255
    assert np.abs(frames[5].astype(float) - expected_rgb).mean() < 5

    # Cancellation must preserve an existing destination and the source.
    prior = target.read_bytes()
    with pytest.raises(ExportCancelled):
        encode_video(job, tmp_path, cancelled=lambda: True)
    assert target.read_bytes() == prior
    with pytest.raises(ValueError, match='原视频'):
        encode_video({**job, 'target': str(video)}, tmp_path)


def test_imported_multiline_srt_keeps_video_and_matches_preview_in_encoded_output(application, tmp_path, monkeypatch):
    import imageio_ffmpeg
    from app.caption_style import render_caption
    monkeypatch.setattr('app.project_session.OUTPUT', tmp_path / 'projects')
    video, target = tmp_path / 'video.mp4', tmp_path / 'subtitled.mp4'
    run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=25:d=2',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', video])
    source = tmp_path / 'external.srt'
    source.write_text('1\n00:00:00,200 --> 00:00:01,200\nFirst line\nSecond line\n', encoding='utf-8')
    original = source.read_bytes()
    window = Studio()
    window.load_video(video)
    media_source = window.player.source()
    style = CaptionStyle(font_size=72, translation_size=24, color='#00FF00', translation_color='#FF0000', shadow=0)
    window.preview.set_style(style)
    window.import_srt(source)
    window.preview.set_video_size(QSizeF(320, 180))
    window.position_changed(500)
    assert window.player.source() == media_source and window.session.video == video.resolve()
    assert window.preview.caption.toPlainText() == 'First line\nSecond line'
    expected_tile = render_caption('First line\nSecond line', style, 320, 180, original_lines=2)
    assert np.array_equal(rgb(window.preview.caption.pixmap().toImage()), rgb(expected_tile))
    encode_video({'video': str(video), 'target': str(target), 'mode': window.export_mode.currentData(),
                  'cues': [dataclasses.asdict(cue) for cue in window.cues], 'subtitle_style': style.to_dict(),
                  'preset': 'ultrafast', 'crf': 18, 'encoder': 'cpu'}, tmp_path)
    reader = imageio_ffmpeg.read_frames(str(target))
    next(reader)
    frames = [np.frombuffer(frame, np.uint8).reshape(180, 320, 3) for frame in reader]
    expected = rgb(render_overlay('First line\nSecond line', style, 320, 180, original_lines=2))
    expected_rgb = expected[:, :, :3].astype(float) * expected[:, :, 3:] / 255
    assert np.abs(frames[10].astype(float) - expected_rgb).mean() < 5
    assert frames[0].max() < 20 and frames[35].max() < 20
    assert source.read_bytes() == original
    window.close()


def test_overlay_pool_reports_disk_errors_and_stops_on_cancel(application, tmp_path):
    from app.video_export import build_overlays
    cues = [Cue(i, i + .5, f'Line {i}') for i in range(40)]
    with pytest.raises(OSError, match='字幕图层'):
        build_overlays(cues, 'original', CaptionStyle(), (320, 180), tmp_path / 'missing', lambda *args: None, lambda: False)
    checks = []
    def cancelled():
        checks.append(None)
        return len(checks) > 10
    with pytest.raises(ExportCancelled):
        build_overlays(cues, 'original', CaptionStyle(), (320, 180), tmp_path, lambda *args: None, cancelled)
    # The pool is drained before returning, so nothing is written for cues after the cancel point.
    assert len(list(tmp_path.glob('caption-*.png'))) <= 10
    assert not list(tmp_path.glob('captions.ffconcat'))


def test_audio_only_rejected_and_style_values_sanitized(application, tmp_path):
    audio = tmp_path / 'audio.wav'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'sine=duration=0.1', audio])
    with pytest.raises(ValueError, match='纯音频'):
        probe_video(audio)
    style = CaptionStyle.from_dict({'x': float('nan'), 'y': 900, 'font_size': -4,
                                     'color': 'not a color', 'alignment': 'bad'})
    assert style.x == 50 and style.y == 100 and style.font_size == 8
    assert style.color == '#FFFFFF' and style.alignment == 'center'


def test_gui_export_process_success_cancel_and_failure(application, tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    video = tmp_path / 'source.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=25:d=1', '-c:v', 'libx264', video])
    project = tmp_path / 'project.yingziproj'
    save_project(project, video, [Cue(.1, .6, 'Hello', '你好', 'English')])
    window = Studio()
    window.load_saved_project(project)
    window.style_panel.controls['font_size'].setValue(45)
    target = tmp_path / 'finished.mp4'

    def wait():
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(15000)
        window.jobs.process.finished.connect(loop.quit)
        loop.exec()
        assert not window.busy(), window.status.text()
        assert window.jobs.export_work is None
        assert window.export_video_button.isEnabled()
        assert not list(tmp_path.glob('.yingzi-export-*'))

    window.start_video_export(target, {'preset': 'ultrafast'})
    assert not window.export_video_button.isEnabled()
    wait()
    assert target.is_file() and '视频已导出' in window.status.text()
    old = target.read_bytes()
    window.start_video_export(target)
    window.cancel_job()
    wait()
    assert '已取消' in window.status.text() and target.read_bytes() == old
    audio = tmp_path / 'audio.wav'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'sine=duration=0.1', audio])
    window.session.video = audio
    notices = []
    monkeypatch.setattr('app.gui.QMessageBox.information', lambda *args: notices.append(args[2]))
    window.start_video_export(target)
    window.refresh_workspace()
    assert notices and '音频文件' in notices[0]
    assert not window.busy() and not window.export_video_button.isEnabled() and target.read_bytes() == old
    window.close()


def test_thick_outline_keeps_cjk_fill_and_pending_style_saves(application, tmp_path):
    style = CaptionStyle(outline=6, shadow=0)
    pixels = rgb(render_overlay('每一句都有字幕\n每一句都有字幕', style, 1280, 720))
    assert ((pixels[:, :, :3] > 240).all(axis=2) & (pixels[:, :, 3] > 240)).sum() > 150
    translated = (pixels[:, :, 0] > 240) & (pixels[:, :, 1] > 200) & (pixels[:, :, 2] < 200)
    assert translated.sum() > 150
    half = rgb(render_overlay('半透明字幕', CaptionStyle(opacity=50, outline=6), 1280, 720))
    assert 120 <= half[:, :, 3].max() <= 128
    first, second = tmp_path / 'one.yingziproj', tmp_path / 'two.yingziproj'
    for path in (first, second):
        save_project(path, tmp_path / 'missing.mp4', [Cue(0, 1, '字幕')])
    window = Studio()
    window.load_saved_project(first)
    window.style_panel.controls['font_size'].setValue(70)
    # Switching before the debounce fires must not lose changes to the first project.
    window.load_saved_project(second)
    assert load_project(first)[0]['options']['subtitle_style']['font_size'] == 70
    assert window.preview.caption_style.font_size == 30
    window.close()


@pytest.mark.parametrize('rotated', [False, True])
def test_display_geometry_matches_export(application, tmp_path, rotated):
    from PySide6.QtCore import QEventLoop, QTimer
    video = tmp_path / 'wide.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=25:d=0.4',
                '-vf', 'setsar=1' if rotated else 'setsar=2', '-c:v', 'libx264', video])
    if rotated:
        tagged = tmp_path / 'rotated.mp4'
        run_ffmpeg(['-y', '-display_rotation', '90', '-i', video, '-c', 'copy', tagged])
        video = tagged
    metadata, _ = probe_video(video)
    expected = (90, 160) if rotated else (320, 90)
    assert metadata['size'] == expected
    window = Studio()
    window.load_video(video)
    window.audio.setVolume(0)
    window.player.play()
    loop = QEventLoop()
    QTimer.singleShot(800, loop.quit)
    loop.exec()
    assert window.player.hasVideo()
    assert (round(window.preview.sceneRect().width()), round(window.preview.sceneRect().height())) == expected
    target = tmp_path / 'normalized.mp4'
    encode_video({'video': str(video), 'target': str(target), 'mode': 'original',
                  'cues': [dataclasses.asdict(Cue(0, .2, '字幕'))]}, tmp_path)
    assert probe_video(target)[0]['size'] == expected
    window.close()
