import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QComboBox

from app.gui import Studio, STYLE, initialize_fonts
from app.widgets import IntegerChoice, SpinBox


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    return app


def wheel(widget, pixels=False):
    position = widget.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(widget.mapToGlobal(position)),
        QPoint(0, -60) if pixels else QPoint(), QPoint() if pixels else QPoint(0, -120),
        Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate if pixels else Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize('pixels', [False, True])
def test_focused_settings_scroll_page_without_changing_selection(application, pixels):
    window = Studio()
    window.resize(1080, 780)
    window.show()
    application.processEvents()
    window.language.setCurrentIndex(1)
    window.language.setFocus()
    area = window.language.parentWidget()
    while not isinstance(area, QAbstractScrollArea):
        area = area.parentWidget()
    bar = area.verticalScrollBar()
    bar.setValue(0)
    wheel(window.language, pixels)
    assert window.language.currentIndex() == 1
    assert bar.value() > 0
    assert isinstance(window.max_chars, QComboBox)
    window.max_chars.setFocus()
    bar.setValue(0)
    wheel(window.max_chars, pixels)
    assert window.max_chars.currentText() == '智能分割' and bar.value() > 0
    window.max_chars.setValue(67)
    window.max_chars.setFocus()
    bar.setValue(0)
    wheel(window.max_chars, pixels)
    assert window.max_chars.value() == 67 and bar.value() > 0
    window.close()


def test_style_numbers_and_font_scroll_without_changes(application):
    window = Studio()
    window.resize(1080, 780)
    window.show()
    window.settings_tabs.setCurrentIndex(1)
    application.processEvents()
    panel = window.style_panel
    number = panel.controls['font_size']
    for control in (number, panel.font):
        control.setFocus()
        before = window.preview.caption_style.to_dict()
        panel.verticalScrollBar().setValue(0)
        wheel(control)
        assert panel.verticalScrollBar().value() > 0
        assert window.preview.caption_style.to_dict() == before
    window.close()
    # A dialog control without a scrollable page must also keep its value.
    spin = SpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    spin.show()
    spin.setFocus()
    wheel(spin)
    assert spin.value() == 50
    spin.close()


def test_dropdown_list_scrolls_and_choice_requires_click(application):
    combo = IntegerChoice(16, 100, ' 字符/条')
    combo.setValue(30)
    combo.resize(180, 36)
    combo.show()
    combo.showPopup()
    application.processEvents()
    bar = combo.view().verticalScrollBar()
    bar.setValue(0)
    previous = bar.value()
    wheel(combo.view().viewport())
    assert combo.value() == 30 and bar.value() > previous
    index = combo.model().index(combo.findData(40), 0)
    combo.view().scrollTo(index)
    application.processEvents()
    QTest.mouseClick(combo.view().viewport(), Qt.LeftButton, pos=combo.view().visualRect(index).center())
    assert combo.value() == 40
    combo.close()
