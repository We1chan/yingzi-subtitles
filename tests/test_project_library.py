import json
import os
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtGui import QPixmap

from app.gui import Studio, initialize_fonts
from app.project_library import ProjectLibrary
from app.project_home import ProjectHome, NewProjectDialog
from app.subtitles import Cue, load_project, save_project, project_workspace
from app.pipeline import cache_ready


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    return app


@pytest.fixture
def studio(application, monkeypatch):
    monkeypatch.setattr('app.gui.audio_tracks', lambda path: ['默认音轨'])
    window = Studio()
    window.model_timer.stop()
    yield window
    window.close()


def test_named_blank_project_attaches_media_without_creating_another_project(studio, tmp_path, monkeypatch):
    dialog = NewProjectDialog(studio.library.root)
    assert not dialog.create.isEnabled()
    dialog.name.setText('空工程 · 自选名称')
    assert dialog.create.isEnabled()
    monkeypatch.setattr(dialog, 'exec', lambda: QDialog.Accepted)
    monkeypatch.setattr('app.gui.NewProjectDialog', lambda *args: dialog)
    studio.new_project()
    project = studio.project
    data, cues = load_project(project)
    assert data['video'] == '' and data['status'] == 'draft' and not cues
    assert studio.video is None and studio.library.contains(project)
    assert not studio.start_button.isEnabled()
    media = tmp_path / 'new.mp4'
    media.touch()
    studio.load_video(media)
    assert studio.project == project and len(studio.library.scan()) == 1
    assert load_project(project)[0]['video'] == str(media)
    assert project_workspace(project) == project.parent / 'work'
    assert studio.export_folder() == project.parent / 'exports'


def test_project_home_search_sort_filter_open_and_return(studio, tmp_path):
    first = studio.library.create('Alpha', tmp_path / 'missing.mp4', [Cue(0, 2, 'Hello', language='English')], status='edited')
    second = studio.library.create('Beta')
    studio.show_project_library()
    home = studio.project_home
    assert studio.screen_stack.currentWidget() is home and home.cards.count() == 2
    home.search.setText('alpha')
    assert home.cards.count() == 1
    home.cards.setCurrentRow(0)
    home.open_button.click()
    assert studio.project == first and studio.screen_stack.currentIndex() == 1
    studio.open_project_button.click()
    assert studio.screen_stack.currentIndex() == 0 and not home.resume.isHidden()
    home.resume.click()
    assert studio.screen_stack.currentIndex() == 1 and studio.windowTitle().startswith('Alpha.yingziproj')
    studio.show_project_library()
    home.search.clear()
    home.sort.setCurrentIndex(1)
    assert home.cards.item(0).data(Qt.UserRole).name == 'Alpha'
    home.filter.setCurrentIndex(2)
    assert home.cards.count() == 1 and home.cards.item(0).data(Qt.UserRole).path == first
    assert second.exists()


def test_rename_active_project_keeps_autosave_on_new_filename(studio, monkeypatch):
    path = studio.library.create('原名称')
    studio.load_saved_project(path)
    studio.show_project_library()
    monkeypatch.setattr('app.gui.QInputDialog.getText', lambda *args, **kwargs: ('新名称', True))
    studio.rename_library_project(str(path))
    assert not path.exists() and studio.project.name == '新名称.yingziproj'
    studio.context.setText('改名后继续编辑')
    studio.save_edits()
    assert load_project(studio.project)[0]['options']['context'] == '改名后继续编辑'
    assert not path.exists() and studio.library.scan()[0].name == '新名称'


def test_trash_active_project_cannot_be_recreated_by_autosave_and_restores_cache(studio, tmp_path, monkeypatch):
    media = tmp_path / 'source.mp4'
    media.touch()
    path = studio.library.create('可恢复', media, [Cue(0, 1, '保存字幕')], status='aligning')
    work = project_workspace(path)
    (work / '.pipeline').mkdir(parents=True)
    state = work / '.pipeline/state.json'
    state.write_text(json.dumps({'fingerprint': {'video': str(media)}, 'aligned_complete': True}))
    (work / '.pipeline/audio.wav').write_bytes(b'unchanged audio cache')
    studio.load_saved_project(path)
    studio.show_project_library()
    monkeypatch.setattr('app.gui.QMessageBox.question', lambda *args: __import__('PySide6.QtWidgets', fromlist=['QMessageBox']).QMessageBox.Yes)
    studio.trash_library_project(str(path))
    QTest.qWait(500)
    assert studio.project is None and not path.exists() and media.exists()
    assert studio.library.scan() == []
    trashed = studio.library.scan(trash=True)[0].path
    assert project_workspace(trashed).is_dir()
    assert cache_ready(trashed, complete=True)
    studio.restore_library_project(str(trashed))
    restored = studio.library.scan()[0].path
    assert cache_ready(restored, complete=True) and load_project(restored)[1][0].text == '保存字幕'
    assert (project_workspace(restored) / '.pipeline/audio.wav').read_bytes() == b'unchanged audio cache'


def test_import_and_duplicate_have_independent_caches_and_preserve_original(tmp_path):
    external = tmp_path / 'external/project.yingziproj'
    work = tmp_path / 'external/work'
    (work / '.pipeline').mkdir(parents=True)
    (work / '.pipeline/audio.wav').write_bytes(b'cached audio')
    save_project(external, tmp_path / 'missing.mp4', [Cue(0, 1, '原文', '译文', 'English')],
                 {'video_export': {'codec': 'hevc'}, 'subtitle_style': {'font_size': 48}}, status='aligning', workspace=work)
    original = external.read_bytes()
    library = ProjectLibrary(tmp_path / 'projects')
    first = library.adopt(external)
    second = library.clone(first, '独立副本')
    assert external.read_bytes() == original
    assert project_workspace(first) != work and project_workspace(second) != project_workspace(first)
    assert (project_workspace(second) / '.pipeline/audio.wav').read_bytes() == b'cached audio'
    library.trash(first)
    assert second.exists() and project_workspace(second).is_dir()
    data, cues = load_project(second)
    assert data['options']['video_export']['codec'] == 'hevc' and cues[0].translation == '译文'


def test_reopening_imported_original_resumes_the_edited_library_copy(tmp_path):
    external = tmp_path / 'original.yingziproj'
    save_project(external, tmp_path / 'video.mp4', [Cue(0, 1, 'Original')])
    original = external.read_bytes()
    library = ProjectLibrary(tmp_path / 'projects')
    imported = library.adopt(external)
    save_project(imported, tmp_path / 'video.mp4', [Cue(0, 1, 'Edited')], workspace=project_workspace(imported))
    assert library.adopt(external) == imported and len(library.scan()) == 1
    assert load_project(imported)[1][0].text == 'Edited' and external.read_bytes() == original


def test_unreadable_project_can_be_recycled_and_restored_without_changing_bytes(tmp_path):
    library = ProjectLibrary(tmp_path / 'projects')
    path = library.create('待修复')
    content = b'{broken project'
    path.write_bytes(content)
    cache = path.parent / 'work/.pipeline/state.json'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'{broken cache')
    assert library.scan()[0].error
    library.trash(path)
    trashed = library.scan(trash=True)[0].path
    assert trashed.read_bytes() == content
    restored = library.restore(trashed)
    assert restored.read_bytes() == content and library.scan()[0].error
    assert (restored.parent / 'work/.pipeline/state.json').read_bytes() == b'{broken cache'


def test_failed_organize_restores_exact_files_and_cross_folder_references(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    media = output / 'one/video.mp4'
    media.parent.mkdir(parents=True)
    media.write_bytes(b'media')
    first = output / 'one/first.yingziproj'
    second = output / 'two/second.yingziproj'
    save_project(first, media, [Cue(0, 1, 'one')], workspace=media.parent)
    save_project(second, media, [Cue(0, 1, 'two')])
    state = media.parent / '.pipeline/state.json'
    state.parent.mkdir()
    state.write_text(json.dumps({'fingerprint': {'video': str(media)}}))
    originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (first, second, state)}
    library = ProjectLibrary(tmp_path / 'projects')
    update = library.update_metadata
    def fail_second(path, **kwargs):
        if Path(path).stem == 'second':
            raise OSError('injected metadata failure')
        update(path, **kwargs)
    monkeypatch.setattr(library, 'update_metadata', fail_second)
    with pytest.raises(OSError, match='injected'):
        library.organize(output)
    assert not library.scan() and media.read_bytes() == b'media'
    for path, (content, modified) in originals.items():
        assert path.read_bytes() == content and path.stat().st_mtime_ns == modified
        assert not (path.parent / '.project-meta.json').exists()


def test_organize_moves_full_folders_and_rebases_media_cache_and_shared_references(tmp_path):
    output, root = tmp_path / 'output', tmp_path / 'projects'
    folder = output / 'old'
    folder.mkdir(parents=True)
    media = folder / 'video.mp4'
    media.write_bytes(b'original media')
    first = folder / 'subtitles.yingziproj'
    save_project(first, media, [Cue(0, 1, '工程一')], workspace=folder, status='aligning')
    (folder / '.pipeline').mkdir()
    (folder / '.pipeline/state.json').write_text(json.dumps({'fingerprint': {'video': str(media)}, 'aligned_complete': True}))
    (folder / '.pipeline/audio.wav').write_bytes(b'original cache')
    save_project(output / 'other/second.yingziproj', media, [Cue(0, 1, '工程二')])
    before_time = first.stat().st_mtime_ns
    library = ProjectLibrary(root)
    paths = library.organize(output)
    assert len(paths) == 2 and not folder.exists()
    first = root / 'old/subtitles.yingziproj'
    assert first.stat().st_mtime_ns == before_time
    assert load_project(first)[0]['video'] == str(root / 'old/video.mp4')
    assert load_project(root / 'other/second.yingziproj')[0]['video'] == str(root / 'old/video.mp4')
    assert project_workspace(first) == root / 'old' and cache_ready(first, complete=True)
    assert (root / 'old/.pipeline/audio.wav').read_bytes() == b'original cache'
    assert (root / 'old/video.mp4').read_bytes() == b'original media'
    with pytest.raises(ValueError, match='媒体或缓存'):
        library.trash(first)


@pytest.mark.parametrize('name', ['', '../escape', 'A/B', 'CON', 'bad.', 'x' * 101])
def test_invalid_project_names_cannot_escape_library(tmp_path, name):
    library = ProjectLibrary(tmp_path / 'projects')
    with pytest.raises(ValueError):
        library.create(name)
    assert not library.scan()


def test_real_thumbnail_process_produces_cached_cover(application, tmp_path):
    from app.media import run_ffmpeg
    media = tmp_path / 'video.mp4'
    run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', media])
    library = ProjectLibrary(tmp_path / 'projects')
    path = library.create('封面工程', media)
    home = ProjectHome(library)
    try:
        home.refresh()
        for _ in range(80):
            QTest.qWait(50)
            if (path.parent / 'cover.jpg').is_file():
                break
        assert not QPixmap(str(path.parent / 'cover.jpg')).isNull()
        assert library.metadata(path)['cover_media'][0] == str(media)
        home.refresh()
        assert home.thumbnails.current is None and home.thumbnails.queue == []
    finally:
        home.thumbnails.stop()
        home.close()


@pytest.mark.parametrize('fail', [False, True])
def test_trash_releases_embedded_real_media_and_recovers_if_move_fails(studio, monkeypatch, fail):
    from app.media import run_ffmpeg
    from PySide6.QtWidgets import QMessageBox
    path = studio.library.create('目录内媒体')
    media = path.parent / 'source.mp4'
    run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', media])
    content = media.read_bytes()
    save_project(path, media, [Cue(0, .8, '保留字幕')], workspace=path.parent / 'work', editor={'position_ms': 400})
    studio.load_saved_project(path)
    QTest.qWait(150)
    studio.show_project_library()
    monkeypatch.setattr('app.gui.QMessageBox.question', lambda *args: QMessageBox.Yes)
    if fail:
        def fail_move(path):
            raise OSError('injected move failure')
        monkeypatch.setattr(studio.library, 'trash', fail_move)
    studio.trash_library_project(str(path))
    if fail:
        assert studio.project == path and media.read_bytes() == content
        assert Path(studio.player.source().toLocalFile()) == media
        assert studio.cues[0].text == '保留字幕'
        assert 'injected move failure' in studio.project_home.notice.text()
    else:
        assert studio.project is None and not media.exists()
        entry = studio.library.scan(trash=True)[0]
        assert entry.video.read_bytes() == content
        restored = studio.library.restore(entry.path)
        data, cues = load_project(restored)
        assert Path(data['video']).read_bytes() == content and cues[0].text == '保留字幕'
