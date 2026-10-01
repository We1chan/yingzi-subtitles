"""Bounded local project snapshots; media and processing caches remain referenced."""
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .subtitles import atomic_write, load_project

MAX_VERSIONS = 30
AUTO_INTERVAL = 300


@dataclass
class ProjectVersion:
    path: Path
    created: float
    reason: str
    cue_count: int
    digest: str


def version_folder(project):
    project = Path(project).resolve()
    base = project.parent / '.history'
    folder = base / hashlib.sha256(project.name.encode('utf-8')).hexdigest()[:16]
    if base.is_symlink() or folder.is_symlink():
        raise ValueError('历史版本目录不能是符号链接。')
    return folder


def list_versions(project):
    folder = version_folder(project)
    if not folder.is_dir():
        return []
    result = []
    for path in folder.glob('*.yingziproj'):
        if path.is_symlink():
            continue
        try:
            data = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            result.append(ProjectVersion(path, float(data['created']), str(data['reason']),
                                         int(data['cue_count']), str(data['digest'])))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(result, key=lambda version: (version.created, version.path.name), reverse=True)


def create_version(project, reason='自动保存前', *, force=False, now=None):
    project = Path(project).resolve()
    if not project.is_file():
        return None
    # Validate before copying. Keep the original relative paths for restoration into the original folder.
    load_project(project)
    content = project.read_text(encoding='utf-8-sig')
    data = json.loads(content)
    comparable = {key: value for key, value in data.items() if key != 'editor'}
    digest = hashlib.sha256(json.dumps(comparable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    versions = list_versions(project)
    if not force and versions and versions[0].digest == digest:
        return versions[0]
    created = time.time() if now is None else now
    folder = version_folder(project)
    path = folder / f'{int(created * 1000000):020d}_{uuid.uuid4().hex[:8]}.yingziproj'
    atomic_write(path, content)
    atomic_write(path.with_suffix('.json'), json.dumps({'created': created, 'reason': reason,
                 'cue_count': len(data['cues']), 'digest': digest}, ensure_ascii=False))
    version = ProjectVersion(path, created, reason, len(data['cues']), digest)
    for old in sorted([version, *versions], key=lambda item: (item.created, item.path.name), reverse=True)[MAX_VERSIONS:]:
        old.path.unlink(missing_ok=True)
        old.path.with_suffix('.json').unlink(missing_ok=True)
    return version


def checked_version(project, version):
    path = Path(version)
    folder = version_folder(project).resolve()
    if path.is_symlink() or path.resolve().parent != folder or path.suffix != '.yingziproj':
        raise ValueError('请选择此工程的历史版本。')
    load_project(path)
    return path


def compare_version(project, version):
    path = checked_version(project, version)
    current, cues = load_project(project)
    saved, previous = load_project(path)
    fields = [('文字', ('text', 'translation', 'translation_error')), ('时间轴', ('start', 'end')),
              ('校对标记', ('reviewed',))]
    lines = [f'当前 {len(cues)} 条 → 此版本 {len(previous)} 条字幕']
    for label, keys in fields:
        count = sum(any(getattr(a, key) != getattr(b, key) for key in keys) for a, b in zip(cues, previous))
        lines.append(f'{label}不同：{count} 条（按当前行号对照）')
    lines.append('生成、翻译、样式或编码设置：' + ('有变化' if current.get('options') != saved.get('options') else '相同'))
    differences = [(index, a, b) for index, (a, b) in enumerate(zip(cues, previous), 1) if a != b]
    if differences:
        lines.append('\n文字和时间差异（当前 → 所选版本，最多显示 12 条）：')
        for index, a, b in differences[:12]:
            lines.append(f'\n第 {index} 条：{a.start:.3f}–{a.end:.3f} → {b.start:.3f}–{b.end:.3f}')
            if a.text != b.text:
                lines.append(f'原文：{a.text[:200]}\n   → {b.text[:200]}')
            if a.translation != b.translation:
                lines.append(f'译文：{a.translation[:200]}\n   → {b.translation[:200]}')
            if a.reviewed != b.reviewed:
                lines.append('校对：' + ('已校对' if a.reviewed else '未校对') + ' → ' + ('已校对' if b.reviewed else '未校对'))
    lines.append('\n版本保存字幕、配置及编辑位置；原媒体和处理缓存仍引用原来的文件。')
    return '\n'.join(lines)


def restore_version(project, version):
    path = checked_version(project, version)
    content = path.read_text(encoding='utf-8-sig')
    create_version(project, '恢复历史版本前', force=True)
    atomic_write(project, content)
