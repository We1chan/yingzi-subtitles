import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from app.gui import initialize_fonts, make_icon
from app.config import RUNTIME

app = QApplication([])
initialize_fonts(app)
if not make_icon().pixmap(128, 128).save(str(RUNTIME / 'yingzi.ico')):
    raise RuntimeError('Unable to create application icon')
