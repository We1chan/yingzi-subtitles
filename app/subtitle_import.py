"""Supported local inputs and validated, text-preserving SubRip import."""
import html
import re
from pathlib import Path

from .subtitles import Cue, parse_time, validate


MEDIA_SUFFIXES = ('.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.ts', '.flv', '.wmv',
                  '.mpg', '.mpeg', '.mts', '.m2ts', '.ogv', '.3gp', '.3g2',
                  '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.opus', '.mka', '.aif', '.aiff', '.wma')
MEDIA_FILTER = '视频和音频 (' + ' '.join('*' + suffix for suffix in MEDIA_SUFFIXES) + ')'
TIME_RANGE = re.compile(r'\s*(\d+:[0-5]\d:[0-5]\d[,.]\d{3})\s*-->\s*(\d+:[0-5]\d:[0-5]\d[,.]\d{3})\s*')


def input_kind(path):
    path = Path(path)
    if path.name.lower().endswith(('.yingzi.json', '.yingziproj')):
        return 'project'
    if path.suffix.lower() == '.srt':
        return 'srt'
    if path.suffix.lower() in MEDIA_SUFFIXES:
        return 'media'
    return None


def read_srt(path, language=''):
    raw = Path(path).read_bytes()
    encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    else:
        raise ValueError('无法读取字幕编码，请将 SRT 保存为 UTF-8 后重试。')
    if '\x00' in text:
        raise ValueError('SRT 包含无效字符，请检查文件格式或另存为 UTF-8。')
    return parse_srt(text, language)


def parse_srt(text, language=''):
    text = text.replace('\r\n', '\n').replace('\r', '\n').lstrip('\ufeff').strip()
    if not text:
        raise ValueError('SRT 文件为空，没有可导入的字幕。')
    cues = []
    for number, block in enumerate(re.split(r'\n[ \t]*\n+', text), 1):
        lines = block.splitlines()
        if re.fullmatch(r'\s*\d+\s*', lines[0]):
            lines = lines[1:]
        timing = TIME_RANGE.fullmatch(lines[0]) if lines else None
        if not timing:
            raise ValueError(f'SRT 第 {number} 段时间格式无效，应为 00:00:01,000 --> 00:00:02,000。')
        if any(TIME_RANGE.fullmatch(line) for line in lines[1:]):
            raise ValueError(f'SRT 第 {number} 段包含多组时间，请在字幕条目之间保留空行。')
        body = '\n'.join(lines[1:]).strip()
        body = re.sub(r'<br\s*/?>', '\n', body, flags=re.IGNORECASE)
        # SRT styling is replaced by the application's editable caption style.
        body = re.sub(r'</?(?:b|i|u|s|font)(?:\s+[^>]*)?\s*>', '', body, flags=re.IGNORECASE)
        body = html.unescape(body).strip()
        if not body:
            raise ValueError(f'SRT 第 {number} 段没有字幕文字。')
        cues.append(Cue(parse_time(timing[1]), parse_time(timing[2]), body))
    if language not in ('Chinese', 'Japanese', 'English'):
        combined = '\n'.join(cue.text for cue in cues)
        language = ('Japanese' if re.search(r'[\u3040-\u30ff]', combined) else
                    'Chinese' if re.search(r'[\u3400-\u9fff]', combined) else
                    'English' if re.search(r'[A-Za-z]', combined) else '')
    for cue in cues:
        cue.language = language
    validate(cues)
    return cues
