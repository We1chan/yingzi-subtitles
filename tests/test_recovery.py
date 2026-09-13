import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication
from app.gui import Studio, initialize_fonts
from app.subtitles import Cue, save_project, load_project


def test_incomplete_alignment_cannot_be_promoted_to_complete_translation(tmp_path):
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    project = tmp_path / 'partial.yingzi.json'
    save_project(project, str(tmp_path / 'video.mp4'), [Cue(1, 3, 'まだ途中です。', language='Japanese')], status='aligning')
    window = Studio()
    window.load_saved_project(project)
    assert not window.retry.isEnabled()
    window.save_edits()
    data, _ = load_project(project)
    assert data['status'] == 'aligning'
    window.close()
