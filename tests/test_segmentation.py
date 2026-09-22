from types import SimpleNamespace

import pytest

from app.subtitles import aligned_cues, normalized_chars, validate


def tokens(words, step=.15):
    return [SimpleNamespace(text=word, start_time=i * step, end_time=(i + 1) * step)
            for i, word in enumerate(words)]


def split(text, language='Chinese', step=.15, items=None, **kwargs):
    if items is None:
        words = text.split() if language == 'English' else [c for c in text if normalized_chars(c)[0]]
        items = tokens(words, step)
    return aligned_cues(text, items, language=language, segmentation_mode='smart', **kwargs)


def test_complete_sentence_can_exceed_old_limit_without_flashing_short_tail():
    text = '今天我们一起来学习如何使用这个软件自动识别视频里的语音并生成中文字幕。'
    assert len(text) > 30
    assert [cue.text for cue in split(text, max_chars=30)] == [text]


def test_long_sentence_uses_clause_boundary_and_preserves_absolute_timing():
    clauses = ['今天我们先把需要处理的视频导入到软件里面，', '然后选择需要识别的语言和音轨，最后点击按钮生成字幕。']
    cues = split(''.join(clauses), step=.13, offset=123.4, duration=8)
    assert [cue.text for cue in cues] == clauses
    assert cues[0].start == 123.4
    assert cues[0].end == cues[1].start == 126
    validate(cues)


def test_sentence_endings_with_closing_quotes():
    text = '第一步是打开视频。第二步是选择语言。第三步是生成字幕。'
    assert [cue.text for cue in split(text, step=.2)] == ['第一步是打开视频。', '第二步是选择语言。', '第三步是生成字幕。']
    assert [cue.text for cue in split('「今日は晴れです。」明日は雨が降ります。', 'Japanese', step=.2)] == ['「今日は晴れです。」', '明日は雨が降ります。']


def test_short_replies_remain_separate_across_silence():
    items = tokens(['好', '继续'])
    items[1].start_time, items[1].end_time = 1.2, 1.8
    cues = split('好，继续。', items=items, offset=10)
    assert [cue.text for cue in cues] == ['好，', '继续。']
    assert cues[0].end == 10.15 and cues[1].start == 11.2


def test_moderate_pause_is_preferred_within_a_long_sentence():
    parts = ['我们会先检查这段视频里面是否包含清楚的人声', '接着选择合适的识别模型生成字幕并且仔细校对每一条内容']
    items = tokens(''.join(parts), step=.13)
    for item in items[len(parts[0]):]:
        item.start_time += .4
        item.end_time += .4
    cues = split(''.join(parts), items=items)
    assert [cue.text for cue in cues] == parts
    assert cues[1].start - cues[0].end == pytest.approx(.4)


def test_duration_limit_and_no_tiny_tail_when_punctuation_is_absent():
    text = '只要提前做好准备我们就能够在规定的时间里面顺利完成今天所有的工作任务并且取得满意的成果'
    cues = split(text, step=.26)
    assert len(cues) >= 2
    assert ''.join(cue.text for cue in cues) == text
    assert all(1 <= cue.end - cue.start <= 6 and len(cue.text) >= 5 for cue in cues)
    shorter = split(text, step=.26, max_seconds=3)
    assert all(cue.end - cue.start <= 3 for cue in shorter)


def test_english_abbreviations_contractions_and_dependent_words():
    text = "Dr. Smith can't come to the meeting today. We can send him the notes after the discussion."
    cues = split(text, 'English', step=.33)
    assert [cue.text for cue in cues] == ["Dr. Smith can't come to the meeting today.", 'We can send him the notes after the discussion.']
    longer = 'When you import a video into the application you can select the language and choose an audio track before generating subtitles.'
    cues = split(longer, 'English', step=.6)
    assert ' '.join(cue.text for cue in cues) == longer
    assert all(cue.end - cue.start <= 6.001 for cue in cues)
    assert all(cue.text.split()[-1] not in {'a', 'the', 'an', 'and', 'to'} for cue in cues)


@pytest.mark.parametrize('protected', ["can't", 'ＡＰＩ', '12999元', '12个月', '3.14公里'])
def test_words_and_number_units_stay_together_even_with_character_alignment(protected):
    text = '我们需要先仔细检查这个视频中的内容以及' + protected + '然后继续完成剩余任务并保存所有结果'
    cues = split(text, step=.15, max_seconds=4)
    assert len(cues) >= 2
    assert ''.join(cue.text for cue in cues) == text
    assert any(protected in cue.text for cue in cues)


def test_japanese_conjunction_and_ending_are_not_stranded():
    text = 'これから私たちは新しい機能の使い方について詳しく説明していきたいと思いますけれども最後までお付き合いください。'
    cues = split(text, 'Japanese', step=.12)
    assert [cue.text for cue in cues] == ['これから私たちは新しい機能の使い方について詳しく説明していきたいと思います', 'けれども最後までお付き合いください。']
    assert [cue.text for cue in split('そういう姿勢が。', 'Japanese', items=tokens(['そういう', '姿勢', 'が'], .6))] == ['そういう姿勢が。']


@pytest.mark.parametrize('character_alignment', [False, True])
def test_indivisible_long_word_is_preserved_without_inventing_timestamps(character_alignment):
    text = 'a' * 130
    items = tokens(text, .07) if character_alignment else [SimpleNamespace(text=text, start_time=.2, end_time=8)]
    cues = split(text, 'English', items=items)
    assert len(cues) == 1 and cues[0].text == text
    assert cues[0].start == items[0].start_time and cues[0].end == pytest.approx(items[-1].end_time)


def test_missing_alignment_still_fails_in_smart_mode():
    with pytest.raises(ValueError, match='未覆盖全部'):
        split('今天出发。', items=tokens(['今天']))
