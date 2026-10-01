"""Shared desktop theme. Orange denotes the current task and primary actions."""
from pathlib import Path


STYLE = '''
QWidget { color: #302D2B; font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif; font-size: 13px; }
QMainWindow, QDialog, #workspace { background: #F5F4F2; }
QLabel { background: transparent; }
#appHeader { background: white; border-bottom: 1px solid #E8E5E1; }
#brand { font-size: 23px; font-weight: 700; color: #24211F; }
#projectHome { background: #F5F4F2; }
#projectSidebar { background: white; border-right: 1px solid #E8E5E1; }
#homeTitle { font-size: 30px; font-weight: 700; color: #24211F; }
#projectCards { background: transparent; border: none; outline: none; }
#brandMark { background: #FF5522; color: #39170D; border-radius: 9px; font-size: 23px; font-weight: 700; }
#eyebrow { color: #9B8D83; font-size: 10px; font-weight: 600; }
#fileName { font-size: 13px; font-weight: 600; }
#muted { color: #817A74; font-size: 12px; }
#section { font-size: 15px; font-weight: 600; }
#panelTitle { font-size: 19px; font-weight: 700; }
#badge { color: #9F3918; background: #FFF0E9; padding: 5px 10px; border-radius: 6px; font-size: 11px; }
#localBadge { color: #3E6A48; background: #EEF5EF; padding: 5px 10px; border-radius: 6px; font-size: 11px; }
#onlineBadge { color: #8A4B0F; background: #FFF1DC; padding: 5px 10px; border-radius: 6px; font-size: 11px; }
#startHint { color: #8A6A58; font-size: 12px; }
#card, #inspector { background: white; border: 1px solid #E6E2DD; border-radius: 10px; }
#panelBody { background: white; }
#panelFooter { background: #FFFAF7; border-top: 1px solid #F0E7DF; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }
#notice { color: #73564A; background: #FFF4EE; border-radius: 7px; padding: 10px; font-size: 12px; }
#dropZone { background: #242526; border-radius: 8px; }
#dropTitle { color: #FAF7F4; font-size: 25px; font-weight: 600; }
#dropHint { color: #A9AAA9; font-size: 12px; }
#dropSymbol { color: #FF8259; font-size: 38px; }
#emptyTitle { font-size: 17px; color: #675F59; font-weight: 600; }
#taskBar { background: white; border: 1px solid #E6E2DD; border-radius: 8px; }
#taskText { color: #57514C; font-size: 12px; }
QPushButton, QToolButton { background: white; border: 1px solid #DFDAD5; border-radius: 6px; padding: 7px 12px; font-weight: 500; }
QPushButton:hover, QToolButton:hover { background: #FFF4EE; border-color: #FFB196; }
QPushButton:pressed, QToolButton:pressed { background: #FFE1D3; }
QPushButton:focus, QToolButton:focus { border-color: #FF5522; }
QPushButton:disabled, QToolButton:disabled { color: #B3ABA5; background: #F6F3F0; border-color: #EBE6E1; }
#primary { color: #39170D; background: #FF5522; border: 1px solid #FF5522; font-weight: 600; padding: 10px 16px; }
#primary:hover { color: white; background: #CC3C12; border-color: #CC3C12; }
#primary:pressed { color: white; background: #AE300D; border-color: #AE300D; }
#primary:disabled { color: #AE8776; background: #F7E3D8; border-color: #F7E3D8; }
#secondary { background: #FFF0E8; border-color: #FFD3BF; color: #A33917; font-weight: 600; }
#secondary:disabled { background: #F6F3F0; border-color: #EBE6E1; color: #B3ABA5; }
#quiet { background: transparent; border: 1px solid transparent; color: #756A62; padding: 6px 8px; }
#quiet:hover { color: #AC3B16; background: #FFF0E8; }
#quiet:disabled { color: #BEB5AE; background: transparent; border-color: transparent; }
#dropLink { background: transparent; color: #D4C8BF; border: none; }
#dropLink:hover { color: #FFAB8D; }
#step { color: #8C8178; background: transparent; border: 1px solid transparent; padding: 8px 18px; text-align: left; }
#step:checked { color: #A33713; background: #FFE8DC; border-color: #FFD5BF; font-weight: 600; }
#step:hover { background: #FFEEE4; }
#danger { color: #B34840; background: #FFF7F5; border-color: #EACCC7; }
#disclosure { text-align: left; background: #F7F5F2; border-color: #EAE4DE; color: #62554B; padding: 9px 10px; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #FCFBFA; border: 1px solid #DFDAD4; border-radius: 6px; padding: 7px 9px; min-height: 18px; selection-background-color: #FFDFD2; selection-color: #662A17; }
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus { border-color: #FF5522; }
QComboBox:disabled, QLineEdit:disabled { color: #B0A69E; background: #F5F2EF; }
QComboBox { padding-right: 4px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; border: none; width: 28px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #FFE4D7; selection-color: #662A17; border: 1px solid #E8DAD1; }
QProgressBar { background: #F3EAE3; border: none; border-radius: 2px; height: 4px; max-height: 4px; }
QProgressBar::chunk { background: #FF5522; border-radius: 2px; }
QTableWidget { background: white; alternate-background-color: #FDFBF9; border: none; outline: 0; gridline-color: #F0ECE8; selection-background-color: #FFE7DC; selection-color: #592B1B; }
QTableWidget::item { padding: 7px; border-bottom: 1px solid #F0ECE8; }
QHeaderView::section { background: #F6F4F1; border: none; border-bottom: 1px solid #E9E3DD; color: #82776E; font-size: 11px; padding: 6px 5px; }
QScrollArea { border: none; background: white; }
QScrollBar:vertical { background: #F5F2EF; width: 7px; border: none; }
QScrollBar::handle:vertical { background: #D5C8BE; border-radius: 3px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #F5F2EF; height: 7px; border: none; }
QScrollBar::handle:horizontal { background: #D5C8BE; border-radius: 3px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QSlider::groove:horizontal { background: #E6DFD8; height: 3px; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #FF5522; }
QSlider::handle:horizontal { background: #FF5522; width: 12px; margin: -5px 0; border-radius: 6px; }
QSplitter::handle { background: #EAE5DF; }
QSplitter::handle:hover { background: #FFB69A; }
QTabWidget::pane { background: white; border: none; border-top: 1px solid #ECE6E0; }
QTabBar::tab { background: transparent; color: #857970; padding: 13px 15px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #AD3914; border-bottom: 2px solid #FF5522; font-weight: 600; }
QTabBar::tab:hover { background: #FFF5EF; }
QCheckBox { spacing: 7px; }
QPlainTextEdit { background: #272626; color: #E8DED6; border: none; font-family: Consolas; font-size: 12px; selection-background-color: #FFDFD2; selection-color: #662A17; }
QPlainTextEdit#versionDetails { background: white; color: #473B33; border: 1px solid #E5DCD4; border-radius: 6px; padding: 8px; font-family: "Microsoft YaHei"; font-size: 13px; }
QListWidget#versionList { background: white; border: 1px solid #E5DCD4; border-radius: 6px; outline: 0; }
QListWidget#versionList::item { padding: 6px; border-bottom: 1px solid #F0ECE8; }
QListWidget#versionList::item:selected { background: #FFE7DC; color: #592B1B; }
QMenu { background: white; border: 1px solid #E5DCD4; padding: 5px; }
QMenu::item { padding: 8px 24px; border-radius: 4px; }
QMenu::item:selected { background: #FFE8DC; color: #9F3918; }
QToolTip { color: #473B33; background: #FFF8F2; border: 1px solid #E6D8CC; padding: 5px; }
'''
ASSETS = (Path(__file__).parent / 'assets').as_posix()
STYLE += '\nQComboBox::down-arrow { width: 12px; height: 8px; image: url("' + ASSETS + '/chevron-down.svg"); }\n'
# The global box styling removes Fusion's native spin buttons, so draw them explicitly.
STYLE += '''
QSpinBox, QDoubleSpinBox { padding-right: 28px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; width: 24px; border: none; border-left: 1px solid #ECE6E0; background: transparent; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #FFF0E8; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { width: 10px; height: 7px; image: url("''' + ASSETS + '''/chevron-up.svg"); }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { width: 10px; height: 7px; image: url("''' + ASSETS + '''/chevron-down.svg"); }
'''
