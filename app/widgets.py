"""Settings controls: wheel gestures scroll the page, never change a value."""
import math
from PySide6.QtWidgets import (QApplication, QAbstractScrollArea, QAbstractItemView, QComboBox,
    QFontComboBox, QSpinBox, QDoubleSpinBox, QSlider, QProxyStyle, QStyle)


class ListPopupStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, return_data=None):
        if hint == QStyle.SH_ComboBox_Popup:
            return 0
        return super().styleHint(hint, option, widget, return_data)


class ScrollPageOnWheel:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(self, QComboBox):
            # Use a bounded, scrollable list instead of the native full-screen menu.
            self._popup_style = ListPopupStyle('Fusion')
            self._popup_style.setParent(self)
            self.setStyle(self._popup_style)
            self.setMaxVisibleItems(10)
            self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            self.setMinimumContentsLength(8)

    def wheelEvent(self, event):
        # An open dropdown may scroll its list, but committing a choice needs a click.
        if isinstance(self, QComboBox) and self.view().isVisible():
            area = self.view()
        else:
            area = self.parentWidget()
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parentWidget()
        if area is None:
            event.ignore()
            return
        bar = area.verticalScrollBar()
        if event.pixelDelta().y():
            amount = -event.pixelDelta().y()
            if isinstance(area, QAbstractItemView) and area.verticalScrollMode() == QAbstractItemView.ScrollPerItem:
                amount /= max(1, area.sizeHintForRow(0))
        else:
            amount = -event.angleDelta().y() / 120 * bar.singleStep() * QApplication.wheelScrollLines()
        # Retain partial steps from high-resolution wheels and touchpads.
        remainder = getattr(self, '_wheel_remainder', 0) + amount
        steps = math.trunc(remainder)
        self._wheel_remainder = remainder - steps
        bar.setValue(bar.value() + steps)
        event.accept()


class ComboBox(ScrollPageOnWheel, QComboBox):
    pass


class FontComboBox(ScrollPageOnWheel, QFontComboBox):
    pass


class SpinBox(ScrollPageOnWheel, QSpinBox):
    pass


class DoubleSpinBox(ScrollPageOnWheel, QDoubleSpinBox):
    pass


class Slider(ScrollPageOnWheel, QSlider):
    pass


class IntegerChoice(ComboBox):
    def __init__(self, minimum, maximum, suffix='', parent=None):
        super().__init__(parent)
        self.minimum_value, self.maximum_value = minimum, maximum
        for value in range(minimum, maximum + 1):
            self.addItem(f'{value}{suffix}', value)
        self.setMaxVisibleItems(10)

    def value(self):
        return self.currentData()

    def setValue(self, value):
        self.setCurrentIndex(self.findData(max(self.minimum_value, min(self.maximum_value, int(value)))))
