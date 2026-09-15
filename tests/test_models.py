import json

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


def test_settings_persist_external_paths_and_preserve_other_preferences(tmp_path, monkeypatch):
    settings = tmp_path / 'settings.json'
    monkeypatch.setattr(config, 'SETTINGS', settings)
    settings.write_text('{"theme":"light"}')
    selected = {'asr_model': str(tmp_path / '英文模型'), 'translation_model': str(tmp_path / 'translate.gguf')}
    config.save_model_selection(selected)
    loaded = config.load_model_selection()
    assert loaded['asr_model'] == str((tmp_path / '英文模型').resolve())
    assert loaded['translation_model'] == str((tmp_path / 'translate.gguf').resolve())
    assert json.loads(settings.read_text())['theme'] == 'light'
    settings.write_text('bad json')
    assert config.load_model_selection()['asr_model'] == str(config.ASR_PATH)


def test_legacy_jobs_keep_original_defaults():
    assert config.model_paths({}) == config.MODEL_DEFAULTS


def test_cohere_snapshot_is_asr_only_and_requires_explicit_language(tmp_path):
    path = snapshot(tmp_path / 'cohere')
    (path / 'config.json').write_text('{"model_type":"cohere_asr"}')
    (path / 'processor_config.json').write_text('{}')
    assert config.asr_backend(path) == 'cohere'
    assert config.model_issue(path, 'asr_model') == ''
    assert config.model_issue(path, 'align_model')
    assert config.asr_language_issue(path, '')
    assert config.asr_language_issue(path, 'Japanese') == ''


def test_parakeet_requires_external_onnx_weights_and_english(tmp_path):
    path = tmp_path / 'parakeet'
    path.mkdir()
    (path / 'config.json').write_text('{"model_type":"nemo-conformer-tdt"}')
    for name in ['encoder-model.onnx', 'decoder_joint-model.onnx', 'vocab.txt', 'nemo128.onnx']:
        (path / name).write_bytes(b'asset')
    assert 'encoder-model.onnx.data' in config.model_issue(path, 'asr_model')
    (path / 'encoder-model.onnx.data').write_bytes(b'weights')
    assert config.model_issue(path, 'asr_model') == ''
    assert config.model_issue(path, 'align_model')
    assert config.asr_language_issue(path, 'English') == ''
    assert config.asr_language_issue(path, 'Japanese')
    assert config.asr_language_issue(path, '')


def test_extra_runtime_is_required_only_for_new_engines(monkeypatch):
    monkeypatch.setattr(config, 'model_issue', lambda *args: '')
    monkeypatch.setattr(config, 'extra_runtime_ready', lambda: False)
    assert '扩展识别引擎' not in config.readiness({})
    assert config.readiness({'asr_model': 'models/cohere-transcribe-03-2026'})['扩展识别引擎'] is False


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
