import re
import subprocess
import wave
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(arguments):
    result = subprocess.run([ffmpeg(), '-hide_banner', '-nostdin', *map(str, arguments)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace')[-2500:]
        raise RuntimeError(f'视频音频处理失败，请确认所选音轨存在、文件可播放。\n{detail}')
    return result


def extract_audio(video, output, track=0):
    # Async resampling keeps timestamps in sync through discontinuities and delayed audio tracks.
    run_ffmpeg(['-y', '-i', video, '-map', f'0:a:{int(track)}', '-vn', '-af', 'aresample=16000:async=1:first_pts=0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', output])
    with wave.open(str(output), 'rb') as audio:
        return audio.getnframes() / audio.getframerate()


def audio_tracks(video):
    result = subprocess.run([ffmpeg(), '-hide_banner', '-i', str(video)], capture_output=True, creationflags=CREATE_NO_WINDOW)
    output = result.stderr.decode('utf-8', errors='replace')
    return [line.strip() for line in output.splitlines() if re.search(r'Stream #\d+:\d+.*Audio:', line)]


def speech_chunks(audio_path, max_seconds=24.0, emit=lambda *args: None):
    """Run VAD in bounded windows, retaining absolute offsets for multi-hour media."""
    import numpy as np
    import soundfile as sf
    import torch
    from silero_vad import load_silero_vad, get_speech_timestamps

    model = load_silero_vad(onnx=True)
    spans = []
    with sf.SoundFile(audio_path) as audio:
        sr = audio.samplerate
        total = len(audio)
        block_samples = sr * 300
        for base in range(0, total, block_samples):
            audio.seek(base)
            samples = audio.read(min(block_samples, total - base), dtype='float32')
            timestamps = get_speech_timestamps(torch.from_numpy(samples), model, sampling_rate=sr, threshold=0.35, min_speech_duration_ms=180, min_silence_duration_ms=350, speech_pad_ms=180, max_speech_duration_s=max_seconds)
            for span in timestamps:
                start, end = (base + span['start']) / sr, (base + span['end']) / sr
                if spans and start - spans[-1][1] < 0.7 and end - spans[-1][0] <= max_seconds:
                    spans[-1] = (spans[-1][0], end)
                else:
                    spans.append((start, end))
            emit(min(14, 5 + (base + len(samples)) / total * 9), f'检测语音：{(base + len(samples)) / sr:.0f} 秒')
    return spans


def read_span(audio_path, start, end):
    import soundfile as sf
    with sf.SoundFile(audio_path) as audio:
        sr = audio.samplerate
        audio.seek(round(start * sr))
        return audio.read(round((end - start) * sr), dtype='float32'), sr
