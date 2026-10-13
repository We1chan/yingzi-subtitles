import json

import pytest

from app import config


def snapshot(path, align=False, sharded=False):
    path.mkdir(parents=True)
    data = {'model_type': 'qwen3_asr'}
    if align:
        data['timestamp_token_id'] = 123
    (path / 'config.json').write_text(json.dumps(data))
    for name in ['tokenizer_config.json', 'preprocessor_config.json', 'vocab.json', 'merges.txt']:
        (path / name).write_text('{}')
    if sharded:
        (path / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'one': 'part-1.safetensors', 'two': 'part-2.safetensors'}}))
        (path / 'part-1.safetensors').write_bytes(b'weights')
    else:
        (path / 'model.safetensors').write_bytes(b'weights')
    return path


def test_manual_snapshot_requires_complete_weights_and_matching_role(tmp_path):
    path = snapshot(tmp_path / 'manual-asr', sharded=True)
    assert 'part-2.safetensors' in config.model_issue(path, 'asr_model')
    (path / 'part-2.safetensors').write_bytes(b'weights')
    assert config.model_issue(path, 'asr_model') == ''
    assert config.model_issue(path, 'align_model')
    align = snapshot(tmp_path / 'manual-align', align=True)
    assert config.model_issue(align, 'align_model') == ''
    assert config.model_issue(align, 'asr_model')
    (path / 'config.json').write_text('{"model_type":"whisper"}')
    assert config.model_issue(path, 'asr_model')


def test_gguf_validation_does_not_require_private_download_marker(tmp_path):
    path = tmp_path / 'external.gguf'
    path.write_bytes(b'not a model')
    assert config.model_issue(path, 'translation_model')
    path.write_bytes(b'GGUF' + b'\x00' * 32)
    assert config.model_issue(path, 'translation_model') == ''


@pytest.mark.parametrize('name', ['Qwen3-ASR-0.6B', 'cohere-transcribe-03-2026', 'parakeet-tdt-0.6b-v2-onnx', '英文模型'])
def test_settings_force_qwen_and_preserve_other_preferences(tmp_path, monkeypatch, name):
    settings = tmp_path / 'settings.json'
    monkeypatch.setattr(config, 'SETTINGS', settings)
    settings.write_text(json.dumps({'theme': 'light', 'asr_model': str(tmp_path / name)}))
    assert config.load_model_selection()['asr_model'] == str(config.ASR_PATH.resolve())
    selected = {'asr_model': str(tmp_path / name), 'align_model': str(tmp_path / 'align'),
                'translation_model': str(tmp_path / 'translate.gguf')}
    config.save_model_selection(selected)
    loaded = config.load_model_selection()
    assert loaded['asr_model'] == str(config.ASR_PATH.resolve())
    assert loaded['align_model'] == str((tmp_path / 'align').resolve())
    assert loaded['translation_model'] == str((tmp_path / 'translate.gguf').resolve())
    assert json.loads(settings.read_text())['theme'] == 'light'
    assert json.loads(settings.read_text())['asr_model'] == str(config.ASR_PATH.resolve())
    settings.write_text('bad json')
    assert config.load_model_selection()['asr_model'] == str(config.ASR_PATH)


def test_legacy_jobs_keep_original_defaults():
    assert config.model_paths({}) == config.MODEL_DEFAULTS


@pytest.mark.parametrize('model_type', ['cohere_asr', 'nemo-conformer-tdt', 'whisper'])
def test_other_recognition_architectures_are_rejected(tmp_path, model_type):
    path = snapshot(tmp_path / 'other-model')
    (path / 'config.json').write_text(json.dumps({'model_type': model_type}))
    assert 'Qwen3-ASR-1.7B' in config.model_issue(path, 'asr_model')
    assert config.model_issue(path, 'align_model')


def test_readiness_uses_fixed_qwen_without_an_extra_environment(monkeypatch):
    checked = {}
    def issue(path, kind):
        checked[kind] = path
        return ''
    monkeypatch.setattr(config, 'model_issue', issue)
    state = config.readiness({'asr_model': 'models/cohere-transcribe-03-2026'})
    assert checked['asr_model'] == config.ASR_PATH.resolve()
    assert set(state) == {'语音识别', '时间轴对齐', '字幕翻译', '翻译引擎'}


@pytest.mark.parametrize('task', ['asr-small', 'cohere', 'parakeet', 'extra-runtime'])
def test_retired_download_tasks_are_rejected_before_any_download(monkeypatch, task):
    from tools import download_assets
    monkeypatch.setattr(download_assets, 'snapshot', lambda *args: pytest.fail('Unexpected download'))
    with pytest.raises(SystemExit) as exc:
        download_assets.main(['asr', task])
    assert exc.value.code == 2


def test_asr_download_only_fetches_qwen_17b(monkeypatch):
    from tools import download_assets
    fetched = []
    monkeypatch.setattr(download_assets, 'snapshot', lambda *args: fetched.append(args))
    download_assets.main(['asr'])
    assert fetched == [('Qwen/Qwen3-ASR-1.7B', 'Qwen3-ASR-1.7B')]


def test_model_download_redirect_does_not_forward_hf_credential():
    from urllib.request import Request
    from tools.download_assets import SafeRedirect
    original = Request('https://huggingface.co/model/file', headers={'Authorization': 'Bearer test-only', 'Range': 'bytes=12-'})
    handler = SafeRedirect()
    cross_host = handler.redirect_request(original, None, 302, 'Found', {}, 'https://cdn.example/model')
    assert cross_host.get_header('Authorization') is None
    assert cross_host.get_header('Range') == 'bytes=12-'
    same_host = handler.redirect_request(original, None, 302, 'Found', {}, 'https://huggingface.co/new-path')
    assert same_host.get_header('Authorization') == 'Bearer test-only'
