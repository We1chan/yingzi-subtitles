import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / 'models'
CACHE = ROOT / '.cache'
OUTPUT = ROOT / 'output'
PROJECTS = ROOT / 'projects'
RUNTIME = ROOT / 'runtime'
LOGS = ROOT / 'logs'
ASR_PATH = MODELS / 'Qwen3-ASR-1.7B'
ALIGN_PATH = MODELS / 'Qwen3-ForcedAligner-0.6B'
TRANSLATE_PATH = MODELS / 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
SETTINGS = ROOT / 'settings.json'
MODEL_DEFAULTS = {'asr_model': ASR_PATH, 'align_model': ALIGN_PATH, 'translation_model': TRANSLATE_PATH}

for directory in (MODELS, CACHE, OUTPUT, RUNTIME, LOGS):
    directory.mkdir(parents=True, exist_ok=True)

# Keep CLI login discoverable after moving inference caches into the project.
hf_login_home = Path(os.environ.get('HF_HOME', str(Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'huggingface'))).expanduser()
os.environ.setdefault('HF_TOKEN_PATH', str(hf_login_home / 'token'))
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


def model_paths(options=None):
    result = {}
    for key, default in MODEL_DEFAULTS.items():
        if key == 'asr_model':
            # Old settings and projects may still point at a retired recognition model.
            result[key] = ASR_PATH.resolve()
            continue
        value = (options or {}).get(key)
        path = Path(value).expanduser() if isinstance(value, str) and value.strip() else default
        result[key] = (path if path.is_absolute() else ROOT / path).resolve()
    return result


def load_model_selection():
    try:
        data = json.loads(SETTINGS.read_text(encoding='utf-8'))
        return {key: str(value) for key, value in model_paths(data if isinstance(data, dict) else {}).items()}
    except (OSError, ValueError):
        return {key: str(value) for key, value in MODEL_DEFAULTS.items()}


def save_model_selection(options):
    from .subtitles import atomic_write
    try:
        data = json.loads(SETTINGS.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.update({key: str(value) for key, value in model_paths(options).items()})
    atomic_write(SETTINGS, json.dumps(data, ensure_ascii=False, indent=2))


def model_issue(path, kind):
    """Check local assets without loading tensors or requiring our download marker."""
    path = Path(path)
    try:
        if kind == 'translation_model':
            if not path.is_file() or path.suffix.lower() != '.gguf':
                return '请选择本地 GGUF 翻译模型文件'
            with path.open('rb') as source:
                if source.read(4) != b'GGUF' or path.stat().st_size < 32:
                    return 'GGUF 文件不完整或格式无效'
            return ''
        if not path.is_dir():
            return '模型目录不存在，请先下载或选择本地目录'
        config = json.loads((path / 'config.json').read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            return '模型配置无效'
        model_type = config.get('model_type')
        if model_type != 'qwen3_asr':
            return '语音识别固定使用 Qwen3-ASR-1.7B；对齐需 Qwen3-ForcedAligner'
        is_aligner = 'timestamp_token_id' in config
        if is_aligner != (kind == 'align_model'):
            return '识别模型与时间轴对齐模型不能互换'
        required = ['tokenizer_config.json', 'preprocessor_config.json']
        required.extend(['tokenizer.json'] if (path / 'tokenizer.json').is_file() else ['vocab.json', 'merges.txt'])
        index = path / 'model.safetensors.index.json'
        if index.exists():
            weights = json.loads(index.read_text(encoding='utf-8')).get('weight_map', {})
            if not isinstance(weights, dict) or not weights:
                return '模型权重索引无效'
            required.extend(set(weights.values()))
        else:
            required.append('model.safetensors')
        for name in required:
            file = (path / name).resolve()
            if not file.is_relative_to(path.resolve()) or not file.is_file() or file.stat().st_size == 0:
                return f'模型文件缺失：{name}'
        return ''
    except (OSError, ValueError, TypeError, AttributeError):
        return '无法读取模型配置，请检查文件是否完整'


def readiness(options=None):
    paths = model_paths(options)
    state = {
        '语音识别': not model_issue(paths['asr_model'], 'asr_model'),
        '时间轴对齐': not model_issue(paths['align_model'], 'align_model'),
        '字幕翻译': not model_issue(paths['translation_model'], 'translation_model'),
        '翻译引擎': server_path().exists(),
    }
    if (options or {}).get('translation_backend') == 'online':
        from .api_settings import api_issue
        state['字幕翻译'] = not api_issue()
        state['翻译引擎'] = True
    return state
