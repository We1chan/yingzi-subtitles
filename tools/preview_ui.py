import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
import argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from app.gui import Studio, STYLE, initialize_fonts
from app.config import ROOT

app = QApplication([])
initialize_fonts(app)
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
window = Studio()
window.show()
app.processEvents()
parser = argparse.ArgumentParser()
parser.add_argument('project', nargs='?')
parser.add_argument('--models', action='store_true')
args = parser.parse_args()
if args.project:
    window.load_saved_project(args.project)
    app.processEvents()
window.grab().save(str(ROOT / 'logs' / 'ui-preview.png'))
if args.models:
    from app.model_dialog import ModelDialog
    dialog = ModelDialog(window.model_selection, window)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(ROOT / 'logs' / 'model-preview.png'))
    dialog.close()
print('UI preview rendered', flush=True)
window.close()
