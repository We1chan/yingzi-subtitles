"""Persistent recognition/alignment checkpoints, independent of subtitle segmentation."""
import json
from pathlib import Path
from types import SimpleNamespace

from .subtitles import aligned_cues, atomic_write, save_project, validate, project_workspace


def fingerprint(video, job, paths):
    stat = video.stat()
    return {'video': str(video.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'track': job.get('track', 0), 'language': job.get('language'), 'context': job.get('context', ''),
            **{key: str(paths[key].resolve()) for key in ('asr_model', 'align_model')}}


def cache_ready(project, complete=False):
    try:
        data = json.loads((project_workspace(project) / '.pipeline' / 'state.json').read_text(encoding='utf-8'))
        return bool(data.get('fingerprint')) and (not complete or data.get('aligned_complete') is True)
    except (OSError, ValueError):
        return False


def run_pipeline(job, paths, out, previous_cues, hooks):
    h = hooks
    video = Path(job['video'])
    if not video.is_file():
        raise ValueError('找不到所选视频，请恢复原文件后继续处理。')
    folder = out / '.pipeline'
    folder.mkdir(exist_ok=True)
    state_path, wav = folder / 'state.json', folder / 'audio.wav'
    identity = fingerprint(video, job, paths)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if state.get('fingerprint') != identity:
            raise ValueError('源视频、音轨或识别配置已改变，不能套用旧缓存。请恢复原配置或重新生成字幕。')
    else:
        if job.get('resume_project'):
            raise ValueError('此旧工程没有识别／对齐恢复缓存，需要重新生成字幕。')
        state = {'version': 1, 'fingerprint': identity, 'spans': None, 'records': [], 'alignments': []}

    def persist():
        atomic_write(state_path, json.dumps(state, ensure_ascii=False))

    def checkpoint(cues, status):
        project = Path(job.get('project_path') or out / 'subtitles.yingziproj')
        options = {key: value for key, value in job.items() if key not in ('project_path', 'editor_state', 'resume_project')}
        save_project(project, video, cues, options, status=status, workspace=out, editor=job.get('editor_state'))
        h.event('checkpoint', project=str(project), status=status)

    if job.get('resegment_only') and not state.get('aligned_complete'):
        raise ValueError('时间轴缓存尚未完成，请先继续处理。')
    if job.get('resegment_only'):
        cached = ''.join(''.join(record['text'].split()) for record in state['records'] if record)
        edited = ''.join(''.join(cue.text.split()) for cue in previous_cues)
        if cached != edited:
            raise ValueError('原文已经人工修改，与对齐缓存不一致。请先撤销原文修改或重新生成，避免覆盖校对结果。')
    backend = h.asr_backend(paths['asr_model'])
    if not state.get('aligned_complete'):
        persist()
        checkpoint(previous_cues, 'aligning' if state.get('recognition_complete') else 'recognizing')
        if not wav.exists() or 'duration' not in state or wav.stat().st_size != state.get('audio_size'):
            h.progress(1, '提取视频音轨并保存恢复缓存')
            temporary_audio = folder / 'audio.partial.wav'
            state['duration'] = h.extract_audio(video, temporary_audio, job.get('track', 0))
            if temporary_audio.exists():
                temporary_audio.replace(wav)
                state['audio_size'] = wav.stat().st_size
            persist()
        if state['spans'] is None:
            state['spans'] = h.speech_chunks(wav, emit=h.progress)
            if not state['spans']:
                raise ValueError('所选音轨未检测到人声。请检查音轨或视频内容。')
            state['records'] = [None] * len(state['spans'])
            state['alignments'] = [None] * len(state['spans'])
            persist()
        missing = [i for i, record in enumerate(state['records']) if record is None]
        if missing:
            if issue := h.model_issue(paths['asr_model'], 'asr_model'):
                raise ValueError('所选识别模型不可用：' + issue)
            if issue := h.asr_language_issue(paths['asr_model'], job.get('language')):
                raise ValueError(issue)
            if backend != 'qwen' and not h.extra_runtime_ready():
                raise ValueError('扩展识别引擎未安装，请打开“选择模型”检查。')
            h.progress(15, f'识别缓存 {len(state["records"]) - len(missing)}/{len(state["records"])}，加载识别模型')

            def store_record(index, record):
                state['records'][index] = record
                persist()
                records = [r for r in state['records'] if r is not None and r['text'].strip()]
                atomic_write(out / 'recognition.json', json.dumps({'video': str(video), 'duration': state['duration'],
                    'backend': backend, 'records': records}, ensure_ascii=False, indent=2))
                completed = sum(r is not None for r in state['records'])
                h.progress(16 + 37 * completed / len(state['spans']), f'识别语音 {completed}/{len(state["spans"])}')

            if backend == 'qwen':
                from qwen_asr import Qwen3ASRModel
                model = Qwen3ASRModel.from_pretrained(str(paths['asr_model']), max_inference_batch_size=1,
                    max_new_tokens=512, **h.load_options(job.get('device', 'auto')))
                try:
                    for index in missing:
                        start, end = state['spans'][index]
                        result = model.transcribe(audio=h.read_span(wav, start, end), language=job.get('language') or None,
                                                  context=job.get('context', ''))[0]
                        store_record(index, {'start': start, 'end': end, 'text': result.text.strip(), 'language': result.language})
                finally:
                    del model
                    h.release()
            else:
                from .asr_process import external_records
                for message in external_records(job, wav, [state['spans'][i] for i in missing]):
                    if message['type'] == 'record':
                        message['record']['inference_seconds'] = message['seconds']
                        store_record(missing[message['index']], message['record'])
                    elif message['type'] == 'backend':
                        h.event('backend', **{key: value for key, value in message.items() if key != 'type'})
            if any(record is None for record in state['records']):
                raise ValueError('识别结果不完整，已保留缓存，可继续处理剩余片段。')
        state['recognition_complete'] = True
        persist()
        if not any(record['text'].strip() for record in state['records']):
            raise ValueError('检测到了声音，但未识别出文字。请确认音轨与语言设置。')
        checkpoint(build_cues(state, job), 'aligning')
        missing = [i for i, record in enumerate(state['records']) if record['text'].strip() and state['alignments'][i] is None]
        if missing:
            if issue := h.model_issue(paths['align_model'], 'align_model'):
                raise ValueError('所选时间轴模型不可用：' + issue)
            h.progress(54, f'加载时间轴模型，继续对齐 {len(missing)} 个片段')
            from qwen_asr import Qwen3ForcedAligner
            aligner = Qwen3ForcedAligner.from_pretrained(str(paths['align_model']), **h.load_options(job.get('device', 'auto')))
            try:
                for index in missing:
                    record = state['records'][index]
                    alignment = aligner.align(audio=h.read_span(wav, record['start'], record['end']),
                        text=record['text'], language=record['language'])[0]
                    state['alignments'][index] = [{'text': item.text, 'start_time': float(item.start_time),
                        'end_time': float(item.end_time)} for item in alignment.items]
                    # Validate the raw alignment before marking this segment durable.
                    try:
                        aligned_cues(record['text'], alignment.items, language=record['language'])
                    except Exception:
                        state['alignments'][index] = None
                        raise
                    persist()
                    checkpoint(build_cues(state, job), 'aligning')
                    h.progress(55 + 19 * (index + 1) / len(state['records']), f'对齐时间轴 {index + 1}/{len(state["records"])}')
            finally:
                del aligner
                h.release()
        state['aligned_complete'] = True
        persist()
    cues = build_cues(state, job)
    previous = {(c.start, c.end, c.text, c.language): c for c in previous_cues}
    for cue in cues:
        old = previous.get((cue.start, cue.end, cue.text, cue.language))
        if old:
            cue.translation, cue.translation_error = old.translation, old.translation_error
    return cues


def build_cues(state, job):
    cues = []
    for record, alignment in zip(state['records'], state['alignments']):
        if record is None or not record['text'].strip():
            continue
        if alignment is None:
            break
        segment = aligned_cues(record['text'], [SimpleNamespace(**item) for item in alignment],
            offset=record['start'], duration=record['end'] - record['start'], language=record['language'],
            max_chars=int(job.get('max_chars') or 30), segmentation_mode=job.get('segmentation_mode', 'smart'))
        if cues and segment and segment[0].start < cues[-1].end:
            segment[0].start = cues[-1].end
        cues.extend(segment)
    validate(cues)
    return cues
