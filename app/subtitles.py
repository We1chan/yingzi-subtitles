import dataclasses
import json
import math
import os
import re
import unicodedata
from pathlib import Path


@dataclasses.dataclass
class Cue:
    start: float
    end: float
    text: str
    translation: str = ''
    language: str = ''
    translation_error: str = ''


def needs_translation(cue):
    return cue.language in {'Japanese', 'English'} and (not cue.translation.strip() or bool(cue.translation_error))


def sentence_end(text, language):
    tail = text.rstrip('"\'”’）)]」』】》〉')
    if tail.endswith(('。', '！', '？', '!', '?')):
        return True
    if language != 'English' or not tail.endswith('.'):
        return False
    word = tail.split()[-1]
    if word.lower() in {'mr.', 'mrs.', 'ms.', 'dr.', 'prof.', 'sr.', 'jr.', 'st.', 'vs.', 'etc.', 'e.g.', 'i.e.'}:
        return False
    return not re.fullmatch(r'(?:[A-Za-z]\.)+', word)


def timestamp(seconds, separator=','):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('时间必须是非负数。')
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}'


def parse_time(value):
    match = re.fullmatch(r'(\d+):([0-5]\d):([0-5]\d)[,.](\d{3})', value.strip())
    if not match:
        raise ValueError('时间格式应为 00:00:01,250')
    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def validate(cues):
    previous_end = 0.0
    for i, cue in enumerate(cues, 1):
        if not math.isfinite(cue.start) or not math.isfinite(cue.end) or cue.start < 0 or cue.end <= cue.start:
            raise ValueError(f'第 {i} 条字幕时间无效，结束时间必须晚于开始时间。')
        if cue.start < previous_end - 0.001:
            raise ValueError(f'第 {i} 条字幕与上一条时间重叠，请调整。')
        if not cue.text.strip():
            raise ValueError(f'第 {i} 条字幕原文为空。')
        previous_end = cue.end


def clean_line(text):
    return '\n'.join(' '.join(line.split()) for line in str(text).replace('\x00', '').splitlines() if line.strip())


def subtitle_text(cue, mode='bilingual'):
    original, translated = clean_line(cue.text), clean_line(cue.translation)
    if mode == 'translated':
        return translated or original
    if mode == 'original' or not translated or translated == original:
        return original
    return original + '\n' + translated


def render(cues, mode='bilingual', format='srt'):
    validate(cues)
    parts = ['WEBVTT\n'] if format == 'vtt' else []
    separator = '.' if format == 'vtt' else ','
    for i, cue in enumerate(cues, 1):
        body = subtitle_text(cue, mode)
        if format == 'vtt':
            body = body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        parts.append(f'{i}\n{timestamp(cue.start, separator)} --> {timestamp(cue.end, separator)}\n{body}\n')
    return '\n'.join(parts)


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text, encoding='utf-8-sig' if path.suffix == '.srt' else 'utf-8')
    temporary.replace(path)


def relative_path(path, folder):
    try:
        return os.path.relpath(Path(path).resolve(), Path(folder).resolve())
    except ValueError:
        return None


def resolve_project_path(value, relative, project):
    path = Path(value)
    if not path.is_absolute():
        path = Path(project).resolve().parent / path
    if relative:
        candidate = Path(project).resolve().parent / relative
        if candidate.exists():
            return candidate.resolve()
    return value if Path(value).is_absolute() else path


def save_project(path, video, cues, options=None, status='complete', *, workspace=None, editor=None):
    path = require_project_path(path)
    validate(cues)
    data = {'version': 1, 'video': str(video), 'options': options or {}, 'status': status, 'cues': [dataclasses.asdict(cue) for cue in cues]}
    data['video_relative'] = relative_path(video, Path(path).parent)
    if workspace is not None:
        data['workspace'] = str(Path(workspace).resolve())
        data['workspace_relative'] = relative_path(workspace, Path(path).parent)
    if editor is not None:
        data['editor'] = editor
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def load_project(path):
    return _load_project_data(require_project_path(path))


def require_project_path(path):
    path = Path(path)
    if path.suffix.lower() != '.yingziproj':
        raise ValueError('请选择 .yingziproj 映字工程。旧工程请先使用迁移工具转换。')
    return path


def _load_project_data(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if (not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('cues'), list)
            or not isinstance(data.get('video'), str) or not data['video'].strip()
            or not isinstance(data.get('options', {}), dict)
            or not isinstance(data.get('editor', {}), dict)):
        raise ValueError('这不是受支持的映字字幕工程。')
    try:
        cues = [Cue(**item) for item in data['cues']]
        if any(not isinstance(getattr(cue, key), str) for cue in cues
               for key in ('text', 'translation', 'language', 'translation_error')):
            raise ValueError('工程中的字幕文字或语言记录无效。')
        validate(cues)
        data['video'] = str(resolve_project_path(data['video'], data.get('video_relative'), path))
        if data.get('workspace'):
            data['workspace'] = str(resolve_project_path(data['workspace'], data.get('workspace_relative'), path))
    except (TypeError, AttributeError) as exc:
        raise ValueError('工程中的字幕或媒体路径无效。') from exc
    return data, cues


def project_workspace(path, data=None):
    if data is None:
        data, _ = load_project(path)
    return Path(data.get('workspace') or Path(path).resolve().parent)


def normalized_chars(text):
    # Same retained characters as the aligner; NFKC also handles Japanese full-width forms.
    result, offsets = [], []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize('NFKC', char):
            if unicodedata.category(normalized)[0] in 'LN' or normalized == "'":
                result.append(normalized.lower())
                offsets.append(index)
    return ''.join(result), offsets


def aligned_cues(text, items, offset=0.0, duration=None, max_chars=30, max_seconds=6.0, language='', segmentation_mode='fixed'):
    """Preserve original spelling/punctuation; split only at aligned word boundaries."""
    if not text.strip():
        return []
    if not items:
        raise ValueError('识别出了文字，但未获得有效时间戳。请重试该视频。')
    normalized, char_positions = normalized_chars(text)
    cursor = 0
    boundaries = []
    for item in items:
        token, _ = normalized_chars(item.text)
        if not token:
            continue
        found = normalized.find(token, cursor)
        if found < 0 or found > cursor + 4:
            raise ValueError(f'时间轴对齐文本不一致：{item.text!r}。请重试或缩短片段。')
        cursor = found + len(token)
        end_char = char_positions[cursor - 1] + 1
        # Attach punctuation and whitespace to the preceding word.
        while end_char < len(text) and not normalized_chars(text[end_char])[0]:
            end_char += 1
        boundaries.append((end_char, float(item.start_time), float(item.end_time)))
    if not boundaries or cursor != len(normalized):
        raise ValueError('时间轴未覆盖全部识别文本，未生成不完整字幕。')
    smart = segmentation_mode == 'smart'
    if smart:
        from .segmentation import smart_endpoints
        endpoints = iter(smart_endpoints(text, boundaries, language, sentence_end, max_seconds))
    cues = []
    first, char_start = 0, 0
    while first < len(boundaries):
        if smart:
            last = next(endpoints)
        else:
            last = first
            soft_break = None
            for i in range(first, len(boundaries)):
                char_end, start, end = boundaries[i]
                section = text[char_start:char_end].strip()
                elapsed = end - boundaries[first][1]
                if i > first and (len(section) > max_chars or elapsed > max_seconds):
                    if soft_break is not None:
                        last = soft_break
                    # Keep Japanese conjunctions such as けれども together.
                    elif last > first and text[char_start:boundaries[last][0]].endswith(('けれど', 'けど')):
                        last -= 1
                    break
                last = i
                if section.endswith(('、', '，', ',', ';', '；', ':', '：')) and len(section) >= 8:
                    soft_break = i
                gap = i + 1 < len(boundaries) and boundaries[i + 1][1] - end > 0.5
                if gap or (sentence_end(section, language) and elapsed >= 0.8):
                    break
            # Keep a final Japanese particle with a word, instead of flashing “が。” alone.
            if language == 'Japanese' and first < last < len(boundaries) - 1:
                tail = text[boundaries[last][0]:].strip()
                if len(normalized_chars(tail)[0]) <= 1 and not sentence_end(text[char_start:boundaries[last][0]], language):
                    candidate = last - 1
                    new_tail = text[boundaries[candidate][0]:].strip()
                    if (len(new_tail) <= max_chars and boundaries[-1][2] - boundaries[last][1] <= max_seconds
                            and boundaries[last][1] - boundaries[candidate][2] <= .5):
                        last = candidate
        char_end, _, end = boundaries[last]
        cue_start = max(0.0, boundaries[first][1])
        cue_end = max(cue_start + 0.08, end)
        if duration is not None:
            cue_start = min(cue_start, max(0, duration - 0.08))
            cue_end = min(cue_end, duration)
        section = text[char_start:char_end].strip()
        cues.append(Cue(round(offset + cue_start, 3), round(offset + cue_end, 3), section, language=language))
        first, char_start = last + 1, char_end
    # Repair zero-length/overlapping predictions without spreading text across silence.
    for i in range(len(cues) - 1):
        if cues[i].end > cues[i + 1].start:
            midpoint = (cues[i].start + cues[i + 1].end) / 2
            boundary = max(cues[i].start + 0.001, min(cues[i + 1].start, cues[i + 1].end - 0.001))
            if boundary <= cues[i].start or boundary >= cues[i + 1].end:
                boundary = midpoint
            cues[i].end = boundary
            cues[i + 1].start = boundary
    validate(cues)
    return cues
