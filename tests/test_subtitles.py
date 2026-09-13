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
    path = tmp_path / '映字.yingzi.json'
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
