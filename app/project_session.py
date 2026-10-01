"""The open project: its file, media and processing workspace, and when the editor may write it."""
import uuid
from pathlib import Path

from .config import OUTPUT
from .subtitles import load_project, project_workspace, save_project

# Unfinished processing keeps its status so "继续处理" can resume from the cache.
RESUMABLE = ('recognizing', 'aligning', 'draft')


class ProjectSession:
    def __init__(self, library):
        self.library = library
        self.project = None
        self.video = None
        self.output_dir = OUTPUT
        self.status = 'complete'
        self.loading = False
        self.closed = False
        # While a worker runs it owns the project file and writes its own checkpoints.
        self.worker_owns = lambda: False

    def can_save(self):
        return bool(self.project) and not self.loading and not self.closed and not self.worker_owns()

    def save(self, cues, options, editor):
        if not self.can_save():
            return False
        status = self.status if self.status in RESUMABLE else 'edited'
        save_project(self.project, self.video, cues, options, status=status, workspace=self.output_dir, editor=editor)
        return True

    def is_open(self, path):
        return self.project is not None and self.project.resolve() == Path(path).resolve()

    def create(self, options):
        self.project = self.library.create(self.video.stem[:100], self.video, options=options)
        self.output_dir = project_workspace(self.project)

    def clone(self, name):
        self.project = self.library.clone(self.project, name)
        self.output_dir = project_workspace(self.project)
        return self.project

    def opened(self, path, data):
        self.project = Path(path).resolve()
        self.status = data.get('status', 'complete')
        self.output_dir = project_workspace(self.project, data)

    def fresh_workspace(self):
        """A fresh generation gets a fresh cache, while retaining the chosen project file."""
        if (self.output_dir / '.pipeline').exists():
            self.output_dir = self.project.parent / 'work' / f'run_{uuid.uuid4().hex[:8]}'
        return self.output_dir

    def checkpoint(self, path, status):
        """Reload what the worker saved and return its cues."""
        self.project = Path(path)
        self.status = status
        return load_project(self.project)[1]

    def recover(self):
        """After a failed or cancelled job, return the cues of its last checkpoint, if any."""
        candidate = self.project or self.output_dir / 'subtitles.yingziproj'
        if not candidate.exists():
            return None
        try:
            data, cues = load_project(candidate)
        except Exception:
            return None
        self.project = candidate
        self.status = data.get('status', 'complete')
        return cues
