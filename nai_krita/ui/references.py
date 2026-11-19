"""Reference list: Vibe Transfer and Character Reference rows.

Like Krita AI Diffusion's reference control layers, each row takes its image from a
layer (by uniqueId) or a file. Vibe rows also accept NovelAI vibe files
(.naiv4vibe / .naiv4vibebundle), whose stored encodings are used directly.
"""
import os

from ..qt import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QToolButton,
                  QComboBox, QSlider, QFileDialog, QMessageBox, pyqtSignal, STYLED_PANEL,
                  HORIZONTAL, SP_IGNORED, SP_PREFERRED)
from .. import models, vibes
from ..request import CHAR_REF_KINDS

VIBE, CHAR = "vibe", "char"
KIND_LABELS = {"character": "Character", "style": "Style", "character&style": "Character & style"}
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
VIBE_FILTER = "NovelAI vibes (*.naiv4vibe *.naiv4vibebundle)"


def _slider(value, tip):
    s = QSlider(HORIZONTAL)
    s.setRange(1, 100)
    s.setValue(int(round(value * 100)))
    s.setToolTip(tip)
    return s


class ReferenceRow(QFrame):
    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(self, kind: str, state: dict, get_active_layer, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._get_active_layer = get_active_layer
        self.setFrameShape(STYLED_PANEL)
        self.source = dict(state.get("source") or {})   # {"type": layer|image|vibefile, ...}
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(2)

        top = QHBoxLayout()
        self.enabled = QCheckBox("Vibe" if kind == VIBE else "Char ref")
        self.enabled.setChecked(bool(state.get("enabled", True)))
        self.label = QLabel()
        self.label.setSizePolicy(SP_IGNORED, SP_PREFERRED)
        from_layer = QToolButton()
        from_layer.setText("Layer")
        from_layer.setToolTip("Use the active layer's content")
        from_layer.clicked.connect(self._pick_layer)
        from_file = QToolButton()
        from_file.setText("File…")
        from_file.setToolTip("Use an image file" + (" or a NovelAI vibe file" if kind == VIBE else ""))
        from_file.clicked.connect(self._pick_file)
        remove = QToolButton()
        remove.setText("✕")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(self.enabled)
        top.addWidget(self.label, 1)
        for w in (from_layer, from_file, remove):
            top.addWidget(w)
        v.addLayout(top)

        row = QHBoxLayout()
        self.strength = _slider(state.get("strength", 0.6 if kind == VIBE else 1.0),
                                "How strongly the reference steers the image")
        row.addWidget(QLabel("Strength"))
        row.addWidget(self.strength, 1)
        v.addLayout(row)
        if kind == VIBE:
            row2 = QHBoxLayout()
            self.info = _slider(state.get("information_extracted", 1.0),
                                "How much of the image to take in (lower: mostly style and "
                                "colour). Changing it needs a new encoding (about 2 Anlas) "
                                "unless the vibe file already has one at that value.")
            row2.addWidget(QLabel("Info"))
            row2.addWidget(self.info, 1)
            v.addLayout(row2)
            self.info.valueChanged.connect(lambda *_: self.changed.emit())
        else:
            row2 = QHBoxLayout()
            self.type = QComboBox()
            for k in CHAR_REF_KINDS:
                self.type.addItem(KIND_LABELS[k], k)
            idx = self.type.findData(state.get("kind", "character&style"))
            self.type.setCurrentIndex(max(0, idx))
            self.fidelity = _slider(state.get("fidelity", 1.0),
                                    "How closely to match the reference (lower: looser)")
            row2.addWidget(self.type)
            row2.addWidget(QLabel("Fidelity"))
            row2.addWidget(self.fidelity, 1)
            v.addLayout(row2)
            self.type.currentIndexChanged.connect(lambda *_: self.changed.emit())
            self.fidelity.valueChanged.connect(lambda *_: self.changed.emit())
        self.enabled.toggled.connect(lambda *_: self.changed.emit())
        self.strength.valueChanged.connect(lambda *_: self.changed.emit())
        self._refresh_label()

    # ---- sources
    def _pick_layer(self):
        active = self._get_active_layer()
        if not active:
            QMessageBox.information(self, "No active layer", "Select a layer first.")
            return
        self.source = {"type": "layer", "id": active[0], "name": active[1]}
        self._refresh_label()
        self.changed.emit()

    def _pick_file(self):
        filt = f"{VIBE_FILTER};;{IMAGE_FILTER}" if self.kind == VIBE else IMAGE_FILTER
        path, _ = QFileDialog.getOpenFileName(self, "Reference", "", filt)
        if not path:
            return
        if vibes.is_vibe_file(path):
            try:
                entries = vibes.load_vibe_file(path)
            except Exception as e:
                QMessageBox.warning(self, "Vibe file", f"Could not read {path}:\n{e}")
                return
            e = entries[0]
            self.source = {"type": "vibefile", "path": path, "index": 0, "name": e.name}
            self.strength.setValue(int(round(e.strength * 100)))
            self.info.setValue(int(round(e.information_extracted * 100)))
        else:
            self.source = {"type": "image", "path": path, "name": os.path.basename(path)}
        self._refresh_label()
        self.changed.emit()

    def _refresh_label(self):
        src = self.source
        if not src:
            text = "no source: pick a layer or file"
        elif src["type"] == "layer":
            text = f"layer “{src.get('name', '?')}”"
        elif src["type"] == "vibefile":
            text = f"vibe “{src.get('name', '?')}”"
        else:
            text = src.get("name", "image")
        self.label.setText(text)
        self.label.setToolTip(src.get("path", "") if src else "")

    def state(self) -> dict:
        d = {"kind_row": self.kind, "enabled": self.enabled.isChecked(), "source": self.source,
             "strength": self.strength.value() / 100}
        if self.kind == VIBE:
            d["information_extracted"] = self.info.value() / 100
        else:
            d["kind"] = self.type.currentData()
            d["fidelity"] = self.fidelity.value() / 100
        return d


class ReferenceList(QWidget):
    changed = pyqtSignal()

    def __init__(self, get_active_layer, parent=None):
        super().__init__(parent)
        self._get_active_layer = get_active_layer
        self.rows = []
        self._loading = False
        self.model_id = models.DEFAULT_MODEL
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("References"), 1)
        add_vibe = QToolButton()
        add_vibe.setText("+ Vibe")
        add_vibe.setToolTip("Vibe Transfer: steer style and mood from an image or vibe file "
                            "(V4/V4.5; not together with character references)")
        add_vibe.clicked.connect(lambda: self.add(VIBE, {}))
        add_char = QToolButton()
        add_char.setText("+ Char ref")
        add_char.setToolTip("Character Reference: keep a character's look from an image "
                            "(V4.5 only; 5 Anlas per image, Opus included; not together "
                            "with vibes)")
        add_char.clicked.connect(lambda: self.add(CHAR, {}))
        head.addWidget(add_vibe)
        head.addWidget(add_char)
        v.addLayout(head)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: #d08020;")
        self.notice.setSizePolicy(SP_IGNORED, SP_PREFERRED)
        self.notice.setVisible(False)
        v.addWidget(self.notice)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(3)
        v.addLayout(self.rows_box)

    def add(self, kind, state):
        row = ReferenceRow(kind, state, self._get_active_layer, self)
        row.changed.connect(self._changed)
        row.remove_requested.connect(self.remove)
        row.enabled.toggled.connect(lambda on, r=row: on and self._exclusive(r))
        self.rows.append(row)
        self.rows_box.addWidget(row)
        if row.enabled.isChecked() and not self._loading:
            self._exclusive(row)
        self._changed()
        return row

    def _exclusive(self, row):
        """NovelAI can't combine Vibe Transfer with Character Reference, so turning a row
        of one kind on turns every row of the other kind off (as NovelAI's own app does)."""
        for r in self.rows:
            if r.kind != row.kind and r.enabled.isChecked():
                r.enabled.blockSignals(True)
                r.enabled.setChecked(False)
                r.enabled.blockSignals(False)
        if not self._loading:
            self._changed()

    def remove(self, row):
        self.rows.remove(row)
        self.rows_box.removeWidget(row)
        row.deleteLater()
        self._changed()

    def set_state(self, items):
        for r in list(self.rows):
            self.remove(r)
        self._loading = True
        try:
            for d in items or []:
                if d.get("kind_row") in (VIBE, CHAR):
                    self.add(d["kind_row"], d)
        finally:
            self._loading = False
        # Saved before the exclusivity rule: keep the character references.
        chars = [r for r in self.rows if r.kind == CHAR and r.enabled.isChecked()]
        if chars:
            self._exclusive(chars[0])
            self._changed()

    def state(self):
        return [r.state() for r in self.rows]

    def active(self, kind):
        """Enabled rows of a kind that have a source and that the model can use."""
        if kind == VIBE and not vibes.supports_vibes(self.model_id):
            return []
        if kind == CHAR and models.get(self.model_id).family != models.V4_5:
            return []
        return [r for r in self.rows if r.kind == kind and r.enabled.isChecked() and r.source]

    def set_model(self, model_id):
        self.model_id = model_id
        self._changed(emit=False)

    def _changed(self, emit=True):
        info = models.get(self.model_id)
        vibe_ok = vibes.supports_vibes(self.model_id)
        char_ok = info.family == models.V4_5
        problems = []
        if not vibe_ok and any(r.kind == VIBE and r.enabled.isChecked() for r in self.rows):
            problems.append(f"Vibe Transfer works on V4/V4.5 only; ignored on {info.label}.")
        if not char_ok and any(r.kind == CHAR and r.enabled.isChecked() for r in self.rows):
            problems.append(f"Character Reference works on V4.5 only; ignored on {info.label}.")
        if {r.kind for r in self.rows} == {VIBE, CHAR}:
            problems.append("Vibes and character references can't be used together: ticking "
                            "one kind unticks the other.")
        for r in self.rows:
            r.setEnabled((vibe_ok if r.kind == VIBE else char_ok) or True)
            r.setStyleSheet("" if (vibe_ok if r.kind == VIBE else char_ok)
                            else "QFrame { color: palette(mid); }")
        self.notice.setText(" ".join(problems))
        self.notice.setVisible(bool(problems))
        if emit:
            self.changed.emit()
