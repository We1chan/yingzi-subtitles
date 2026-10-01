"""Exercise real playback and the GUI video-export worker; retain review artifacts."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.caption_style import CaptionStyle
from app.config import ROOT, PROJECTS
from app.gui import Studio, STYLE, initialize_fonts
from app.subtitles import load_project, save_project

parser = argparse.ArgumentParser()
parser.add_argument('project', nargs='?', default=str(PROJECTS / '验证_日中双语/subtitles.yingziproj'))
args = parser.parse_args()
data, cues = load_project(args.project)
out = PROJECTS / '验证_字幕视频导出'
out.mkdir(parents=True, exist_ok=True)
project = out / 'subtitles.yingziproj'
style = CaptionStyle(font_size=29, translation_size=33, background_opacity=40, padding=12)
save_project(project, data['video'], cues, {**data.get('options', {}), 'subtitle_style': style.to_dict()})
application = QApplication([])
initialize_fonts(application)
application.setStyle('Fusion')
application.setStyleSheet(STYLE)
window = Studio()
window.show()
window.load_saved_project(project)
window.audio.setVolume(0)
window.settings_tabs.setCurrentIndex(1)


def screenshot_and_export():
    try:
        window.player.pause()
        assert window.player.hasVideo(), window.player.errorString()
        assert window.preview.caption.toPlainText()
        window.grab().save(str(ROOT / 'logs/subtitle-style-preview.png'))
        window.resize(1080, 780)
        application.processEvents()
        window.grab().save(str(ROOT / 'logs/subtitle-style-compact.png'))
        window.start_video_export(out / '字幕合成示例.mp4', {'preset': 'fast', 'crf': 18})
        window.worker.finished.connect(finished)
    except Exception as exc:
        print(f'FAIL: {exc}', flush=True)
        application.exit(1)


def finished(*args):
    try:
        assert window.export_work is None
        assert window.progress_bar.value() == 100, window.status.text()
        assert (out / '字幕合成示例.mp4').stat().st_size > 1000
        print('PASS: video decoding, styled preview, GUI export, progress and cleanup', flush=True)
        print(window.status.text(), flush=True)
        window.close()
        application.exit(0)
    except Exception as exc:
        print(f'FAIL: {exc}', flush=True)
        application.exit(1)


def play():
    window.player.setPosition(round((cues[0].start + .2) * 1000))
    window.player.play()
    QTimer.singleShot(600, screenshot_and_export)


QTimer.singleShot(400, play)
QTimer.singleShot(120000, lambda: application.exit(2))
raise SystemExit(application.exec())
