"""Small asynchronous audio waveform for adjusting the selected subtitle."""
import json
import sys

from PySide6.QtCore import Qt, QProcess, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .config import ROOT


class Waveform(QWidget):
    rangeChanged = Signal(float, float)
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.process = None
        self.peaks = []
        self.interval = None
        self.dragging = None
        self.message = '选择一条字幕查看波形；拖动橙色边线调整起止时间。'
        self.generation = 0

    def load(self, video, cue, track=0, lower=0, upper=None):
        self.generation += 1
        generation = self.generation
        self.stop()
        self.peaks = []
        self.interval = [cue.start, cue.end]
        self.original = tuple(self.interval)
        self.left = max(0, cue.start - 2)
        self.right = cue.end + 2
        self.lower = max(self.left, lower)
        self.upper = min(self.right, upper if upper is not None else self.right)
        self.message = '正在读取当前片段波形…'
        if not video or not video.is_file():
            self.message = '原媒体不可用；仍可手动修改时间。'
            self.update()
            return
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(ROOT))
        def finished(code, status):
            if generation != self.generation:
                process.deleteLater()
                return
            try:
                if code:
                    raise ValueError()
                self.peaks = json.loads(bytes(process.readAllStandardOutput()).decode('utf-8'))
                self.message = ''
            except (ValueError, UnicodeError):
                self.message = '此片段波形不可用；可继续编辑字幕。'
            self.process = None
            process.deleteLater()
            self.update()
        process.finished.connect(finished)
        process.start(str(ROOT / '.venv/Scripts/python.exe'), ['-m', 'app.waveform', str(video),
                      str(self.left), str(self.right), str(track)])
        self.update()

    def stop(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        self.process = None

    def clear(self):
        self.generation += 1
        self.stop()
        self.interval, self.peaks = None, []
        self.message = '选择一条字幕查看波形；拖动橙色边线调整起止时间。'
        self.update()

    def time_to_x(self, seconds):
        return 12 + (seconds - self.left) / max(.001, self.right - self.left) * (self.width() - 24)

    def seconds(self, x):
        return self.left + (x - 12) / max(1, self.width() - 24) * (self.right - self.left)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#FAF6F3'))
        if self.interval:
            start, end = self.interval
            painter.fillRect(round(self.time_to_x(start)), 20, max(1, round(self.time_to_x(end) - self.time_to_x(start))), self.height() - 40, QColor('#FFE2D3'))
            painter.setPen(QPen(QColor('#9C8578'), 1))
            for index, amplitude in enumerate(self.peaks):
                x = 12 + index / max(1, len(self.peaks) - 1) * (self.width() - 24)
                half = amplitude * (self.height() - 48) / 2
                painter.drawLine(round(x), round(self.height() / 2 - half), round(x), round(self.height() / 2 + half))
            painter.setPen(QPen(QColor('#FF5522'), 3))
            for boundary in self.interval:
                x = round(self.time_to_x(boundary))
                painter.drawLine(x, 18, x, self.height() - 18)
            painter.setPen(QColor('#6F5A4C'))
            painter.drawText(12, 14, f'{start:.3f} → {end:.3f} 秒  ·  拖动边线调时，点击波形定位')
        if self.message:
            painter.setPen(QColor('#87776E'))
            painter.drawText(self.rect().adjusted(12, 16, -12, -4), Qt.AlignCenter, self.message)

    def mousePressEvent(self, event):
        if not self.interval or event.button() != Qt.LeftButton:
            return
        distances = [abs(event.position().x() - self.time_to_x(value)) for value in self.interval]
        index = 0 if distances[0] < distances[1] else 1
        if distances[index] <= 12:
            self.dragging = index
            self.original = tuple(self.interval)
        else:
            self.seekRequested.emit(max(0, self.seconds(event.position().x())))

    def mouseMoveEvent(self, event):
        if self.dragging is None:
            return
        value = self.seconds(event.position().x())
        if self.dragging == 0:
            value = max(self.lower, min(value, self.interval[1] - .05))
        else:
            value = min(self.upper, max(value, self.interval[0] + .05))
        self.interval[self.dragging] = round(value, 3)
        self.update()

    def mouseReleaseEvent(self, event):
        if self.dragging is not None:
            self.dragging = None
            if tuple(self.interval) != self.original:
                self.rangeChanged.emit(*self.interval)


def extract_peaks(video, start, end, track):
    import numpy as np
    from .media import run_ffmpeg
    result = run_ffmpeg(['-v', 'error', '-ss', start, '-i', video, '-t', min(60, end - start),
                        '-map', f'0:a:{track}', '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', 'pipe:1'])
    audio = np.frombuffer(result.stdout, dtype='<f4')
    if not len(audio):
        return []
    peaks = [float(np.max(np.abs(part))) for part in np.array_split(audio, min(500, len(audio)))]
    ceiling = max(max(peaks), .01)
    return [round(value / ceiling, 4) for value in peaks]


if __name__ == '__main__':
    print(json.dumps(extract_peaks(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]))))
