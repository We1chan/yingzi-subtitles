import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / 'models'
CACHE = ROOT / '.cache'
OUTPUT = ROOT / 'output'
RUNTIME = ROOT / 'runtime'
LOGS = ROOT / 'logs'
ASR_PATH = MODELS / 'Qwen3-ASR-1.7B'
ALIGN_PATH = MODELS / 'Qwen3-ForcedAligner-0.6B'
TRANSLATE_PATH = MODELS / 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'

for directory in (MODELS, CACHE, OUTPUT, RUNTIME, LOGS):
    directory.mkdir(parents=True, exist_ok=True)

os.environ.setdefault('HF_HOME', str(CACHE / 'huggingface'))
os.environ.setdefault('TORCH_HOME', str(CACHE / 'torch'))
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')


def server_path():
    cuda = RUNTIME / 'llama-cuda' / 'llama-server.exe'
    if cuda.exists():
        return cuda
    candidates = list(RUNTIME.glob('**/llama-server.exe'))
    return candidates[0] if candidates else RUNTIME / 'llama-server.exe'


def readiness():
    return {
        '语音识别': (ASR_PATH / '.complete').exists(),
        '时间轴对齐': (ALIGN_PATH / '.complete').exists(),
        '日中翻译': TRANSLATE_PATH.exists() and TRANSLATE_PATH.with_suffix('.gguf.complete').exists(),
        '翻译引擎': server_path().exists(),
    }
