"""Isolated inference process. stdout contains JSON events; diagnostics go to stderr."""
import argparse
import contextlib
import gc
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

from .config import CACHE, ROOT, model_paths, model_issue, readiness, asr_backend, asr_language_issue, extra_runtime_ready
from .media import extract_audio, read_span, speech_chunks
from .subtitles import Cue, aligned_cues, atomic_write, load_project, render, save_project, validate, needs_translation

EVENT_STREAM = sys.stdout


def event(type, **data):
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
    paths = model_paths(job)
    job = {**job, **{key: str(value) for key, value in paths.items()}}
    video, out = Path(job['video']), Path(job['output'])
    out.mkdir(parents=True, exist_ok=True)
    project = out / 'subtitles.yingzi.json'
    bilingual = job.get('bilingual', True)
    device = job.get('device', 'auto')
    start_clock = time.monotonic()
    if data is not None:
        if data.get('status') == 'aligning':
            raise ValueError('此工程的时间轴尚未全部生成，请重新生成字幕以处理完整视频。')
        video = Path(data['video'])
    else:
        cues = []
        if not video.is_file():
            raise ValueError('找不到所选视频。')
        missing = [f'{name}：{issue}' for name, key in [('语音识别', 'asr_model'), ('时间轴对齐', 'align_model')] if (issue := model_issue(paths[key], key))]
        if missing:
            raise ValueError('所选模型不可用：' + '；'.join(missing) + '。请打开“选择模型”检查。')
        backend = asr_backend(paths['asr_model'])
        if issue := asr_language_issue(paths['asr_model'], job.get('language')):
            raise ValueError(issue)
        if backend != 'qwen' and not extra_runtime_ready():
            raise ValueError('扩展识别引擎未安装。请打开“选择模型”，下载缺失的内置模型及运行环境。')
        progress(1, '提取视频音轨')
        (CACHE / 'jobs').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='job-', dir=CACHE / 'jobs') as work:
            wav = Path(work) / 'audio.wav'
            duration = extract_audio(video, wav, job.get('track', 0))
            progress(5, '定位语音，过滤静音片段')
            spans = speech_chunks(wav, emit=progress)
            if not spans:
                raise ValueError('所选音轨未检测到人声。请检查音轨或视频内容。')
            progress(15, f'加载 {paths["asr_model"].name}，共 {len(spans)} 个语音片段')
            records = []
            def store_record(i, record):
                if record['text'].strip():
                    records.append(record)
                progress(16 + 37 * (i + 1) / len(spans), f'识别语音 {i + 1}/{len(spans)}')
                atomic_write(out / 'recognition.json', json.dumps({'video': str(video), 'duration': duration, 'backend': backend, 'records': records}, ensure_ascii=False, indent=2))
            if backend == 'qwen':
                from qwen_asr import Qwen3ASRModel
                model = Qwen3ASRModel.from_pretrained(str(paths['asr_model']), max_inference_batch_size=1, max_new_tokens=512, **load_options(device))
                for i, (start, end) in enumerate(spans):
                    result = model.transcribe(audio=read_span(wav, start, end), language=job.get('language') or None, context=job.get('context', ''))[0]
                    store_record(i, {'start': start, 'end': end, 'text': result.text.strip(), 'language': result.language})
                del model
                release()
            else:
                from .asr_process import external_records
                for message in external_records(job, wav, spans):
                    if message['type'] == 'record':
                        message['record']['inference_seconds'] = message['seconds']
                        store_record(message['index'], message['record'])
                    elif message['type'] == 'backend':
                        event('backend', **{key: value for key, value in message.items() if key != 'type'})
            if not records:
                raise ValueError('检测到了声音，但未识别出文字。请确认音轨与语言设置。')
            progress(54, '加载时间轴模型，逐字对齐语音')
            from qwen_asr import Qwen3ForcedAligner
            aligner = Qwen3ForcedAligner.from_pretrained(str(paths['align_model']), **load_options(device))
            supported = {'Chinese', 'Japanese', 'English', 'Cantonese', 'French', 'German', 'Italian', 'Korean', 'Portuguese', 'Russian', 'Spanish'}
            for i, item in enumerate(records):
                language = item['language']
                if language not in supported:
                    raise ValueError(f'检测到暂不支持时间轴对齐的语言：{language}。可手动指定中文、日语或英语。')
                alignment = aligner.align(audio=read_span(wav, item['start'], item['end']), text=item['text'], language=language)[0]
                segment_cues = aligned_cues(item['text'], alignment.items, offset=item['start'], duration=item['end'] - item['start'], max_chars=int(job.get('max_chars', 30)), language=language)
                if cues and segment_cues and segment_cues[0].start < cues[-1].end:
                    segment_cues[0].start = cues[-1].end
                cues.extend(segment_cues)
                save_project(project, video, cues, job, status='aligning')
                progress(55 + 19 * (i + 1) / len(records), f'对齐时间轴 {i + 1}/{len(records)}')
            del aligner
            release()
    validate(cues)
    atomic_write(out / 'original.srt', render(cues, 'original'))
    save_project(project, video, cues, job, status='translating' if bilingual else 'complete')
    event('checkpoint', project=str(project))
    pending = [i for i, cue in enumerate(cues) if needs_translation(cue)]
    if bilingual and pending:
        status = readiness(job)
        if not status['字幕翻译'] or not status['翻译引擎']:
            raise ValueError('所选翻译模型或引擎未准备完成。原文字幕已保存，请在“选择模型”中检查后继续翻译。')
        progress(75, f'加载翻译模型 {paths["translation_model"].name}')
        from .translation import LocalTranslator
        with LocalTranslator(progress, gpu_layers=0 if device == 'cpu' else 99, model_path=paths['translation_model']) as translator:
            for first in range(0, len(pending), 8):
                indexes = pending[first:first + 8]
                rows = [{'id': i + 1, 'text': cues[i].text, 'language': cues[i].language} for i in indexes]
                context = '\n'.join(cue.text for cue in cues[max(0, indexes[0] - 3):indexes[0]])
                translations = translator.translate(rows, context)
                for i in indexes:
                    cues[i].translation = translations[i + 1]
                save_project(project, video, cues, job, status='translating')
                progress(76 + 21 * min(first + 8, len(pending)) / len(pending), f'翻译字幕 {min(first + 8, len(pending))}/{len(pending)}')
    save_project(project, video, cues, job)
    for mode in ('original', 'translated', 'bilingual'):
        atomic_write(out / f'{mode}.srt', render(cues, mode))
    atomic_write(out / 'bilingual.vtt', render(cues, 'bilingual', 'vtt'))
    progress(100, f'已完成 {len(cues)} 条字幕')
    event('done', project=str(project), output=str(out), count=len(cues), seconds=round(time.monotonic() - start_clock, 1))


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
