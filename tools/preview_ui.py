import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
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
if len(sys.argv) > 1:
    window.load_saved_project(sys.argv[1])
    app.processEvents()
window.grab().save(str(ROOT / 'logs' / 'ui-preview.png'))
print('UI preview rendered', flush=True)
window.close()
