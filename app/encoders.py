"""Probe the installed FFmpeg and driver before choosing optional GPU encoding."""
import functools
import subprocess

from .media import ffmpeg, CREATE_NO_WINDOW


@functools.lru_cache(maxsize=2)
def nvenc_available(codec='h264'):
    try:
        result = subprocess.run([ffmpeg(), '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
            'color=c=black:s=640x360:r=24:d=1', '-frames:v', '1', '-pix_fmt', 'yuv420p',
            '-c:v', 'hevc_nvenc' if codec == 'hevc' else 'h264_nvenc', '-f', 'null', '-'], capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def select_encoder(choice, codec='h264'):
    if choice not in ('cpu', 'auto', 'nvidia') or codec not in ('h264', 'hevc'):
        raise ValueError('不支持的视频编码方式。')
    if choice != 'cpu' and nvenc_available(codec):
        return 'hevc_nvenc' if codec == 'hevc' else 'h264_nvenc'
    return 'libx265' if codec == 'hevc' else 'libx264'


def encoder_arguments(encoder, quality, preset, bitrate=None):
    if encoder not in ('h264_nvenc', 'hevc_nvenc', 'libx264', 'libx265'):
        raise ValueError('不支持的视频编码器。')
    arguments = ['-c:v', encoder]
    if encoder.endswith('_nvenc'):
        speed = {'ultrafast': 'p1', 'fast': 'p3', 'medium': 'p4', 'slow': 'p6'}[preset]
        arguments += ['-preset', speed, '-rc', 'vbr']
        if bitrate is None:
            arguments += ['-cq', str(quality), '-b:v', '0']
    else:
        arguments += ['-preset', preset]
        if bitrate is None:
            arguments += ['-crf', str(quality)]
    if bitrate is not None:
        arguments += ['-b:v', f'{bitrate}k', '-maxrate', f'{round(bitrate * 1.5)}k', '-bufsize', f'{bitrate * 2}k']
    if encoder in ('hevc_nvenc', 'libx265'):
        arguments += ['-tag:v', 'hvc1']
    return arguments
