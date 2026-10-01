"""Undoable subtitle operations and lightweight, advisory quality checks."""
import copy
from dataclasses import dataclass

from .subtitles import Cue, needs_translation, validate


def quality_issues(cue, previous=None):
    issues = []
    if cue.translation_error:
        issues.append('待重试：' + cue.translation_error)
    elif needs_translation(cue):
        issues.append('待翻译')
    duration = cue.end - cue.start
    if duration < .7:
        issues.append('显示时间短于 0.7 秒')
    if duration > 7:
        issues.append('显示时间超过 7 秒')
    body = cue.translation or cue.text
    limit = 25 if not cue.translation and cue.language == 'English' else 15
    if len(body) / max(.001, duration) > limit:
        issues.append('阅读速度偏快')
    if len(body) > (112 if not cue.translation and cue.language == 'English' else 44):
        issues.append('字幕偏长，检查换行')
    if previous and previous.translation and cue.translation == previous.translation and cue.text != previous.text:
        issues.append('与上一条译文相同，请核对')
    return issues


@dataclass
class CueChange:
    index: int
    before: list
    after: list


class EditHistory:
    def __init__(self):
        self.undo_stack, self.redo_stack = [], []
        self.pending = None
        self.last_change = None

    def record_change(self, index, before, after):
        if before == after:
            return
        self.undo_stack.append(CueChange(index, copy.deepcopy(before), copy.deepcopy(after)))
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()

    def record(self, before, after):
        start, end_before, end_after = 0, len(before), len(after)
        while start < min(end_before, end_after) and before[start] == after[start]:
            start += 1
        while end_before > start and end_after > start and before[end_before - 1] == after[end_after - 1]:
            end_before -= 1
            end_after -= 1
        self.record_change(start, before[start:end_before], after[start:end_after])

    def remember(self, cues):
        # A background job can change many cues. Compress its baseline into a splice when it ends.
        self.pending = [copy.copy(cue) for cue in cues]

    def finish(self, cues):
        if self.pending is not None:
            self.record(self.pending, cues)
            self.pending = None

    def apply(self, cues, change, reverse=False):
        old, new = (change.after, change.before) if reverse else (change.before, change.after)
        result = list(cues)
        result[change.index:change.index + len(old)] = copy.deepcopy(new)
        self.last_change = (change.index, len(old), len(new))
        return result

    def undo(self, cues):
        self.finish(cues)
        if not self.undo_stack:
            return None
        change = self.undo_stack.pop()
        self.redo_stack.append(change)
        return self.apply(cues, change, reverse=True)

    def redo(self, cues):
        if not self.redo_stack:
            return None
        change = self.redo_stack.pop()
        self.undo_stack.append(change)
        return self.apply(cues, change)


def merge_cues(cues, indexes):
    indexes = sorted(set(indexes))
    if len(indexes) < 2 or indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ValueError('请选择至少两条连续字幕。')
    chosen = [cues[i] for i in indexes]
    if len({c.language for c in chosen}) > 1:
        raise ValueError('不同语言的字幕请分别合并。')
    sep = ' ' if chosen[0].language == 'English' else ''
    merged = Cue(chosen[0].start, chosen[-1].end, sep.join(c.text for c in chosen), language=chosen[0].language)
    # The old translations remain available through undo; new boundaries need a fresh translation.
    result = cues[:indexes[0]] + [merged] + cues[indexes[-1] + 1:]
    validate(result)
    return result


def split_cue(cues, index, position, seconds):
    cue = cues[index]
    first, second = cue.text[:position].strip(), cue.text[position:].strip()
    if not first or not second or not cue.start < seconds < cue.end:
        raise ValueError('拆分后两条字幕都必须有文字和有效时间。')
    result = list(cues)
    result[index:index + 1] = [Cue(cue.start, seconds, first, language=cue.language),
                             Cue(seconds, cue.end, second, language=cue.language)]
    validate(result)
    return result
