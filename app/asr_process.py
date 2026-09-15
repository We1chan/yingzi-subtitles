"""Bridge from the main worker to the optional ASR runtime."""
import json
import subprocess

from .config import EXTRA_PYTHON, ROOT
from .media import CREATE_NO_WINDOW
from .subtitles import atomic_write


def external_records(job, wav, spans):
    request = wav.parent / 'asr-request.json'
    atomic_write(request, json.dumps({**job, 'wav': str(wav), 'spans': spans}, ensure_ascii=False))
    completed = False
    error = ''
    process = subprocess.Popen([str(EXTRA_PYTHON), '-u', '-m', 'app.external_asr', str(request)],
        cwd=ROOT, stdout=subprocess.PIPE, encoding='utf-8', errors='replace', creationflags=CREATE_NO_WINDOW)
    try:
        for line in process.stdout:
            message = json.loads(line)
            if message['type'] == 'error':
                error = message['message']
            elif message['type'] == 'done':
                completed = True
            yield message
        code = process.wait()
        if code or not completed:
            raise RuntimeError(error or f'扩展识别进程意外退出（{code}），请检查运行日志。')
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, creationflags=CREATE_NO_WINDOW)
            process.wait(timeout=10)
        process.stdout.close()
