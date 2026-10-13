import json
import subprocess
from pathlib import Path

import pytest
from tools.configure_install import configure

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('profile,assets', [('original', ['asr', 'align']), ('online', ['asr', 'align']),
                                          ('local', ['asr', 'align', 'translate', 'runtime'])])
def test_setup_plan_only_selects_assets_and_cpu_wheels_without_installing(profile, assets):
    result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                             str(ROOT / 'tools/setup.ps1'), '-Profile', profile, '-Device', 'cpu', '-PlanOnly'],
                            capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert plan['profile'] == profile and plan['assets'] == assets
    assert plan['device'] == 'cpu' and plan['torch_index'].endswith('/cpu')
    assert plan['torch_packages'] == ['torch==2.8.0+cpu', 'torchaudio==2.8.0+cpu']
    assert plan['available_gb'] > 0 and plan['required_free_gb'] >= 2


def test_install_configuration_preserves_existing_api_and_preferences(tmp_path):
    path = tmp_path / 'settings.json'
    original = {'translation_backend': 'local', 'online_translation_secret': 'encrypted-test-data',
                'online_translation': {'base_url': 'https://service.example'}}
    path.write_text(json.dumps(original), encoding='utf-8')
    configure(path, 'online', 'cpu')
    data = json.loads(path.read_text(encoding='utf-8'))
    assert all(data[key] == value for key, value in original.items())
    assert data['installation_profile'] == 'online' and data['installation_device'] == 'cpu'
    fresh = tmp_path / 'new.json'
    configure(fresh, 'online', 'cuda')
    assert json.loads(fresh.read_text(encoding='utf-8'))['translation_backend'] == 'online'


def test_bad_settings_are_not_overwritten_by_installer(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        configure(path, 'original', 'cpu')
    assert path.read_text(encoding='utf-8') == '{broken'


def test_original_cpu_installation_defaults_and_project_options_override(monkeypatch, tmp_path):
    import os
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    from app.gui import Studio
    from app.subtitles import Cue, save_project
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr('app.studio_layout.read_settings', lambda: {'installation_profile': 'original', 'installation_device': 'cpu'})
    monkeypatch.setattr('app.gui.readiness', lambda *args: {'语音识别': True, '时间轴对齐': True, '字幕翻译': False, '翻译引擎': False})
    window = Studio()
    try:
        assert window.mode.currentData() is False and window.device.currentData() == 'cpu'
        assert '识别功能已准备好' in window.model_status.text()
        project = tmp_path / 'existing.yingziproj'
        save_project(project, tmp_path / 'missing.mp4', [Cue(0, 1, 'Existing')], {'bilingual': True, 'device': 'auto'})
        window.load_saved_project(project)
        assert window.mode.currentData() is True and window.device.currentData() == 'auto'
        assert '2 / 4' in window.model_status.text()
    finally:
        window.close()
