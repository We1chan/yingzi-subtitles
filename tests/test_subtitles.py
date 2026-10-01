from types import SimpleNamespace

import pytest

from app.subtitles import Cue, aligned_cues, timestamp, parse_time, render, validate, save_project, load_project
from app.translation import parse_translations


def token(text, start, end):
    return SimpleNamespace(text=text, start_time=start, end_time=end)


def test_timestamp_rounds_across_minute_and_supports_long_video():
    assert timestamp(59.9996) == '00:01:00,000'
    assert timestamp(360000.123) == '100:00:00,123'
    assert parse_time('100:00:00.123') == 360000.123
    with pytest.raises(ValueError):
        parse_time('00:60:00,000')


def test_japanese_punctuation_absolute_offsets_and_sentence_breaks():
    items = [token('今日', .4, .8), token('は', .8, 1), token('晴れ', 1, 1.5), token('です', 1.5, 2), token('東京', 3.5, 4), token('へ', 4, 4.2), token('行き', 4.2, 4.6), token('ます', 4.6, 5)]
    cues = aligned_cues('今日は晴れです。東京へ行きます！', items, offset=123.4, duration=6, language='Japanese')
    assert [c.text for c in cues] == ['今日は晴れです。', '東京へ行きます！']
    assert cues[0].start == 123.8
    assert cues[1].start == 126.9
    assert cues[0].end < cues[1].start


def test_full_width_latin_does_not_lose_original_spelling():
    cues = aligned_cues('ＡＰＩを使います。', [token('API', 0, .5), token('を', .5, .6), token('使い', .6, 1), token('ます', 1, 1.5)], duration=2)
    assert cues[0].text == 'ＡＰＩを使います。'


def test_alignment_missing_words_is_an_error():
    with pytest.raises(ValueError):
        aligned_cues('今日は晴れです。', [token('今日', 0, 1)])
    with pytest.raises(ValueError):
        aligned_cues('今日は晴れです。', [])


def test_subtitle_export_bilingual_pairs_share_timestamp():
    cues = [Cue(1.23, 3.5, 'こんにちは。', '你好。', 'Japanese'), Cue(5, 7, '已经是中文。', '', 'Chinese')]
    result = render(cues)
    assert '00:00:01,230 --> 00:00:03,500\nこんにちは。\n你好。' in result
    assert result.count('已经是中文。') == 1
    assert 'こんにちは。' not in render(cues, 'translated')
    assert render(cues, format='vtt').startswith('WEBVTT\n')


def test_vtt_escapes_markup_from_speech():
    assert '&lt;script&gt;' in render([Cue(0, 1, '<script>')], format='vtt')


def test_reject_invalid_or_overlapping_subtitles():
    for cues in ([Cue(2, 1, 'a')], [Cue(-1, 1, 'a')], [Cue(0, float('nan'), 'a')], [Cue(0, 2, 'a'), Cue(1, 3, 'b')]):
        with pytest.raises(ValueError):
            validate(cues)


def test_project_roundtrip_unicode_and_timing(tmp_path):
    path = tmp_path / '映字.yingziproj'
    cues = [Cue(2.3, 5, '日本語', '日语', 'Japanese')]
    save_project(path, 'D:/视频/日本語.mp4', cues, {'bilingual': True})
    data, loaded = load_project(path)
    assert loaded == cues
    assert data['video'] == 'D:/视频/日本語.mp4'


def test_translation_requires_exact_unique_ids():
    assert parse_translations('{"translations":[{"id":7,"text":"你好"}]}', [7]) == {7: '你好'}
    for content in ('{"translations":[{"id":8,"text":"你好"}]}', '{"translations":[{"id":7,"text":""}]}', '{"translations":[{"id":7,"text":"甲"},{"id":7,"text":"乙"}]}'):
        with pytest.raises(ValueError):
            parse_translations(content, [7])


def test_english_word_spacing_apostrophes_and_sentence_breaks():
    words = ['Dr.', 'Smith', "can't", 'come.', 'We', 'leave', 'now.']
    items = [token(word, i * .4, (i + 1) * .4) for i, word in enumerate(words)]
    cues = aligned_cues(' '.join(words), items, language='English', max_chars=80, offset=10)
    assert [cue.text for cue in cues] == ["Dr. Smith can't come.", 'We leave now.']
    assert cues[0].start == 10
    assert cues[1].start == 11.6
    short = aligned_cues(' '.join(words), items, language='English', max_chars=16)
    assert ' '.join(cue.text for cue in short) == ' '.join(words)


def test_japanese_short_final_particle_keeps_a_word_and_character_limit():
    cues = aligned_cues('そういう姿勢が。', [token('そういう', 0, 1), token('姿勢', 1, 1.7), token('が', 1.7, 1.9)], max_chars=7, language='Japanese')
    assert [cue.text for cue in cues] == ['そういう', '姿勢が。']
    assert all(len(cue.text) <= 7 for cue in cues)


def test_translation_retries_punctuation_only_rows_and_keeps_context():
    import io
    import json
    from app.translation import LocalTranslator
    translator = LocalTranslator(slots=1)
    translator.port = 1234
    payloads = []
    def respond(request, timeout):
        data = json.loads(request.data)
        content = json.loads(data['messages'][1]['content'])
        payloads.append(content)
        rows = content['subtitles']
        assert len(rows) == 1
        answer = [{'id': row['id'], 'text': '。' if len(payloads) % 2 else '需要购买。'} for row in rows]
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'translations': answer})}}]}).encode())
    translator.client.open = respond
    result = translator.translate([{'id': 1, 'text': 'マレーシア'}, {'id': 2, 'text': 'から買わなくてはならないのです。'}], 'context')
    assert result == {1: '需要购买。', 2: '需要购买。'}
    assert payloads[-1]['previous_context'] == 'context\nマレーシア'
    assert '買わなくてはならない' in payloads[0]['following_context']
    assert len(payloads) == 4


def answer_rows(payloads, barrier=None):
    import io
    import json
    def respond(request, timeout):
        content = json.loads(json.loads(request.data)['messages'][1]['content'])
        row = content['subtitles'][0]
        payloads[row['id']] = content
        if barrier:
            barrier.wait()  # Times out unless the slots really run together.
        answer = [{'id': row['id'], 'text': f'第{row["id"]}句。'}]
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'translations': answer})}}]}).encode())
    return respond


def test_local_slots_run_batch_together_with_the_sequential_prompts():
    import threading
    from app.translation import LocalTranslator
    rows = [{'id': i, 'text': f'Line {i}', 'language': 'English'} for i in range(1, 7)]
    results, prompts = [], []
    for slots, barrier in ((1, None), (3, threading.Barrier(3, timeout=5))):
        translator = LocalTranslator(slots=slots)
        translator.port = 1234
        translator.references = [{'original': 'Earlier', 'translation': '之前。'}]
        payloads = {}
        translator.client.open = answer_rows(payloads, barrier)
        results.append(translator.translate(rows, 'context', 'after'))
        prompts.append(payloads)
    assert results[0] == results[1] == {i: f'第{i}句。' for i in range(1, 7)}
    assert prompts[0] == prompts[1]
    assert prompts[1][4]['previous_context'] == 'context\nLine 1\nLine 2\nLine 3'
    assert prompts[1][4]['following_context'] == 'Line 5\nLine 6\nafter'


def test_local_slot_failure_still_names_the_cue():
    import io
    import json
    from app.translation import LocalTranslator
    translator = LocalTranslator(slots=2)
    translator.port = 1234
    def respond(request, timeout):
        row = json.loads(json.loads(request.data)['messages'][1]['content'])['subtitles'][0]
        text = '。' if row['id'] == 2 else '好。'
        answer = {'translations': [{'id': row['id'], 'text': text}]}
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}).encode())
    translator.client.open = respond
    with pytest.raises(RuntimeError, match='字幕 2 翻译失败'):
        translator.translate([{'id': i, 'text': f'Line {i}'} for i in range(1, 5)])


def test_local_server_uses_gpu_slots_and_falls_back_to_one(tmp_path, monkeypatch):
    import contextlib
    from app import translation
    commands = []
    class Process:
        def __init__(self, command, **kwargs):
            commands.append(command)
            self.code = 1 if len(commands) == 1 else None  # The multi-slot server cannot allocate.
        def poll(self):
            return self.code
        def terminate(self):
            self.code = 0
        def wait(self, timeout=None):
            return self.code
    monkeypatch.setattr(translation, 'LOGS', tmp_path)
    monkeypatch.setattr(translation.subprocess, 'Popen', Process)
    assert translation.LocalTranslator(gpu_layers=0).slots == 1
    translator = translation.LocalTranslator(gpu_layers=99)
    translator.client.open = lambda request, timeout: contextlib.nullcontext(SimpleNamespace(status=200))
    with translator:
        assert translator.slots == 1
    option = lambda command, name: command[command.index(name) + 1]
    assert [(option(c, '--parallel'), option(c, '-c')) for c in commands] == [('4', '16384'), ('1', '4096')]
