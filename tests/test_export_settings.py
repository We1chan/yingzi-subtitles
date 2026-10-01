import dataclasses
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from app.export_settings import (normalize_settings, parse_media_info, probe_media_info,
                                 video_bitrate, estimated_size, audio_arguments, audio_plan)
from app.encoders import encoder_arguments, select_encoder
from app.media import run_ffmpeg
from app.subtitles import Cue, save_project, load_project
from app.video_export import encode_video


@pytest.fixture(scope='module')
def application():
    from app.gui import initialize_fonts
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    return app


@pytest.fixture
def info():
    return parse_media_info('''
  Duration: 00:10:00.00, start: 0.000000, bitrate: 8000 kb/s
  Stream #0:0[0x1](und): Video: hevc (Main), yuv420p, 1280x720, 7500 kb/s, 25 fps
  Stream #0:1[0x2](eng): Audio: aac (LC), 48000 Hz, stereo, fltp, 128 kb/s
  Stream #0:2(jpn): Audio: pcm_s16le, 44100 Hz, stereo, s16, 1411 kb/s
''', 600_000_000)


def test_source_budget_accounts_for_audio_and_reports_estimate(info):
    settings = normalize_settings()
    assert info['duration'] == 600
    assert info['video']['codec'] == 'hevc'
    assert audio_plan(settings, info) == [(0, 'copy'), (1, 'aac')]
    assert video_bitrate(settings, info) == 7500
    assert 580_000_000 < estimated_size(settings, info) < 600_000_000
    settings.update(audio_mode='aac', audio_bitrate=320)
    assert video_bitrate(settings, info) == 7200
    assert estimated_size(settings, info) < info['size_bytes']
    settings.update(rate_mode='quality')
    assert estimated_size(settings, info) is None


def test_unknown_bitrate_does_not_silently_switch_to_unbounded_quality():
    info = parse_media_info('Stream #0:0: Video: h264, yuv420p, 320x180', 10000)
    with pytest.raises(ValueError, match='自定义码率'):
        video_bitrate(normalize_settings(), info)
    assert video_bitrate(normalize_settings({'rate_mode': 'bitrate', 'video_bitrate': 1200}), info) == 1200
    assert estimated_size(normalize_settings({'rate_mode': 'bitrate'}), info) is None
    info = parse_media_info('Duration: 00:10:00.00\nStream #0:0: Video: h264\nStream #0:1: Audio: aac', 600_000_000)
    assert video_bitrate(normalize_settings(), info) == 7872
    assert estimated_size(normalize_settings(), info) is None


def test_audio_selection_copy_and_per_stream_transcode_arguments(info):
    settings = normalize_settings({'sample_rate': 44100, 'channels': 1, 'audio_bitrate': 96})
    assert audio_arguments(settings, info) == [
        '-map', '0:a:0', '-c:a:0', 'copy', '-map', '0:a:1', '-c:a:1', 'aac',
        '-b:a:1', '96k', '-ar:a:1', '44100', '-ac:a:1', '1']
    settings.update(audio_mode='copy')
    with pytest.raises(ValueError, match='AAC'):
        audio_arguments(settings, info)
    settings.update(audio_track=0)
    assert audio_arguments(settings, info) == ['-map', '0:a:0', '-c:a:0', 'copy']
    settings.update(audio_track=10)
    with pytest.raises(ValueError, match='不存在'):
        audio_plan(settings, info)
    settings.update(audio_mode='none')
    assert audio_arguments(settings, info) == ['-an']
    settings.update(audio_mode='auto')
    assert audio_arguments(settings, {**info, 'audio': []}) == ['-an']


@pytest.mark.parametrize('invalid', [dict(codec='av1'), dict(encoder='unknown'), dict(rate_mode='constant'),
                                   dict(video_bitrate=-1), dict(video_bitrate=float('nan')),
                                   dict(audio_mode='opus'), dict(audio_track=-1), dict(sample_rate=999)])
def test_invalid_settings_rejected(invalid):
    with pytest.raises(ValueError):
        normalize_settings(invalid)


@pytest.mark.parametrize('encoder', ['libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc'])
def test_bitrate_and_quality_modes_are_exclusive(encoder):
    bitrate = encoder_arguments(encoder, 18, 'medium', 1800)
    assert '-crf' not in bitrate and '-cq' not in bitrate
    assert bitrate[bitrate.index('-b:v') + 1] == '1800k'
    assert bitrate[bitrate.index('-maxrate') + 1] == '2700k'
    quality = encoder_arguments(encoder, 23, 'fast')
    assert ('-cq' in quality) if encoder.endswith('nvenc') else ('-crf' in quality)
    if encoder in ('libx265', 'hevc_nvenc'):
        assert quality[-2:] == ['-tag:v', 'hvc1']


def test_hardware_probe_and_fallback_keep_selected_codec(monkeypatch):
    seen = []
    monkeypatch.setattr('app.encoders.nvenc_available', lambda codec: seen.append(codec) or False)
    assert select_encoder('nvidia', 'hevc') == 'libx265'
    assert select_encoder('auto', 'h264') == 'libx264'
    assert seen == ['hevc', 'h264']


def make_job(video, target, **settings):
    return dict(video=str(video), target=str(target), mode='original', encoder='cpu',
                cues=[dataclasses.asdict(Cue(.1, 1, 'Subtitle'))], **settings)


def test_real_source_bitrate_controls_size_and_copies_audio(application, tmp_path):
    video, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'testsrc2=s=640x360:r=25:d=8',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8',
                '-c:v', 'libx264', '-b:v', '450k', '-maxrate', '600k', '-bufsize', '900k',
                '-c:a', 'aac', '-b:a', '96k', video])
    encode_video(make_job(video, target), tmp_path)
    original, result = probe_media_info(video), probe_media_info(target)
    assert result['video']['codec'] == 'h264'
    assert abs(original['duration'] - result['duration']) < .1
    assert target.stat().st_size < video.stat().st_size * 1.35
    hashes = [run_ffmpeg(['-v', 'error', '-i', path, '-map', '0:a:0', '-c:a', 'copy',
                          '-f', 'hash', '-hash', 'sha256', '-']).stdout for path in (video, target)]
    assert hashes[0] == hashes[1]


def test_real_hevc_failure_falls_back_and_keeps_bitrate_and_audio(application, tmp_path, monkeypatch):
    from app import video_export
    video, target = tmp_path / 'source.mkv', tmp_path / 'hevc.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=24:d=2',
                '-f', 'lavfi', '-i', 'sine=frequency=300:duration=2',
                '-f', 'lavfi', '-i', 'sine=frequency=700:duration=2',
                '-map', '0:v', '-map', '1:a', '-map', '2:a', '-c:v', 'libx264', '-c:a', 'pcm_s16le', video])
    target.write_bytes(b'prior result')
    original = video_export.run_encoder
    calls = []
    def encode(command, *args):
        encoder = command[command.index('-c:v') + 1]
        calls.append(encoder)
        assert target.read_bytes() == b'prior result'
        assert command[command.index('-b:v') + 1] == '300k'
        assert command[command.index('-map', command.index('[v]')) + 1] == '0:a:1'
        return 1 if encoder == 'hevc_nvenc' else original(command, *args)
    monkeypatch.setattr(video_export, 'select_encoder', lambda *args: 'hevc_nvenc')
    monkeypatch.setattr(video_export, 'run_encoder', encode)
    encode_video(make_job(video, target, codec='hevc', rate_mode='bitrate', video_bitrate=300,
                          audio_track=1, audio_mode='aac', audio_bitrate=96, sample_rate=48000,
                          channels=2, preset='ultrafast'), tmp_path)
    result = probe_media_info(target)
    assert calls == ['hevc_nvenc', 'libx265']
    assert result['video']['codec'] == 'hevc'
    assert len(result['audio']) == 1
    assert result['audio'][0]['codec'] == 'aac' and result['audio'][0]['sample_rate'] == 48000
    assert 'stereo' in result['audio'][0]['description']


def test_real_muted_export(application, tmp_path):
    video, target = tmp_path / 'source.mp4', tmp_path / 'muted.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=s=160x90:d=1', '-f', 'lavfi', '-i',
                'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', video])
    encode_video(make_job(video, target, audio_mode='none', rate_mode='bitrate', video_bitrate=200), tmp_path)
    assert probe_media_info(target)['audio'] == []


def test_dialog_dynamic_controls_estimates_and_project_restore(application, tmp_path, info):
    from app.export_dialog import ExportDialog
    video = tmp_path / 'source.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=s=160x90:d=1', '-c:v', 'libx264', video])
    dialog = ExportDialog(video, '原文字幕')
    dialog.show()
    for _ in range(100):
        QTest.qWait(20)
        if dialog.info is not None:
            break
    assert dialog.info is not None and dialog.save_button.isEnabled()
    assert not dialog.bitrate.isVisible()
    assert not dialog.controls['audio_track'].isEnabled()
    dialog.set_source_info(info)
    assert '预计' in dialog.estimate_label.text()
    controls = dialog.controls
    controls['rate_mode'].setCurrentIndex(controls['rate_mode'].findData('bitrate'))
    dialog.bitrate.setValue(12000)
    assert dialog.bitrate.isVisible() and '大于源文件' in dialog.estimate_label.text()
    controls['audio_mode'].setCurrentIndex(controls['audio_mode'].findData('copy'))
    assert not dialog.save_button.isEnabled()
    controls['audio_track'].setCurrentIndex(controls['audio_track'].findData(0))
    assert dialog.save_button.isEnabled() and not controls['sample_rate'].isEnabled()
    controls['codec'].setCurrentIndex(controls['codec'].findData('hevc'))
    settings = dialog.settings()
    project = tmp_path / 'saved.yingziproj'
    save_project(project, video, [Cue(0, 1, 'Text')], {'video_export': settings})
    restored = ExportDialog(video, '原文字幕', load_project(project)[0]['options']['video_export'])
    restored.stop_probe()
    restored.set_source_info(info)
    assert restored.settings() == settings
    restored.close()
    controls['rate_mode'].setCurrentIndex(controls['rate_mode'].findData('quality'))
    assert '可能明显大于源文件' in dialog.estimate_label.text()
    assert not dialog.bitrate.isVisible() and controls['crf'].isVisible()
    dialog.close()
