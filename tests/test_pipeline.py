import json
import sys
from types import SimpleNamespace as NS

import pytest

from app import worker
from app.config import model_paths
from app.pipeline import build_cues, fingerprint
from app.subtitles import load_project, save_project, validate


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
    project = tmp_path / ('工程/保存的工程.yingziproj' if named_project else 'subtitles.yingziproj')
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


@pytest.mark.parametrize('name', ['Qwen3-ASR-0.6B', 'cohere-transcribe-03-2026', 'parakeet-tdt-0.6b-v2-onnx'])
def test_retired_model_cache_requires_regeneration_without_overwriting_project(tmp_path, name):
    video = tmp_path / 'video.mp4'
    video.touch()
    project = tmp_path / 'subtitles.yingziproj'
    job = {'video': str(video), 'output': str(tmp_path), 'asr_model': str(tmp_path / name), 'bilingual': False}
    save_project(project, video, [], job, status='recognizing')
    before = project.read_bytes()
    folder = tmp_path / '.pipeline'
    folder.mkdir()
    legacy_paths = {**model_paths(job), 'asr_model': tmp_path / name}
    (folder / 'state.json').write_text(json.dumps({'fingerprint': fingerprint(video, job, legacy_paths)}))
    with pytest.raises(ValueError, match='配置已改变'):
        worker.process({**job, 'resume_project': str(project)})
    assert project.read_bytes() == before


@pytest.mark.parametrize('language, parts, expected', [
    ('English', ['We need to finish the', 'remaining work before leaving.'],
     'We need to finish the remaining work before leaving.'),
    ('Chinese', ['今天我们一起学习如何使用', '这个软件生成字幕。'], '今天我们一起学习如何使用这个软件生成字幕。'),
    ('Japanese', ['この機能はとても便利', 'です。'], 'この機能はとても便利です。'),
])
def test_sentence_continues_across_recognition_windows(language, parts, expected):
    records, alignments = [], []
    for i, text in enumerate(parts):
        records.append({'start': 30 + i * 2, 'end': 32 + i * 2, 'text': text, 'language': language})
        alignments.append([{'text': text, 'start_time': .1, 'end_time': 1.9}])
    state = {'records': records, 'alignments': alignments}
    cues = build_cues(state, {'segmentation_mode': 'smart'})
    assert [cue.text for cue in cues] == [expected]
    assert cues[0].start == 30.1 and cues[0].end == 33.9
    validate(cues)
    assert [cue.text for cue in build_cues(state, {'segmentation_mode': 'fixed'})] == parts


def test_resegmentation_does_not_bridge_unaligned_speech_or_language_changes():
    records = [
        {'start': 10, 'end': 12, 'text': 'We still need', 'language': 'English'},
        {'start': 12, 'end': 14, 'text': 'to finish this.', 'language': 'English'},
        {'start': 14, 'end': 16, 'text': '继续工作。', 'language': 'Chinese'},
    ]
    state = {'records': records, 'alignments': [
        [{'text': record['text'], 'start_time': .1, 'end_time': 1.9}] for record in records]}
    partial = build_cues({**state, 'alignments': [state['alignments'][0], None, state['alignments'][2]]}, {'segmentation_mode': 'smart'})
    assert [cue.text for cue in partial] == ['We still need']
    assert [cue.text for cue in build_cues(state, {'segmentation_mode': 'smart'})] == ['We still need to finish this.', '继续工作。']


def test_cached_resegmentation_repairs_fragments_without_inference_or_translation(tmp_path, monkeypatch):
    video = tmp_path / 'video.mp4'
    video.touch()
    project = tmp_path / 'subtitles.yingziproj'
    job = {'video': str(video), 'output': str(tmp_path), 'language': 'English',
           'bilingual': True, 'translation_backend': 'online', 'segmentation_mode': 'smart'}
    records = [{'start': i * 2, 'end': (i + 1) * 2, 'text': text, 'language': 'English'}
               for i, text in enumerate(['We need the', 'remaining work today.', 'Done.'])]
    state = {'fingerprint': fingerprint(video, job, model_paths(job)), 'records': records,
             'aligned_complete': True, 'alignments': [
                 [{'text': record['text'], 'start_time': .1, 'end_time': 1.9}] for record in records]}
    previous = build_cues(state, {'segmentation_mode': 'fixed'})
    for cue in previous:
        cue.translation = '旧译文'
    save_project(project, video, previous, job)
    cache = tmp_path / '.pipeline/state.json'
    cache.parent.mkdir()
    cache.write_text(json.dumps(state), encoding='utf-8')
    cached_bytes = cache.read_bytes()
    monkeypatch.setattr(worker, 'model_issue', lambda *args: pytest.fail('Unexpected model load'))
    monkeypatch.setattr(worker, 'extract_audio', lambda *args: pytest.fail('Unexpected extraction'))
    monkeypatch.setattr('app.online_translation.OnlineTranslator', lambda *args, **kwargs: pytest.fail('Unexpected API call'))
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    worker.process({**job, 'resume_project': str(project), 'resegment_only': True})
    data, cues = load_project(project)
    assert [cue.text for cue in cues] == ['We need the remaining work today.', 'Done.']
    assert cues[0].start == .1 and cues[0].end == 3.9 and not cues[0].translation
    assert cues[1].translation == '旧译文'
    assert data['status'] == 'complete' and cache.read_bytes() == cached_bytes


def test_empty_alignment_cannot_be_hidden_by_a_neighbouring_record():
    state = {'records': [
        {'start': 0, 'end': 1, 'text': 'We', 'language': 'English'},
        {'start': 1, 'end': 3, 'text': 'need this.', 'language': 'English'},
    ], 'alignments': [[], [{'text': 'need this', 'start_time': .1, 'end_time': 1.9}]]}
    with pytest.raises(ValueError, match='未获得有效时间戳'):
        build_cues(state, {'segmentation_mode': 'smart'})
