"""Probe the installed FFmpeg and driver before choosing optional GPU encoding."""
import functools
import subprocess

from .media import ffmpeg, CREATE_NO_WINDOW


@functools.lru_cache(maxsize=1)
def nvenc_available():
    try:
        result = subprocess.run([ffmpeg(), '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
            'color=c=black:s=640x360:r=24:d=1', '-frames:v', '1', '-pix_fmt', 'yuv420p',
            '-c:v', 'h264_nvenc', '-f', 'null', '-'], capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def select_encoder(choice):
    if choice not in ('cpu', 'auto', 'nvidia'):
        raise ValueError('不支持的视频编码方式。')
    return 'h264_nvenc' if choice != 'cpu' and nvenc_available() else 'libx264'


def encoder_arguments(encoder, quality, preset):
    if encoder == 'h264_nvenc':
        speed = {'ultrafast': 'p1', 'fast': 'p3', 'medium': 'p4', 'slow': 'p6'}[preset]
        return ['-c:v', encoder, '-rc', 'vbr', '-cq', str(quality), '-b:v', '0', '-preset', speed]
    return ['-c:v', 'libx264', '-crf', str(quality), '-preset', preset]
