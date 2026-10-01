"""Shared export settings, source inspection and size estimates (no Qt required)."""
import math
import re
import subprocess
from pathlib import Path

from .media import ffmpeg, CREATE_NO_WINDOW


DEFAULTS = dict(codec='h264', encoder='auto', resolution='original', rate_mode='source',
                video_bitrate=4000, crf=23, preset='medium', audio_mode='auto',
                audio_track='all', audio_bitrate=128, sample_rate=0, channels=0)
COPY_AUDIO = {'aac', 'mp3', 'ac3', 'eac3', 'alac'}


def normalize_settings(values=None):
    values = values or {}
    settings = {**DEFAULTS, **{k: v for k, v in values.items() if k in DEFAULTS}}
    # Retain explicitly requested quality and track behavior in older worker jobs.
    if 'rate_mode' not in values and 'crf' in values:
        settings['rate_mode'] = 'quality'
    if 'audio_track' not in values and not values.get('all_audio', True):
        settings['audio_track'] = int(values.get('track', 0))
    choices = dict(codec=('h264', 'hevc'), encoder=('auto', 'cpu', 'nvidia'),
                   resolution=('original', '1080', '720'), rate_mode=('source', 'bitrate', 'quality'),
                   crf=(18, 23, 28), preset=('ultrafast', 'fast', 'medium', 'slow'),
                   audio_mode=('auto', 'copy', 'aac', 'none'), audio_bitrate=(64, 96, 128, 160, 192, 256, 320),
                   sample_rate=(0, 44100, 48000), channels=(0, 1, 2))
    for key, allowed in choices.items():
        if settings[key] not in allowed:
            raise ValueError(f'不支持的导出设置：{key}')
    bitrate = settings['video_bitrate']
    if not isinstance(bitrate, (int, float)) or not math.isfinite(bitrate) or not 1 <= bitrate <= 200000:
        raise ValueError('视频码率应在 1～200000 kbps 之间。')
    settings['video_bitrate'] = round(bitrate)
    track = settings['audio_track']
    if track != 'all' and (type(track) is not int or track < 0):
        raise ValueError('不支持的音轨选项。')
    return settings


def parse_media_info(output, size_bytes):
    """Parse input-only FFmpeg metadata; bundled FFmpeg does not include ffprobe."""
    duration = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', output)
    seconds = sum(float(value) * factor for value, factor in zip(duration.groups(), (3600, 60, 1))) if duration else 0
    streams = []
    for line in output.splitlines():
        match = re.search(r'Stream #0:\d+.*?: (Video|Audio): (\w+)', line)
        if not match:
            continue
        bitrate = re.search(r'(\d+(?:\.\d+)?) kb/s', line)
        sample_rate = re.search(r'(\d+) Hz', line)
        streams.append(dict(kind=match[1].lower(), codec=match[2],
                            bitrate=float(bitrate[1]) if bitrate else None,
                            sample_rate=int(sample_rate[1]) if sample_rate else None,
                            description=line.strip()))
    video = next((s for s in streams if s['kind'] == 'video'), None)
    return dict(duration=seconds, size_bytes=size_bytes, video=video,
                total_bitrate=size_bytes * 8 / seconds / 1000 if seconds else None,
                audio=[s for s in streams if s['kind'] == 'audio'])


def probe_media_info(path):
    result = subprocess.run([ffmpeg(), '-hide_banner', '-nostdin', '-i', str(path)],
                            capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    return parse_media_info(result.stderr.decode('utf-8', errors='replace'), Path(path).stat().st_size)


def audio_plan(settings, info):
    """Return source track indices and codecs; retain ordering for per-stream options."""
    if settings['audio_mode'] == 'none':
        return []
    tracks = info['audio']
    if settings['audio_track'] == 'all':
        indices = range(len(tracks))
    elif not tracks:
        return []
    elif settings['audio_track'] >= len(tracks):
        raise ValueError('所选音轨不存在，请重新选择音轨。')
    else:
        indices = [settings['audio_track']]
    plan = []
    for index in indices:
        compatible = tracks[index]['codec'] in COPY_AUDIO
        mode = settings['audio_mode']
        if mode == 'copy' and not compatible:
            raise ValueError(f'第 {index + 1} 音轨不能直接复制到 MP4，请选择自动或 AAC 转码。')
        plan.append((index, 'copy' if mode == 'copy' or (mode == 'auto' and compatible) else 'aac'))
    return plan


def audio_arguments(settings, info):
    plan = audio_plan(settings, info)
    if not plan:
        return ['-an']
    arguments = []
    for output_index, (index, codec) in enumerate(plan):
        arguments += ['-map', f'0:a:{index}', f'-c:a:{output_index}', codec]
        if codec == 'aac':
            arguments += [f'-b:a:{output_index}', f"{settings['audio_bitrate']}k"]
            if settings['sample_rate']:
                arguments += [f'-ar:a:{output_index}', str(settings['sample_rate'])]
            if settings['channels']:
                arguments += [f'-ac:a:{output_index}', str(settings['channels'])]
    return arguments


def output_audio_bitrate(settings, info):
    rates = [info['audio'][index]['bitrate'] if codec == 'copy' else settings['audio_bitrate']
             for index, codec in audio_plan(settings, info)]
    return sum(rates) if all(rate is not None for rate in rates) else None


def video_bitrate(settings, info):
    if settings['rate_mode'] == 'quality':
        return None
    if settings['rate_mode'] == 'bitrate':
        return settings['video_bitrate']
    # Respect both the source video rate and the original total size budget.
    source_rate = (info.get('video') or {}).get('bitrate')
    total = info.get('total_bitrate')
    output_audio = output_audio_bitrate(settings, info)
    if not source_rate and total:
        source_rate = total - sum(track['bitrate'] or 128 for track in info['audio'])
    if total and output_audio is not None:
        budget = total * .98 - output_audio
        if budget <= 0:
            raise ValueError('音频码率已超过源文件体积预算，请降低音频码率或自定义视频码率。')
        source_rate = min(source_rate, budget) if source_rate else budget
    if not source_rate or source_rate <= 0:
        raise ValueError('无法估算源视频码率，请选择自定义码率或恒定画质。')
    return max(1, round(source_rate))


def estimated_size(settings, info):
    video = video_bitrate(settings, info)
    audio = output_audio_bitrate(settings, info)
    if video is None or audio is None or not info['duration']:
        return None
    return (video + audio) * 1000 * info['duration'] / 8 * 1.02


def format_size(size):
    return f'{size / 1_000_000_000:.2f} GB' if size >= 1_000_000_000 else f'{size / 1_000_000:.1f} MB'
