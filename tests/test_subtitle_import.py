import pytest

from app.subtitle_import import input_kind, parse_srt, read_srt
from app.subtitles import render


SRT = '8\r\n00:00:01,250 --> 00:00:03,500\r\n第一行\r\n第二行 &amp; 中文\r\n\r\n12\r\n00:00:04.000 --> 00:00:05.000\r\n<i>结束。</i>\r\n'


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'gb18030'])
def test_srt_encodings_multiline_and_export_roundtrip(tmp_path, encoding):
    path = tmp_path / '双行 字幕.SRT'
    path.write_bytes(SRT.encode(encoding))
    cues = read_srt(path)
    assert [(c.start, c.end) for c in cues] == [(1.25, 3.5), (4, 5)]
    assert cues[0].text == '第一行\n第二行 & 中文'
    assert cues[1].text == '结束。'
    assert all(c.language == 'Chinese' and not c.translation for c in cues)
    assert parse_srt(render(cues, 'original')) == cues
    assert path.read_bytes() == SRT.encode(encoding)


def test_timestamp_only_blocks_and_full_text_are_not_guessed_as_translation():
    cues = parse_srt('00:00:01,000 --> 00:00:02,000\nHello there.<br>你好。')
    assert cues[0].text == 'Hello there.\n你好。' and not cues[0].translation
    cues = parse_srt('1\n100:00:01,000 --> 100:00:02,000\n漢字\n\n2\n100:00:03,000 --> 100:00:04,000\nこんにちは。')
    assert all(c.language == 'Japanese' for c in cues)
    assert cues[0].start == 360001
    assert parse_srt('1\n00:00:00,000 --> 00:00:01,000\nHello', language='Japanese')[0].language == 'Japanese'


@pytest.mark.parametrize('text', [
    '', 'This is not a subtitle.',
    '1\n00:99:00,000 --> 00:00:02,000\nText',
    '1\n00:00:03,000 --> 00:00:02,000\nText',
    '1\n00:00:01,000 --> 00:00:02,000\n<i></i>',
    '1\n00:00:01,000 --> 00:00:03,000\nOne\n\n2\n00:00:02,000 --> 00:00:04,000\nTwo',
    '1\n00:00:01,000 --> 00:00:02,000\nOne\n2\n00:00:03,000 --> 00:00:04,000\nTwo',
])
def test_invalid_srt_is_rejected_completely(text):
    with pytest.raises(ValueError):
        parse_srt(text)


@pytest.mark.parametrize('path,expected', [('video.MP4', 'media'), ('sound.OPUS', 'media'),
    ('subtitle.SRT', 'srt'), ('project.YINGZI.JSON', 'project'), ('notes.txt', None),
    ('image.png', None), ('subtitle.srt.exe', None), ('settings.json', None), ('subtitles.ass', None)])
def test_supported_input_types(path, expected):
    assert input_kind(path) == expected
