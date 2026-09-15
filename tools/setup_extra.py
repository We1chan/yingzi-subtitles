"""Install optional ASR libraries separately from Qwen's Transformers 4 runtime."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / 'runtime' / 'asr-env'
PYTHON = ENV / 'Scripts' / 'python.exe'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    os.environ['UV_CACHE_DIR'] = str(ROOT / '.cache' / 'uv')
    os.environ['PIP_CACHE_DIR'] = str(ROOT / '.cache' / 'pip')
    os.environ['HF_HUB_OFFLINE'] = '0'
    os.environ['TRANSFORMERS_OFFLINE'] = '0'
    uv = shutil.which('uv')
    def run(args):
        print('安装扩展识别环境…', flush=True)
        subprocess.run(args, cwd=ROOT, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if not PYTHON.is_file():
        run([uv, 'venv', '--python', sys.executable, str(ENV)] if uv else [sys.executable, '-m', 'venv', str(ENV)])
    (ENV / '.ready').unlink(missing_ok=True)
    installer = [uv, 'pip', 'install', '--python', str(PYTHON)] if uv else [str(PYTHON), '-m', 'pip', 'install']
    run([*installer, 'torch==2.8.0', '--index-url', 'https://download.pytorch.org/whl/cu128'])
    run([*installer, '-r', str(ROOT / 'requirements-extra-lock.txt'), '--index-url', 'https://pypi.org/simple'])
    run([str(PYTHON), '-c', 'import torch, onnx_asr, onnxruntime, soundfile; from transformers import CohereAsrForConditionalGeneration, AutoProcessor'])
    (ENV / '.ready').write_text('Cohere + Parakeet runtime\n', encoding='utf-8')
    print('扩展识别引擎已就绪。', flush=True)


if __name__ == '__main__':
    main()
