"""Project home, card rendering and nonblocking thumbnail generation."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QRectF, QProcess, QTimer, QObject, Signal, QUrl
from PySide6.QtGui import QColor, QFont, QPixmap, QPainterPath, QPainter, QDesktopServices
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QAbstractItemView, QStyledItemDelegate, QStyle,
    QMenu, QDialog, QDialogButtonBox, QFormLayout, QFileDialog)

from .media import ffmpeg
from .subtitle_import import MEDIA_FILTER
from .widgets import ComboBox


def label(value, name='muted'):
    item = QLabel(value)
    item.setTextFormat(Qt.PlainText)
    item.setObjectName(name)
    return item


def button(value, callback, name=''):
    item = QPushButton(value)
    item.setObjectName(name)
    item.setCursor(Qt.PointingHandCursor)
    item.clicked.connect(callback)
    return item


class NewProjectDialog(QDialog):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.setWindowTitle('新建工程')
        self.setMinimumWidth(520)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 22)
        box.setSpacing(14)
        box.addWidget(label('开始一个新工程', 'panelTitle'))
        box.addWidget(label('先给工程命名，视频或音频也可以稍后导入。'))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText('例如：夏日回忆 · 第 2 期')
        form.addRow('工程名称', self.name)
        self.media = QLineEdit()
        self.media.setReadOnly(True)
        self.media.setPlaceholderText('可选：选择视频或音频')
        row = QHBoxLayout()
        row.addWidget(self.media, 1)
        row.addWidget(button('选择…', self.choose_media))
        form.addRow('原媒体', row)
        box.addLayout(form)
        location = label(f'工程保存在：{root}')
        location.setWordWrap(True)
        box.addWidget(location)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.create = buttons.button(QDialogButtonBox.Ok)
        self.create.setText('创建工程')
        self.create.setObjectName('primary')
        self.create.setEnabled(False)
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.name.textChanged.connect(lambda text: self.create.setEnabled(bool(text.strip())))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    def choose_media(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择视频或音频', '', MEDIA_FILTER)
        if path:
            self.media.setText(path)
            if not self.name.text().strip():
                self.name.setText(Path(path).stem[:100])


class ThumbnailQueue(QObject):
    ready = Signal(str)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library, self.queue, self.current = library, [], None
        self.failed_keys = set()
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(lambda error: self.finished(-1) if error == QProcess.FailedToStart else None)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.process.kill)

    def enqueue(self, entries):
        for entry in entries:
            if entry.video is None or entry.media_missing or entry.error:
                continue
            if entry.video.suffix.lower() in {'.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.opus', '.mka', '.aif', '.aiff', '.wma'}:
                continue
            stat = entry.video.stat()
            key = [str(entry.video), stat.st_size, stat.st_mtime_ns]
            if entry.cover.exists() and self.library.metadata(entry.path).get('cover_media') == key:
                continue
            if ((str(entry.path), str(key)) not in self.failed_keys and all(item[0].path != entry.path for item in self.queue)
                    and (self.current is None or self.current[0].path != entry.path)):
                self.queue.append((entry, key))
        self.start_next()

    def start_next(self):
        if self.current is not None or not self.queue:
            return
        self.current = self.queue.pop(0)
        entry, _ = self.current
        if not entry.path.is_file() or not entry.video.is_file():
            self.current = None
            self.start_next()
            return
        self.process.setProgram(ffmpeg())
        self.process.setArguments(['-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(entry.video),
            '-frames:v', '1', '-vf', 'scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2',
            '-q:v', '4', str(entry.path.parent / 'cover.partial.jpg')])
        self.process.start()
        self.timer.start(8000)

    def finished(self, code, *args):
        self.timer.stop()
        if self.current is None:
            return
        entry, key = self.current
        partial = entry.path.parent / 'cover.partial.jpg'
        try:
            if code == 0 and partial.is_file() and entry.path.is_file():
                partial.replace(entry.cover)
                self.library.update_metadata(entry.path, cover_media=key)
                self.ready.emit(str(entry.path))
            else:
                self.failed_keys.add((str(entry.path), str(key)))
        except OSError:
            self.failed_keys.add((str(entry.path), str(key)))
        finally:
            partial.unlink(missing_ok=True)
            self.current = None
            QTimer.singleShot(0, self.start_next)

    def stop(self):
        self.queue.clear()
        self.timer.stop()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(2000)


class ProjectCardDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole)
        if entry is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        painter.setPen(QColor('#FF8057' if selected else '#E6E2DD'))
        painter.setBrush(QColor('#FFF8F4' if selected else '#FFFFFF'))
        painter.drawRoundedRect(bounds, 10, 10)
        image_rect = QRectF(bounds.left() + 10, bounds.top() + 10, bounds.width() - 20, 130)
        clip = QPainterPath()
        clip.addRoundedRect(image_rect, 7, 7)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(image_rect, QColor('#272726'))
        cover = QPixmap(str(entry.cover)) if entry.cover.is_file() else QPixmap()
        if not cover.isNull():
            scaled = cover.scaled(round(image_rect.width()), round(image_rect.height()), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(round(image_rect.center().x() - scaled.width() / 2), round(image_rect.center().y() - scaled.height() / 2), scaled)
        else:
            font = QFont(option.font)
            font.setPixelSize(34)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor('#FF8057'))
            painter.drawText(image_rect, Qt.AlignCenter, '字')
        painter.restore()
        font = QFont(option.font)
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('#302D2B'))
        x, y, width = bounds.left() + 12, image_rect.bottom() + 12, bounds.width() - 24
        painter.drawText(QRectF(x, y, width, 24), Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(entry.name, Qt.ElideRight, int(width)))
        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor('#817A74'))
        modified = datetime.fromtimestamp(entry.modified, timezone(timedelta(hours=8))).strftime('%m月%d日 %H:%M')
        painter.drawText(QRectF(x, y + 26, width, 20), Qt.AlignLeft, f'{entry.cue_count} 条字幕  ·  {modified}')
        painter.setPen(QColor('#B34A32' if entry.media_missing or entry.error else '#6D725C'))
        painter.drawText(QRectF(x, y + 51, width, 20), Qt.AlignLeft, entry.label)
        painter.restore()


class ProjectHome(QWidget):
    openRequested = Signal(str)
    newRequested = Signal()
    importRequested = Signal()
    renameRequested = Signal(str)
    trashRequested = Signal(str)
    restoreRequested = Signal(str)
    resumeRequested = Signal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library, self.entries, self.in_trash = library, [], False
        self.setObjectName('projectHome')
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('projectSidebar')
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 22, 24)
        side.setSpacing(12)
        side.addWidget(label('映字', 'brand'))
        side.addWidget(label('字幕工程工作台'))
        side.addSpacing(22)
        side.addWidget(button('＋  新建工程', self.newRequested.emit, 'primary'))
        side.addWidget(button('导入工程…', self.importRequested.emit))
        side.addSpacing(18)
        self.projects_button = button('所有工程', lambda: self.show_trash(False), 'step')
        self.projects_button.setCheckable(True)
        side.addWidget(self.projects_button)
        self.trash_button = button('回收站', lambda: self.show_trash(True), 'step')
        self.trash_button.setCheckable(True)
        side.addWidget(self.trash_button)
        side.addStretch()
        self.resume = button('返回正在编辑的工程 →', self.resumeRequested.emit, 'quiet')
        self.resume.hide()
        side.addWidget(self.resume)
        side.addWidget(button('打开工程文件夹', self.open_root, 'quiet'))
        side.addWidget(label('保存在本机 · 随时继续编辑', 'eyebrow'))
        root.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(30, 28, 30, 22)
        main.setSpacing(18)
        self.title = label('你的工程', 'homeTitle')
        main.addWidget(self.title)
        self.subtitle = label('从上次停下的地方继续，或开始一个新工程。')
        main.addWidget(self.subtitle)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索工程名称或媒体…   Ctrl+F')
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.search, 1)
        self.filter = ComboBox()
        self.filter.addItems(['全部工程', '待完成', '原媒体缺失'])
        toolbar.addWidget(self.filter)
        self.sort = ComboBox()
        self.sort.addItems(['最近编辑', '名称', '字幕数量'])
        toolbar.addWidget(self.sort)
        toolbar.addWidget(button('刷新', self.refresh, 'quiet'))
        main.addLayout(toolbar)
        self.cards = QListWidget()
        self.cards.setObjectName('projectCards')
        self.cards.setViewMode(QListWidget.IconMode)
        self.cards.setMovement(QListWidget.Static)
        self.cards.setResizeMode(QListWidget.Adjust)
        self.cards.setWrapping(True)
        self.cards.setSpacing(12)
        self.cards.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.cards.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards.setItemDelegate(ProjectCardDelegate(self.cards))
        self.cards.setContextMenuPolicy(Qt.CustomContextMenu)
        main.addWidget(self.cards, 1)
        self.empty = label('', 'emptyTitle')
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        main.addWidget(self.empty, 1)
        footer = QHBoxLayout()
        self.count = label('')
        footer.addWidget(self.count, 1)
        self.open_button = button('打开工程', self.open_selected, 'primary')
        self.rename_button = button('重命名', lambda: self.emit_selected(self.renameRequested))
        self.delete_button = button('移至回收站', lambda: self.emit_selected(self.trashRequested), 'quiet')
        self.restore_button = button('恢复工程', lambda: self.emit_selected(self.restoreRequested), 'primary')
        for item in (self.open_button, self.rename_button, self.delete_button, self.restore_button):
            footer.addWidget(item)
        main.addLayout(footer)
        self.notice = label('')
        self.notice.setWordWrap(True)
        main.addWidget(self.notice)
        root.addLayout(main, 1)
        for signal in (self.search.textChanged, self.filter.currentIndexChanged, self.sort.currentIndexChanged):
            signal.connect(self.apply_filter)
        self.cards.itemSelectionChanged.connect(self.update_actions)
        self.cards.itemActivated.connect(lambda item: self.open_selected())
        self.cards.customContextMenuRequested.connect(self.context_menu)
        self.thumbnails = ThumbnailQueue(library, self)
        self.thumbnails.ready.connect(lambda path: self.cards.viewport().update())
        self.projects_button.setChecked(True)
        self.apply_filter()

    def refresh(self):
        self.entries = self.library.scan(trash=self.in_trash)
        self.apply_filter()
        if not self.in_trash:
            self.thumbnails.enqueue(self.entries)

    def apply_filter(self, *args):
        selected = self.selected()
        query = self.search.text().strip().casefold()
        items = [entry for entry in self.entries if not query or query in (entry.name + ' ' + str(entry.video or '')).casefold()]
        if self.filter.currentIndex() == 1:
            items = [entry for entry in items if not entry.cue_count or entry.pending or entry.status in ('recognizing', 'aligning')]
        elif self.filter.currentIndex() == 2:
            items = [entry for entry in items if entry.media_missing]
        items.sort(key=(lambda entry: entry.name.casefold()) if self.sort.currentIndex() == 1 else
                   (lambda entry: entry.cue_count) if self.sort.currentIndex() == 2 else (lambda entry: entry.modified), reverse=self.sort.currentIndex() != 1)
        self.cards.clear()
        for entry in items:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, entry)
            item.setToolTip(f'{entry.name}\n{entry.label}\n{entry.video or "尚未导入媒体"}\n{entry.path}' + (f'\n{entry.error}' if entry.error else ''))
            item.setSizeHint(QSize(265, 245))
            self.cards.addItem(item)
            if selected and selected.path == entry.path:
                self.cards.setCurrentItem(item)
        self.cards.setGridSize(QSize(265, 245))
        self.cards.setVisible(bool(items))
        self.empty.setVisible(not items)
        self.empty.setText('没有匹配的工程，请换个关键词或筛选条件。' if self.entries else '回收站是空的。' if self.in_trash else '还没有工程。点击「新建工程」，给下一段视频留一个位置。')
        self.count.setText(f'{len(items)} / {len(self.entries)} 个工程')
        self.update_actions()

    def selected(self):
        item = self.cards.currentItem()
        return item.data(Qt.UserRole) if item else None

    def update_actions(self):
        entry = self.selected()
        self.open_button.setVisible(not self.in_trash)
        self.rename_button.setVisible(not self.in_trash)
        self.delete_button.setVisible(not self.in_trash)
        self.restore_button.setVisible(self.in_trash)
        self.open_button.setEnabled(bool(entry) and not entry.error)
        for item in (self.rename_button, self.delete_button, self.restore_button):
            item.setEnabled(bool(entry))

    def emit_selected(self, signal):
        entry = self.selected()
        if entry:
            signal.emit(str(entry.path))

    def open_selected(self):
        entry = self.selected()
        if entry and not entry.error and not self.in_trash:
            self.openRequested.emit(str(entry.path))

    def show_trash(self, value):
        self.thumbnails.stop()
        self.in_trash = value
        self.projects_button.setChecked(not value)
        self.trash_button.setChecked(value)
        self.title.setText('回收站' if value else '你的工程')
        self.subtitle.setText('移除的工程与目录内文件保留在这里，需要时可以恢复。' if value else '从上次停下的地方继续，或开始一个新工程。')
        self.refresh()

    def open_root(self):
        self.library.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root)))

    def context_menu(self, position):
        item = self.cards.itemAt(position)
        if item is None:
            return
        self.cards.setCurrentItem(item)
        entry = self.selected()
        menu = QMenu(self)
        if self.in_trash:
            menu.addAction('恢复工程', lambda: self.emit_selected(self.restoreRequested))
        else:
            menu.addAction('打开工程', self.open_selected).setEnabled(not entry.error)
            menu.addAction('重命名', lambda: self.emit_selected(self.renameRequested))
            menu.addAction('移至回收站', lambda: self.emit_selected(self.trashRequested))
        menu.addAction('在文件夹中查看', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.path.parent))))
        menu.exec(self.cards.viewport().mapToGlobal(position))
        menu.deleteLater()
