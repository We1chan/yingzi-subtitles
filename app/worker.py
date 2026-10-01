"""Isolated inference process. stdout contains JSON events; diagnostics go to stderr."""
import argparse
import contextlib
import gc
import json
import os
import sys
import time
import traceback
import threading
from pathlib import Path

from .config import CACHE, ROOT, model_paths, model_issue, readiness, asr_backend, asr_language_issue, extra_runtime_ready
from .media import extract_audio, read_span, speech_chunks
from .subtitles import atomic_write, load_project, render, save_project, validate, needs_translation, require_project_path
from .job_metrics import RequestMetrics

EVENT_STREAM = sys.stdout
EVENT_LOCK = threading.RLock()


def event(type, **data):
    with EVENT_LOCK:
        print(json.dumps({'type': type, **data}, ensure_ascii=False), file=EVENT_STREAM, flush=True)


def progress(percent, message):
    event('progress', percent=round(percent), message=message)


def release():
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_options(device):
    import torch
    torch.set_num_threads(min(8, os.cpu_count() or 4))
    if device == 'cpu' or not torch.cuda.is_available():
        return {'device_map': 'cpu', 'dtype': torch.float32, 'attn_implementation': 'sdpa', 'local_files_only': True}
    free, total = torch.cuda.mem_get_info()
    budget = max(512 * 1024**2, free - 900 * 1024**2)
    return {'device_map': 'auto', 'max_memory': {0: budget, 'cpu': 14 * 1024**3}, 'offload_folder': str(CACHE / 'offload'), 'dtype': torch.bfloat16, 'attn_implementation': 'sdpa', 'local_files_only': True}


def process(job):
    data = None
    if job.get('resume_project'):
        data, cues = load_project(job['resume_project'])
        job = {**data.get('options', {}), **job}
    job = {**job, 'segmentation_mode': job.get('segmentation_mode', 'fixed' if data is not None or 'max_chars' in job else 'smart')}
    paths = model_paths(job)
    job = {**job, **{key: str(value) for key, value in paths.items()}}
    video, out = Path(job['video']), Path(job['output'])
    out.mkdir(parents=True, exist_ok=True)
    project = require_project_path(job.get('project_path') or job.get('resume_project') or out / 'subtitles.yingziproj')
    editor = job.get('editor_state', (data or {}).get('editor'))
    job['project_path'] = str(project)
    job['editor_state'] = editor
    bilingual = job.get('bilingual', True)
    device = job.get('device', 'auto')
    start_clock = time.monotonic()
    if data is not None:
        video = Path(data['video'])
        job['video'] = str(video)
    if data is None or data.get('status') in ('recognizing', 'aligning') or job.get('resegment_only'):
        from .pipeline import run_pipeline
        cues = run_pipeline(job, paths, out, [] if data is None else cues, sys.modules[__name__])
    if job.pop('resegment_only', False):
        # Resegmentation is local; translation runs only when the user requests it.
        bilingual = False
    job.pop('resume_project', None)
    job.pop('project_path', None)
    job.pop('editor_state', None)
    selected = job.pop('selected_ids', None)
    validate(cues)
    atomic_write(out / 'original.srt', render(cues, 'original'))
    save_project(project, video, cues, job, status='translating' if bilingual else 'complete', workspace=out, editor=editor)
    event('checkpoint', project=str(project))
    pending = [i for i, cue in enumerate(cues) if (needs_translation(cue) if selected is None else
               i + 1 in selected and cue.language in ('Japanese', 'English'))]
    metrics = RequestMetrics(out / 'api-diagnostics.jsonl', event)
    if bilingual and pending:
        online = job.get('translation_backend', 'local') == 'online'
        if online:
            from .online_translation import OnlineTranslator, translation_batches
            manager = OnlineTranslator(emit=progress)
            manager.diagnostic = metrics.record
            progress(75, f'使用在线翻译模型 {manager.options["model"]} · 每组最多 {manager.options["batch_size"]} 条字幕')
        else:
            status = readiness(job)
            if not status['字幕翻译'] or not status['翻译引擎']:
                raise ValueError('所选翻译模型或引擎未准备完成。原文字幕已保存，请在“选择模型”中检查后继续翻译。')
            progress(75, f'加载翻译模型 {paths["translation_model"].name}')
            from .translation import LocalTranslator
            manager = LocalTranslator(progress, gpu_layers=0 if device == 'cpu' else 99, model_path=paths['translation_model'])
        manager.glossary = job.get('translation_glossary', '')
        manager.style_prompt = job.get('translation_prompt', '')
        with manager as translator:
            batches = translation_batches(cues, pending, manager.options['batch_size']) if online else (
                pending[first:first + 8] for first in range(0, len(pending), 8))
            completed = 0
            failed = 0
            def checkpoint(translations=None, failure=None):
                nonlocal completed, failed
                updates = []
                for cue_id, text in (translations or {}).items():
                    cues[cue_id - 1].translation = text
                    cues[cue_id - 1].translation_error = ''
                    updates.append({'id': cue_id, 'translation': text, 'error': ''})
                    completed += 1
                if failure:
                    cue_id, error = failure
                    cues[cue_id - 1].translation_error = error
                    updates.append({'id': cue_id, 'translation': cues[cue_id - 1].translation, 'error': error})
                    failed += 1
                save_project(project, video, cues, job, status='translating', workspace=out, editor=editor)
                done = completed + failed
                event('translation_update', updates=updates, completed=completed, failed=failed,
                      total=len(pending), **metrics.snapshot(done, len(pending) - done))
                progress(76 + 21 * done / len(pending), f'翻译字幕 {done}/{len(pending)}' + (f' · {failed} 条待重试' if failed else ''))
            if online:
                from .online_queue import translate_groups
                for kind, value in translate_groups(manager, cues, batches):
                    checkpoint(translations=value) if kind == 'result' else checkpoint(failure=value)
                batches = []
            for indexes in batches:
                translator.references = [{'original': cues[i].text[:300], 'translation': cues[i].translation[:300]}
                    for i in range(max(0, indexes[0] - 3), indexes[0]) if cues[i].translation and not cues[i].translation_error]
                rows = [{'id': i + 1, 'text': cues[i].text, 'language': cues[i].language} for i in indexes]
                context = '\n'.join(cue.text for cue in cues[max(0, indexes[0] - 3):indexes[0]])
                checkpoint(translator.translate(rows, context))
    remaining = sum(needs_translation(cue) for cue in cues) if bilingual else 0
    save_project(project, video, cues, job, status='translation_partial' if remaining else 'complete', workspace=out, editor=editor)
    for mode in ('original', 'translated', 'bilingual'):
        atomic_write(out / f'{mode}.srt', render(cues, mode))
    atomic_write(out / 'bilingual.vtt', render(cues, 'bilingual', 'vtt'))
    progress(100, f'本轮处理结束 · {remaining} 条待重试' if remaining else f'已完成 {len(cues)} 条字幕')
    event('done', project=str(project), output=str(out), count=len(cues), failed=remaining,
          status='translation_partial' if remaining else 'complete', seconds=round(time.monotonic() - start_clock, 1))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    try:
        job = json.loads(Path(args.job).read_text(encoding='utf-8-sig'))
        with contextlib.redirect_stdout(sys.stderr):
            process(job)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        event('error', message=str(exc))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
