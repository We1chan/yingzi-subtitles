import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaPlayer
from app.config import ROOT
from app.gui import Studio, STYLE, initialize_fonts, make_icon
from app.subtitles import load_project, save_project

application = QApplication([])
initialize_fonts(application)
application.setStyle('Fusion')
application.setStyleSheet(STYLE)
window = Studio()
window.show()
source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'output/验证_日中双语/subtitles.yingzi.json'
data, cues = load_project(source)
cues[0].translation = ''
project = ROOT / f'output/验证_界面翻译_{cues[0].language}/subtitles.yingzi.json'
save_project(project, data['video'], cues, data.get('options'), status='translating')
window.load_saved_project(project)
window.audio.setVolume(0)
window.retry_translation()
deadline = time.monotonic() + 180
timer = QTimer()


def check():
    if time.monotonic() > deadline:
        window.cancel_job()
        print('FAIL: GUI workflow timed out', flush=True)
        application.exit(1)
    elif not window.busy():
        timer.stop()
        try:
            assert window.cues[0].translation, window.status.text()
            assert window.export_button.isEnabled()
            window.player.play()
            QTimer.singleShot(2500, finish)
        except Exception as exc:
            print(f'FAIL: {exc}', flush=True)
            window.close()
            application.exit(1)


def finish():
    try:
        assert window.player.error() == QMediaPlayer.NoError, window.player.errorString()
        assert window.player.position() > 500
        assert window.player.hasVideo()
        window.player.pause()
        assert window.preview.caption.toPlainText()
        window.grab().save(str(ROOT / 'logs/ui-preview.png'))
        make_icon().pixmap(128, 128).save(str(ROOT / 'runtime/yingzi.ico'))
        print('PASS: GUI QProcess translation, progress, recovery, subtitle preview and video decoding', flush=True)
        print(window.status.text(), flush=True)
        window.close()
        application.exit(0)
    except Exception as exc:
        print(f'FAIL: {exc}', flush=True)
        window.close()
        application.exit(1)


timer.timeout.connect(check)
timer.start(250)
raise SystemExit(application.exec())
