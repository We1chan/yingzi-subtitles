import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
import argparse
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl
from app.gui import Studio, STYLE, initialize_fonts
from app.config import ROOT

parser = argparse.ArgumentParser()
parser.add_argument('project', nargs='?')
parser.add_argument('--models', action='store_true')
parser.add_argument('--demo', action='store_true', help='Render a temporary sample project without touching user media')
parser.add_argument('--stage', choices=('generate', 'review', 'export'))
parser.add_argument('--size', default='1440x940')
parser.add_argument('--output', type=Path, default=ROOT / 'logs/ui-preview.png')
args = parser.parse_args()
try:
    width, height = map(int, args.size.lower().split('x'))
except ValueError:
    parser.error('--size must be WIDTHxHEIGHT')

app = QApplication([])
initialize_fonts(app)
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
window = Studio()
window.resize(width, height)
window.model_timer.stop()
window.show()
app.processEvents()
demo = None
if args.demo:
    from app.media import run_ffmpeg
    from app.subtitles import Cue, save_project
    demo = tempfile.TemporaryDirectory(prefix='yingzi-preview-', dir=ROOT / 'logs')
    video = Path(demo.name) / '界面演示.mp4'
    run_ffmpeg(['-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x242526:s=960x540:r=24:d=24',
                '-f', 'lavfi', '-i', 'sine=frequency=220:duration=24', '-c:v', 'libx264',
                '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', video])
    cues = [Cue(i * 4, i * 4 + 3.5, source, translated, 'English') for i, (source, translated) in enumerate([
        ('Every voice deserves to be heard.', '让每一句，都有字幕。'),
        ('Start with a video. We will handle the subtitles.', '导入视频，即可开始生成字幕。'),
        ('Click a line to find the right moment.', '点击字幕，定位对应画面。'),
        ('Make every word sound natural.', '逐句校对，让表达更自然。'),
        ('Choose the style that fits your story.', '选择适合视频的字幕样式。'),
        ('Your subtitles are ready to share.', '校对完成，导出你的作品。')])]
    project = Path(demo.name) / 'sample.yingzi.json'
    save_project(project, video, cues, {'language': 'English', 'segmentation_mode': 'smart'})
    window.load_saved_project(project)
    window.player.setPosition(1000)
    from PySide6.QtTest import QTest
    QTest.qWait(250)
    window.position_changed(1000)
    window.notify('界面演示 · 示例字幕，仅用于预览布局')
if args.project:
    window.load_saved_project(args.project)
if args.stage:
    window.go_to_step(('generate', 'review', 'export').index(args.stage))
app.processEvents()
args.output.parent.mkdir(parents=True, exist_ok=True)
window.grab().save(str(args.output))
if args.models:
    from app.model_dialog import ModelDialog
    dialog = ModelDialog(window.model_selection, window)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(ROOT / 'logs' / 'model-preview.png'))
    dialog.close()
print('UI preview rendered', flush=True)
# A preview must never rewrite an existing project when the window closes.
window.project = None
window.close()
window.player.setSource(QUrl())
app.processEvents()
if demo:
    if Path(demo.name).resolve().parent != (ROOT / 'logs').resolve():
        raise ValueError('Unexpected preview directory')
    demo.cleanup()
