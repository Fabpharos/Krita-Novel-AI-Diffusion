"""Small widgets: a wrapping flow layout and the history entry (one per click)."""
import time

from ..qt import (QLayout, QRect, QPoint, QSize, QWidget, QVBoxLayout, QLabel, QToolButton,
                  QFrame, QIcon, QPixmap, pyqtSignal, STYLED_PANEL, KEEP_ASPECT_EXPANDING,
                  SMOOTH, TEXT_SELECTABLE, image_from_png)

THUMB = 72


class FlowLayout(QLayout):
    """Lays children out left to right and wraps to the next row."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect, dry):
        x, y, row_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and row_h > 0:
                x, y, row_h = rect.x(), y + row_h + self._spacing, 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_h = max(row_h, hint.height())
        return y + row_h - rect.y()


class Thumb(QToolButton):
    """A result thumbnail. Click previews, double-click applies."""
    double_clicked = pyqtSignal()

    def __init__(self, png: bytes, tooltip: str, parent=None):
        super().__init__(parent)
        pix = QPixmap.fromImage(image_from_png(png)).scaled(THUMB, THUMB,
                                                            KEEP_ASPECT_EXPANDING, SMOOTH)
        self.setIcon(QIcon(pix))
        self.setIconSize(QSize(THUMB, THUMB))
        self.setFixedSize(THUMB + 8, THUMB + 8)
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setToolTip(tooltip)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()


class HistoryEntry(QFrame):
    """All results from one Generate click: a header line and wrapping thumbnails."""

    def __init__(self, summary: str, expected: int, parent=None):
        super().__init__(parent)
        self.setFrameShape(STYLED_PANEL)
        self.expected = expected
        self.received = 0
        self.failed = 0
        self.cancelled = 0
        self.spent = 0
        self.summary = summary
        self.started = time.strftime("%H:%M")
        self.thumbs = []
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        self.header = QLabel()
        self.header.setWordWrap(True)
        self.header.setTextInteractionFlags(TEXT_SELECTABLE)
        v.addWidget(self.header)
        self.grid_host = QWidget()
        self.flow = FlowLayout(self.grid_host)
        v.addWidget(self.grid_host)
        self.update_header()

    def add_thumb(self, thumb: Thumb):
        self.thumbs.append(thumb)
        self.flow.addWidget(thumb)
        self.grid_host.updateGeometry()

    def update_header(self):
        parts = [self.started, self.summary, f"{self.received}/{self.expected}"]
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.cancelled:
            parts.append(f"{self.cancelled} cancelled")
        parts.append(f"{self.spent} Anlas spent" if self.spent else "0 Anlas")
        self.header.setText(" · ".join(parts))
