"""Character prompt list: the plugin's version of Krita AI Diffusion's regions."""
from . import tag_completer
from ..qt import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                  QPlainTextEdit, QCheckBox, QToolButton, QPushButton, QMenu, QWidgetAction,
                  QInputDialog, QDialog, QDialogButtonBox, QMessageBox, QPainter, QColor, QPen,
                  QBrush, QRect, QSize, pyqtSignal, STYLED_PANEL, INSTANT_POPUP, SP_IGNORED,
                  SP_PREFERRED, DBB_OK, DBB_CANCEL, ANTIALIAS, ALIGN_CENTER, NO_PEN, CROSS_CURSOR,
                  event_pos, exec_dialog)
from .. import characters as chars
from ..request import Character, GRID, clamp01, snap

HIGHLIGHT = "QFrame#charRow { border: 2px solid palette(highlight); border-radius: 3px; }"


class PositionPad(QWidget):
    """Click or drag to place a character on the image.

    The pad has the generation area's shape. Free mode (V5) keeps the exact
    point; grid mode (V4/V4.5) snaps to the centre of a 5x5 cell and draws the
    grid. Other characters show as grey dots for reference.
    """
    moved = pyqtSignal(object)   # (x, y) in 0..1

    WIDTH = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pos_ = None
        self.free = True
        self.others = []          # [(pos, label)]
        self.setCursor(CROSS_CURSOR)
        self.set_aspect(1, 1)

    def set_aspect(self, w, h):
        height = int(max(110, min(320, self.WIDTH * h / max(1, w))))
        self.setFixedSize(QSize(self.WIDTH, height))
        self.update()

    def _from_mouse(self, event):
        x, y = event_pos(event)
        pos = (clamp01(x / self.width()), clamp01(y / self.height()))
        if not self.free:
            pos = (snap(pos[0]), snap(pos[1]))
        if pos != self.pos_:
            self.pos_ = pos
            self.update()
            self.moved.emit(pos)

    def mousePressEvent(self, event):
        self._from_mouse(event)

    def mouseMoveEvent(self, event):
        self._from_mouse(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(ANTIALIAS)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(128, 128, 128, 45))
        p.setPen(QPen(QColor(128, 128, 128, 70 if self.free else 160)))
        for i in range(1, GRID):
            p.drawLine(int(w * i / GRID), 0, int(w * i / GRID), h)
            p.drawLine(0, int(h * i / GRID), w, int(h * i / GRID))
        p.drawRect(0, 0, w - 1, h - 1)
        for pos, label in self.others:
            self._dot(p, pos, QColor(150, 150, 150, 200), label)
        if self.pos_ is None:
            p.setPen(QColor(128, 128, 128, 220))
            p.drawText(QRect(0, 0, w, h), ALIGN_CENTER, "Click to place")
        else:
            self._dot(p, self.pos_, self.palette().highlight().color(), "")
        p.end()

    def _dot(self, p, pos, color, label):
        x, y = pos[0] * self.width(), pos[1] * self.height()
        p.setPen(NO_PEN)
        p.setBrush(QBrush(color))
        p.drawEllipse(int(x) - 6, int(y) - 6, 12, 12)
        if label:
            p.setPen(color)
            p.drawText(int(x) + 8, int(y) + 4, label[:12])


class PositionPopup(QWidget):
    """Pad plus a 'Centre' button, shown in the row's menu."""
    picked = pyqtSignal(object)   # (x, y)

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.pad = PositionPad()
        self.pad.moved.connect(self.picked.emit)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        v.addWidget(self.pad)
        v.addWidget(self.hint)
        centre = QPushButton("Centre")
        centre.clicked.connect(lambda: self.picked.emit((0.5, 0.5)))
        v.addWidget(centre)


class CharacterRow(QFrame):
    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)
    link_requested = pyqtSignal(object)

    def __init__(self, character: Character, parent=None):
        super().__init__(parent)
        self.setObjectName("charRow")
        self.setFrameShape(STYLED_PANEL)
        self.pos = character.pos
        self.cid = character.cid or chars.new_cid()
        self.free = True            # model takes free positions (V5)
        self.positioned = False     # list-level "Set positions" switch
        self.context = lambda row: ([], (1, 1))   # set by the list: (others, aspect)
        self.layer_id = character.layer_id
        self.layer_name = ""
        self.layer_pos = None       # live position from the linked layer, for display
        self.layer_missing = False

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(2)
        top = QHBoxLayout()
        self.enabled = QCheckBox()
        self.enabled.setChecked(character.enabled)
        self.enabled.setToolTip("Include this character")
        self.name = QLineEdit(character.name)
        self.name.setPlaceholderText("Name")
        self.name.setToolTip("Your label for this character (not sent to NovelAI)")
        self.name.setSizePolicy(SP_IGNORED, SP_PREFERRED)
        self.position = QToolButton()
        self.position.setPopupMode(INSTANT_POPUP)
        menu = QMenu(self.position)
        self.popup = PositionPopup(menu)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self.popup)
        menu.addAction(action)
        menu.aboutToShow.connect(self._prepare_popup)
        self.popup.picked.connect(self._picked)
        self.position.setMenu(menu)
        self.link = QToolButton()
        self.link.clicked.connect(self._link_clicked)
        remove = QToolButton()
        remove.setText("✕")
        remove.setToolTip("Remove this character from the page (every panel).\n"
                          "To leave them out of one panel, untick them instead.")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        for w in (self.enabled, self.name, self.position, self.link, remove):
            top.addWidget(w, 1 if w is self.name else 0)
        v.addLayout(top)

        self.prompt = QPlainTextEdit(character.prompt)
        self.prompt.setPlaceholderText("Character prompt, e.g. girl, red hair, school uniform")
        self.prompt.setMaximumHeight(56)
        v.addWidget(self.prompt)
        self.uc = QLineEdit(character.uc)
        self.uc.setPlaceholderText("Undesired for this character (optional)")
        v.addWidget(self.uc)
        tag_completer.attach(self.prompt)
        tag_completer.attach(self.uc)

        for sig in (self.enabled.toggled, self.name.textChanged, self.prompt.textChanged,
                    self.uc.textChanged):
            sig.connect(lambda *_: self.changed.emit())
        self._refresh()

    # ---- state
    def set_pos(self, pos):
        """Placing a character by hand replaces any layer link."""
        self.pos = pos
        self.layer_id, self.layer_name, self.layer_pos = "", "", None
        self._refresh()
        self.changed.emit()

    def set_free(self, free: bool):
        self.free = free
        self._refresh()

    def set_positioned(self, on: bool):
        self.positioned = on
        self.position.setVisible(on)
        self.link.setVisible(on)

    def _prepare_popup(self):
        others, aspect = self.context(self)
        pad = self.popup.pad
        pad.free = self.free
        pad.others = others
        pad.pos_ = self.shown_pos()
        pad.set_aspect(*aspect)
        self.popup.hint.setText(
            "Click or drag anywhere." if self.free else
            "This model uses NovelAI's 5×5 grid; positions snap to cell centres.")

    def _picked(self, pos):
        self.popup.pad.pos_ = pos
        self.popup.pad.update()
        self.set_pos(pos)

    def set_link(self, layer_id: str, layer_name: str):
        self.layer_id, self.layer_name = layer_id, layer_name
        self.layer_missing = False
        self._refresh()
        self.changed.emit()

    def set_layer_state(self, name: str, pos, missing: bool):
        if (name, pos, missing) != (self.layer_name, self.layer_pos, self.layer_missing):
            self.layer_name, self.layer_pos, self.layer_missing = name, pos, missing
            self._refresh()

    def shown_pos(self):
        return self.layer_pos if self.layer_id else self.pos

    def character(self) -> Character:
        """With positions off, the character has no position and no link."""
        on = self.positioned
        return Character(prompt=self.prompt.toPlainText().strip(), uc=self.uc.text().strip(),
                         name=self.name.text().strip(), enabled=self.enabled.isChecked(),
                         pos=self.pos if on else None, layer_id=self.layer_id if on else "",
                         cid=self.cid)

    def set_highlight(self, on: bool):
        self.setStyleSheet(HIGHLIGHT if on else "")

    # ---- display
    def _refresh(self):
        if self.layer_id:
            shown = "?" if self.layer_missing else \
                chars.pos_label(self.layer_pos, self.free) if self.layer_pos else "…"
            self.position.setText(f"🔗 {shown}")
            self.position.setToolTip(
                f"Linked to layer “{self.layer_name}”: position follows the centre of its "
                "content." + (" The layer no longer exists." if self.layer_missing else "")
                + "\nPlace it by hand to unlink.")
            self.link.setText("🔓")
            self.link.setToolTip("Unlink layer")
        else:
            self.position.setText(chars.pos_label(self.pos, self.free))
            self.position.setToolTip(
                "Where this character goes: click to place it anywhere" if self.free else
                "Where this character goes (snapped to NovelAI's 5×5 grid on this model)")
            self.link.setText("🔗")
            self.link.setToolTip("Link the active layer: position follows its content")

    def _link_clicked(self):
        if self.layer_id:
            self.set_pos(self.pos)   # unlink, keep the last manual position
        else:
            self.link_requested.emit(self)


class CharacterList(QWidget):
    """Rows of character prompts with add, link, saved sets and a per-model limit."""
    changed = pyqtSignal()

    def __init__(self, get_active_layer, get_settings, save_settings, parent=None):
        super().__init__(parent)
        self._get_active_layer = get_active_layer   # () -> (uid str, name) or None
        self._get_settings = get_settings
        self._save_settings = save_settings
        self.limit = 6
        self.free = True
        self.aspect = (1, 1)   # generation area w, h; set by the docker
        self.positioned = False
        self.rows = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.title = QLabel("Characters")
        add = QToolButton()
        add.setText("+")
        add.setToolTip("Add a character")
        add.clicked.connect(lambda: self.add(Character(prompt="")))
        add_layer = QToolButton()
        add_layer.setText("+🔗")
        add_layer.setToolTip("Add a character linked to the active layer")
        add_layer.clicked.connect(self._add_linked)
        self.sets = QToolButton()
        self.sets.setText("Sets")
        self.sets.setPopupMode(INSTANT_POPUP)
        self.sets.setToolTip("Save, load or import character sets")
        self.sets_menu = QMenu(self.sets)
        self.sets_menu.aboutToShow.connect(self._fill_sets_menu)
        self.sets.setMenu(self.sets_menu)
        self.add_buttons = (add, add_layer)
        head.addWidget(self.title, 1)
        for w in (add, add_layer, self.sets):
            head.addWidget(w)
        v.addLayout(head)
        self.positions = QCheckBox("Set positions")
        self.positions.setToolTip(
            "Off: NovelAI places every character.\n"
            "On: you place every character (NovelAI can't mix the two).")
        self.positions.toggled.connect(self.set_positioned)
        v.addWidget(self.positions)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #d08020;")
        self.warning.setSizePolicy(SP_IGNORED, SP_PREFERRED)
        self.warning.setVisible(False)
        v.addWidget(self.warning)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(3)
        v.addLayout(self.rows_box)
        self._update_limit_ui()

    # ---- rows
    def add(self, character: Character, layer_name: str = "", force: bool = False):
        """Add a row. The Add buttons stop at the model's limit; loading a panel
        (force) keeps every cast member, and only enabled ones count."""
        if len(self.rows) >= self.limit and not force:
            return None
        row = CharacterRow(character, self)
        if character.layer_id:
            row.layer_name = layer_name
            row._refresh()
        row.set_free(self.free)
        if self.positioned and row.pos is None:
            row.pos = chars.default_position(len(self.rows), len(self.rows) + 1)
            row._refresh()
        row.set_positioned(self.positioned)
        row.context = self._pad_context
        row.changed.connect(self._row_changed)
        row.remove_requested.connect(self.remove)
        row.link_requested.connect(self._link_row)
        self.rows.append(row)
        self.rows_box.addWidget(row)
        self._row_changed()
        return row

    def remove(self, row):
        self.rows.remove(row)
        self.rows_box.removeWidget(row)
        row.deleteLater()
        self._row_changed()

    def clear(self):
        for row in list(self.rows):
            self.remove(row)

    def characters(self):
        return [r.character() for r in self.rows]

    def characters_raw(self):
        """Rows with their stored positions and links even while positions are off
        (for saving per-panel state)."""
        out = []
        for r in self.rows:
            c = r.character()
            c.pos, c.layer_id = r.pos, r.layer_id
            out.append(c)
        return out

    def set_characters(self, items, positioned=None):
        """Replace all rows. Unless `positioned` is given, positions switch on if any
        item has a position; the rest get default spots, since NovelAI positions all
        characters or none."""
        self.clear()
        on = any(c.pos is not None for c in items) if positioned is None else positioned
        if on:
            chars.fill_positions(items)
        self.positions.blockSignals(True)
        self.positions.setChecked(on)
        self.positions.blockSignals(False)
        self.positioned = on
        for c in items:
            self.add(c, force=True)

    def set_positioned(self, on: bool):
        """List-level switch. Turning it on gives unplaced characters default spots
        spread across the image."""
        self.positioned = on
        if self.positions.isChecked() != on:
            self.positions.setChecked(on)
            return
        n = len(self.rows)
        for i, r in enumerate(self.rows):
            if on and r.pos is None:
                r.pos = chars.default_position(i, n)
                r._refresh()
            r.set_positioned(on)
        self._row_changed()

    def _add_linked(self):
        active = self._get_active_layer()
        if not active:
            QMessageBox.information(self, "No active layer", "Select a layer first.")
            return
        if not self.positioned:
            self.set_positioned(True)
        row = self.add(Character(prompt=""))
        if row:
            row.set_link(*active)

    def _link_row(self, row):
        active = self._get_active_layer()
        if not active:
            QMessageBox.information(self, "No active layer", "Select a layer first.")
            return
        row.set_link(*active)

    def _row_changed(self):
        self._update_limit_ui()
        self.changed.emit()

    # ---- limits
    def _pad_context(self, row):
        others = []
        for i, r in enumerate(self.rows):
            if r is not row and r.shown_pos() is not None:
                others.append((r.shown_pos(), r.name.text() or f"#{i + 1}"))
        return others, self.aspect

    def set_limit(self, limit: int, model_label: str = "", free: bool = True):
        self.limit = limit
        self.free = free
        for r in self.rows:
            r.set_free(free)
        self.setVisible(limit > 0)
        self._model_label = model_label
        self._update_limit_ui()

    def active_count(self):
        return sum(1 for r in self.rows if r.enabled.isChecked() and r.prompt.toPlainText().strip())

    def _update_limit_ui(self):
        n = len(self.rows)
        for b in self.add_buttons:
            b.setEnabled(n < self.limit)
        self.title.setText(f"Characters ({n}/{self.limit})" if n else "Characters")
        over = self.active_count() - self.limit
        label = getattr(self, "_model_label", "") or "this model"
        self.warning.setVisible(over > 0)
        if over > 0:
            self.warning.setText(f"{label} takes at most {self.limit} characters; "
                                 f"disable or remove {over}.")

    # ---- highlight and linked positions (called by the docker's poll)
    def sync_layers(self, active_id: str, resolve):
        """resolve(layer_id) -> (name, pos or None, missing)."""
        for r in self.rows:
            r.set_highlight(bool(r.layer_id) and r.layer_id == active_id)
            if r.layer_id:
                r.set_layer_state(*resolve(r.layer_id))

    # ---- saved sets
    def _fill_sets_menu(self):
        m = self.sets_menu
        m.clear()
        m.addAction("Save current as…", self._save_set)
        m.addAction("Import labelled lines…", self._import_text)
        saved = self._get_settings().saved_characters
        if saved:
            m.addSeparator()
            for name in sorted(saved, key=str.lower):
                m.addAction(f"Load “{name}”", lambda n=name: self._load_set(n))
            delete = m.addMenu("Delete")
            for name in sorted(saved, key=str.lower):
                delete.addAction(name, lambda n=name: self._delete_set(n))

    def _save_set(self):
        items = [c for c in self.characters() if c.prompt]
        if not items:
            QMessageBox.information(self, "Nothing to save", "Add a character with a prompt first.")
            return
        name, ok = QInputDialog.getText(self, "Save character set", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        s = self._get_settings()
        s.saved_characters[name] = [chars.to_dict(c) for c in items]
        self._save_settings()

    def _load_set(self, name):
        data = self._get_settings().saved_characters.get(name, [])
        if self.rows and not _confirm(self, "Replace characters",
                                      f"Replace the current characters with “{name}”?"):
            return
        self.set_characters([chars.from_dict(d) for d in data])

    def _delete_set(self, name):
        if _confirm(self, "Delete character set", f"Delete the saved set “{name}”?"):
            self._get_settings().saved_characters.pop(name, None)
            self._save_settings()

    def _import_text(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Import labelled lines")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("One character per line, optionally labelled:\n"
                           "Mika: 1girl, red hair, school uniform\nRen: 1boy, glasses"))
        text = QPlainTextEdit()
        v.addWidget(text)
        buttons = QDialogButtonBox(DBB_OK | DBB_CANCEL)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        v.addWidget(buttons)
        if not exec_dialog(dlg):
            return
        items = chars.parse_labelled(text.toPlainText())
        room = self.limit - len(self.rows)
        for c in items[:room]:
            self.add(c)
        if len(items) > room:
            QMessageBox.information(self, "Character limit",
                                    f"Only {max(room, 0)} of {len(items)} lines were added "
                                    f"(limit {self.limit}).")


def _confirm(parent, title, text) -> bool:
    from ..qt import MB_OK, MB_CANCEL
    return QMessageBox.question(parent, title, text, MB_OK | MB_CANCEL, MB_CANCEL) == MB_OK
