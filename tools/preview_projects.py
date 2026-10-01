"""Render the project home without opening or saving a user project."""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from app.config import ROOT, PROJECTS
from app.gui import STYLE, initialize_fonts
from app.project_library import ProjectLibrary
from app.project_home import ProjectHome
from app.subtitles import Cue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--size', default='1440x940')
    parser.add_argument('--output', type=Path, default=ROOT / 'logs/project-home-preview.png')
    args = parser.parse_args()
    app = QApplication([])
    initialize_fonts(app)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    temporary = tempfile.TemporaryDirectory(prefix='yingzi-project-home-') if args.demo else None
    library = ProjectLibrary(Path(temporary.name) / 'projects' if temporary else PROJECTS)
    if args.demo:
        from app.media import run_ffmpeg
        for i, (name, color, count) in enumerate([('夏日回忆 · 第 2 期', '0x255E73', 120),
                                                  ('赛事集锦 · 字幕校对', '0x64742B', 86),
                                                  ('课程分享 · 中文字幕', '0x94663F', 42)]):
            media = Path(temporary.name) / f'演示视频{i}.mp4'
            run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s=640x360:r=24:d=1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', media])
            cues = [Cue(j * 2, j * 2 + 1.5, '字幕示例', '翻译示例' if i != 1 or j < 70 else '', 'English') for j in range(count)]
            path = library.create(name, media, cues, status='edited')
            modified = time.time() - i * 86400
            os.utime(path, (modified, modified))
        library.create('下一个故事')
    home = ProjectHome(library)
    width, height = map(int, args.size.split('x'))
    home.resize(width, height)
    home.show()
    home.refresh()
    if args.demo:
        home.cards.setCurrentRow(1)
    for _ in range(100):
        QTest.qWait(50)
        if home.thumbnails.current is None and not home.thumbnails.queue:
            break
    app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    home.grab().save(str(args.output))
    home.thumbnails.stop()
    home.close()
    if temporary:
        # The TemporaryDirectory owns this exact system-temp directory only.
        temporary.cleanup()
    print('Project home preview rendered')


if __name__ == '__main__':
    main()
