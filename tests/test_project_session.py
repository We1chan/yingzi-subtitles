"""When the editor may write the open project, without the editor window."""
from app.project_library import ProjectLibrary
from app.project_session import ProjectSession
from app.subtitles import Cue, load_project, project_workspace, save_project


def session(tmp_path, status):
    project = tmp_path / 'named.yingziproj'
    save_project(project, tmp_path / 'video.mp4', [Cue(0, 1, '原文')], status=status, workspace=tmp_path / 'cache')
    result = ProjectSession(ProjectLibrary(tmp_path / 'projects'))
    result.video = tmp_path / 'video.mp4'
    result.opened(project, load_project(project)[0])
    return result, project


def test_save_guards_and_status_mapping(tmp_path):
    current, project = session(tmp_path, 'complete')
    assert current.output_dir == tmp_path / 'cache'
    owned = [False]
    current.worker_owns = lambda: owned[0]
    for flag in ('loading', 'closed'):
        setattr(current, flag, True)
        assert not current.save([Cue(0, 1, '不应写入')], {}, {})
        setattr(current, flag, False)
    owned[0] = True
    assert not current.save([Cue(0, 1, '不应写入')], {}, {})
    owned[0] = False
    assert current.save([Cue(0, 1, '人工修改')], {}, {'step': 1})
    data, cues = load_project(project)
    assert data['status'] == 'edited' and cues[0].text == '人工修改'
    assert project_workspace(project, data) == tmp_path / 'cache'
    resumable, other = session(tmp_path / 'resume', 'aligning')
    resumable.save([Cue(0, 1, '原文')], {}, {})
    assert load_project(other)[0]['status'] == 'aligning'


def test_fresh_workspace_only_when_a_cache_exists(tmp_path):
    current, project = session(tmp_path, 'complete')
    assert current.fresh_workspace() == tmp_path / 'cache'
    (tmp_path / 'cache' / '.pipeline').mkdir(parents=True)
    fresh = current.fresh_workspace()
    assert fresh.parent == project.parent / 'work' and fresh.name.startswith('run_')
    assert current.project == project.resolve()


def test_recover_reads_the_workspace_checkpoint_and_ignores_damage(tmp_path):
    current = ProjectSession(ProjectLibrary(tmp_path / 'projects'))
    current.output_dir = tmp_path
    assert current.recover() is None
    save_project(tmp_path / 'subtitles.yingziproj', tmp_path / 'video.mp4', [Cue(0, 1, '检查点')], status='translating')
    assert [cue.text for cue in current.recover()] == ['检查点']
    assert current.project == tmp_path / 'subtitles.yingziproj' and current.status == 'translating'
    current.project.write_text('{broken', encoding='utf-8')
    assert current.recover() is None and current.status == 'translating'
