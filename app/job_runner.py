"""Child-process jobs: JSON-line events on stdout, stderr appended to a log, cancellation of the whole tree.

The runner owns the process and its files; the window decides what each event means.
"""
import json
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .config import ROOT
from .media import CREATE_NO_WINDOW
from .subtitles import atomic_write

TERMINAL_EVENTS = {'done', 'error', 'export_done', 'export_cancelled'}


class JobRunner(QObject):
    event = Signal(object)
    # True when the child exited without reporting a final state (crash, kill or protocol break).
    ended = Signal(bool)
    failedToStart = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.kind = 'recognition'
        self.export_work = None
        self.export_parent = None
        self.log_path = None
        self.log_handle = None
        self.buffer = b''
        self.terminal_event = False

    def busy(self):
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def start_processing(self, job, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        job_path = output_dir / 'job.json'
        atomic_write(job_path, json.dumps(job, ensure_ascii=False, indent=2))
        self.start('recognition', 'app.worker', job_path, output_dir / 'run.log')

    def start_export(self, job, target, log_folder):
        # Work lives beside the target so the encoder's final rename stays on one volume.
        self.export_parent = target.parent
        self.export_work = tempfile.TemporaryDirectory(prefix='.yingzi-export-', dir=target.parent)
        try:
            job_path = Path(self.export_work.name) / 'job.json'
            atomic_write(job_path, json.dumps(job, ensure_ascii=False, indent=2))
            log_folder.mkdir(parents=True, exist_ok=True)
            self.start('export', 'app.video_export', job_path, log_folder / 'video-export.log')
        except Exception:
            self.cleanup_export()
            raise

    def start(self, kind, module, job_path, log_path):
        self.log_path = log_path
        self.log_handle = log_path.open('ab')
        self.kind = kind
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.setProgram(str(ROOT / '.venv' / 'Scripts' / 'python.exe'))
        self.process.setArguments(['-u', '-m', module, '--job', str(job_path)])
        self.buffer = b''
        self.terminal_event = False
        self.process.readyReadStandardOutput.connect(self.read_events)
        self.process.readyReadStandardError.connect(self.read_errors)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start()

    def read_events(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') in TERMINAL_EVENTS:
                self.terminal_event = True
            self.event.emit(event)

    def read_errors(self):
        content = bytes(self.process.readAllStandardError())
        if self.log_handle:
            self.log_handle.write(content)
            self.log_handle.flush()

    def close_log(self):
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None

    def finished(self, *args):
        self.read_events()
        self.read_errors()
        self.close_log()
        try:
            if self.kind == 'export':
                self.cleanup_export()
        finally:
            self.ended.emit(not self.terminal_event)

    def process_error(self, error):
        # QProcess does not emit finished() when the program never started.
        if error == QProcess.FailedToStart:
            self.terminal_event = True
            self.close_log()
            if self.kind == 'export':
                self.cleanup_export()
            self.failedToStart.emit()

    def cancel(self):
        """Stop the running job. Returns True when an export was asked to stop and will report back."""
        if self.kind == 'export' and self.export_work:
            (Path(self.export_work.name) / 'cancel').touch()
            return True
        self.terminal_event = True
        # Only terminate this application-owned worker and its child model process.
        if self.process.processId():
            subprocess.run(['taskkill', '/PID', str(self.process.processId()), '/T', '/F'], capture_output=True, creationflags=CREATE_NO_WINDOW)
        else:
            self.process.kill()
        return False

    def wait(self, milliseconds):
        return self.process.waitForFinished(milliseconds)

    def cleanup_export(self):
        self.close_log()
        if self.export_work:
            work = Path(self.export_work.name).resolve()
            if work.parent != self.export_parent or not work.name.startswith('.yingzi-export-'):
                raise ValueError('导出临时目录与预期位置不一致。')
            log = work / 'ffmpeg.log'
            if log.exists() and self.log_path:
                with self.log_path.open('ab') as saved:
                    saved.write(log.read_bytes())
            self.export_work.cleanup()
            self.export_work = None
