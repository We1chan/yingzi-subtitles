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

from .config import ALIGN_PATH, ASR_PATH, CACHE, ROOT, readiness
from .media import extract_audio, read_span, speech_chunks
from .subtitles import Cue, aligned_cues, atomic_write, load_project, render, save_project, validate

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
    video, out = Path(job['video']), Path(job['output'])
    out.mkdir(parents=True, exist_ok=True)
    project = out / 'subtitles.yingzi.json'
    bilingual = job.get('bilingual', True)
    device = job.get('device', 'auto')
    cues = []
    start_clock = time.monotonic()
    if job.get('resume_project'):
        data, cues = load_project(job['resume_project'])
        if data.get('status') == 'aligning':
            raise ValueError('此工程的时间轴尚未全部生成，请重新生成字幕以处理完整视频。')
        video = Path(data['video'])
    else:
        if not video.is_file():
            raise ValueError('找不到所选视频。')
        status = readiness()
        missing = [name for name in ('语音识别', '时间轴对齐') if not status[name]]
        if missing:
            raise ValueError('模型未安装完成：' + '、'.join(missing) + '。请运行“安装或修复环境”。')
        progress(1, '提取视频音轨')
        (CACHE / 'jobs').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='job-', dir=CACHE / 'jobs') as work:
            wav = Path(work) / 'audio.wav'
            duration = extract_audio(video, wav, job.get('track', 0))
            progress(5, '定位语音，过滤静音片段')
            spans = speech_chunks(wav, emit=progress)
            if not spans:
                raise ValueError('所选音轨未检测到人声。请检查音轨或视频内容。')
            progress(15, f'加载识别模型，共 {len(spans)} 个语音片段')
            from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner
            model = Qwen3ASRModel.from_pretrained(str(ASR_PATH), max_inference_batch_size=1, max_new_tokens=512, **load_options(device))
            records = []
            for i, (start, end) in enumerate(spans):
                result = model.transcribe(audio=read_span(wav, start, end), language=job.get('language') or None, context=job.get('context', ''))[0]
                if result.text.strip():
                    records.append({'start': start, 'end': end, 'text': result.text.strip(), 'language': result.language})
                progress(16 + 37 * (i + 1) / len(spans), f'识别语音 {i + 1}/{len(spans)}')
                atomic_write(out / 'recognition.json', json.dumps({'video': str(video), 'duration': duration, 'records': records}, ensure_ascii=False, indent=2))
            del model
            release()
            if not records:
                raise ValueError('检测到了声音，但未识别出文字。请确认音轨与语言设置。')
            progress(54, '加载时间轴模型，逐字对齐语音')
            aligner = Qwen3ForcedAligner.from_pretrained(str(ALIGN_PATH), **load_options(device))
            supported = {'Chinese', 'Japanese', 'English', 'Cantonese', 'French', 'German', 'Italian', 'Korean', 'Portuguese', 'Russian', 'Spanish'}
            for i, item in enumerate(records):
                language = item['language']
                if language not in supported:
                    raise ValueError(f'检测到暂不支持时间轴对齐的语言：{language}。可手动指定中文或日语。')
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
    japanese = [i for i, cue in enumerate(cues) if cue.language == 'Japanese' and not cue.translation]
    if bilingual and japanese:
        status = readiness()
        if not status['日中翻译'] or not status['翻译引擎']:
            raise ValueError('日中翻译模型或引擎未准备完成。原文字幕已保存，可稍后重试翻译。')
        progress(75, '加载日中翻译模型')
        from .translation import LocalTranslator
        with LocalTranslator(progress, gpu_layers=0 if device == 'cpu' else 99) as translator:
            for first in range(0, len(japanese), 8):
                indexes = japanese[first:first + 8]
                rows = [{'id': i + 1, 'text': cues[i].text} for i in indexes]
                context = '\n'.join(cue.text for cue in cues[max(0, indexes[0] - 3):indexes[0]])
                translations = translator.translate(rows, context)
                for i in indexes:
                    cues[i].translation = translations[i + 1]
                save_project(project, video, cues, job, status='translating')
                progress(76 + 21 * min(first + 8, len(japanese)) / len(japanese), f'翻译字幕 {min(first + 8, len(japanese))}/{len(japanese)}')
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
