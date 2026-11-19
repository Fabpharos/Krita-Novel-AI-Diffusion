"""Single place for Qt imports. Krita 6 ships PyQt6, Krita 5 ships PyQt5.

Other modules import Qt names from here and use the enum constants below, so
the PyQt5/PyQt6 difference in enum spelling stays in this file.
"""
try:  # Krita 6
    from PyQt6.QtCore import (Qt, QObject, QThread, QTimer, QSize, QByteArray, QBuffer,
                              QIODevice, QStandardPaths, QRect, QPoint, QUuid, QEvent,
                              QCoreApplication, pyqtSignal)
    from PyQt6.QtGui import (QImage, QPixmap, QIcon, QPainter, QColor, QPen, QBrush,
                             QTextCursor)
    from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
                                 QLabel, QPushButton, QToolButton, QComboBox, QPlainTextEdit,
                                 QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QSlider,
                                 QProgressBar, QListWidget, QListWidgetItem, QDialog,
                                 QDialogButtonBox, QMessageBox, QFileDialog, QGroupBox,
                                 QSizePolicy, QFrame, QScrollArea, QLayout, QButtonGroup,
                                 QMenu, QWidgetAction, QInputDialog, QAbstractSpinBox)
    QT6 = True
except ImportError:  # Krita 5
    from PyQt5.QtCore import (Qt, QObject, QThread, QTimer, QSize, QByteArray, QBuffer,
                              QIODevice, QStandardPaths, QRect, QPoint, QUuid, QEvent,
                              QCoreApplication, pyqtSignal)
    from PyQt5.QtGui import (QImage, QPixmap, QIcon, QPainter, QColor, QPen, QBrush,
                             QTextCursor)
    from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
                                 QLabel, QPushButton, QToolButton, QComboBox, QPlainTextEdit,
                                 QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QSlider,
                                 QProgressBar, QListWidget, QListWidgetItem, QDialog,
                                 QDialogButtonBox, QMessageBox, QFileDialog, QGroupBox,
                                 QSizePolicy, QFrame, QScrollArea, QLayout, QButtonGroup,
                                 QMenu, QWidgetAction, QInputDialog, QAbstractSpinBox)
    QT6 = False


def _enum(owner, scope, name):
    """Qt5 puts enum members on the class; Qt6 only on the scoped enum."""
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, scope), name)


FMT_ARGB32 = _enum(QImage, "Format", "Format_ARGB32")
FMT_ARGB32_PM = _enum(QImage, "Format", "Format_ARGB32_Premultiplied")
FMT_RGB32 = _enum(QImage, "Format", "Format_RGB32")
FMT_GRAY8 = _enum(QImage, "Format", "Format_Grayscale8")
FMT_ALPHA8 = _enum(QImage, "Format", "Format_Alpha8")
FMT_RGBA8888 = _enum(QImage, "Format", "Format_RGBA8888")
FAST = _enum(Qt, "TransformationMode", "FastTransformation")
DEST_IN = _enum(QPainter, "CompositionMode", "CompositionMode_DestinationIn")
KEEP_ASPECT_EXPANDING = _enum(Qt, "AspectRatioMode", "KeepAspectRatioByExpanding")
IGNORE_ASPECT = _enum(Qt, "AspectRatioMode", "IgnoreAspectRatio")
KEEP_ASPECT = _enum(Qt, "AspectRatioMode", "KeepAspectRatio")
SMOOTH = _enum(Qt, "TransformationMode", "SmoothTransformation")
HORIZONTAL = _enum(Qt, "Orientation", "Horizontal")
ICON_MODE = _enum(QListWidget, "ViewMode", "IconMode")
LEFT_TO_RIGHT = _enum(QListWidget, "Flow", "LeftToRight")
ADJUST = _enum(QListWidget, "ResizeMode", "Adjust")
PASSWORD = _enum(QLineEdit, "EchoMode", "Password")
WRITE_ONLY = _enum(QIODevice, "OpenModeFlag", "WriteOnly")
APP_DATA = _enum(QStandardPaths, "StandardLocation", "AppDataLocation")
USER_ROLE = _enum(Qt, "ItemDataRole", "UserRole")
TEXT_SELECTABLE = _enum(Qt, "TextInteractionFlag", "TextSelectableByMouse")

SCROLL_OFF = _enum(Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")
NO_FRAME = _enum(QFrame, "Shape", "NoFrame")
STYLED_PANEL = _enum(QFrame, "Shape", "StyledPanel")
COMBO_SHRINK = _enum(QComboBox, "SizeAdjustPolicy", "AdjustToMinimumContentsLengthWithIcon")
SP_PREFERRED = _enum(QSizePolicy, "Policy", "Preferred")
SP_IGNORED = _enum(QSizePolicy, "Policy", "Ignored")
SP_FIXED = _enum(QSizePolicy, "Policy", "Fixed")
INSTANT_POPUP = _enum(QToolButton, "ToolButtonPopupMode", "InstantPopup")
ANTIALIAS = _enum(QPainter, "RenderHint", "Antialiasing")
ALIGN_CENTER = _enum(Qt, "AlignmentFlag", "AlignCenter")
NO_PEN = _enum(Qt, "PenStyle", "NoPen")
CROSS_CURSOR = _enum(Qt, "CursorShape", "CrossCursor")
WHEEL = _enum(QEvent, "Type", "Wheel")
TOOL_ICON_ONLY = _enum(Qt, "ToolButtonStyle", "ToolButtonIconOnly")

# tag autocomplete popup
EV_KEY_PRESS = _enum(QEvent, "Type", "KeyPress")
EV_FOCUS_OUT = _enum(QEvent, "Type", "FocusOut")
EV_HIDE = _enum(QEvent, "Type", "Hide")
TOOLTIP_WINDOW = _enum(Qt, "WindowType", "ToolTip")
FRAMELESS = _enum(Qt, "WindowType", "FramelessWindowHint")
NO_FOCUS = _enum(Qt, "FocusPolicy", "NoFocus")
WA_SHOW_NO_ACTIVATE = _enum(Qt, "WidgetAttribute", "WA_ShowWithoutActivating")
KEY_UP = _enum(Qt, "Key", "Key_Up")
KEY_DOWN = _enum(Qt, "Key", "Key_Down")
KEY_RETURN = _enum(Qt, "Key", "Key_Return")
KEY_ENTER = _enum(Qt, "Key", "Key_Enter")
KEY_TAB = _enum(Qt, "Key", "Key_Tab")
KEY_ESCAPE = _enum(Qt, "Key", "Key_Escape")
KEY_PAGE_UP = _enum(Qt, "Key", "Key_PageUp")
KEY_PAGE_DOWN = _enum(Qt, "Key", "Key_PageDown")
MOD_CTRL = _enum(Qt, "KeyboardModifier", "ControlModifier")
KEY_SPACE = _enum(Qt, "Key", "Key_Space")
KEEP_ANCHOR = _enum(QTextCursor, "MoveMode", "KeepAnchor")
SELECT_DOCUMENT = _enum(QTextCursor, "SelectionType", "Document")
CUSTOM_CONTEXT_MENU = _enum(Qt, "ContextMenuPolicy", "CustomContextMenu")
ECHO_NORMAL = _enum(QLineEdit, "EchoMode", "Normal")
MB_YES = _enum(QMessageBox, "StandardButton", "Yes")
MB_NO = _enum(QMessageBox, "StandardButton", "No")

MB_WARNING =_enum(QMessageBox, "Icon", "Warning")
MB_OK = _enum(QMessageBox, "StandardButton", "Ok")
MB_CANCEL = _enum(QMessageBox, "StandardButton", "Cancel")
DBB_OK = _enum(QDialogButtonBox, "StandardButton", "Ok")
DBB_CANCEL = _enum(QDialogButtonBox, "StandardButton", "Cancel")
ACCEPT_ROLE = _enum(QMessageBox, "ButtonRole", "AcceptRole")
REJECT_ROLE = _enum(QMessageBox, "ButtonRole", "RejectRole")


def exec_dialog(dialog):
    """QDialog.exec() in Qt6, exec_() in Qt5."""
    return dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()


def image_from_png(data: bytes) -> QImage:
    img = QImage()
    img.loadFromData(QByteArray(data), "PNG")
    return img


def image_to_png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(WRITE_ONLY)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def event_pos(event):
    """Mouse position as (x, y) floats: Qt6 has position(), Qt5 has localPos()."""
    p = event.position() if hasattr(event, "position") else event.localPos()
    return p.x(), p.y()


class WheelGuard(QObject):
    """Stops the mouse wheel from changing combo boxes, spin boxes and sliders.
    The wheel scrolls the given scroll area instead, so the panel still scrolls
    when the pointer happens to be over a control."""

    def __init__(self, scroll_area, parent=None):
        super().__init__(parent)
        self._scroll = scroll_area

    def install(self, root):
        for cls in (QComboBox, QAbstractSpinBox, QSlider):
            for w in root.findChildren(cls):
                w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == WHEEL:
            if self._scroll is not None:
                QCoreApplication.sendEvent(self._scroll.verticalScrollBar(), event)
            return True
        return False


def image_from_bytes(data: bytes, w: int, h: int, fmt, bytes_per_pixel: int) -> QImage:
    """QImage that owns a copy of raw pixel bytes (rows packed, no padding)."""
    return QImage(bytes(data), w, h, w * bytes_per_pixel, fmt).copy()


def image_bytes(img: QImage) -> bytes:
    """Raw pixel bytes of an image whose rows have no padding."""
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return bytes(ptr)
