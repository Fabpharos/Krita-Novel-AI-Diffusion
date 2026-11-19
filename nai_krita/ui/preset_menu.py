"""The Presets menu for a prompt box: NovelAI's defaults for the current model, the
user's saved presets, and saving/deleting them. Opened from a "Presets" button or
the box's right-click menu; built fresh each time so it follows the style's model.
"""
from ..qt import (QObject, QMenu, QInputDialog, QMessageBox, CUSTOM_CONTEXT_MENU, ECHO_NORMAL,
                  MB_YES, MB_NO, SELECT_DOCUMENT)
from .. import models, presets


# Dialogs as module functions so tests can replace them.
def ask_name(parent, title, label, text=""):
    name, ok = QInputDialog.getText(parent, title, label, ECHO_NORMAL, text)
    return name.strip() if ok else ""


def confirm(parent, title, text):
    return QMessageBox.question(parent, title, text, MB_YES | MB_NO, MB_NO) == MB_YES


class PresetMenu(QObject):
    def __init__(self, box, kind, get_model, get_settings, save_settings, parent=None):
        """box: QPlainTextEdit; kind: presets.POSITIVE or NEGATIVE; get_model() → the
        current style's model id; save_settings() persists the settings file."""
        super().__init__(parent or box)
        self.box, self.kind = box, kind
        self._model, self._settings, self._save = get_model, get_settings, save_settings
        box.setContextMenuPolicy(CUSTOM_CONTEXT_MENU)
        box.customContextMenuRequested.connect(self._context_menu)

    # ---- menus
    def menu(self, parent=None) -> QMenu:
        m = QMenu(parent or self.box)
        m.setToolTipsVisible(True)
        self.fill(m)
        return m

    def attach_button(self, button):
        """Give a tool button this menu, rebuilt every time it opens."""
        m = QMenu(button)
        m.setToolTipsVisible(True)
        m.aboutToShow.connect(lambda: (m.clear(), self.fill(m)))
        button.setMenu(m)

    def fill(self, m: QMenu):
        model = self._model()
        label = models.get(model).label if model in models.BY_ID else model
        store = self._store()
        replace = self._settings().preset_replace
        if self.kind == presets.POSITIVE:
            q = presets.nai_quality(model)
            a = m.addAction(f"NovelAI quality tags ({label})",
                            lambda: self.load(q, at_start=False))
            a.setToolTip(q)
            a.setEnabled(bool(q))
        else:
            sub = m.addMenu(f"NovelAI undesired content ({label})")
            sub.setToolTipsVisible(True)
            ucs = presets.nai_uc_presets(model)
            for name, text in ucs.items():
                a = sub.addAction(name, lambda t=text: self.load(t, at_start=True))
                a.setToolTip(text)
            sub.setEnabled(bool(ucs))
        m.addSeparator()
        names = presets.sorted_names(store, self.kind)
        if not names:
            a = m.addAction("No saved presets yet")
            a.setEnabled(False)
        for name in names:
            text = store[self.kind][name]
            a = m.addAction(name, lambda t=text: self.load(t, at_start=False))
            a.setToolTip(text)
        m.addSeparator()
        r = m.addAction("Replace the text instead of adding",
                        lambda: self._set_replace(not replace))
        r.setCheckable(True)
        r.setChecked(replace)
        r.setToolTip("Off: only tags that aren't in the box yet are added.\n"
                     "On: the box's text is replaced by the preset.")
        m.addAction("Save this text as a preset…", self.save_current)
        if names:
            d = m.addMenu("Delete preset")
            for name in names:
                d.addAction(name, lambda n=name: self.delete(n))

    def _context_menu(self, pos):
        m = self.box.createStandardContextMenu()
        m.addSeparator()
        sub = m.addMenu("Presets")
        sub.setToolTipsVisible(True)
        self.fill(sub)
        run = getattr(m, "exec", None) or m.exec_
        run(self.box.mapToGlobal(pos))

    # ---- actions
    def _store(self):
        s = self._settings()
        s.tag_presets = presets.normalize_store(s.tag_presets)
        return s.tag_presets

    def _set_replace(self, on):
        self._settings().preset_replace = bool(on)
        self._save()

    def load(self, text: str, at_start: bool = False):
        """Put a preset into the box: add the missing tags, or replace the text.
        One undo step either way."""
        current = self.box.toPlainText()
        if self._settings().preset_replace:
            new = text
        else:
            new = presets.merge(current, text, at_start=at_start)
        if new == current:
            return
        c = self.box.textCursor()
        c.select(SELECT_DOCUMENT)
        c.insertText(new)
        self.box.setTextCursor(c)

    def save_current(self):
        text = self.box.toPlainText().strip(" ,\n")
        what = "prompt" if self.kind == presets.POSITIVE else "undesired content"
        if not text:
            QMessageBox.information(self.box, "Save preset", f"The {what} box is empty.")
            return
        name = ask_name(self.box, "Save preset", f"Name for this {what} preset:")
        if not name:
            return
        store = self._store()
        if name in store[self.kind] and not confirm(
                self.box, "Save preset", f"Replace the preset “{name}”?"):
            return
        store[self.kind][name] = text
        self._save()

    def delete(self, name):
        store = self._store()
        if name not in store[self.kind]:
            return
        if not confirm(self.box, "Delete preset", f"Delete the preset “{name}”?"):
            return
        del store[self.kind][name]
        self._save()
