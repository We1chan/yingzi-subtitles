from pathlib import Path
import wave

import numpy as np

from app.media import run_ffmpeg, extract_audio, speech_chunks


def test_audio_track_selection_preserves_delayed_timeline(tmp_path):
    video = tmp_path / '多音轨 视频.mp4'
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=10:d=4', '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono:d=4', '-itsoffset', '1.5', '-f', 'lavfi', '-i', 'sine=frequency=600:sample_rate=16000:duration=2', '-map', '0:v', '-map', '1:a', '-map', '2:a', '-t', '4', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', video])
    first, second = tmp_path / 'silent.wav', tmp_path / 'delayed.wav'
    extract_audio(video, first, track=0)
    extract_audio(video, second, track=1)
    def read(path):
        with wave.open(str(path), 'rb') as source:
            return np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16)
    silent, delayed = read(first), read(second)
    assert np.abs(silent).max() == 0
    assert np.abs(delayed[:16000]).max() < 10
    assert np.abs(delayed[32000:40000]).max() > 500
    assert speech_chunks(first) == []
