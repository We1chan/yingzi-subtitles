"""Background video export with the same Qt subtitle pixels as the live preview."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

from .media import CREATE_NO_WINDOW, ffmpeg
from .subtitles import Cue, validate, subtitle_text, needs_translation, clean_line
from .encoders import select_encoder, encoder_arguments


class ExportCancelled(Exception):
    pass


def probe_video(path, resolution='original'):
    import imageio_ffmpeg
    if resolution not in ('original', '1080', '720'):
        raise ValueError('不支持的输出分辨率。')
    # FFmpeg autorotates first; normalize non-square pixels for both layout and export.
    filters = 'scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1'
    if resolution != 'original':
        limit = int(resolution)
        filters += f",scale=w='min(iw,{limit}*16/9)':h='min(ih,{limit})':force_original_aspect_ratio=decrease:force_divisible_by=2"
    reader = imageio_ffmpeg.read_frames(str(path), output_params=['-vf', filters, '-frames:v', '1'])
    try:
        metadata = next(reader)
    except Exception as exc:
        raise ValueError('无法读取视频画面。请选择可解码的视频文件，纯音频不能导出为视频。') from exc
    finally:
        reader.close()
    return metadata, filters


def build_overlays(cues, mode, style, size, work, progress, cancelled):
    from .caption_style import render_overlay
    width, height = size
    manifest = ['ffconcat version 1.0']
    blank = render_overlay('', style, width, height)
    if not blank.save(str(work / 'blank.png')):
        raise OSError('无法保存字幕图层，请检查磁盘空间。')

    def append(filename, duration=None):
        manifest.extend([f"file '{filename}'", 'option framerate 1000'])
        if duration is not None:
            manifest.append(f'duration {duration:.6f}')

    cursor = 0
    for i, cue in enumerate(cues):
        if cancelled():
            raise ExportCancelled()
        start, end = round(cue.start, 3), round(cue.end, 3)
        if start > cursor:
            append('blank.png', start - cursor)
        image = render_overlay(subtitle_text(cue, mode), style, width, height, mode == 'translated',
                               len(clean_line(cue.text).splitlines()))
        filename = f'caption-{i:06d}.png'
        if not image.save(str(work / filename)):
            raise OSError('无法保存字幕图层，请检查磁盘空间。')
        append(filename, max(.001, end - max(start, cursor)))
        cursor = end
        progress(round(5 + 20 * (i + 1) / len(cues)), f'准备字幕样式 {i + 1}/{len(cues)}')
    # Hold a transparent frame after the last subtitle. Never shorten the main video.
    append('blank.png')
    target = work / 'captions.ffconcat'
    target.write_text('\n'.join(manifest) + '\n', encoding='utf-8')
    return target


def encode_video(job, work, progress=lambda *args: None, cancelled=lambda: False):
    from .caption_style import CaptionStyle
    video, target = Path(job['video']).resolve(), Path(job['target']).resolve()
    if video == target or (target.exists() and video.samefile(target)):
        raise ValueError('请另选导出文件名，不能覆盖原视频。')
    if target.suffix.lower() != '.mp4':
        raise ValueError('当前视频导出格式为 MP4。')
    cues = [Cue(**row) for row in job['cues']]
    validate(cues)
    if not cues:
        raise ValueError('请先生成字幕。')
    mode = job.get('mode', 'bilingual')
    if mode not in ('original', 'translated', 'bilingual'):
        raise ValueError('不支持的字幕内容选项。')
    if mode != 'original' and any(needs_translation(cue) for cue in cues):
        raise ValueError('译文尚未完成，请继续翻译或选择原文字幕。')
    progress(1, '读取视频分辨率与时长')
    metadata, filters = probe_video(video, job.get('resolution', 'original'))
    if cancelled():
        raise ExportCancelled()
    style = CaptionStyle.from_dict(job.get('subtitle_style'))
    manifest = build_overlays(cues, mode, style, metadata['size'], work, progress, cancelled)
    partial = work / 'encoded.mp4'
    crf = int(job.get('crf', 18))
    preset = job.get('preset', 'fast')
    if crf not in (18, 23, 28) or preset not in ('fast', 'medium', 'slow', 'ultrafast'):
        raise ValueError('不支持的视频质量或速度设置。')
    audio_map = '0:a?' if job.get('all_audio', True) else f"0:a:{max(0, int(job.get('track', 0)))}?"
    encoder = select_encoder(job.get('encoder', 'cpu'))
    log_path = work / 'ffmpeg.log'
    log_path.write_text('', encoding='utf-8')
    for attempt in range(2):
        if cancelled():
            raise ExportCancelled()
        progress(26, '使用 NVIDIA 显卡编码' if encoder == 'h264_nvenc' else '使用 CPU 编码')
        command = [ffmpeg(), '-hide_banner', '-nostdin', '-y', '-i', str(video),
                   '-f', 'concat', '-safe', '0', '-i', str(manifest),
                   '-filter_complex', f'[0:v:0]{filters}[base];[base][1:v:0]overlay=eof_action=repeat:repeatlast=1:format=auto,format=yuv420p,sidedata=mode=delete:type=DISPLAYMATRIX[v]',
                   '-map', '[v]', '-map', audio_map, '-map_metadata', '0', '-map_chapters', '0',
                   *encoder_arguments(encoder, crf, preset), '-fps_mode', 'vfr',
                   '-c:a', 'aac', '-b:a', '192k', '-metadata:s:v:0', 'rotate=0',
                   '-movflags', '+faststart', '-progress', 'pipe:1', '-nostats', str(partial)]
        code = run_encoder(command, log_path, metadata['duration'], progress, cancelled)
        if code == 0:
            break
        if encoder == 'h264_nvenc':
            progress(26, '显卡编码失败，正在回退 CPU；原视频与目标文件保持完整')
            encoder = 'libx264'
        else:
            raise RuntimeError('视频编码失败：\n' + log_path.read_text(encoding='utf-8', errors='replace')[-2500:])
    if not partial.exists() or not partial.stat().st_size:
        raise RuntimeError('编码器没有生成有效的视频文件。')
    # Work is created beside the destination so this is an atomic same-volume rename.
    partial.replace(target)
    progress(100, f'视频已导出：{target}')
    return target


def run_encoder(command, log_path, duration, progress, cancelled):
    messages = queue.Queue()
    with log_path.open('ab') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, creationflags=CREATE_NO_WINDOW)

        def read_progress():
            for line in iter(process.stdout.readline, b''):
                messages.put(line.decode('utf-8', errors='replace').strip())

        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        try:
            while process.poll() is None:
                if cancelled():
                    raise ExportCancelled()
                try:
                    line = messages.get(timeout=.1)
                except queue.Empty:
                    continue
                if line.startswith('out_time_us='):
                    try:
                        seconds = int(line.split('=', 1)[1]) / 1_000_000
                        ratio = seconds / duration if duration else 0
                        progress(min(98, round(26 + 72 * ratio)), f'正在导出视频 · 已处理 {seconds:.1f} 秒')
                    except ValueError:
                        pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=2)
            process.stdout.close()
    if cancelled():
        raise ExportCancelled()
    return process.returncode


def main():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    from .gui import initialize_fonts
    application = QApplication([])
    initialize_fonts(application)
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    work = Path(args.job).parent

    def event(kind, **data):
        print(json.dumps({'type': kind, **data}, ensure_ascii=False), flush=True)

    try:
        job = json.loads(Path(args.job).read_text(encoding='utf-8'))
        target = encode_video(job, work, lambda percent, message: event('progress', percent=percent, message=message),
                              lambda: (work / 'cancel').exists())
        event('export_done', target=str(target))
        return 0
    except ExportCancelled:
        event('export_cancelled')
        return 0
    except Exception as exc:
        event('error', message=str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
