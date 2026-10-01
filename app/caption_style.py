"""Shared subtitle layout and rasterization for the player and video export."""
import math
from dataclasses import asdict, dataclass, fields

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPen, QTextCharFormat,
                         QTextLayout, QTextOption, QTransform)


@dataclass
class CaptionStyle:
    font_family: str = 'Microsoft YaHei'
    font_size: float = 30
    translation_size: float = 30
    bold: bool = True
    italic: bool = False
    underline: bool = False
    color: str = '#FFFFFF'
    translation_color: str = '#FFE0A3'
    outline_color: str = '#161210'
    outline: float = 2
    shadow_color: str = '#000000'
    shadow: float = 2
    background_color: str = '#211A17'
    background_opacity: float = 0
    opacity: float = 100
    padding: float = 10
    radius: float = 8
    line_spacing: float = 5
    width: float = 90
    alignment: str = 'center'
    x: float = 50
    y: float = 95
    rotation: float = 0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, values=None):
        result = cls()
        if not isinstance(values, dict):
            return result
        limits = {'font_size': (8, 120), 'translation_size': (8, 120),
                  'outline': (0, 12), 'shadow': (0, 20), 'background_opacity': (0, 100),
                  'opacity': (0, 100), 'padding': (0, 40), 'radius': (0, 40),
                  'line_spacing': (0, 40), 'width': (10, 100), 'x': (0, 100),
                  'y': (0, 100), 'rotation': (-180, 180)}
        for field in fields(cls):
            value = values.get(field.name, getattr(result, field.name))
            if field.name in limits:
                try:
                    value = float(value)
                    if not math.isfinite(value):
                        continue
                    value = max(limits[field.name][0], min(limits[field.name][1], value))
                except (ValueError, TypeError):
                    continue
            elif field.name.endswith('color'):
                if not isinstance(value, str) or not QColor(value).isValid():
                    continue
                value = QColor(value).name()
            elif field.name == 'alignment':
                if value not in ('left', 'center', 'right'):
                    continue
            elif field.name == 'font_family':
                if not isinstance(value, str) or not value.strip():
                    continue
                value = value[:150]
            elif not isinstance(value, bool):
                continue
            setattr(result, field.name, value)
        return result


def caption_position(style, tile_width, tile_height, width, height):
    """x is the horizontal center, y the bottom edge; clamp to the actual frame."""
    return (max(0, min(width - tile_width, width * style.x / 100 - tile_width / 2)),
            max(0, min(height - tile_height, height * style.y / 100 - tile_height)))


def render_caption(text, style, width, height, translated_only=False, original_lines=1):
    """Render literal Unicode text once; preview and export use these exact pixels.

    Dimensions are video pixels. Font/spacing controls use a 720-pixel-high canvas.
    Only the subtitle tile is allocated here, keeping redraw and dragging cheap.
    """
    if not text:
        image = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        return image
    scale = height / 720
    edge = math.ceil((style.padding + style.outline + style.shadow + 2) * scale)
    max_width = max(1, width * style.width / 100 - 2 * edge)
    layouts, widest, content_height = [], 0, 0
    for i, paragraph in enumerate(text.splitlines()):
        translated = translated_only or i >= original_lines
        font = QFont(style.font_family)
        font.setPixelSize(max(1, round((style.translation_size if translated else style.font_size) * scale)))
        font.setBold(style.bold)
        font.setItalic(style.italic)
        font.setUnderline(style.underline)
        layout = QTextLayout(paragraph, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(Qt.AlignLeft)
        layout.setTextOption(option)
        layout.beginLayout()
        lines = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max_width)
            widest = max(widest, line.naturalTextWidth())
            lines.append((line, content_height))
            content_height += line.height() + style.line_spacing * scale
        layout.endLayout()
        layouts.append((layout, lines, style.translation_color if translated else style.color))
    content_height = max(1, content_height - style.line_spacing * scale)
    for layout, lines, color in layouts:
        for line, y in lines:
            remaining = widest - line.naturalTextWidth()
            x = remaining * {'left': 0, 'center': .5, 'right': 1}[style.alignment]
            line.setPosition(QPointF(x, y))
    image = QImage(max(1, math.ceil(widest + 2 * edge)), max(1, math.ceil(content_height + 2 * edge)), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    background = QColor(style.background_color)
    background.setAlphaF(style.background_opacity / 100)
    painter.setBrush(background)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(image.rect(), style.radius * scale, style.radius * scale)

    def draw(layout, color, point, outline):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if outline:
            pen = QPen(QColor(style.outline_color), 2 * style.outline * scale)
            pen.setJoinStyle(Qt.RoundJoin)
            fmt.setTextOutline(pen)
        else:
            fmt.setTextOutline(QPen(Qt.NoPen))
        selection = QTextLayout.FormatRange()
        selection.start, selection.length, selection.format = 0, len(layout.text().encode('utf-16-le')) // 2, fmt
        layout.draw(painter, point, [selection])

    if style.shadow:
        for layout, lines, color in layouts:
            draw(layout, style.shadow_color, QPointF(edge + style.shadow * scale, edge + style.shadow * scale), False)
    for layout, lines, color in layouts:
        if style.outline:
            draw(layout, color, QPointF(edge, edge), True)
        # Paint the fill last so thick strokes never swallow thin CJK glyphs.
        draw(layout, color, QPointF(edge, edge), False)
    if style.opacity < 100:
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.fillRect(image.rect(), QColor(0, 0, 0, round(255 * style.opacity / 100)))
    painter.end()
    if style.rotation:
        image = image.transformed(QTransform().rotate(style.rotation), Qt.SmoothTransformation)
    if image.width() > width or image.height() > height:
        image = image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image


def render_overlay(text, style, width, height, translated_only=False, original_lines=1):
    return place_caption(render_caption(text, style, width, height, translated_only, original_lines), style, width, height)


def place_caption(tile, style, width, height):
    """Put a rendered tile on a transparent full frame. No text layout, so it is safe in worker threads."""
    canvas = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    x, y = caption_position(style, tile.width(), tile.height(), width, height)
    painter.drawImage(QPointF(round(x), round(y)), tile)
    painter.end()
    return canvas
