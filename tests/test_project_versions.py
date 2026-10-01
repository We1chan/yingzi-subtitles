import pytest

from app.project_library import ProjectLibrary
from app.project_session import ProjectSession
from app.project_versions import create_version, list_versions, restore_version, compare_version, MAX_VERSIONS
from app.subtitles import Cue, load_project, save_project, project_workspace


def project(tmp_path):
    path = tmp_path / '字幕.yingziproj'
    save_project(path, tmp_path / 'video.mp4', [Cue(1, 2, 'Original', '原译文', 'English', reviewed=True)],
                 {'subtitle_style': {'font_size': 36}}, workspace=tmp_path / 'cache', editor={'position_ms': 1500})
    return path


def test_compare_and_restore_preserve_current_as_a_new_version(tmp_path):
    path = project(tmp_path)
    original = load_project(path)
    saved = create_version(path, '重新生成前', force=True)
    save_project(path, tmp_path / 'video.mp4', [Cue(3, 4, 'Changed', '新译文')], {'language': 'English'})
    comparison = compare_version(path, saved.path)
    assert '时间轴不同：1 条' in comparison and 'Changed' in comparison and 'Original' in comparison
    restore_version(path, saved.path)
    assert load_project(path) == original
    versions = list_versions(path)
    assert versions[0].reason == '恢复历史版本前'
    assert load_project(versions[0].path)[1][0].text == 'Changed'


def test_versions_are_bounded_and_duplicate_playhead_changes_do_not_add_versions(tmp_path):
    path = project(tmp_path)
    saved = create_version(path, now=1)
    data, cues = load_project(path)
    save_project(path, data['video'], cues, data['options'], workspace=tmp_path / 'cache', editor={'position_ms': 1900})
    assert create_version(path, now=2).path == saved.path
    for index in range(MAX_VERSIONS + 3):
        create_version(path, f'操作 {index}', force=True, now=10 + index)
    assert len(list_versions(path)) == MAX_VERSIONS
    assert len(list(saved.path.parent.glob('*.json'))) == MAX_VERSIONS


def test_wrong_project_or_corrupt_version_cannot_replace_current_project(tmp_path):
    path = project(tmp_path)
    saved = create_version(path)
    before = path.read_bytes()
    outside = tmp_path / 'outside.yingziproj'
    outside.write_bytes(saved.path.read_bytes())
    with pytest.raises(ValueError):
        restore_version(path, outside)
    saved.path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        restore_version(path, saved.path)
    assert path.read_bytes() == before


def test_automatic_versions_follow_interval_and_worker_save_guard(tmp_path, monkeypatch):
    path = project(tmp_path)
    session = ProjectSession(ProjectLibrary(tmp_path / 'projects'))
    session.video = tmp_path / 'video.mp4'
    session.opened(path, load_project(path)[0])
    clock = [0]
    monkeypatch.setattr('app.project_session.time.monotonic', lambda: clock[0])
    session.save([Cue(1, 2, 'First edit')], {}, {})
    session.save([Cue(1, 2, 'Second edit')], {}, {})
    assert len(list_versions(path)) == 1
    clock[0] = 301
    session.save([Cue(1, 2, 'Third edit')], {}, {})
    assert len(list_versions(path)) == 2
    assert load_project(list_versions(path)[0].path)[1][0].text == 'Second edit'
    session.worker_owns = lambda: True
    assert session.snapshot('重新生成前') is None
    assert not session.save([Cue(1, 2, 'Worker conflict')], {}, {})
    assert load_project(path)[1][0].text == 'Third edit'


def test_rename_trash_and_restore_keep_history_and_internal_media_paths(tmp_path):
    library = ProjectLibrary(tmp_path / 'projects')
    path = library.create('原名')
    media = path.parent / 'video.mp4'
    media.write_bytes(b'original-media')
    save_project(path, media, [Cue(0, 1, 'Keep')], workspace=path.parent / 'work')
    create_version(path)
    renamed = library.rename(path, '新名')
    assert len(list_versions(renamed)) == 1 and len(library.scan()) == 1
    library.trash(renamed)
    trashed = library.scan(trash=True)[0].path
    assert len(list_versions(trashed)) == 1
    restored = library.restore(trashed)
    saved = list_versions(restored)[0]
    restore_version(restored, saved.path)
    assert load_project(restored)[0]['video'] == str(restored.parent / 'video.mp4')
    assert project_workspace(restored) == restored.parent / 'work'
    assert (restored.parent / 'video.mp4').read_bytes() == b'original-media'


def test_snapshot_write_failure_prevents_overwriting_project(tmp_path, monkeypatch):
    path = project(tmp_path)
    session = ProjectSession(ProjectLibrary(tmp_path / 'projects'))
    session.video = tmp_path / 'video.mp4'
    session.opened(path, load_project(path)[0])
    before = path.read_bytes()
    monkeypatch.setattr('app.project_versions.atomic_write', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        session.save([Cue(1, 2, 'Would overwrite')], {}, {})
    assert path.read_bytes() == before
