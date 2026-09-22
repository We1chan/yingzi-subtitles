"""Small, explicit live API comparison. Reads a project, never changes its subtitles."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.online_translation import OnlineTranslator
from app.subtitles import load_project


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('project', type=Path)
    parser.add_argument('--start', type=int, default=1)
    parser.add_argument('--count', type=int, default=3)
    parser.add_argument('--request-limit', type=int, default=8)
    parser.add_argument('--output', type=Path, default=Path('logs/translation-comparison.json'))
    args = parser.parse_args()
    if not 1 <= args.count <= 10 or args.start < 1 or not 1 <= args.request_limit <= 30:
        parser.error('Invalid trial size or request limit')
    data, cues = load_project(args.project)
    indexes = list(range(args.start - 1, min(len(cues), args.start - 1 + args.count)))
    if not indexes:
        parser.error('No subtitles in the selected range')
    request_count = 0
    report = {'sample': [{'id': i + 1, 'start': cues[i].start, 'end': cues[i].end,
                        'original': cues[i].text, 'existing_translation': cues[i].translation} for i in indexes], 'modes': {}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for mode in ('single', 'grouped'):
        mode_request_start = request_count
        translator = OnlineTranslator()
        translator.options['timeout'] = min(translator.options['timeout'], 60)
        translator.glossary = data.get('options', {}).get('translation_glossary', '')
        translator.style_prompt = data.get('options', {}).get('translation_prompt', '')
        diagnostics, translated, failures = [], {}, {}
        translator.diagnostic = diagnostics.append
        original_open = translator.client.open
        def limited_open(*values, **kwargs):
            nonlocal request_count
            if request_count >= args.request_limit:
                raise RuntimeError('本次对比已达到请求上限。')
            request_count += 1
            return original_open(*values, **kwargs)
        translator.client.open = limited_open
        started = time.monotonic()
        for group in ([indexes] if mode == 'grouped' else [[i] for i in indexes]):
            rows = [{'id': i + 1, 'text': cues[i].text, 'language': cues[i].language} for i in group]
            previous = '\n'.join(c.text for c in cues[max(0, group[0] - 3):group[0]])
            following = '\n'.join(c.text for c in cues[group[-1] + 1:group[-1] + 4])
            try:
                for result in translator.translate_batches(rows, previous, following,
                        on_failure=lambda cue_id, error: failures.update({cue_id: error})):
                    translated.update(result)
            except (ValueError, RuntimeError) as exc:
                failures.update({i + 1: str(exc) for i in group if i + 1 not in translated})
            print(f'{mode}: IDs {group[0] + 1}-{group[-1] + 1}; requests so far {request_count}', flush=True)
        report['model'] = translator.options['model']
        report['modes'][mode] = {'seconds': round(time.monotonic() - started, 2), 'requests': request_count - mode_request_start,
                                'translations': translated, 'failures': failures, 'diagnostics': diagnostics}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Comparison saved:', args.output, flush=True)


if __name__ == '__main__':
    main()
