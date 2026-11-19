"""The "NovelAI" docker.

Phase 1: txt2img, preview/apply, Anlas guard.
Phase 2: image count with Queue and Batch modes, cancel, grouped history.
Phase 3: character prompts with positions, layer links and saved sets.
Phase 4: manga panels: per-panel prompt and characters, clipped placement, fill empty panels.
Phase 5: Refine (strength below 100%) and Fill (inpaint the selection), blended at the edges.
Phase 6: Vibe Transfer and Character Reference rows; 2x upscale of a result.
Phase 7: import settings from a NovelAI PNG (file or history result).
Phase 8: Danbooru tag suggestions in every prompt box.
Phase 10: tag presets (saved, plus NovelAI's quality tags and UC presets).
V5 transparent backgrounds for new images.
"""
import base64
import dataclasses
import logging
import os
from collections import namedtuple

from krita import DockWidget  # type: ignore

from ..qt import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
                  QComboBox, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QProgressBar,
                  QGroupBox, QFormLayout, QMessageBox, QScrollArea, QButtonGroup, QSizePolicy,
                  QSlider, HORIZONTAL,
                  TEXT_SELECTABLE, SCROLL_OFF, NO_FRAME, COMBO_SHRINK, SP_IGNORED, SP_PREFERRED,
                  QTimer, WheelGuard, MB_OK, MB_CANCEL, exec_dialog, QMenu, QFileDialog,
                  INSTANT_POPUP, FMT_ARGB32, image_from_png, QFrame, STYLED_PANEL)
from .. import characters as chars
from .. import cost, document, jobs, models, panels, regions, request, resolution
from .character_list import CharacterList
from .references import ReferenceList, VIBE, CHAR
from .. import vibes, metadata
import json
import hashlib
from ..request import Character, VibeRef, CharRef
from .dialogs import SettingsDialog, confirm_paid, confirm_import
from .widgets import HistoryEntry, Thumb
from . import tag_completer
from .preset_menu import PresetMenu
from .. import presets

log = logging.getLogger("nai_krita")

SIZE_FREE, SIZE_LARGE, SIZE_CUSTOM = "Auto ≤1 MP (free)", "Auto ≤1.5 MP (paid)", "Custom"
MODE_QUEUE, MODE_BATCH = "Queue", "Batch"
GENERATE, REFINE, FILL, REFINE_SEL = "generate", "refine", "fill", "refine selection"
KIND_LABELS = {GENERATE: "Generate", REFINE: "Refine", FILL: "Fill", REFINE_SEL: "Refine selection"}
CONTEXT_WHOLE, CONTEXT_AROUND = "Context: whole panel/page", "Context: around selection"
MAX_HISTORY = 30
TRANSPARENT_TAG = "transparent background"
Refs = namedtuple("Refs", "vibes sources char_refs encodes")
UPSCALE_ESTIMATE = 1   # Anlas per upscale (measured live 2026-11-13, Opus, 832x1216 source)
POLL_MS = 600   # active-layer highlight and linked-layer positions
AUTOSAVE_TICKS = 5   # store prompts/panel state every ~3 s


def _combo(items):
    c = QComboBox()
    c.addItems(items)
    c.setSizeAdjustPolicy(COMBO_SHRINK)
    c.setMinimumContentsLength(6)
    return c


def _wrap_label(text=""):
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(TEXT_SELECTABLE)
    lab.setSizePolicy(SP_IGNORED, SP_PREFERRED)  # never forces the docker wider
    return lab


class NovelAIDocker(DockWidget):
    def __init__(self):
        super().__init__()
        from .. import extension
        self.ext = extension.instance()
        self.setWindowTitle("NovelAI")
        self.layers = document.ResultLayers()
        self.entries = []          # HistoryEntry, newest first
        self._session = {}         # "don't ask again this session"
        self._run_total = 0        # requests submitted since the queue was last idle
        self._run_done = 0
        self._doc_key = None       # which document the state below belongs to
        self._doc_state = {}       # {"prompt", "negative", "contexts": {key: {...}}}
        self._ctx_key = panels.PAGE_KEY   # "page" or a panel label like "P3"
        self._ctx_panel = None     # document.Panel when generating for a panel
        self._inherited = {"prompt": "", "negative": ""}   # what this context copied in
        self._panel_default = ""   # the panel prompt's starting text for this context
        self._build()
        q = self.ext.queue
        q.balance_changed.connect(self._show_balance)
        q.balance_failed.connect(lambda e: self.balance.setText(f"Balance: error ({e[:80]})"))
        q.job_started.connect(self._on_started)
        q.job_finished.connect(self._on_finished)
        q.jobs_cancelled.connect(self._on_cancelled)
        q.queue_changed.connect(self._on_queue)
        if q.balance:
            self._show_balance(q.balance)
        else:
            q.refresh_balance()
        self._update_button()

    # ---------------------------------------------------------------- layout
    def _build(self):
        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(4, 4, 4, 4)

        top = QHBoxLayout()
        self.style = _combo([])
        self._fill_styles()
        self.style.currentIndexChanged.connect(self._style_changed)
        refresh = QToolButton()
        refresh.setText("↻")
        refresh.setToolTip("Refresh Anlas balance")
        refresh.clicked.connect(self.ext.queue.refresh_balance)
        settings_btn = QToolButton()
        settings_btn.setText("⚙")
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(self._open_settings)
        import_btn = QToolButton()
        import_btn.setText("⇩")
        import_btn.setToolTip("Import settings from a NovelAI image: prompt, undesired content, "
                              "characters and positions, seed, steps, guidance and vibes")
        import_btn.setPopupMode(INSTANT_POPUP)
        menu = QMenu(import_btn)
        menu.addAction("From image file…", self.import_from_file)
        menu.addAction("From selected result", self.import_from_result)
        import_btn.setMenu(menu)
        top.addWidget(self.style, 1)
        top.addWidget(import_btn)
        top.addWidget(refresh)
        top.addWidget(settings_btn)
        v.addLayout(top)
        self.balance = _wrap_label("Balance: loading…")
        v.addWidget(self.balance)

        panel_row = QHBoxLayout()
        self.panels_on = QCheckBox("Panels")
        self.panels_on.setChecked(self.settings.read_panel_names)
        self.panels_on.setToolTip("Read manga_layout.py panel groups (P1, P2, …) from the layer "
                                  "stack and generate for the panel holding the active layer")
        self.panels_on.toggled.connect(self._panels_toggled)
        self.panel_label = _wrap_label()
        self.fill_btn = QToolButton()
        self.fill_btn.setText("Fill empty")
        self.fill_btn.setToolTip("Generate one image for every panel that has nothing but its "
                                 "base layer, using each panel's own prompt and characters")
        self.fill_btn.clicked.connect(self.fill_empty_panels)
        panel_row.addWidget(self.panels_on)
        panel_row.addWidget(self.panel_label, 1)
        panel_row.addWidget(self.fill_btn)
        v.addLayout(panel_row)
        # The panel's note from its marker, on its own full-width line: in the row
        # above, a wrapped second line gets clipped in a narrow docker.
        self.panel_note = _wrap_label()
        self.panel_note.setStyleSheet("font-style: italic;")
        self.panel_note.setVisible(False)
        v.addWidget(self.panel_note)

        prompt_row = QHBoxLayout()
        prompt_row.addWidget(QLabel("Prompt"), 1)
        self.prompt_presets_btn = self._presets_button(
            "Load NovelAI's quality tags or one of your saved prompt presets, or save this "
            "prompt as a preset (also on right-click)")
        prompt_row.addWidget(self.prompt_presets_btn)
        v.addLayout(prompt_row)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Prompt, e.g. 1girl, black hair, school uniform, classroom")
        self.prompt.setMinimumHeight(70)
        self.prompt.setMaximumHeight(140)
        v.addWidget(self.prompt)

        # Panel prompt: positive text for the active panel only, right under the main prompt
        self.panel_prompt_label = QLabel("Panel prompt")
        self.panel_prompt_label.setToolTip("Added to the prompt above for this panel only. "
                                           "Starts with the panel's shot tags.")
        self.panel_prompt = QPlainTextEdit()
        self.panel_prompt.setPlaceholderText("Added after the prompt above, for this panel only")
        self.panel_prompt.setMaximumHeight(60)
        self.panel_prompt.textChanged.connect(self._update_button)
        for w in (self.panel_prompt_label, self.panel_prompt):
            w.setVisible(False)
            v.addWidget(w)
        self.neg_toggle = QToolButton()
        self.neg_toggle.setText("▸ Undesired content")
        self.neg_toggle.setCheckable(True)
        self.neg_toggle.setAutoRaise(True)
        self.neg_toggle.setSizePolicy(SP_IGNORED, SP_PREFERRED)
        self.negative = QPlainTextEdit()
        self.negative.setPlaceholderText("Added after the style's undesired-content preset")
        self.negative.setMaximumHeight(70)
        self.negative.setVisible(False)
        self.neg_toggle.toggled.connect(self._toggle_negative)
        neg_row = QHBoxLayout()
        neg_row.addWidget(self.neg_toggle, 1)
        self.negative_presets_btn = self._presets_button(
            "Load one of NovelAI's undesired-content presets (Heavy, Light, Human Focus…) or "
            "your own, or save this text as a preset (also on right-click)")
        neg_row.addWidget(self.negative_presets_btn)
        v.addLayout(neg_row)
        v.addWidget(self.negative)

        self.characters = CharacterList(self._active_layer, lambda: self.settings,
                                        self.ext.save_settings)
        self.characters.changed.connect(self._update_button)
        v.addWidget(self.characters)

        self.references = ReferenceList(self._active_layer)
        self.references.changed.connect(self._update_button)
        v.addWidget(self.references)

        self.transparent = QCheckBox("Transparent background")
        self.transparent.setToolTip(
            "V5: new images come back with a transparent background (a PNG with alpha), "
            "e.g. a character to layer over a separate background. Adds the tag "
            "“transparent background” to the prompt.\nNot used by Refine or Fill, which "
            "blend into what is already there.")
        self.transparent.toggled.connect(self._update_button)
        v.addWidget(self.transparent)

        strength_row = QHBoxLayout()
        self.strength = QSlider(HORIZONTAL)
        self.strength.setRange(5, 100)
        self.strength.setSingleStep(5)
        self.strength.setPageStep(10)
        self.strength.setValue(100)
        self.strength.setToolTip(
            "100%: Generate a new image (or Fill the selection).\n"
            "Below 100%: Refine what is there; lower keeps more of it.\n"
            "Refine and Fill always cost Anlas.")
        self.strength_label = QLabel("100%")
        self.strength_label.setMinimumWidth(34)
        self.strength.valueChanged.connect(self._strength_changed)
        strength_row.addWidget(QLabel("Strength"))
        strength_row.addWidget(self.strength, 1)
        strength_row.addWidget(self.strength_label)
        v.addLayout(strength_row)
        self.fill_context = _combo([CONTEXT_WHOLE, CONTEXT_AROUND])
        self.fill_context.setCurrentText(CONTEXT_AROUND if self.settings.fill_context == "around"
                                         else CONTEXT_WHOLE)
        self.fill_context.setToolTip(
            "What NovelAI sees around the selection when you Fill or Refine a selection.\n"
            "Whole panel/page: like NovelAI's own inpainting; the model sees everything.\n"
            "Around selection: a crop with some margin; more detail for small areas.")
        self.fill_context.currentIndexChanged.connect(self._fill_context_changed)
        self.fill_context.setVisible(False)
        v.addWidget(self.fill_context)

        size_row = QHBoxLayout()
        self.size_mode = _combo([SIZE_FREE, SIZE_LARGE, SIZE_CUSTOM])
        self.size_mode.currentIndexChanged.connect(self._size_mode_changed)
        size_row.addWidget(QLabel("Size"))
        size_row.addWidget(self.size_mode, 1)
        v.addLayout(size_row)
        self.custom_row = QWidget()
        cr = QHBoxLayout(self.custom_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.width_box, self.height_box = QSpinBox(), QSpinBox()
        for box, val in ((self.width_box, 832), (self.height_box, 1216)):
            box.setRange(resolution.MIN_SIDE, resolution.MAX_SIDE)
            box.setSingleStep(64)
            box.setValue(val)
            box.valueChanged.connect(self._update_button)
            cr.addWidget(box, 1)
            if box is self.width_box:
                cr.addWidget(QLabel("×"))
        v.addWidget(self.custom_row)
        self.size_info = _wrap_label()
        v.addWidget(self.size_info)

        adv = QGroupBox("Advanced")
        adv.setToolTip("When ticked, these values override the style")
        adv.setCheckable(True)
        adv.setChecked(False)
        self.advanced_body = QWidget()     # shown only while Advanced is ticked
        QVBoxLayout(adv).addWidget(self.advanced_body)
        adv.layout().setContentsMargins(4, 0, 4, 4)
        form = QFormLayout(self.advanced_body)
        form.setContentsMargins(0, 0, 0, 0)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.seed.setSpecialValueText("Random")
        self.seed.setToolTip("Queue mode uses this seed, then seed+1, seed+2, …")
        self.steps = QSpinBox()
        self.steps.setRange(1, 50)
        self.guidance = QDoubleSpinBox()
        self.guidance.setRange(0.0, 10.0)
        self.guidance.setSingleStep(0.1)
        self.variety = QCheckBox("Variety+")
        for w in (self.seed, self.steps, self.guidance):
            w.setSizePolicy(SP_IGNORED, SP_PREFERRED)
            w.setMinimumWidth(50)
        form.addRow("Seed", self.seed)
        form.addRow("Steps", self.steps)
        form.addRow("Guidance", self.guidance)
        form.addRow(self.variety)
        self.advanced = adv
        adv.toggled.connect(self._advanced_toggled)
        self.steps.valueChanged.connect(self._update_button)
        v.addWidget(adv)

        # ---- bottom bar: always visible under the scrolling part of the docker
        bottom = QVBoxLayout()
        bottom.setContentsMargins(4, 4, 4, 4)
        bottom.setSpacing(3)
        count_row = QHBoxLayout()
        self.count = QSpinBox()
        self.count.setMinimumWidth(40)
        self.count.setRange(1, cost.MAX_IMAGES)
        self.count.setToolTip("Number of images per click (Queue or Batch: see ⚙ settings)")
        self.count.valueChanged.connect(self._update_button)
        self.generate_btn = QPushButton("Generate")
        self.generate_btn.setMinimumHeight(32)
        self.generate_btn.setSizePolicy(SP_IGNORED, SP_PREFERRED)
        self.generate_btn.clicked.connect(self.generate)
        count_row.addWidget(self.count)
        count_row.addWidget(self.generate_btn, 1)
        bottom.addLayout(count_row)

        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("%v/%m")
        self.progress.setTextVisible(False)
        self.cancel_btn = QToolButton()
        self.cancel_btn.setText("✕")
        self.cancel_btn.setToolTip("Cancel requests that have not been sent yet")
        self.cancel_btn.clicked.connect(self.ext.queue.cancel)
        self.cancel_btn.setEnabled(False)
        prog.addWidget(self.progress, 1)
        prog.addWidget(self.cancel_btn)
        bottom.addLayout(prog)
        self.status = _wrap_label()
        bottom.addWidget(self.status)

        hist_row = QHBoxLayout()
        hist_row.addWidget(QLabel("History"), 1)
        apply_btn = QToolButton()
        apply_btn.setText("Apply")
        apply_btn.setToolTip("Keep the previewed result as a layer (or double-click a thumbnail)")
        apply_btn.clicked.connect(self.apply)
        discard = QToolButton()
        discard.setText("Discard")
        discard.setToolTip("Remove the preview layer")
        discard.clicked.connect(self.discard)
        hist_row.addWidget(apply_btn)
        hist_row.addWidget(discard)
        upscale = QToolButton()
        upscale.setText("2×")
        upscale.setToolTip("Upscale the selected result 2× with NovelAI (sharper when it is "
                           "scaled up to a large panel). Costs 1 Anlas, Opus included.")
        upscale.clicked.connect(self.upscale_selected)
        hist_row.addWidget(upscale)
        v.addLayout(hist_row)
        self.thumb_group = QButtonGroup(self)
        self.thumb_group.setExclusive(True)
        self.history_box = QVBoxLayout()
        self.history_box.setSpacing(4)
        v.addLayout(self.history_box)
        v.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(SCROLL_OFF)
        scroll.setFrameShape(NO_FRAME)
        scroll.setWidget(content)
        outer = QWidget()
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)
        ov.addWidget(scroll, 1)
        bar = QFrame()
        bar.setFrameShape(STYLED_PANEL)
        bar.setLayout(bottom)
        ov.addWidget(bar)
        self.setWidget(outer)
        self._wheel_guard = WheelGuard(scroll, self)
        self._wheel_guard.install(content)
        self._wheel_guard.install(bar)
        # rows added later (reference sliders and type boxes) get the guard too
        self.references.changed.connect(lambda: self._wheel_guard.install(self.references))

        self.prompt.textChanged.connect(self._update_button)
        model = lambda: self.settings.style(self.style.currentText()).model   # noqa: E731
        settings = lambda: self.settings   # noqa: E731
        self.prompt_presets = PresetMenu(self.prompt, presets.POSITIVE, model, settings,
                                         self.ext.save_settings, self)
        self.prompt_presets.attach_button(self.prompt_presets_btn)
        self.panel_prompt_presets = PresetMenu(self.panel_prompt, presets.POSITIVE, model,
                                               settings, self.ext.save_settings, self)
        self.negative_presets = PresetMenu(self.negative, presets.NEGATIVE, model, settings,
                                           self.ext.save_settings, self)
        self.negative_presets.attach_button(self.negative_presets_btn)
        tag_completer.set_enabled(self.settings.tag_autocomplete)
        for box in (self.prompt, self.panel_prompt, self.negative):
            tag_completer.attach(box)
        self._advanced_toggled(False)
        self._mode_changed()
        self._size_mode_changed()
        self._apply_model_limit()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._poll_layers)
        self._poll.start(POLL_MS)

    def canvasChanged(self, canvas):
        self._update_button()

    # ---------------------------------------------------------------- helpers
    @property
    def settings(self):
        return self.ext.settings

    def _fill_styles(self):
        current = self.style.currentText() or self.settings.default_style
        self.style.blockSignals(True)
        self.style.clear()
        self.style.addItems([s.name for s in self.settings.styles])
        self.style.setCurrentText(current)
        self.style.blockSignals(False)

    def _style_changed(self, *_):
        self._apply_model_limit()
        if not self.advanced.isChecked():
            self._advanced_toggled(False)
        self._update_button()

    def _apply_model_limit(self):
        info = models.get(self.settings.style(self.style.currentText()).model)
        self.characters.set_limit(info.max_characters, info.label, info.free_positions)
        self.references.set_model(info.id)
        self.transparent.setVisible(info.family == models.V5)

    def _active_layer(self):
        try:
            return document.active_layer(document.active_document())
        except document.DocumentError:
            return None

    def _resolve_link(self, doc, area, layer_id):
        """(name, pos or None, missing) for a linked layer."""
        found = document.layer_center(doc, layer_id)
        if found is None:
            return "", None, True
        name, center = found
        return name, chars.pos_from_point(center[0], center[1], area) if center else None, False

    def _poll_layers(self):
        if not self.isVisible():
            return
        try:
            doc = document.active_document()
        except document.DocumentError:
            return
        self._refresh_context(doc)
        self._autosave_tick = getattr(self, "_autosave_tick", 0) + 1
        if self._autosave_tick % AUTOSAVE_TICKS == 0 and self._ctx_key is not None:
            self._store_context()   # so closing the file soon after typing loses nothing
            self._persist(doc)
        sel = document.selection_rect(doc)
        if sel != getattr(self, "_last_sel", None):
            self._last_sel = sel
            self.fill_context.setVisible(sel is not None)
            self._update_button()
        if not self.characters.rows:
            return
        active = document.active_layer(doc)
        try:
            area = self._target(doc).rect
        except ValueError:   # e.g. selection outside the panel; shown when generating
            area = self._base_target(doc).rect
        self.characters.aspect = (area[2], area[3])
        self.characters.sync_layers(active[0] if active else "",
                                    lambda uid: self._resolve_link(doc, area, uid))

    # ---------------------------------------------------------------- panel context
    def _base_target(self, doc):
        if self._ctx_panel is not None:
            return self._ctx_panel.target
        return document.canvas_target(doc)

    def _kind(self, doc=None):
        """Generate, Refine, Fill or Refine selection, from the strength slider and
        whether there is a selection."""
        try:
            doc = doc or document.active_document()
            has_sel = document.selection_rect(doc) is not None
        except document.DocumentError:
            has_sel = False
        full = self.strength.value() >= 100
        if has_sel:
            return FILL if full else REFINE_SEL
        return GENERATE if full else REFINE

    def _target(self, doc=None, kind=None):
        """Where the next request goes. Generate: the active panel or the canvas.
        Refine: the same area, patched on top. With a selection: the selection plus
        surrounding context, grown towards the generation size's shape."""
        doc = doc or document.active_document()
        base = self._base_target(doc)
        kind = kind or self._kind(doc)
        if kind == GENERATE:
            return base
        if kind == REFINE:
            return dataclasses.replace(base, stretch=True, on_top=True)
        sel = regions.intersect(document.selection_rect(doc), base.rect)
        if sel is None:
            raise ValueError("The selection is outside " +
                             (f"panel {base.label}." if base.is_panel else "the canvas."))
        if self.settings.fill_context == "around":
            area = regions.pad(sel, base.rect)
            gw, gh = self._gen_size(area)
            area = regions.grow_to_aspect(area, gw / gh, base.rect)
        else:   # whole panel/page, as NovelAI's inpainting sees it
            area = base.rect
        return dataclasses.replace(base, rect=area, stretch=True, on_top=True)

    def _gen_size(self, area):
        if self.size_mode.currentText() == SIZE_CUSTOM:
            return self.width_box.value() // 64 * 64, self.height_box.value() // 64 * 64
        cap = resolution.FREE_PIXELS if self.size_mode.currentText() == SIZE_FREE \
            else resolution.LARGE_PIXELS
        return resolution.nearest_size(area[2], area[3], cap)

    def _fill_context_changed(self, *_):
        self.settings.fill_context = "around" if self.fill_context.currentText() == CONTEXT_AROUND \
            else "whole"
        self.ext.save_settings()
        self._update_button()

    def _strength_changed(self, value):
        self.strength_label.setText(f"{value}%")
        self._update_button()

    def _refresh_context(self, doc, force=False):
        """Follow the active document and, with Panels on, the active panel.

        The page has one cast of characters (name, prompt, undesired). Each panel
        stores its panel prompt and, per cast member, whether they are enabled and
        where they stand. Leaving a panel or document stores its state."""
        key = document.document_key(doc)
        if key != self._doc_key:
            if self._doc_key is not None and self._ctx_key is not None:
                self._store_context()   # save the document we are leaving
                old = document.document_by_key(self._doc_key)
                if old is not None:
                    try:
                        document.save_state(old, self._doc_state)
                    except Exception as e:
                        log.warning("could not store NovelAI state: %s", e)
            self._doc_key = key
            self._doc_state = document.load_state(doc)
            self._doc_state.setdefault("contexts", {})
            self.references.set_state(self._doc_state.get("references", []))
            self.transparent.blockSignals(True)
            self.transparent.setChecked(bool(self._doc_state.get("transparent", False)))
            self.transparent.blockSignals(False)
            if "prompt" in self._doc_state:
                self.prompt.setPlainText(self._doc_state.get("prompt", ""))
                self.negative.setPlainText(self._doc_state.get("negative", ""))
            self._ctx_key, self._ctx_panel = None, None
            force = True
        panel = document.active_panel(doc) if self.panels_on.isChecked() else None
        new_key = panel.label if panel else panels.PAGE_KEY
        if force or new_key != self._ctx_key:
            if self._ctx_key is not None:
                self._store_context()
                self._persist(doc)
            self._load_context(new_key, panel)
        else:
            self._ctx_panel = panel   # bounds may have changed
        self._show_context()

    def _store_context(self):
        rows = self.characters.characters_raw()
        self._doc_state["cast"] = [{"cid": c.cid, "name": c.name, "prompt": c.prompt, "uc": c.uc}
                                   for c in rows]
        slots = {}
        for c in rows:
            # each panel keeps its own character text (actions, expressions); the
            # cast above holds the latest text, which panels not visited yet start from
            slot = {"enabled": c.enabled, "pos": list(c.pos) if c.pos is not None else None,
                    "prompt": c.prompt, "uc": c.uc}
            if c.layer_id:
                slot["layer_id"] = c.layer_id
            slots[c.cid] = slot
        ctx = {"slots": slots, "positioned": self.characters.positioned,
               "prompt": self.prompt.toPlainText(),
               "prompt_inherited": self._inherited["prompt"],
               "negative": self.negative.toPlainText(),
               "negative_inherited": self._inherited["negative"]}
        if self._ctx_key != panels.PAGE_KEY:
            ctx["panel_prompt"] = self.panel_prompt.toPlainText()
            ctx["panel_prompt_default"] = self._panel_default
        self._doc_state.setdefault("contexts", {})[self._ctx_key] = ctx
        self._doc_state["prompt"] = self.prompt.toPlainText()
        self._doc_state["negative"] = self.negative.toPlainText()
        self._doc_state["references"] = self.references.state()
        self._doc_state["transparent"] = self.transparent.isChecked()

    @staticmethod
    def _stored_panel_prompt(ctx, panel):
        """A saved panel prompt, refreshed if it is still the untouched starting text
        and the starting text has changed since (e.g. the page gained panel markers
        with a cast, or the group was renamed to another shot). Edited text is kept."""
        stored = ctx.get("panel_prompt", "")
        if panel is None:
            return stored
        new = panels.default_panel_prompt(panel.info)
        old = ctx.get("panel_prompt_default")
        if old is None:   # saved before this was recorded: the name-only default
            old = panels.default_panel_prompt(dataclasses.replace(panel.info, cast=""))
        if stored.strip(" ,") == old.strip(" ,") and stored.strip(" ,") != new.strip(" ,"):
            return new
        return stored

    def _context_data(self, key, panel):
        """(panel prompt, characters, positions on) for a context.

        A panel seen before gets its own enabled flags, positions and character text
        for every cast member (members added elsewhere start disabled, with the
        latest text). A panel never visited
        enables the first N cast members, N being its char count, disables the
        rest, and adds empty rows if the cast is smaller than N."""
        ctx = self._doc_state.get("contexts", {}).get(key)
        cast = self._doc_state.get("cast")
        if ctx is not None and "characters" in ctx and cast is None:   # older state format
            return (self._stored_panel_prompt(ctx, panel),
                    [chars.from_dict(d) for d in ctx.get("characters", [])],
                    bool(ctx.get("positioned", False)))
        cast = cast or []
        members = [Character(m.get("prompt", ""), uc=m.get("uc", ""), name=m.get("name", ""),
                             cid=m.get("cid") or chars.new_cid()) for m in cast]
        if ctx is not None and "slots" in ctx:
            for c in members:
                slot = ctx["slots"].get(c.cid)
                c.enabled = bool(slot and slot.get("enabled"))
                if slot and "prompt" in slot:   # this panel's own text (older state: shared)
                    c.prompt, c.uc = slot["prompt"], slot.get("uc", "")
                if slot and slot.get("pos") is not None:
                    c.pos = tuple(slot["pos"])
                c.layer_id = (slot or {}).get("layer_id", "")
            return (self._stored_panel_prompt(ctx, panel), members,
                    bool(ctx.get("positioned", False)))
        n = panel.info.chars if panel is not None and panel.info.chars is not None \
            else len(members)
        for i, c in enumerate(members):
            c.enabled = i < n
        while len(members) < n:
            members.append(Character("", cid=chars.new_cid()))
        prompt = panels.default_panel_prompt(panel.info) if panel is not None else ""
        return prompt, members, False

    def _main_prompts(self, key, prev_prompt, prev_negative):
        """Main prompt and undesired content for a context, as {field: (text, inherited)}.

        Each panel keeps its own. A panel whose stored text is empty, missing, or
        still exactly what it copied in last time takes the previous context's text
        (copy forward); once edited, it keeps its own. Panel prompts (P1, P2, …)
        are never copied between panels."""
        ctx = self._doc_state.get("contexts", {}).get(key) or {}
        out = {}
        for field, prev in (("prompt", prev_prompt), ("negative", prev_negative)):
            stored, inherited = ctx.get(field), ctx.get(field + "_inherited")
            if stored is None or not stored.strip() or stored == inherited:
                out[field] = (prev, prev)
            else:
                out[field] = (stored, inherited if inherited is not None else "")
        return out

    def _load_context(self, key, panel):
        mains = self._main_prompts(key, self.prompt.toPlainText(), self.negative.toPlainText())
        for field, box in (("prompt", self.prompt), ("negative", self.negative)):
            text, inherited = mains[field]
            if box.toPlainText() != text:
                box.blockSignals(True)
                box.setPlainText(text)
                box.blockSignals(False)
            self._inherited[field] = inherited
        self._ctx_key, self._ctx_panel = key, panel
        panel_prompt, items, positioned = self._context_data(key, panel)
        self._panel_default = panels.default_panel_prompt(panel.info) if panel is not None else ""
        self.panel_prompt.blockSignals(True)
        self.panel_prompt.setPlainText(panel_prompt)
        self.panel_prompt.blockSignals(False)
        self.panel_prompt.setVisible(panel is not None)
        self.panel_prompt_label.setVisible(panel is not None)
        if panel is not None:
            self.panel_prompt_label.setText(f"{panel.label} prompt (added to the prompt above)")
        self.characters.set_characters(items, positioned)
        self._update_button()

    def _show_context(self):
        p = self._ctx_panel
        note = p.info.note if p is not None and self.panels_on.isChecked() else ""
        self.panel_note.setText(f"Note: {note}" if note else "")
        self.panel_note.setVisible(bool(note))
        if not self.panels_on.isChecked():
            self.panel_label.setText("")
        elif p is None:
            self.panel_label.setText("Whole page (active layer is not in a panel)")
        else:
            bits = [p.label]
            if p.info.shot:
                bits.append(p.info.shot)
            if p.info.chars is not None:
                bits.append(f"{p.info.chars} char")
            r = p.target.rect
            bits.append(f"{r[2]}×{r[3]}")
            self.panel_label.setText(" · ".join(bits))
            tip = []
            if p.info.cast:
                tip.append(f"Cast: {p.info.cast}")
            if p.info.note:
                tip.append(f"Note: {p.info.note}")
            tip.append({"name": "Read from the group name",
                        "marker": "Read from the panel marker (group renamed)",
                        "name+marker": "Read from the group name and the panel marker"}
                       .get(p.info.source, ""))
            self.panel_label.setToolTip("\n".join(t for t in tip if t))

    def _persist(self, doc=None):
        """Write prompts and per-panel state into the document (saved with it)."""
        try:
            doc = doc or document.active_document()
            if document.document_key(doc) == self._doc_key:
                document.save_state(doc, self._doc_state)
        except Exception as e:
            log.warning("could not store NovelAI state in the document: %s", e)

    def _panels_toggled(self, on):
        self.settings.read_panel_names = on
        self.ext.save_settings()
        try:
            self._refresh_context(document.active_document())
        except document.DocumentError:
            pass

    def _collect_characters(self, area, items=None, positioned=None):
        """Characters with linked layers resolved to positions in this area. Without
        `items`, the docker's rows; with them, stored rows (used by Fill empty)."""
        if items is None:
            self.characters.aspect = (area[2], area[3])
            out = self.characters.characters()
        else:
            out = [Character(c.prompt, uc=c.uc, name=c.name, enabled=c.enabled,
                             pos=c.pos if positioned else None,
                             layer_id=c.layer_id if positioned else "") for c in items]
        linked = [c for c in out if c.layer_id]
        if linked:
            doc = document.active_document()
            for c in linked:
                _, pos, missing = self._resolve_link(doc, area, c.layer_id)
                if missing:
                    raise ValueError(f"Character “{c.name or c.prompt[:20]}” is linked to a "
                                     "layer that no longer exists. Unlink it or place it by hand.")
                if pos is not None:   # an empty layer keeps the row's own position
                    c.pos = pos
                elif c.pos is None:
                    c.pos = (0.5, 0.5)
        return out

    @staticmethod
    def _presets_button(tip):
        b = QToolButton()
        b.setText("Presets")
        b.setToolTip(tip)
        b.setPopupMode(INSTANT_POPUP)
        return b

    def _toggle_negative(self, on):
        self.negative.setVisible(on)
        self.neg_toggle.setText(("▾" if on else "▸") + " Undesired content")

    def _size_mode_changed(self, *_):
        self.custom_row.setVisible(self.size_mode.currentText() == SIZE_CUSTOM)
        self._update_button()

    def _mode_changed(self, *_):
        batch = self._mode_key() == cost.BATCH
        self.count.setMaximum(cost.MAX_BATCH if batch else cost.MAX_IMAGES)
        self._update_button()

    def _advanced_toggled(self, on):
        self.advanced_body.setVisible(on)
        if not on:  # show the style's values
            st = self.settings.style(self.style.currentText())
            self.steps.setValue(st.steps)
            self.guidance.setValue(st.scale)
            self.variety.setChecked(st.variety)
        self._update_button()

    def _show_balance(self, b: cost.Balance):
        v5 = ""
        if b.v5_allowance is not None:
            v5 = f" · V5 allowance {b.v5_allowance:g}%"
        self.balance.setText(f"{b.tier_name} · {b.fixed:,} + {b.purchased:,} Anlas{v5}")
        self._update_button()

    # ---------------------------------------------------------------- import (phase 7)
    def import_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings from a NovelAI image", "",
                                              "PNG images (*.png)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                png = f.read()
        except OSError as e:
            self._error(f"Could not read {path}: {e}")
            return
        self.import_png(png, os.path.basename(path))

    def import_from_result(self):
        thumb = self.thumb_group.checkedButton()
        if thumb is None or not getattr(thumb, "png", None):
            self._info("Click a result in the history first, then import from it.")
            return
        self.import_png(thumb.png, "the selected result")

    def import_png(self, png: bytes, source: str) -> bool:
        """Fill the docker from the settings NovelAI stored in `png`."""
        img = image_from_png(png)
        alpha, size = None, (0, 0)
        if not img.isNull() and img.hasAlphaChannel():
            argb = img.convertToFormat(FMT_ARGB32)
            alpha = lambda x, y: (argb.pixel(x, y) >> 24) & 0xFF   # noqa: E731
            size = (argb.width(), argb.height())
        try:
            imp = metadata.parse(metadata.read_chunks(png, alpha, size))
        except metadata.NoMetadata as e:
            self._error(f"{source}: {e}")
            return False

        # Style: the current one if it uses the image's model, else the first that does
        style_name = self.style.currentText()
        if imp.model and self.settings.style(style_name).model != imp.model:
            match = [st.name for st in self.settings.styles if st.model == imp.model]
            if match:
                style_name = match[0]
        st = self.settings.style(style_name)
        prompt, negative, notes = metadata.fit_to_style(imp, st)
        info = models.get(st.model)

        will = [f"Style: {style_name}", "Prompt and undesired content",
                f"Seed {imp.seed}, steps {imp.steps}, guidance {imp.scale:g}"
                + (", Variety+" if imp.variety else "") + " (Advanced)"]
        if imp.characters:
            where = "with positions" if imp.positioned else "no positions"
            will.append(f"{len(imp.characters)} character(s), {where}")
        else:
            will.append("Characters (the image has none)")
        vibe_ok = vibes.supports_vibes(st.model)
        if imp.vibes and vibe_ok:
            will.append(f"{len(imp.vibes)} vibe(s), added to References")
        if info.family == models.V5:
            will.append("Transparent background: " + ("on" if imp.transparent else "off"))
        missing = []
        if imp.model is None:
            missing.append("Model: not recorded in the image; style left as it is")
        elif st.model != imp.model:
            missing.append(f"Model: no style uses {models.get(imp.model).label}; "
                           f"kept {style_name}")
        elif imp.model_guessed:
            missing.append(f"Model: Full or Curated is not recorded; using {info.label}")
        if imp.characters and not info.has_characters:
            missing.append(f"Characters: {info.label} has no character prompts")
        if imp.vibes and not vibe_ok:
            missing.append(f"{len(imp.vibes)} vibe(s): {info.label} can't use them")
        if imp.char_ref_count:
            missing.append(f"{imp.char_ref_count} character reference(s): NovelAI doesn't "
                           "store the image; add it again")
        missing += [f"Style setting: {d}" for d in metadata.differences(imp, st)]
        missing.append(f"Size {imp.width}×{imp.height}: sizes follow the canvas or panel")
        if imp.strength is not None:
            missing.append(f"Refine strength {round(imp.strength * 100)}%: strength left as it is")
        if not confirm_import(self, f"Import settings from {source}?\n" + metadata.describe(imp),
                              will + notes, missing):
            self._info("Import cancelled; nothing changed.")
            return False

        if self.style.currentText() != style_name:
            self.style.setCurrentText(style_name)   # applies the style's limits first
        self.prompt.setPlainText(prompt)
        self.negative.setPlainText(negative)
        if negative and not self.neg_toggle.isChecked():
            self.neg_toggle.setChecked(True)
        self.advanced.setChecked(True)
        self.seed.setValue(max(0, min(imp.seed, self.seed.maximum())))
        self.steps.setValue(imp.steps)
        self.guidance.setValue(imp.scale)
        self.variety.setChecked(imp.variety)
        if info.family == models.V5:
            self.transparent.setChecked(imp.transparent)
        if info.has_characters:
            items = [chars.from_dict({"prompt": c.prompt, "uc": c.uc, "pos": c.pos,
                                      "cid": chars.new_cid()}) for c in imp.characters]
            self.characters.set_characters(items, imp.positioned and bool(items))
        if imp.vibes and vibe_ok:
            for i, (enc, strength) in enumerate(imp.vibes):
                path = self._save_imported_vibe(enc, st.model, f"{source} vibe {i + 1}")
                if any(r.source.get("path") == path for r in self.references.rows):
                    continue
                self.references.add(VIBE, {"source": {"type": "vibefile", "path": path,
                                                      "index": 0, "name": f"vibe {i + 1}"},
                                           "strength": strength})
        self._update_button()
        self._info(f"Imported settings from {source}.")
        return True

    def _save_imported_vibe(self, encoding: str, model_id: str, name: str) -> str:
        """Keep a vibe encoding found in an image as a .naiv4vibe file, so the
        reference row works like any other vibe file (and survives reopening)."""
        folder = os.path.join(self._vibe_cache_dir(), "imported")
        os.makedirs(folder, exist_ok=True)
        digest = hashlib.sha256(f"{model_id}|{encoding}".encode()).hexdigest()[:16]
        path = os.path.join(folder, digest + ".naiv4vibe")
        if not os.path.exists(path):
            data = {"identifier": "novelai-vibe-transfer", "version": 1, "type": "encoding",
                    "name": name,
                    "encodings": {vibes.MODEL_KEYS[model_id]: {"unknown": {"encoding": encoding}}},
                    "importInfo": {"model": model_id, "information_extracted": 1.0,
                                   "strength": 0.6}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        return path

    def _vibe_cache_dir(self):
        return os.path.join(self.ext.folder, "nai_krita_vibes")

    def _resolve_references(self, doc, model_id):
        """Turn the reference rows into request data for model_id.

        Vibe files: their stored encoding for this model (and info value) is used as
        is. Layers, image files and vibe files that carry their image: the encoding
        cache is checked; anything missing becomes a source the worker encodes
        (about 2 Anlas each, counted in the Anlas check) and then caches."""
        cache = vibes.VibeCache(self._vibe_cache_dir())
        label = models.get(model_id).label
        vibe_refs, sources, char_refs = [], [], []
        for row in self.references.active(VIBE):
            st = row.state()
            src, ie, strength = st["source"], st["information_extracted"], st["strength"]
            name = src.get("name", "vibe")
            if src["type"] == "vibefile":
                try:
                    entries = vibes.load_vibe_file(src["path"])
                except Exception as e:
                    raise ValueError(f"Could not read vibe file {src['path']}: {e}")
                entry = entries[min(src.get("index", 0), len(entries) - 1)]
                enc = entry.encoding_for(model_id, ie)
                if enc:
                    vibe_refs.append(VibeRef(enc, strength, entry.name))
                    continue
                if not entry.image_b64:
                    have = ", ".join(models.get(m).label for m in entry.models) or "none"
                    raise ValueError(f"Vibe “{entry.name}” has no encoding for {label} and no "
                                     f"image to encode. It is encoded for: {have}.")
                png = base64.b64decode(entry.image_b64)
            elif src["type"] == "layer":
                png = document.layer_png(doc, src["id"])
            else:
                png = document.file_png(src["path"])
            key = vibes.cache_key(png, model_id, ie)
            enc = cache.get(key)
            if enc:
                vibe_refs.append(VibeRef(enc, strength, name))
            else:
                sources.append((png, ie, key, strength, name))
        for row in self.references.active(CHAR):
            st = row.state()
            src = st["source"]
            png = document.layer_png(doc, src["id"]) if src["type"] == "layer" \
                else document.file_png(src["path"])
            char_refs.append(CharRef(base64.b64encode(document.reference_png(png)).decode(),
                                     st["kind"], st["strength"], st["fidelity"],
                                     src.get("name", "")))
        return Refs(vibe_refs, sources, char_refs, len(sources))

    def _area(self):
        try:
            return self._target(document.active_document()).rect
        except (document.DocumentError, ValueError):
            return None

    def _build_params(self, area, panel_prompt=None, items=None, positioned=None, kind=None,
                      main_prompt=None, main_negative=None, refs=None):
        """Request parameters (without image data; Refine/Fill sources are attached
        by _attach_sources at click time)."""
        st = self.settings.style(self.style.currentText())
        if panel_prompt is None:
            panel_prompt = self.panel_prompt.toPlainText() if self._ctx_panel else ""
        kind = kind or self._kind()
        w, h = self._gen_size(area)
        adv = self.advanced.isChecked()
        action = {GENERATE: request.GENERATE, FILL: request.INFILL,
                  REFINE_SEL: request.INFILL}.get(kind, request.IMG2IMG)
        transparent = self._transparent_on(st.model, kind)
        prompt = panels.join_prompts(
            self.prompt.toPlainText() if main_prompt is None else main_prompt, panel_prompt)
        if transparent:   # as NovelAI's site does, before the quality tags
            prompt = presets.merge(prompt, TRANSPARENT_TAG)
        return request.GenerationParams(
            action=action, strength=self.strength.value() / 100,
            inpaint_strength=self.strength.value() / 100 if kind == REFINE_SEL else 1.0,
            transparent=transparent,
            prompt=st.compose_prompt(prompt),
            negative=st.compose_negative(
                self.negative.toPlainText() if main_negative is None else main_negative),
            model=st.model, width=w, height=h,
            steps=self.steps.value() if adv else st.steps,
            scale=self.guidance.value() if adv else st.scale,
            cfg_rescale=st.cfg_rescale, sampler=st.sampler, noise_schedule=st.noise_schedule,
            seed=self.seed.value() if adv else 0,
            variety=self.variety.isChecked() if adv else st.variety,
            quality_toggle=bool(st.quality_tags),
            characters=self._collect_characters(area, items, positioned),
            vibes=list(refs.vibes) if refs else
            [VibeRef("")] * len(self.references.active(VIBE)),
            char_refs=list(refs.char_refs) if refs else
            [CharRef("")] * len(self.references.active(CHAR)),
            vibe_encodes=refs.encodes if refs else 0)

    def _transparent_on(self, model_id, kind):
        """The checkbox applies to new images on V5 only."""
        return (self.transparent.isChecked() and kind == GENERATE
                and models.get(model_id).family == models.V5)

    def _mode_key(self):
        return cost.BATCH if self.settings.multi_mode == "batch" else cost.QUEUE

    def _update_button(self, *_):
        if not hasattr(self, "generate_btn"):
            return
        try:
            doc = document.active_document()
        except document.DocumentError:
            self.generate_btn.setText("Generate")
            self.size_info.setText("No document open")
            return
        kind = self._kind(doc)
        self.transparent.setEnabled(kind == GENERATE)
        try:
            area = self._target(doc, kind).rect
            p = self._build_params(area, kind=kind)
            planned, verdict = cost.plan(p, self.count.value(), self._mode_key(),
                                         self.ext.queue.balance)
        except Exception as e:
            self.size_info.setText(str(e))
            return
        where = f"{self._ctx_panel.label} ({area[2]}×{area[3]})" if self._ctx_panel \
            else f"a {area[2]}×{area[3]} area"
        self.size_info.setText(f"{p.width}×{p.height} for {where} · {p.info.label}")
        n = self.count.value()
        label = KIND_LABELS[kind] + ("" if n == 1 else f" ×{n}")
        if not verdict.free:
            label += f"  (≈{verdict.estimate} Anlas)" if verdict.estimate else "  (may cost Anlas)"
        self.generate_btn.setText(label)
        self.generate_btn.setToolTip(verdict.describe() if not verdict.free else
                                     f"Free: {len(planned)} request(s) within Opus free limits")

    def _error(self, text):
        self.status.setStyleSheet("color: #d04040;")
        self.status.setText(text)

    def _info(self, text):
        self.status.setStyleSheet("")
        self.status.setText(text)

    # ---------------------------------------------------------------- actions
    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if exec_dialog(dlg):
            dlg.apply_to(self.settings)
            self.ext.save_settings()
            tag_completer.set_enabled(self.settings.tag_autocomplete)
            self._mode_changed()
            self._fill_styles()
            if self.panels_on.isChecked() != self.settings.read_panel_names:
                self.panels_on.setChecked(self.settings.read_panel_names)
            self.ext.queue.refresh_balance()

    def generate(self):
        s = self.settings
        if not s.token:
            self._error("Set your NovelAI API token in settings (⚙) first.")
            return
        if not (self.prompt.toPlainText().strip() or
                (self._ctx_panel and self.panel_prompt.toPlainText().strip())):
            self._error("Write a prompt first.")
            return
        balance = self.ext.queue.balance
        try:
            doc = document.active_document()
            self._refresh_context(doc)
            self._store_context()
            self._persist(doc)
            document.check_format(doc)
            kind = self._kind(doc)
            target = self._target(doc, kind)
            area = target.rect
            refs = self._resolve_references(doc, self.settings.style(self.style.currentText()).model)
            planned, verdict = cost.plan(self._build_params(area, kind=kind, refs=refs),
                                         self.count.value(), self._mode_key(), balance)
            if kind != GENERATE:
                target = self._attach_sources(doc, kind, target, planned)
            for p, _ in planned:
                request.build_body(p)  # validate before asking about cost
        except (document.DocumentError, ValueError) as e:
            self._error(str(e))
            return
        if not verdict.free:
            if s.free_only:
                self._error("Blocked: this would cost Anlas (" + verdict.describe() +
                            "). Turn off “Free generations only” in settings to allow it.")
                return
            if not confirm_paid(self, verdict, balance, len(planned), self._session):
                self._info("Cancelled; nothing was sent.")
                return
        first = planned[0][0]
        images = sum(p.n_samples for p, _ in planned)
        mode = "batch" if first.n_samples > 1 else ("queue" if len(planned) > 1 else "")
        summary = f"{first.info.label} {first.width}×{first.height}" + (f" · {mode}" if mode else "")
        if kind != GENERATE:
            summary = KIND_LABELS[kind] + (f" {round(first.strength * 100)}%" if kind in (REFINE, REFINE_SEL) else "") + " · " + summary
        if target.is_panel:
            summary = f"{target.label} · " + summary
        n_chars = len(first.enabled_characters())
        if n_chars:
            summary += f" · {n_chars} char" + ("s" if n_chars > 1 else "")
        entry = HistoryEntry(summary, images)
        self._add_entry(entry)
        prefix = "NovelAI" if kind == GENERATE else f"NovelAI {'fill' if kind == FILL else 'refine'}"
        job_list = [jobs.Job(params=p, verdict=v, target=target, entry=entry,
                             label=f"{prefix} {p.seed}", vibe_sources=list(refs.sources),
                             vibe_cache_dir=self._vibe_cache_dir()) for p, v in planned]
        if not self.ext.queue.busy and self.ext.queue.count() == 0:
            self._run_total = self._run_done = 0
        self._run_total += len(job_list)
        self._info("")
        self.ext.queue.submit(job_list)

    def _attach_sources(self, doc, kind, target, planned):
        """Read what is under the area now and attach it (and the mask for Fill) to
        every request of this click. Returns the target with its blend mask."""
        if self.layers.has_preview:
            raise ValueError("Apply or discard the preview first: Refine and Fill work on "
                             "what is in the document, and the preview would be included.")
        x, y, w, h = target.rect
        src = document.read_source(doc, target)
        first = planned[0][0]
        image_b64 = base64.b64encode(document.source_png(src, first.width, first.height)).decode()
        mask_b64 = ""
        if kind in (FILL, REFINE_SEL):
            alpha = document.selection_alpha(doc, target.rect)
            if not any(alpha):
                raise ValueError("The selection is empty inside this area.")
            mask_b64 = base64.b64encode(
                document.mask_png(alpha, w, h, first.width, first.height)).decode()
        for p, _ in planned:
            p.image_b64 = image_b64
            p.mask_b64 = mask_b64
        if kind in (FILL, REFINE_SEL):
            feather = max(4, min(48, int(min(w, h) * 0.02)))
            target = dataclasses.replace(
                target, mask=document.selection_alpha(doc, target.rect, feather))
        return target

    def fill_empty_panels(self):
        """One request per panel group holding only its base layer, each with that
        panel's stored prompt and characters (or its defaults), placed straight in."""
        s = self.settings
        if not s.token:
            self._error("Set your NovelAI API token in settings (⚙) first.")
            return
        try:
            doc = document.active_document()
            document.check_format(doc)
            self._refresh_context(doc)
            self._store_context()
            self._persist(doc)
            empty = [p for p in document.all_panels(doc) if document.panel_is_empty(p)]
            refs = self._resolve_references(doc, self.settings.style(self.style.currentText()).model)
        except document.DocumentError as e:
            self._error(str(e))
            return
        if not empty:
            self._info("No empty panels: every panel group already has a layer above its base.")
            return
        balance = self.ext.queue.balance
        planned = []
        try:
            for panel in empty:
                panel_prompt, items, positioned = self._context_data(panel.label, panel)
                if panel.label == self._ctx_key:
                    main, neg = self.prompt.toPlainText(), self.negative.toPlainText()
                else:
                    mains = self._main_prompts(panel.label, self.prompt.toPlainText(),
                                               self.negative.toPlainText())
                    main, neg = mains["prompt"][0], mains["negative"][0]
                params = self._build_params(panel.target.rect, panel_prompt, items, positioned,
                                            kind=GENERATE, main_prompt=main, main_negative=neg,
                                            refs=refs._replace(encodes=refs.encodes if not planned else 0))
                if not panels.join_prompts(main, panel_prompt):
                    raise ValueError(f"{panel.label} has no prompt: write a prompt first.")
                one, _ = cost.plan(params, 1, cost.QUEUE, balance)
                request.build_body(one[0][0])
                planned.append((panel, one[0][0], one[0][1]))
        except (ValueError, document.DocumentError) as e:
            self._error(str(e))
            return
        paid = [v for _, _, v in planned if not v.free]
        labels = ", ".join(p.label for p, _, _ in planned)
        if paid:
            reasons = []
            for v in paid:
                reasons += [r for r in v.reasons if r not in reasons]
            reasons.append(f"{len(paid)} of {len(planned)} panels cost Anlas")
            verdict = cost.Verdict(free=False, reasons=reasons,
                                   estimate=sum(v.estimate for v in paid))
            if s.free_only:
                self._error("Blocked: filling the empty panels would cost Anlas (" +
                            verdict.describe() + ").")
                return
            if not confirm_paid(self, verdict, balance, len(planned), self._session):
                self._info("Cancelled; nothing was sent.")
                return
        elif QMessageBox.question(self, "Fill empty panels",
                                  f"Generate one image each for {labels}?\nAll free on Opus.",
                                  MB_OK | MB_CANCEL, MB_CANCEL) != MB_OK:
            return
        first = planned[0][1]
        entry = HistoryEntry(f"Fill {labels} · {first.info.label}", len(planned))
        self._add_entry(entry)
        job_list = [jobs.Job(params=p, verdict=v, target=panel.target, entry=entry,
                             label=f"NovelAI {p.seed}", auto_apply=True,
                             vibe_sources=list(refs.sources), vibe_cache_dir=self._vibe_cache_dir())
                    for panel, p, v in planned]
        if not self.ext.queue.busy and self.ext.queue.count() == 0:
            self._run_total = self._run_done = 0
        self._run_total += len(job_list)
        self._info(f"Filling {len(job_list)} panel(s)…")
        self.ext.queue.submit(job_list)

    def _add_entry(self, entry):
        self.history_box.insertWidget(0, entry)
        self.entries.insert(0, entry)
        while len(self.entries) > MAX_HISTORY:
            old = self.entries.pop()
            for t in old.thumbs:
                self.thumb_group.removeButton(t)
            self.history_box.removeWidget(old)
            old.deleteLater()

    def _set_progress(self):
        self.progress.setRange(0, max(1, self._run_total))
        self.progress.setValue(self._run_done)
        self.progress.setTextVisible(self._run_total > 1)

    def upscale_selected(self):
        thumb = self.thumb_group.checkedButton()
        if thumb is None or not getattr(thumb, "png", None):
            self._info("Click a result first, then 2× to upscale it.")
            return
        src_job = thumb.job
        verdict = cost.Verdict(free=False, estimate=UPSCALE_ESTIMATE, reasons=[
            "Upscale 2×: 1 Anlas (charged on Opus too)"])
        if self.settings.free_only:
            self._error("Blocked: upscaling may cost Anlas. Turn off “Free generations only” "
                        "in settings to allow it.")
            return
        if not confirm_paid(self, verdict, self.ext.queue.balance, 1, self._session):
            self._info("Cancelled; nothing was sent.")
            return
        entry = src_job.entry
        if entry is not None:
            entry.expected += 1
            entry.update_header()
        job = jobs.Job(params=src_job.params, verdict=verdict, target=src_job.target,
                       entry=entry, label=f"{src_job.label} 2x", upscale_png=thumb.png)
        if not self.ext.queue.busy and self.ext.queue.count() == 0:
            self._run_total = self._run_done = 0
        self._run_total += 1
        self.ext.queue.submit([job])

    def _on_started(self, job):
        self._set_progress()
        if job.upscale_png:
            self._info("Upscaling 2×…")
            return
        n = self._run_done + 1
        of = f" {n} of {self._run_total}" if self._run_total > 1 else ""
        imgs = f" ({job.params.n_samples} images)" if job.params.n_samples > 1 else ""
        self._info(f"Generating{of}{imgs}: {job.params.width}×{job.params.height} on "
                   f"{job.params.info.label}…")

    def _on_queue(self, n):
        self.cancel_btn.setEnabled(n > 1 or (n == 1 and not self.ext.queue.busy))
        if n == 0:
            self._set_progress()

    def _on_cancelled(self, dropped):
        self._run_total -= len(dropped)
        by_entry = {}
        for job in dropped:
            by_entry.setdefault(id(job.entry), [job.entry, 0])[1] += job.params.n_samples
        for entry, n in by_entry.values():
            entry.expected -= n
            entry.cancelled += n
            entry.update_header()
        self._info(f"Cancelled {len(dropped)} queued request(s); "
                   "the one already sent will still arrive.")
        self._set_progress()

    def _on_finished(self, job):
        self._run_done += 1
        self._set_progress()
        entry = job.entry
        if job.status == jobs.FAILED:
            if entry is not None:
                entry.failed += job.params.n_samples
                entry.update_header()
            self._error(job.error)
            return
        spent = job.spent
        if entry is not None:
            entry.received += len(job.images)
            entry.spent += max(0, spent or 0)
            entry.update_header()
            first_of_entry = not entry.thumbs
            where = f"{job.target.label} · " if job.target.is_panel else ""
            for i, png in enumerate(job.images):
                seed = job.params.seed + i if job.params.n_samples > 1 else job.params.seed
                what = "2× upscale" if job.upscale_png else f"Seed {seed}"
                t = Thumb(png, f"{where}{what}\n{job.params.prompt[:200]}")
                t.job, t.png = job, png
                t.clicked.connect(lambda _=False, j=job, img=png, b=t: self._preview(j, img, b))
                t.double_clicked.connect(lambda j=job, img=png, b=t: self._apply_thumb(j, img, b))
                self.thumb_group.addButton(t)
                entry.add_thumb(t)
            if job.auto_apply:
                try:
                    self.layers.place(job.images[0], job.target, job.label)
                except document.DocumentError as e:
                    self._error(f"{job.target.label}: {e}")
            elif job.upscale_png and entry.thumbs:
                self._preview(job, job.images[0], entry.thumbs[-1])
            elif first_of_entry and entry.thumbs:
                self._preview(job, job.images[0], entry.thumbs[0])
        if self.ext.queue.count() == 0:
            self._info(f"Done · {entry.received if entry else len(job.images)} image(s)"
                       + (" · autosaved" if job.saved_paths else ""))
        if job.verdict.free and spent and spent > 0:
            QMessageBox.warning(self, "Unexpected Anlas cost",
                                f"This request was judged free but cost {spent} Anlas. The "
                                "parameters were written to Krita's log. Please report it so the "
                                "free test can be fixed.")

    def _preview(self, job, png, thumb):
        thumb.setChecked(True)
        try:
            self.layers.show(png, job.target, job.label)
        except document.DocumentError as e:
            self._error(str(e))

    def _apply_thumb(self, job, png, thumb):
        self._preview(job, png, thumb)
        self.apply()

    def apply(self):
        try:
            if self.layers.apply():
                self._info("Applied as a new layer.")
            else:
                self._info("Nothing to apply: click a thumbnail first.")
        except document.DocumentError as e:
            self._error(str(e))

    def discard(self):
        self.layers.discard()
        checked = self.thumb_group.checkedButton()
        if checked:
            self.thumb_group.setExclusive(False)
            checked.setChecked(False)
            self.thumb_group.setExclusive(True)
