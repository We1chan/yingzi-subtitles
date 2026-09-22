"""Choose readable subtitle boundaries from punctuation and aligned speech timing.

The scores are application heuristics, not a language model or a linguistic parser.
No text is rewritten, and no timestamp is interpolated to split an aligned token.
"""
import bisect
import math
import re
import unicodedata


CLAUSE_STARTS = {
    'Chinese': ('但是', '不过', '然后', '所以', '因此', '而且', '另外', '如果', '因为',
                '虽然', '尽管', '否则', '接下来', '与此同时', '也就是说'),
    'Japanese': ('しかし', 'そして', 'だから', 'それでも', 'それから', 'けれども',
                 'けれど', 'だけど', 'つまり', 'ところが', 'そのため'),
    'English': ('but ', 'because ', 'although ', 'however ', 'therefore ', 'so ',
                'while ', 'unless ', 'then ', 'and '),
}
ENGLISH_DEPENDENT = set(('a an the this that these those my your his her its our their '
    'to of in on at by for from with into over under not very more most and or '
    'if because although would could should must can will shall').split())
JAPANESE_SUFFIXES = {'は', 'が', 'を', 'に', 'へ', 'と', 'も', 'で', 'の', 'ね', 'よ',
    'から', 'まで', 'より', 'だけ', 'って', 'です', 'ます', 'た', 'て', 'ない', 'たい', 'でした', 'ません'}
LATIN_WORD = re.compile(r"[A-Za-z0-9Ａ-Ｚａ-ｚ０-９]+(?:[.'’:/_%％-][A-Za-z0-9Ａ-Ｚａ-ｚ０-９]+)*")
NUMBER_UNIT = re.compile(r'\d+(?:[.,．:/_-]\d+)*\s*(?:万元|亿元|小时|分钟|公里|千米|公斤|千克|毫升|个月|元|年|月|日|号|点|分|秒|米|℃|度|岁|个|次|天|周|%|％)')


def display_length(text, english):
    if english:
        return len(text)
    return sum(1 if unicodedata.east_asian_width(char) in ('W', 'F') else .5 for char in text)


def smart_endpoints(text, boundaries, language, sentence_end, max_seconds):
    """Return inclusive aligned-token indices, optimizing each continuous utterance."""
    if not boundaries:
        return []
    english = language == 'English'
    target, limit, minimum = (72, 112, 12) if english else (28, 44, 5)
    positions = [item[0] for item in boundaries]
    pieces = [text[0 if i == 0 else positions[i - 1]:end].strip() for i, end in enumerate(positions)]
    blocked = set()

    def protect(start, end):
        first = bisect.bisect_right(positions, start)
        last = bisect.bisect_left(positions, end)
        blocked.update(range(first, last))

    for expression in (LATIN_WORD, NUMBER_UNIT):
        for match in expression.finditer(text):
            protect(match.start(), match.end())
    if not english:
        for phrase in CLAUSE_STARTS.get(language, ()):
            for match in re.finditer(re.escape(phrase), text):
                protect(match.start(), match.end())

    # Pauses always separate utterances, including short replies and speaker turns.
    pauses = [i for i in range(len(boundaries) - 1)
              if boundaries[i + 1][1] - boundaries[i][2] >= .65]
    block_ends = pauses + [len(boundaries) - 1]
    costs, skipped = [], [0.0]
    for i, cut in enumerate(positions):
        left, right = text[:cut].rstrip(), text[cut:].lstrip()
        ending = sentence_end(left, language)
        gap = boundaries[i + 1][1] - boundaries[i][2] if i + 1 < len(boundaries) else 0
        stripped = left.rstrip('"\'”’）)]」』】》〉')
        comma = stripped.endswith(('、', '，', ',', ';', '；', ':', '：'))
        cost = 0 if ending else 1.5 if comma else 2 if gap >= .3 else 4 if gap >= .15 else 8
        if right.lower().startswith(CLAUSE_STARTS.get(language, ())):
            cost = min(cost, 2)
        if not ending and not comma:
            token = pieces[i].lower().strip('"\'“”‘’()[]')
            if english and (token in ENGLISH_DEPENDENT or token.rstrip('.') in {'dr', 'mr', 'mrs', 'ms', 'prof'}):
                cost += 12
            if language == 'Chinese' and stripped.endswith(tuple('的地得把被将在向对从为给让不很更最这那每')):
                cost += 9
            if language == 'Japanese':
                next_piece = pieces[i + 1].rstrip('。！？!?、，,') if i + 1 < len(pieces) else ''
                if next_piece in JAPANESE_SUFFIXES:
                    cost += 16
                if token in {'は', 'が', 'を', 'に', 'へ', 'の', 'と'}:
                    cost += 6
                if stripped.endswith(('です', 'ます', 'でした', 'ません', 'だった', 'ください')) and next_piece not in JAPANESE_SUFFIXES:
                    cost = min(cost, 2)
        costs.append(cost)
        skipped.append(skipped[-1] + (6 if ending else 0))

    result, block_start = [], 0
    for block_end in block_ends:
        # A protected word/number can only be split at an actual long pause.
        endpoints = [i for i in range(block_start, block_end + 1) if i not in blocked or i == block_end]
        best = {block_start: 0.0}
        previous = {}
        for endpoint_index, last in enumerate(endpoints):
            stop = last + 1
            best[stop] = math.inf
            for start in sorted((value for value in best if value < stop), reverse=True):
                begin_char = positions[start - 1] if start else 0
                section = text[begin_char:positions[last]].strip()
                length = display_length(section, english)
                duration = max(.001, boundaries[last][2] - boundaries[start][1])
                # A single indivisible word may exceed a limit; never discard it or guess timing.
                indivisible = endpoint_index == 0 or start > endpoints[endpoint_index - 1]
                if (length > limit or duration > max_seconds) and not indivisible:
                    break
                cost = 3 + 2 * (length / target) ** 2
                cost += 12 * max(0, 1 - duration) ** 2 + .6 * max(0, minimum - length)
                cost += max(0, duration - min(5, max_seconds)) ** 2
                cost += skipped[last] - skipped[start]
                if last != block_end:
                    cost += costs[last]
                if best[start] + cost < best[stop]:
                    best[stop], previous[stop] = best[start] + cost, start
            if stop not in previous:
                raise ValueError('无法为识别结果生成有效的智能分段。')
        stop, ends = block_end + 1, []
        while stop != block_start:
            ends.append(stop - 1)
            stop = previous[stop]
        result.extend(reversed(ends))
        block_start = block_end + 1
    return result
