"""Validate and rename legacy projects without rewriting their saved contents."""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .subtitles import _load_project_data, load_project


LEGACY_SUFFIX = '.yingzi.json'


@dataclass(frozen=True)
class ProjectMigration:
    source: Path
    target: Path
    sha256: str
    cue_count: int


def plan_migrations(path):
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError('请选择实际工程文件或文件夹，不能迁移符号链接。')
    if path.is_dir():
        root = path.resolve()
        candidates = sorted(item for item in root.rglob('*.json') if item.name.lower().endswith(LEGACY_SUFFIX))
    elif path.is_file() and path.name.lower().endswith(LEGACY_SUFFIX):
        root, candidates = path.parent.resolve(), [path]
    else:
        raise ValueError('请选择旧 .yingzi.json 工程文件或包含旧工程的文件夹。')
    result = []
    for candidate in candidates:
        source = candidate.resolve()
        target = source.with_name(source.name[:-len(LEGACY_SUFFIX)] + '.yingziproj')
        if candidate.is_symlink() or not source.is_relative_to(root) or not target.is_relative_to(root):
            raise ValueError('工程路径超出所选文件夹，已停止迁移。')
        if target.exists():
            raise ValueError(f'新工程已存在，不能覆盖：{target}')
        original = source.read_bytes()
        _, cues = _load_project_data(source)
        result.append(ProjectMigration(source, target, hashlib.sha256(original).hexdigest(), len(cues)))
    return result


def migrate_projects(path, *, dry_run=False):
    plan = plan_migrations(path)
    if dry_run:
        return plan
    for item in plan:
        # Check the exact sibling paths again immediately before each rename.
        if (item.source.resolve() != item.source or item.target.resolve() != item.target
                or item.source.parent != item.target.parent or item.source.is_symlink()):
            raise ValueError('工程路径已改变，已停止迁移。')
        if hashlib.sha256(item.source.read_bytes()).hexdigest() != item.sha256:
            raise ValueError(f'工程内容在检查后发生变化，已停止迁移：{item.source}')
        if item.target.exists():
            raise ValueError(f'新工程已存在，不能覆盖：{item.target}')
        item.source.rename(item.target)
        try:
            if hashlib.sha256(item.target.read_bytes()).hexdigest() != item.sha256:
                raise ValueError('工程迁移后的文件内容不一致。')
            load_project(item.target)
        except Exception:
            if not item.source.exists():
                item.target.rename(item.source)
            raise
    return plan
