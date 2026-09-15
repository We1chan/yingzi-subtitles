import sys
from types import SimpleNamespace

import pytest

from app import worker, translation
from app.subtitles import Cue, load_project, save_project


def test_resume_translates_english_and_japanese_and_preserves_completed_rows(tmp_path, monkeypatch):
    project = tmp_path / 'subtitles.yingzi.json'
    selected = tmp_path / 'translation.gguf'
    cues = [Cue(0, 1, '中文', language='Chinese'), Cue(1, 2, 'Hello.', language='English'),
            Cue(2, 3, 'こんにちは', language='Japanese'), Cue(3, 4, 'Done.', '已完成。', 'English')]
    save_project(project, tmp_path / 'video.mp4', cues, {'translation_model': str(selected)}, status='translating')
    calls = []
    class Translator:
        def __init__(self, *args, **kwargs):
            assert kwargs['model_path'] == selected
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def translate(self, rows, context):
            calls.extend(rows)
            return {row['id']: '你好。' for row in rows}
    monkeypatch.setattr(translation, 'LocalTranslator', Translator)
    monkeypatch.setattr(worker, 'readiness', lambda options: {'字幕翻译': True, '翻译引擎': True})
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    worker.process({'video': str(tmp_path / 'video.mp4'), 'output': str(tmp_path), 'resume_project': str(project), 'bilingual': True})
    data, result = load_project(project)
    assert [row['id'] for row in calls] == [2, 3]
    assert [row['language'] for row in calls] == ['English', 'Japanese']
    assert result[0].translation == ''
    assert result[3].translation == '已完成。'
    assert data['options']['translation_model'] == str(selected)
    assert data['status'] == 'complete'
    assert 'Hello.\n你好。' in (tmp_path / 'bilingual.srt').read_text(encoding='utf-8-sig')
    assert 'こんにちは\n你好。' in (tmp_path / 'bilingual.vtt').read_text(encoding='utf-8')


@pytest.mark.parametrize('bilingual', [True, False])
def test_missing_translator_keeps_english_checkpoint_and_original_only_works(tmp_path, monkeypatch, bilingual):
    project = tmp_path / 'subtitles.yingzi.json'
    video = str(tmp_path / 'video.mp4')
    save_project(project, video, [Cue(0, 1, 'Hello.', language='English')])
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, 'readiness', lambda options: {'字幕翻译': False, '翻译引擎': False})
    job = {'video': video, 'output': str(tmp_path), 'resume_project': str(project), 'bilingual': bilingual}
    if bilingual:
        with pytest.raises(ValueError, match='翻译模型'):
            worker.process(job)
    else:
        worker.process(job)
    data, cues = load_project(project)
    assert data['status'] == ('translating' if bilingual else 'complete')
    assert cues[0].text == 'Hello.'
    assert (tmp_path / 'original.srt').exists()


def test_worker_routes_selected_asr_and_aligner_and_explicit_english(tmp_path, monkeypatch):
    video = tmp_path / 'video.mp4'
    video.touch()
    seen = {}
    class ASR:
        @staticmethod
        def from_pretrained(path, **kwargs):
            seen['asr'] = path
            return ASR()
        def transcribe(self, **kwargs):
            seen['language'] = kwargs['language']
            return [SimpleNamespace(text='Hello world.', language='English')]
    class Aligner:
        @staticmethod
        def from_pretrained(path, **kwargs):
            seen['align'] = path
            return Aligner()
        def align(self, **kwargs):
            return [SimpleNamespace(items=[SimpleNamespace(text='Hello', start_time=0, end_time=.5), SimpleNamespace(text='world', start_time=.5, end_time=1.5)])]
    monkeypatch.setitem(sys.modules, 'qwen_asr', SimpleNamespace(Qwen3ASRModel=ASR, Qwen3ForcedAligner=Aligner))
    monkeypatch.setattr(worker, 'model_issue', lambda *args: '')
    monkeypatch.setattr(worker, 'extract_audio', lambda *args: 2)
    monkeypatch.setattr(worker, 'speech_chunks', lambda *args, **kwargs: [(0, 2)])
    monkeypatch.setattr(worker, 'read_span', lambda *args: None)
    monkeypatch.setattr(worker, 'load_options', lambda *args: {})
    monkeypatch.setattr(worker, 'release', lambda: None)
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    job = {'video': str(video), 'output': str(tmp_path), 'asr_model': str(tmp_path / 'custom-asr'), 'align_model': str(tmp_path / 'custom-align'), 'language': 'English', 'bilingual': False}
    worker.process(job)
    assert seen == {'asr': job['asr_model'], 'align': job['align_model'], 'language': 'English'}
    data, cues = load_project(tmp_path / 'subtitles.yingzi.json')
    assert data['options']['asr_model'] == job['asr_model']
    assert cues[0].text == 'Hello world.'
