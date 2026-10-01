"""The child-process protocol, without the editor window."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import time

import pytest
from PySide6.QtCore import QCoreApplication, QProcess

from app import job_runner
from app.job_runner import JobRunner


@pytest.fixture(scope='module')
def application():
    return QCoreApplication.instance() or QCoreApplication([])


def child(monkeypatch, script):
    """Run `script` instead of the real module; it receives the job path as sys.argv[1]."""
    class ScriptProcess(QProcess):
        def start(self):
            job = self.arguments()[-1]
            self.setArguments(['-u', '-c', script, job])
            super().start()
    monkeypatch.setattr(job_runner, 'QProcess', ScriptProcess)


def wait(runner, ended, seconds=15):
    deadline = time.monotonic() + seconds
    while (runner.busy() or not ended) and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.01)
    assert ended, 'The job did not finish.'


def test_events_split_across_writes_and_stderr_log_and_unexpected_exit(application, tmp_path, monkeypatch):
    child(monkeypatch, '''
import json, sys, time
line = json.dumps({'type': 'progress', 'percent': 5, 'message': '进度'})
print('library noise'); print('[1, 2]')
sys.stdout.write(line[:9]); sys.stdout.flush(); time.sleep(.2); print(line[9:])
sys.stderr.write('traceback text\\n')
sys.exit(3)
''')
    runner, events, ended = JobRunner(), [], []
    runner.event.connect(events.append)
    runner.ended.connect(ended.append)
    runner.start_processing({'video': 'v.mp4'}, tmp_path / 'work')
    wait(runner, ended)
    assert events == [{'type': 'progress', 'percent': 5, 'message': '进度'}]
    assert ended == [True]
    assert (tmp_path / 'work' / 'job.json').exists()
    assert (tmp_path / 'work' / 'run.log').read_text(encoding='utf-8').strip() == 'traceback text'


def test_terminal_event_and_cancel_are_not_unexpected(application, tmp_path, monkeypatch):
    child(monkeypatch, 'import json; print(json.dumps({"type": "done"}))')
    runner, ended = JobRunner(), []
    runner.ended.connect(ended.append)
    runner.start_processing({}, tmp_path)
    wait(runner, ended)
    child(monkeypatch, 'import time; time.sleep(60)')
    runner.start_processing({}, tmp_path)
    assert runner.busy() and runner.cancel() is False
    wait(runner, ended)
    assert ended == [False, False]


def test_export_cancel_file_and_temporary_work_cleanup(application, tmp_path, monkeypatch):
    child(monkeypatch, '''
import json, sys, time
from pathlib import Path
work = Path(sys.argv[1]).parent
(work / 'ffmpeg.log').write_text('encoder log', encoding='utf-8')
while not (work / 'cancel').exists():
    time.sleep(.02)
print(json.dumps({'type': 'export_cancelled'}))
''')
    runner, events, ended = JobRunner(), [], []
    runner.event.connect(events.append)
    runner.ended.connect(ended.append)
    target = tmp_path / 'out' / 'result.mp4'
    target.parent.mkdir()
    runner.start_export({'target': str(target)}, target, tmp_path / 'logs')
    work = runner.export_work.name
    assert runner.kind == 'export' and runner.cancel() is True
    wait(runner, ended)
    assert [event['type'] for event in events] == ['export_cancelled'] and ended == [False]
    assert runner.export_work is None and not os.path.exists(work)
    assert (tmp_path / 'logs' / 'video-export.log').read_text(encoding='utf-8') == 'encoder log'


def test_missing_interpreter_reports_failed_start(application, tmp_path, monkeypatch):
    class MissingProcess(QProcess):
        def start(self):
            self.setProgram(str(tmp_path / 'missing-python.exe'))
            super().start()
    monkeypatch.setattr(job_runner, 'QProcess', MissingProcess)
    runner, failed = JobRunner(), []
    runner.failedToStart.connect(lambda: failed.append(True))
    runner.start_processing({}, tmp_path)
    deadline = time.monotonic() + 5
    while not failed and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.01)
    assert failed and not runner.busy() and runner.log_handle is None
