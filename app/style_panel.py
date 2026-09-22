from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel,
    QCheckBox, QPushButton, QColorDialog,
    QHBoxLayout, QScrollArea)

from .caption_style import CaptionStyle
from .widgets import ComboBox as QComboBox, FontComboBox as QFontComboBox, DoubleSpinBox as QDoubleSpinBox


class StylePanel(QScrollArea):
    styleChanged = Signal(object)
    sampleChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.style = CaptionStyle()
        self.controls = {}
        self.updating = False
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        box = QVBoxLayout(body)
        body.setObjectName('panelBody')
        box.setContentsMargins(18, 18, 18, 16)
        box.setSpacing(12)
        heading = QLabel('字幕样式')
        heading.setObjectName('panelTitle')
        box.addWidget(heading)
        hint = QLabel('先选一个预设，再微调。\n拖动画面中的字幕即可移动位置。')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        box.addWidget(hint)
        self.sample = QCheckBox('空白时段显示示例字幕')
        self.sample.setChecked(True)
        self.sample.toggled.connect(self.sampleChanged)
        box.addWidget(self.sample)
        self.presets = QComboBox()
        self.presets.addItems(['选择样式预设…', '经典描边', '电影双语', '半透明底板', '醒目大字'])
        self.presets.activated.connect(self.apply_preset)
        box.addWidget(self.presets)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setSpacing(10)
        box.addLayout(form)

        def section(title):
            toggle = QPushButton('＋  ' + title)
            toggle.setObjectName('disclosure')
            toggle.setCheckable(True)
            content = QWidget()
            fields = QFormLayout(content)
            fields.setContentsMargins(0, 2, 0, 4)
            fields.setSpacing(10)
            fields.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            content.hide()
            def expand(checked):
                content.setVisible(checked)
                toggle.setText(('－  ' if checked else '＋  ') + title)
            toggle.toggled.connect(expand)
            box.addWidget(toggle)
            box.addWidget(content)
            return fields
        self.font = QFontComboBox()
        self.font.setMinimumWidth(0)
        self.font.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font.setMinimumContentsLength(10)
        self.font.currentFontChanged.connect(lambda font: self.change('font_family', font.family()))
        self.controls['font_family'] = self.font
        form.addRow('字体', self.font)

        def number(key, title, minimum, maximum, suffix='', step=1):
            spin = QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setSuffix(suffix)
            spin.valueChanged.connect(lambda value: self.change(key, value))
            self.controls[key] = spin
            form.addRow(title, spin)

        def color(key, title):
            control = QPushButton()
            control.clicked.connect(lambda: self.pick_color(key))
            self.controls[key] = control
            form.addRow(title, control)

        number('font_size', '原文字号', 8, 120)
        number('translation_size', '译文字号', 8, 120)
        emphasis = QWidget()
        row = QHBoxLayout(emphasis)
        row.setContentsMargins(0, 0, 0, 0)
        for key, title in [('bold', '粗体'), ('italic', '斜体'), ('underline', '下划线')]:
            check = QCheckBox(title)
            check.toggled.connect(lambda value, k=key: self.change(k, value))
            self.controls[key] = check
            row.addWidget(check)
        form.addRow(emphasis)
        color('color', '原文颜色')
        color('translation_color', '译文颜色')
        form = section('描边、阴影与底板')
        number('opacity', '整体不透明度', 0, 100, '%')
        number('outline', '描边粗细', 0, 12, step=.5)
        color('outline_color', '描边颜色')
        number('shadow', '阴影距离', 0, 20, step=.5)
        color('shadow_color', '阴影颜色')
        color('background_color', '背景颜色')
        number('background_opacity', '背景不透明度', 0, 100, '%')
        number('padding', '背景内边距', 0, 40)
        number('radius', '背景圆角', 0, 40)
        form = section('位置与排版')
        number('line_spacing', '行间距', 0, 40)
        number('width', '最大行宽', 10, 100, '%')
        alignment = QComboBox()
        for title, value in [('左对齐', 'left'), ('居中', 'center'), ('右对齐', 'right')]:
            alignment.addItem(title, value)
        alignment.currentIndexChanged.connect(lambda: self.change('alignment', alignment.currentData()))
        self.controls['alignment'] = alignment
        form.addRow('文字对齐', alignment)
        number('x', '水平中心', 0, 100, '%', .5)
        number('y', '字幕底边', 0, 100, '%', .5)
        number('rotation', '旋转角度', -180, 180, '°')
        positions = QComboBox()
        positions.addItems(['快速定位…', '底部居中', '画面中央', '顶部居中'])
        positions.activated.connect(self.position_preset)
        form.addRow('位置', positions)
        note = QLabel('字号与间距以 720 高画面为基准，随视频分辨率缩放。SRT / VTT 不包含这些样式。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        box.addWidget(note)
        reset = QPushButton('恢复默认样式')
        reset.clicked.connect(lambda: self.set_style(CaptionStyle(), emit=True))
        box.addWidget(reset)
        box.addStretch()
        self.setWidget(body)
        self.set_style(self.style)

    def change(self, key, value):
        if not self.updating:
            setattr(self.style, key, value)
            self.styleChanged.emit(self.style)

    def pick_color(self, key):
        value = QColorDialog.getColor(QColor(getattr(self.style, key)), self, '选择字幕颜色')
        if value.isValid():
            self.change(key, value.name())
            self.set_style(self.style)

    def set_style(self, style, emit=False):
        self.style = CaptionStyle.from_dict(style.to_dict())
        self.updating = True
        for key, control in self.controls.items():
            value = getattr(self.style, key)
            if key == 'font_family':
                control.setCurrentFont(QFont(value))
            elif key.endswith('color'):
                control.setText(value.upper())
                text = '#222222' if QColor(value).lightness() > 145 else '#FFFFFF'
                control.setStyleSheet(f'background: {value}; color: {text}; padding: 5px;')
            elif isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, QDoubleSpinBox):
                control.setValue(value)
            else:
                control.setCurrentIndex(control.findData(value))
        self.updating = False
        if emit:
            self.styleChanged.emit(self.style)

    def position_preset(self, index):
        if index:
            self.style.x, self.style.y = 50, {1: 95, 2: 55, 3: 18}[index]
            self.set_style(self.style, emit=True)

    def apply_preset(self, index):
        if not index:
            return
        values = [{}, {}, {'font_size': 26, 'translation_size': 34, 'outline': 1.5},
                  {'background_opacity': 65, 'outline': 0, 'shadow': 0, 'padding': 14},
                  {'font_size': 44, 'translation_size': 44, 'color': '#FFE044', 'outline': 3}][index]
        self.set_style(CaptionStyle.from_dict(values), emit=True)
