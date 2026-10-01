import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.project_migration import migrate_projects
from app.subtitle_import import input_kind
from app.subtitles import Cue, load_project, project_workspace, save_project


def legacy_project(path):
    data = {'version': 1, 'video': str(path.parent / 'missing.mp4'), 'status': 'aligning',
            'options': {'language': 'English', 'subtitle_style': {'font_size': 42},
                        'video_export': {'codec': 'hevc', 'video_bitrate': 2500}},
            'cues': [{'start': 1, 'end': 3, 'text': 'Hello', 'translation': '你好', 'language': 'English'}],
            'editor': {'position_ms': 2500, 'selected_rows': [0]}, 'extra_metadata': {'retain': True}}
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=4).encode('utf-8-sig')
    path.write_bytes(raw)
    return raw


def test_migration_keeps_exact_bytes_subtitles_metadata_and_cache(tmp_path):
    source = tmp_path / '子文件夹/工程 #1.yingzi.json'
    original = legacy_project(source)
    cache = source.parent / '.pipeline'
    cache.mkdir()
    state = cache / 'state.json'
    state.write_bytes(b'unchanged cache')
    plan = migrate_projects(tmp_path)
    target = source.with_name('工程 #1.yingziproj')
    assert len(plan) == 1 and not source.exists()
    assert target.read_bytes() == original and state.read_bytes() == b'unchanged cache'
    data, cues = load_project(target)
    assert data['status'] == 'aligning' and data['editor']['position_ms'] == 2500
    assert data['options']['video_export']['codec'] == 'hevc' and data['extra_metadata']['retain']
    assert cues == [Cue(1, 3, 'Hello', '你好', 'English')]
    assert project_workspace(target) == source.parent
    assert plan[0].sha256 == hashlib.sha256(original).hexdigest()
    assert migrate_projects(tmp_path) == []


def test_conflicting_new_project_stops_entire_batch(tmp_path):
    first, second = tmp_path / 'a.yingzi.json', tmp_path / 'b.yingzi.json'
    raw = legacy_project(first)
    legacy_project(second)
    target = tmp_path / 'b.yingziproj'
    target.write_text('existing new project', encoding='utf-8')
    with pytest.raises(ValueError, match='不能覆盖'):
        migrate_projects(tmp_path)
    assert first.read_bytes() == raw and second.exists()
    assert not (tmp_path / 'a.yingziproj').exists()
    assert target.read_text(encoding='utf-8') == 'existing new project'


def test_invalid_old_project_preserves_all_originals(tmp_path):
    valid = tmp_path / 'a.yingzi.json'
    raw = legacy_project(valid)
    broken = tmp_path / 'b.yingzi.json'
    broken.write_text('{', encoding='utf-8')
    with pytest.raises(ValueError):
        migrate_projects(tmp_path)
    assert valid.read_bytes() == raw and broken.exists()
    assert not list(tmp_path.glob('*.yingziproj'))


def test_dry_run_and_single_file_migration(tmp_path):
    source = tmp_path / 'existing.yingzi.json'
    raw = legacy_project(source)
    plan = migrate_projects(source, dry_run=True)
    assert len(plan) == 1 and source.read_bytes() == raw and not plan[0].target.exists()
    migrate_projects(source)
    assert plan[0].target.read_bytes() == raw and not source.exists()


def test_normal_app_rejects_old_project_extension(tmp_path):
    source = tmp_path / 'existing.yingzi.json'
    legacy_project(source)
    assert input_kind(source) is None
    with pytest.raises(ValueError, match='迁移'):
        load_project(source)
    with pytest.raises(ValueError, match='yingziproj'):
        save_project(source, tmp_path / 'missing.mp4', [])
    assert input_kind(tmp_path / 'example.YINGZIPROJ') == 'project'


def test_migration_command_produces_verified_report(tmp_path):
    source = tmp_path / '命令行工程.yingzi.json'
    legacy_project(source)
    report = tmp_path / 'report.json'
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'tools/migrate_projects.py'), str(source),
                             '--report', str(report)], cwd=root, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout.decode('utf-8')
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['dry_run'] is False and len(data['projects']) == 1
    target = tmp_path / '命令行工程.yingziproj'
    assert data['projects'][0]['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
