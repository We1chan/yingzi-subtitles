"""Managed project folders and reversible library operations."""
import json
import hashlib
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .subtitles import atomic_write, load_project, save_project, project_workspace, needs_translation, relative_path


def project_name(value):
    value = str(value).strip()
    if not value or len(value) > 100 or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith('.'):
        raise ValueError('工程名称需为 1～100 个字符，不能包含 \\ / : * ? " < > | 或以句点结尾。')
    if value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        raise ValueError('此名称是 Windows 保留名称，请使用其他工程名称。')
    return value


@dataclass
class ProjectInfo:
    path: Path
    name: str
    video: Path | None
    modified: float
    cue_count: int = 0
    pending: int = 0
    status: str = 'draft'
    error: str = ''

    @property
    def media_missing(self):
        return self.video is not None and not self.video.is_file()

    @property
    def cover(self):
        return self.path.parent / 'cover.jpg'

    @property
    def label(self):
        if self.error:
            return '无法读取'
        if self.video is None:
            return '待导入媒体'
        if self.media_missing:
            return '原媒体缺失'
        if self.status in ('recognizing', 'aligning'):
            return '待继续处理'
        if not self.cue_count:
            return '待生成字幕'
        if self.pending:
            return f'{self.pending} 条待翻译'
        return '可以继续编辑'


class ProjectLibrary:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def contains(self, path):
        path = Path(path).resolve()
        return path.is_relative_to(self.root) and not any(part.startswith('.') for part in path.relative_to(self.root).parts)

    def folder(self, path):
        path = Path(path).resolve()
        if not self.contains(path) or path.parent == self.root:
            raise ValueError('此工程不在工程库的独立文件夹中。')
        return self.root / path.relative_to(self.root).parts[0]

    def metadata(self, path):
        try:
            data = json.loads((Path(path).parent / '.project-meta.json').read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def update_metadata(self, path, **values):
        data = {**self.metadata(path), **values}
        atomic_write(Path(path).parent / '.project-meta.json', json.dumps(data, ensure_ascii=False, indent=2))

    def info(self, path):
        path = Path(path).resolve()
        modified = path.stat().st_mtime
        name = self.metadata(path).get('name') or path.stem
        try:
            data, cues = load_project(path)
            video = Path(data['video']) if data['video'] else None
            if name == 'subtitles':
                name = video.stem if video else path.parent.name
            return ProjectInfo(path, str(name), video, modified, len(cues),
                               sum(needs_translation(cue) for cue in cues), data.get('status', 'edited'))
        except (OSError, ValueError, TypeError) as exc:
            return ProjectInfo(path, str(name), None, modified, error=str(exc))

    def scan(self, trash=False):
        root = self.root / '.trash' if trash else self.root
        if not root.is_dir():
            return []
        entries = []
        for path in root.rglob('*.yingziproj'):
            if any(part.startswith('.') for part in path.relative_to(root).parts) or path.is_symlink():
                continue
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            try:
                entries.append(self.info(path))
            except OSError:
                continue
        return sorted(entries, key=lambda info: info.modified, reverse=True)

    def create(self, name, video=None, cues=None, options=None, status='draft', editor=None, workspace=None):
        name = project_name(name)
        folder = self.root / f'{name[:55]}_{uuid.uuid4().hex[:8]}'
        folder.mkdir(parents=True)
        path = folder / f'{name}.yingziproj'
        try:
            save_project(path, video, cues or [], options, status=status, workspace=workspace or folder / 'work', editor=editor)
            self.update_metadata(path, name=name, created_at=time.time())
        except Exception:
            # This folder was just allocated by this operation; retain it out of the list.
            self._trash_folder(folder)
            raise
        return path

    def clone(self, source, name=None):
        source = Path(source).resolve()
        data, cues = load_project(source)
        path = self.create(name or self.info(source).name, data['video'] or None, cues, data.get('options'),
                           data.get('status', 'edited'), data.get('editor'))
        workspace = project_workspace(source, data)
        target = project_workspace(path)
        try:
            target.mkdir(parents=True, exist_ok=True)
            for filename in ('.pipeline', 'recognition.json', 'original.srt', 'translated.srt', 'bilingual.srt',
                             'bilingual.vtt', 'run.log', 'api-diagnostics.jsonl'):
                item = workspace / filename
                if item.is_symlink():
                    raise ValueError('缓存含符号链接，无法复制工程。')
                if item.is_dir():
                    if any(child.is_symlink() for child in item.rglob('*')):
                        raise ValueError('缓存含符号链接，无法复制工程。')
                    shutil.copytree(item, target / filename)
                elif item.is_file():
                    shutil.copy2(item, target / filename)
            if (source.parent / 'cover.jpg').is_file():
                shutil.copy2(source.parent / 'cover.jpg', path.parent / 'cover.jpg')
        except Exception:
            self._trash_folder(path.parent)
            raise
        return path

    def adopt(self, source):
        source = Path(source).resolve()
        if self.contains(source):
            load_project(source)
            return source
        load_project(source)
        fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
        for entry in self.scan():
            metadata = self.metadata(entry.path)
            if not entry.error and metadata.get('imported_from') == str(source) and metadata.get('imported_hash') == fingerprint:
                return entry.path
        path = self.clone(source)
        try:
            self.update_metadata(path, imported_from=str(source), imported_hash=fingerprint)
        except Exception:
            self._trash_folder(path.parent)
            raise
        return path

    def rename(self, path, name):
        path = Path(path).resolve()
        self.folder(path)
        name = project_name(name)
        target = path.with_name(f'{name}.yingziproj')
        if target != path and target.exists():
            raise ValueError('此名称的工程已存在。')
        from .project_versions import version_folder
        history, new_history = version_folder(path), version_folder(target)
        if target != path and history.exists() and new_history.exists():
            raise ValueError('此名称的历史版本目录已存在，请使用其他名称。')
        if target != path:
            path.rename(target)
        moved_history = False
        try:
            if target != path and history.exists():
                history.rename(new_history)
                moved_history = True
            self.update_metadata(target, name=name)
        except Exception:
            if moved_history:
                new_history.rename(history)
            if target != path:
                target.rename(path)
            raise
        return target

    def _trash_folder(self, folder):
        folder = Path(folder).resolve()
        if folder.parent != self.root or folder.is_symlink():
            raise ValueError('不能移除工程库以外的文件夹。')
        target = self.root / '.trash' / f'{folder.name}_{uuid.uuid4().hex[:8]}'
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.resolve().is_relative_to(self.root):
            raise ValueError('回收站路径无效。')
        folder.rename(target)
        try:
            self.rebase_folder(target, folder, target)
        except Exception:
            self.rebase_folder(target, target, folder)
            target.rename(folder)
            raise
        return target

    def trash(self, path):
        folder = self.folder(path)
        if len([file for file in folder.rglob('*.yingziproj') if '.history' not in file.relative_to(folder).parts]) != 1:
            raise ValueError('此文件夹包含多个工程，请先将工程分别导入工程库。')
        for entry in self.scan():
            if entry.path != Path(path).resolve() and not entry.error and (project_workspace(entry.path).resolve().is_relative_to(folder)
                    or entry.video is not None and entry.video.resolve().is_relative_to(folder)):
                raise ValueError('另一个工程仍在使用此文件夹内的媒体或缓存，暂不能移除。')
        return self._trash_folder(folder)

    def restore(self, path):
        path = Path(path).resolve()
        trash = (self.root / '.trash').resolve()
        if trash.parent != self.root or not path.is_relative_to(trash):
            raise ValueError('请选择回收站中的工程。')
        folder = trash / path.relative_to(trash).parts[0]
        target = self.root / f'{folder.name.rsplit("_", 1)[0]}_{uuid.uuid4().hex[:8]}'
        relative = path.relative_to(folder)
        if folder.resolve().parent != trash or target.resolve().parent != self.root:
            raise ValueError('工程路径无效，不能恢复。')
        folder.rename(target)
        try:
            self.rebase_folder(target, folder, target)
        except Exception:
            self.rebase_folder(target, target, folder)
            target.rename(folder)
            raise
        return target / relative

    @staticmethod
    def rebase_folder(folder, old, new):
        """Only location fields change; subtitles, style and encoding choices stay intact."""
        old, new = Path(old).resolve(), Path(new).resolve()
        def relocate(value):
            if not isinstance(value, str) or not value:
                return value
            path = Path(value)
            if path.is_absolute() and path.is_relative_to(old):
                return str(new / path.relative_to(old))
            return value
        for path in Path(folder).rglob('*.yingziproj'):
            stat = path.stat()
            try:
                data = json.loads(path.read_text(encoding='utf-8-sig'))
            except (ValueError, UnicodeError):
                continue
            if not isinstance(data, dict):
                continue
            before = json.dumps(data, ensure_ascii=False, indent=2)
            for key in ('video', 'workspace'):
                if key in data:
                    data[key] = relocate(data[key])
            for section, keys in (('options', ('video', 'output', 'project_path', 'resume_project')),
                                  ('editor', ('last_export_target',))):
                if not isinstance(data.get(section, {}), dict):
                    continue
                for key in keys:
                    if key in data.get(section, {}):
                        data[section][key] = relocate(data[section][key])
            for key in ('video', 'workspace'):
                if isinstance(data.get(key), str) and data[key]:
                    # A snapshot will be restored into the original project folder, not its history subfolder.
                    base = path.parent.parent.parent if path.parent.parent.name == '.history' else path.parent
                    data[key + '_relative'] = relative_path(data[key], base)
            after = json.dumps(data, ensure_ascii=False, indent=2)
            if after != before:
                atomic_write(path, after)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        for state in Path(folder).rglob('state.json'):
            if state.parent.name == '.pipeline':
                try:
                    data = json.loads(state.read_text(encoding='utf-8'))
                except (ValueError, UnicodeError):
                    continue
                if isinstance(data, dict) and isinstance(data.get('fingerprint'), dict) and 'video' in data['fingerprint']:
                    old_video = data['fingerprint']['video']
                    data['fingerprint']['video'] = relocate(old_video)
                    if data['fingerprint']['video'] != old_video:
                        atomic_write(state, json.dumps(data, ensure_ascii=False))

    def organize(self, output):
        output = Path(output).resolve()
        if output == self.root or output.is_relative_to(self.root) or self.root.is_relative_to(output):
            raise ValueError('旧输出目录与工程库目录不能互相包含。')
        plan = []
        snapshots = {}
        if not output.is_dir():
            return []
        for folder in sorted(output.iterdir()):
            if not folder.is_dir() or folder.is_symlink():
                continue
            files = list(folder.rglob('*.yingziproj'))
            if not files:
                continue
            if any(item.is_symlink() or item.is_junction() for item in folder.rglob('*')):
                raise ValueError('旧工程包含链接，不能收纳。')
            if folder.resolve().parent != output or any(not file.resolve().is_relative_to(folder.resolve()) for file in files):
                raise ValueError('旧工程路径超出输出目录，不能收纳。')
            for path in files:
                load_project(path)
            for state in folder.rglob('state.json'):
                if state.parent.name == '.pipeline':
                    data = json.loads(state.read_text(encoding='utf-8'))
                    if not isinstance(data, dict):
                        raise ValueError('工程缓存无效，不能收纳。')
            target = self.root / folder.name
            if target.exists():
                raise ValueError(f'工程文件夹已存在，不能覆盖：{target.name}')
            plan.append((folder, target))
            tracked = set(files) | {path.parent / '.project-meta.json' for path in files}
            tracked.update(state for state in folder.rglob('state.json') if state.parent.name == '.pipeline')
            snapshots[folder] = {path: (path.read_bytes(), path.stat()) if path.exists() else None for path in tracked}
        self.root.mkdir(parents=True, exist_ok=True)
        moved = []
        try:
            for old, new in plan:
                if old.resolve().parent != output or new.resolve().parent != self.root:
                    raise ValueError('工程路径已改变，不能收纳。')
                old.rename(new)
                moved.append((old, new))
                self.rebase_folder(new, old, new)
            # Other projects can reference a media file inside a different moved folder.
            for _, folder in moved:
                for old, new in moved:
                    if folder != new:
                        self.rebase_folder(folder, old, new)
                for path in folder.rglob('*.yingziproj'):
                    if '.history' in path.relative_to(folder).parts:
                        continue
                    data, _ = load_project(path)
                    video = Path(data['video']) if data['video'] else None
                    self.update_metadata(path, name=(video.stem if path.stem == 'subtitles' and video else path.stem))
        except Exception:
            for old, new in reversed(moved):
                if new.exists() and not old.exists():
                    new.rename(old)
                for path, original in snapshots[old].items():
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        content, stat = original
                        temporary = path.with_name(path.name + '.tmp')
                        temporary.write_bytes(content)
                        temporary.replace(path)
                        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            raise
        return [path for _, folder in moved for path in folder.rglob('*.yingziproj') if '.history' not in path.relative_to(folder).parts]
