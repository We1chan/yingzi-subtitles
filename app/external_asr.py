"""Cohere / ONNX inference in an isolated environment; no network during jobs."""
import contextlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

from .config import asr_backend, asr_language_issue
from .media import read_span

EVENT_STREAM = sys.stdout


def emit(kind, **data):
    print(json.dumps({'type': kind, **data}, ensure_ascii=False), file=EVENT_STREAM, flush=True)


def run(job):
    import torch
    torch.set_num_threads(min(8, os.cpu_count() or 4))
    backend = asr_backend(job['asr_model'])
    language = job.get('language')
    issue = asr_language_issue(job['asr_model'], language)
    if issue:
        raise ValueError(issue)
    gpu = job.get('device') != 'cpu' and torch.cuda.is_available()
    load_start = time.monotonic()
    if backend == 'cohere':
        from transformers import AutoProcessor, CohereAsrForConditionalGeneration
        processor = AutoProcessor.from_pretrained(job['asr_model'], local_files_only=True)
        model = CohereAsrForConditionalGeneration.from_pretrained(job['asr_model'],
            dtype=torch.bfloat16 if gpu else torch.float32, device_map='cuda:0' if gpu else 'cpu',
            local_files_only=True, attn_implementation='sdpa')
        code = {'Chinese': 'zh', 'Japanese': 'ja', 'English': 'en'}[language]
        def recognize(audio, sr):
            inputs = processor(audio, sampling_rate=sr, return_tensors='pt', language=code)
            chunk_index = inputs.get('audio_chunk_index')
            inputs.to(model.device, dtype=model.dtype)
            with torch.inference_mode():
                output = model.generate(**inputs, max_new_tokens=512)
            texts = processor.decode(output, skip_special_tokens=True, audio_chunk_index=chunk_index, language=code)
            return texts[0] if isinstance(texts, list) else texts, {}
        provider = 'CUDA' if gpu else 'CPU'
    elif backend == 'parakeet':
        import onnxruntime as ort
        import onnx_asr
        # PyTorch loads its CUDA/cuDNN DLLs before ORT on Windows.
        if gpu:
            ort.preload_dlls()
        providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if gpu else ['CPUExecutionProvider']
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(8, os.cpu_count() or 4)
        options.inter_op_num_threads = 1
        model = onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v2', job['asr_model'],
            providers=providers, sess_options=options).with_timestamps()
        def recognize(audio, sr):
            result = model.recognize(audio, sample_rate=sr)
            return result.text, {'tokens': result.tokens, 'timestamps': result.timestamps}
        provider = model.asr._encoder.get_providers()[0]
        if gpu and provider != 'CUDAExecutionProvider':
            raise RuntimeError('Parakeet 的 CUDA 引擎未成功加载，请修复扩展环境或选择 CPU。')
    else:
        raise ValueError(f'不支持的扩展识别引擎：{backend}')
    emit('backend', backend=backend, provider=provider, load_seconds=round(time.monotonic() - load_start, 3))
    for index, (start, end) in enumerate(job['spans']):
        began = time.monotonic()
        text, extra = recognize(*read_span(job['wav'], start, end))
        emit('record', index=index, record={'start': start, 'end': end, 'text': text.strip(),
            'language': language, **extra}, seconds=round(time.monotonic() - began, 3))
    emit('done')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        job = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        with contextlib.redirect_stdout(sys.stderr):
            run(job)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit('error', message=str(exc))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
