"""Tag suggestions under the cursor in prompt boxes (QPlainTextEdit and QLineEdit).

The popup is a frameless tool-tip window that never takes focus, so typing goes on
in the prompt box. While it shows: Up/Down (or Page Up/Down) choose, Tab or Enter
inserts, Escape closes, a click inserts. Ctrl+Space opens it on demand. It only
opens while typing, never when text is set by the plugin, and it never changes
text the user didn't accept.
"""
from ..qt import (QObject, QTimer, QListWidget, QListWidgetItem, QPlainTextEdit, QLineEdit,
                  QColor, QPoint, EV_KEY_PRESS, EV_FOCUS_OUT, EV_HIDE, TOOLTIP_WINDOW,
                  FRAMELESS, NO_FOCUS, WA_SHOW_NO_ACTIVATE, KEY_UP, KEY_DOWN, KEY_RETURN,
                  KEY_ENTER, KEY_TAB, KEY_ESCAPE, KEY_PAGE_UP, KEY_PAGE_DOWN, KEY_SPACE,
                  MOD_CTRL, KEEP_ANCHOR, USER_ROLE, SCROLL_OFF)
from .. import tags

CATEGORY_COLOURS = {   # Danbooru's tag colours, readable on Krita's dark and light themes
    "general": "#4a9eff", "artist": "#e05555", "copyright": "#c060d0",
    "character": "#3fb860", "meta": "#e0a030", "nai": "#30b8b0",
}
CATEGORY_NAMES = {"nai": "NovelAI quality/aesthetic tag"}
MAX_ROWS = 10

_state = {"enabled": True}


def set_enabled(on: bool):
    _state["enabled"] = bool(on)


def enabled() -> bool:
    return _state["enabled"]


def attach(edit):
    """Give a prompt box tag suggestions. Safe to call once per widget."""
    if getattr(edit, "_tag_completer", None) is None:
        edit._tag_completer = TagCompleter(edit)
        tags.start_loading()
    return edit._tag_completer


class TagCompleter(QObject):
    def __init__(self, edit):
        super().__init__(edit)
        self.edit = edit
        self.plain = isinstance(edit, QPlainTextEdit)
        # parented, so it goes when the box goes; the window flags still make it a
        # separate floating window
        self.popup = QListWidget(edit)
        self.popup.setWindowFlags(TOOLTIP_WINDOW | FRAMELESS)
        self.popup.setAttribute(WA_SHOW_NO_ACTIVATE)
        self.popup.setFocusPolicy(NO_FOCUS)
        self.popup.setHorizontalScrollBarPolicy(SCROLL_OFF)
        self.popup.setUniformItemSizes(True)
        self.popup.itemPressed.connect(lambda item: self.accept(self.popup.row(item)))
        self._range = None
        edit.installEventFilter(self)

    # ---- text access for both widget kinds
    def _text(self):
        return self.edit.toPlainText() if self.plain else self.edit.text()

    def _cursor_pos(self):
        return self.edit.textCursor().position() if self.plain else self.edit.cursorPosition()

    def _has_selection(self):
        return self.edit.textCursor().hasSelection() if self.plain else self.edit.hasSelectedText()

    def _replace(self, start, end, text):
        """Replace a range as one undoable edit and put the cursor after it."""
        if self.plain:
            c = self.edit.textCursor()
            c.setPosition(start)
            c.setPosition(end, KEEP_ANCHOR)
            c.insertText(text)
            self.edit.setTextCursor(c)
        else:
            self.edit.setSelection(start, end - start)
            self.edit.insert(text)

    def _anchor(self):
        """Global point just below the cursor."""
        if self.plain:
            r = self.edit.cursorRect()
            return self.edit.viewport().mapToGlobal(r.bottomLeft() + QPoint(0, 2))
        return self.edit.mapToGlobal(QPoint(0, self.edit.height()))

    # ---- events
    def eventFilter(self, obj, event):
        t = event.type()
        if t == EV_KEY_PRESS:
            key = event.key()
            if self.popup.isVisible():
                if key in (KEY_UP, KEY_DOWN, KEY_PAGE_UP, KEY_PAGE_DOWN):
                    self._move(key)
                    return True
                if key in (KEY_RETURN, KEY_ENTER, KEY_TAB):
                    self.accept(self.popup.currentRow())
                    return True
                if key == KEY_ESCAPE:
                    self.hide()
                    return True
            if key == KEY_SPACE and event.modifiers() & MOD_CTRL:
                QTimer.singleShot(0, lambda: self.update(open_=True, force=True))
                return True
            typed = bool(event.text()) and not (event.modifiers() & MOD_CTRL)
            if typed or self.popup.isVisible():
                QTimer.singleShot(0, lambda: self.update(open_=typed))
        elif t == EV_FOCUS_OUT:
            QTimer.singleShot(150, self._hide_if_unfocused)
        elif t == EV_HIDE:
            self.hide()
        return False

    def _hide_if_unfocused(self):
        try:
            if not self.edit.hasFocus():
                self.hide()
        except RuntimeError:   # the box was closed meanwhile
            pass

    def _move(self, key):
        n = self.popup.count()
        if not n:
            return
        step = {KEY_UP: -1, KEY_DOWN: 1, KEY_PAGE_UP: -MAX_ROWS, KEY_PAGE_DOWN: MAX_ROWS}[key]
        row = max(0, min(n - 1, self.popup.currentRow() + step))
        self.popup.setCurrentRow(row)

    def hide(self):
        self._range = None
        try:
            self.popup.hide()
        except RuntimeError:   # deleted along with its prompt box
            pass

    # ---- suggestions
    def update(self, open_=True, force=False):
        if not enabled() or self._has_selection():
            self.hide()
            return
        index = tags.index()
        if index is None:          # still loading (or no data file)
            self.hide()
            return
        found = tags.find(index, self._text(), self._cursor_pos(), force)
        matches = found[2] if found else []
        if not matches or (len(matches) == 1 and matches[0].kind == tags.EXACT):
            self.hide()
            return
        if not self.popup.isVisible() and not open_:
            return
        self._range = found[:2]
        self._fill(matches)
        self._place()

    def _fill(self, matches):
        self.popup.clear()
        for m in matches:
            label = f"{m.alias} → {m.name}" if m.alias else m.name
            count = tags.format_count(m.count)
            item = QListWidgetItem(f"{label}   {count}" if count else label)
            item.setData(USER_ROLE, m.name)
            item.setForeground(QColor(CATEGORY_COLOURS.get(m.category, "#888888")))
            item.setToolTip(CATEGORY_NAMES.get(m.category, m.category.capitalize() + " tag")
                            + (f", {m.count:,} Danbooru posts" if m.count else ""))
            self.popup.addItem(item)
        self.popup.setCurrentRow(0)

    def _place(self):
        rows = min(self.popup.count(), MAX_ROWS)
        row_h = max(self.popup.sizeHintForRow(0), 12)
        width = min(max(self.popup.sizeHintForColumn(0) + 28, 160), 380)
        height = rows * row_h + 2 * self.popup.frameWidth() + 2
        pos = self._anchor()
        try:
            screen = self.edit.screen().availableGeometry()
            if pos.y() + height > screen.bottom():
                top = self.edit.mapToGlobal(QPoint(0, 0)).y() if not self.plain else \
                    self.edit.viewport().mapToGlobal(self.edit.cursorRect().topLeft()).y()
                pos.setY(top - height - 2)
            if pos.x() + width > screen.right():
                pos.setX(max(screen.left(), screen.right() - width))
        except Exception:
            pass
        self.popup.setGeometry(pos.x(), pos.y(), width, height)
        self.popup.show()
        self.popup.raise_()

    def accept(self, row):
        item = self.popup.item(row) if row >= 0 else None
        if item is None:
            self.hide()
            return
        tag = item.data(USER_ROLE)
        text = self._text()
        found = self._range or tags.find(tags.index(), text, self._cursor_pos())
        if not found:
            self.hide()
            return
        start, end = found[:2]
        new_text, cursor = tags.apply_completion(text, start, end, tag)
        # replace only the typed tag (and trailing spaces at the very end)
        replace_end = len(text) if text[end:].strip(" \t") == "" else end
        self.hide()
        self._replace(start, replace_end, new_text[start:cursor])
