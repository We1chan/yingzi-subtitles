import sys
from types import SimpleNamespace as NS

import pytest

from app import worker
from app.subtitles import load_project, save_project


@pytest.mark.parametrize('failure_stage', ['extraction', 'recognition', 'alignment'])
@pytest.mark.parametrize('named_project', [False, True])
def test_stage_resume_and_resegment_reuse_durable_results(tmp_path, monkeypatch, failure_stage, named_project):
    video = tmp_path / 'video.mp4'
    video.touch()
    calls = {'recognition': [], 'alignment': [], 'extract': 0}
    fail = [True]
    class ASR:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return ASR()
        def transcribe(self, audio, **kwargs):
            calls['recognition'].append(audio)
            if failure_stage == 'recognition' and audio == 2 and fail[0]:
                fail[0] = False
                raise RuntimeError('simulated interruption')
            return [NS(text='Hello wonderful world.', language='English')]
    class Aligner:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return Aligner()
        def align(self, audio, **kwargs):
            calls['alignment'].append(audio)
            if failure_stage == 'alignment' and audio == 2 and fail[0]:
                fail[0] = False
                raise RuntimeError('simulated interruption')
            return [NS(items=[NS(text=text, start_time=i * .5, end_time=(i + 1) * .5)
                             for i, text in enumerate(['Hello', 'wonderful', 'world'])])]
    def extract(video, wav, track):
        calls['extract'] += 1
        wav.write_bytes(b'incomplete' if fail[0] else b'complete')
        if failure_stage == 'extraction' and fail[0]:
            fail[0] = False
            raise RuntimeError('simulated interruption')
        return 6
    monkeypatch.setitem(sys.modules, 'qwen_asr', NS(Qwen3ASRModel=ASR, Qwen3ForcedAligner=Aligner))
    monkeypatch.setattr(worker, 'model_issue', lambda *args: '')
    monkeypatch.setattr(worker, 'extract_audio', extract)
    monkeypatch.setattr(worker, 'speech_chunks', lambda *args, **kwargs: [(0, 2), (2, 4), (4, 6)])
    monkeypatch.setattr(worker, 'read_span', lambda wav, start, end: start)
    monkeypatch.setattr(worker, 'load_options', lambda *args: {})
    monkeypatch.setattr(worker, 'release', lambda: None)
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    job = {'video': str(video), 'output': str(tmp_path), 'language': 'English', 'bilingual': False,
           'max_chars': 16, 'segmentation_mode': 'fixed'}
    project = tmp_path / ('工程/保存的工程.yingziproj' if named_project else 'subtitles.yingzi.json')
    if named_project:
        job['project_path'] = str(project)
    with pytest.raises(RuntimeError, match='interruption'):
        worker.process(job)
    assert load_project(project)[0]['status'] == ('aligning' if failure_stage == 'alignment' else 'recognizing')
    if failure_stage == 'extraction':
        assert not (tmp_path / '.pipeline/audio.wav').exists()
        assert (tmp_path / '.pipeline/audio.partial.wav').read_bytes() == b'incomplete'
    worker.process({**job, 'resume_project': str(project)})
    if failure_stage == 'extraction':
        assert calls['extract'] == 2
        assert (tmp_path / '.pipeline/audio.wav').read_bytes() == b'complete'
        assert not (tmp_path / '.pipeline/audio.partial.wav').exists()
        assert calls['recognition'] == calls['alignment'] == [0, 2, 4]
    else:
        assert calls[failure_stage] == [0, 2, 2, 4]
        assert calls['extract'] == 1
    assert len(load_project(project)[1]) == 6
    count = {k: list(v) if isinstance(v, list) else v for k, v in calls.items()}
    worker.process({**job, 'resume_project': str(project), 'resegment_only': True, 'segmentation_mode': 'smart'})
    assert calls == count
    data, cues = load_project(project)
    assert len(cues) == 3 and data['status'] == 'complete'
    assert 'resegment_only' not in data['options'] and 'resume_project' not in data['options']
    with pytest.raises(ValueError, match='配置已改变'):
        worker.process({**job, 'resume_project': str(project), 'resegment_only': True, 'track': 1})
    cues[0].text = 'Manually corrected source.'
    save_project(project, video, cues, data['options'])
    with pytest.raises(ValueError, match='原文已经人工修改'):
        worker.process({**job, 'resume_project': str(project), 'resegment_only': True})
    assert load_project(project)[1][0].text == 'Manually corrected source.'
