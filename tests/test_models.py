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
