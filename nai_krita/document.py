"""Everything that reads or writes the Krita document. Main thread only."""
import json
import os
from dataclasses import dataclass

from krita import Krita  # type: ignore

from .qt import (QUuid, QByteArray, QImage, QPainter, QColor, FMT_ARGB32, FMT_ARGB32_PM,
                 FMT_RGB32, FMT_GRAY8, FMT_ALPHA8, FMT_RGBA8888, IGNORE_ASPECT, SMOOTH, FAST,
                 KEEP_ASPECT,
                 DEST_IN, image_from_png, image_to_png, image_from_bytes, image_bytes)
from .resolution import cover_rect
from .characters import bounds_center
from . import panels

PREVIEW_NAME = "[NovelAI preview]"
STATE_ANNOTATION = "nai_krita"   # per-document plugin state (panel prompts, characters)


class DocumentError(Exception):
    pass


def active_document():
    doc = Krita.instance().activeDocument()
    if doc is None:
        raise DocumentError("Open a document first.")
    return doc


def document_key(doc) -> str:
    """Identifies the open document across calls (wrappers are new every call)."""
    return doc.rootNode().uniqueId().toString()


def check_format(doc):
    if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8":
        raise DocumentError(
            f"This document is {doc.colorModel()} {doc.colorDepth()}. The NovelAI plugin needs "
            "8-bit RGBA (Image > Convert Image Color Space).")


def canvas_target(doc) -> panels.Target:
    return panels.Target(rect=(0, 0, doc.width(), doc.height()))


def generation_area(doc):
    """(x, y, w, h) of the whole canvas."""
    return 0, 0, doc.width(), doc.height()


def _rect(node):
    b = node.bounds()
    return b.x(), b.y(), b.width(), b.height()


# ---------------------------------------------------------------- panels
@dataclass
class Panel:
    info: panels.PanelInfo
    group: object
    base: object          # base shape layer, or None
    target: panels.Target

    @property
    def label(self) -> str:
        return self.info.label


def _panel_from_group(group, info) -> Panel:
    base = panels.find_base_layer(group)
    rect = _rect(base) if base is not None else _rect(group)
    if rect[2] <= 0 or rect[3] <= 0:
        rect = _rect(group)
    target = panels.Target(rect=rect, group_id=group.uniqueId().toString(),
                           base_id=base.uniqueId().toString() if base is not None else "",
                           label=info.label)
    return Panel(info, group, base, target)


def panel_for_node(node):
    """The panel containing node (walking up through parents), or None."""
    group, info = panels.find_panel_group(node)
    if group is None:
        return None
    return _panel_from_group(group, info)


def active_panel(doc):
    node = doc.activeNode()
    return panel_for_node(node) if node is not None else None


def all_panels(doc):
    """Every panel group in the document, sorted by panel number."""
    found = []

    def walk(node):
        for child in node.childNodes():
            if child.type() == "grouplayer":
                info = panels.group_info(child)
                if info:
                    found.append(_panel_from_group(child, info))
                else:
                    walk(child)
    walk(doc.rootNode())
    return sorted(found, key=lambda p: p.info.id)


def panel_is_empty(panel: Panel) -> bool:
    """True when the group holds nothing but its base layer (and maybe a preview)."""
    for child in panel.group.childNodes():
        if panel.base is not None and child.uniqueId() == panel.base.uniqueId():
            continue
        if child.name() == PREVIEW_NAME or panels.is_marker_name(child.name()):
            continue
        return False
    return True


# ---------------------------------------------------------------- per-document state
SIDECAR_SUFFIX = ".nai.json"


def sidecar_path(doc) -> str:
    """Sidecar file for formats that can't hold Krita annotations (.ora, .png, …):
    page01.ora -> page01.ora.nai.json, next to the document. "" for .kra or unsaved."""
    name = doc.fileName() or ""
    if not name or name.lower().endswith(".kra"):
        return ""
    return name + SIDECAR_SUFFIX


def load_state(doc) -> dict:
    """Plugin state for this document: the .kra annotation, else the sidecar file."""
    try:
        raw = bytes(doc.annotation(STATE_ANNOTATION))
        if raw:
            return json.loads(raw.decode("utf-8"))
    except Exception:
        pass
    path = sidecar_path(doc)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(doc, state: dict):
    """Store state in the document (kept when saved as .kra) and, for other formats,
    in the sidecar file, which survives closing an .ora. Unchanged data is not
    rewritten, so the document is not marked modified for nothing."""
    data = json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8")
    if bytes(doc.annotation(STATE_ANNOTATION)) != data:
        doc.setAnnotation(STATE_ANNOTATION, "NovelAI plugin: panel prompts and characters",
                          QByteArray(data))
    path = sidecar_path(doc)
    if path:
        try:
            with open(path, "rb") as f:
                if f.read() == data:
                    return
        except OSError:
            pass
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)


def document_by_key(key: str):
    """An open document by document_key, or None (it may have been closed)."""
    for doc in Krita.instance().documents():
        try:
            if document_key(doc) == key:
                return doc
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- layers
def active_layer(doc):
    """(uniqueId string, name) of the active layer, or None."""
    node = doc.activeNode()
    if node is None or node.parentNode() is None:
        return None
    return node.uniqueId().toString(), node.name()


def layer_by_id(doc, uid: str):
    if not uid:
        return None
    return _node_by_id(doc, QUuid(uid))


def layer_center(doc, uid: str):
    """Centre of the layer's non-transparent content as (name, (cx, cy) or None),
    or None when the layer no longer exists."""
    node = layer_by_id(doc, uid)
    if node is None:
        return None
    return node.name(), bounds_center(_rect(node))


def _render(png: bytes, target):
    """Result PNG -> BGRA bytes for target.rect.

    Generate: scaled to cover the rect, centred and cropped. Refine/Fill: scaled
    exactly onto the rect (the source was scaled the same way). With a mask, the
    result's alpha is multiplied by the feathered selection so it blends in."""
    x, y, w, h = target.rect
    img = image_from_png(png)
    if img.isNull():
        raise DocumentError("NovelAI returned an image Qt could not read.")
    if target.stretch:
        img = img.scaled(w, h, IGNORE_ASPECT, SMOOTH).convertToFormat(FMT_ARGB32_PM)
    else:
        sw, sh, ox, oy = cover_rect(img.width(), img.height(), x, y, w, h)
        img = img.scaled(sw, sh, IGNORE_ASPECT, SMOOTH).convertToFormat(FMT_ARGB32_PM)
        img = img.copy(x - ox, y - oy, w, h)
    if target.mask:
        mask = image_from_bytes(target.mask, w, h, FMT_ALPHA8, 1)
        p = QPainter(img)
        p.setCompositionMode(DEST_IN)
        p.drawImage(0, 0, mask)
        p.end()
    # ARGB32 (straight alpha) is BGRA in memory on little-endian = Krita 8-bit RGBA
    return image_bytes(img.convertToFormat(FMT_ARGB32))


# ---------------------------------------------------------------- Refine / Fill sources
def selection_rect(doc):
    """Bounds of the active selection, or None when nothing is selected."""
    sel = doc.selection()
    if sel is None or sel.width() <= 0 or sel.height() <= 0:
        return None
    return sel.x(), sel.y(), sel.width(), sel.height()


def selection_alpha(doc, rect, feather: int = 0) -> bytes:
    """Selection coverage (0-255, one byte per pixel) over rect, optionally feathered."""
    sel = doc.selection()
    x, y, w, h = rect
    if sel is None:
        return bytes([255]) * (w * h)
    if feather > 0:
        sel = sel.duplicate()
        sel.feather(feather)
    return bytes(sel.pixelData(x, y, w, h))


def read_source(doc, target) -> QImage:
    """What the model should see in target.rect, flattened onto white. In a panel,
    only that panel group's layers (not labels, borders or other panels)."""
    x, y, w, h = target.rect
    doc.refreshProjection()
    doc.waitForDone()
    group = layer_by_id(doc, target.group_id) if target.group_id else None
    if group is not None:
        data = group.projectionPixelData(x, y, w, h)
    else:
        data = doc.pixelData(x, y, w, h)
    src = image_from_bytes(bytes(data), w, h, FMT_ARGB32, 4)
    flat = QImage(w, h, FMT_RGB32)
    flat.fill(QColor(255, 255, 255))
    p = QPainter(flat)
    p.drawImage(0, 0, src)
    p.end()
    return flat


def source_png(img: QImage, gen_w: int, gen_h: int) -> bytes:
    return image_to_png(img.scaled(gen_w, gen_h, IGNORE_ASPECT, SMOOTH))


MASK_CELL = 8   # NovelAI works on 8x8 latent cells; a cell is repainted if any of it is selected


def mask_png(alpha: bytes, w: int, h: int, gen_w: int, gen_h: int) -> bytes:
    """Inpaint mask at generation size: opaque white where the model repaints,
    transparent elsewhere, snapped to 8 px cells."""
    cw, ch = max(1, gen_w // MASK_CELL), max(1, gen_h // MASK_CELL)
    small = image_from_bytes(alpha, w, h, FMT_GRAY8, 1).scaled(cw, ch, IGNORE_ASPECT, SMOOTH)
    small = small.convertToFormat(FMT_GRAY8)
    rows = []
    for r in range(ch):
        line = small.constScanLine(r)
        line.setsize(cw)
        rows.append(bytes(line))
    cells = b"".join(rows)
    rgba = b"".join(b"\xff\xff\xff\xff" if v else b"\x00\x00\x00\x00" for v in cells)
    img = image_from_bytes(rgba, cw, ch, FMT_RGBA8888, 4).scaled(gen_w, gen_h, IGNORE_ASPECT, FAST)
    return image_to_png(img)


def _node_by_id(doc, uid):
    if uid is None:
        return None
    try:
        return doc.nodeByUniqueID(uid)
    except Exception:
        return None


def _insert(doc, node, target: panels.Target):
    """Add node where target says: in a panel group directly above its base layer,
    clipped to it with Inherit Alpha; otherwise above the active layer."""
    group = layer_by_id(doc, target.group_id) if target.group_id else None
    if target.group_id and group is None:
        raise DocumentError(f"Panel {target.label} is not in the active document any more.")
    if group is not None:
        if target.on_top:   # Refine/Fill patches go over everything in the panel
            kids = group.childNodes()
            group.addChildNode(node, kids[-1] if kids else None)
        else:
            base = layer_by_id(doc, target.base_id) if target.base_id else None
            group.addChildNode(node, base)
        node.setInheritAlpha(True)
        return
    active = doc.activeNode()
    if active is not None and active.parentNode() is not None:
        active.parentNode().addChildNode(node, active)
    else:
        doc.rootNode().addChildNode(node, None)


def _replace_previous_results(doc, target: panels.Target, keep):
    """In a panel, a newly applied result replaces earlier ones. Only layers still
    named "NovelAI <seed>" count; a renamed layer is treated as the user's own.
    Refine/Fill patches (on_top) sit over the panel's image, so they replace nothing."""
    group = layer_by_id(doc, target.group_id) if target.group_id else None
    if group is None or target.on_top:
        return 0
    removed = 0
    for child in list(group.childNodes()):
        if child.uniqueId() != keep.uniqueId() and panels.is_result_layer_name(child.name()):
            child.remove()
            removed += 1
    return removed


class ResultLayers:
    """Shows a result in one temporary preview layer; Apply keeps it.

    The preview is found again by its node uniqueId. (Krita returns a new Python
    wrapper for the same document on every call, so documents can't be compared
    by identity; node ids are unique across documents.)
    """

    def __init__(self):
        self._preview_id = None
        self._target = None
        self._name_hint = ""

    def _preview(self, doc):
        return _node_by_id(doc, self._preview_id)

    def show(self, png: bytes, target: panels.Target, name_hint: str):
        doc = active_document()
        check_format(doc)
        pixels = _render(png, target)
        node = self._preview(doc)
        if node is not None and self._target is not None and \
                (self._target.group_id, self._target.on_top) != (target.group_id, target.on_top):
            node.remove()   # preview moves to a different panel or position
            node = None
        if node is None:
            node = doc.createNode(PREVIEW_NAME, "paintlayer")
            _insert(doc, node, target)
            self._preview_id = node.uniqueId()
        elif self._target is not None:  # clear only what the previous result covered
            x, y, w, h = self._target.rect
            node.setPixelData(bytes(w * h * 4), x, y, w, h)
        x, y, w, h = target.rect
        node.setPixelData(pixels, x, y, w, h)
        self._target = target
        self._name_hint = name_hint
        doc.refreshProjection()

    def apply(self) -> bool:
        doc = active_document()
        node = self._preview(doc)
        if node is None:
            return False
        node.setName(self._name_hint)
        _replace_previous_results(doc, self._target, node)
        self._preview_id, self._target = None, None
        doc.refreshProjection()
        return True

    def place(self, png: bytes, target: panels.Target, name: str):
        """Apply a result directly, without the preview (used by "Fill empty panels")."""
        doc = active_document()
        check_format(doc)
        pixels = _render(png, target)
        node = doc.createNode(name, "paintlayer")
        _insert(doc, node, target)
        x, y, w, h = target.rect
        node.setPixelData(pixels, x, y, w, h)
        _replace_previous_results(doc, target, node)
        doc.refreshProjection()

    def discard(self):
        try:
            doc = active_document()
        except DocumentError:
            return
        node = self._preview(doc)
        if node is not None:
            node.remove()
            doc.refreshProjection()
        self._preview_id, self._target = None, None

    @property
    def has_preview(self) -> bool:
        try:
            return self._preview(active_document()) is not None
        except DocumentError:
            return False


# ---------------------------------------------------------------- references
REFERENCE_CANVASES = ((1024, 1536), (1536, 1024), (1472, 1472))


def layer_png(doc, uid: str) -> bytes:
    """A layer's content (its bounds), flattened onto white, as PNG."""
    node = layer_by_id(doc, uid)
    if node is None:
        raise DocumentError("The reference layer no longer exists.")
    x, y, w, h = _rect(node)
    if w <= 0 or h <= 0:
        raise DocumentError(f"The reference layer “{node.name()}” is empty.")
    doc.waitForDone()
    src = image_from_bytes(bytes(node.projectionPixelData(x, y, w, h)), w, h, FMT_ARGB32, 4)
    flat = QImage(w, h, FMT_RGB32)
    flat.fill(QColor(255, 255, 255))
    p = QPainter(flat)
    p.drawImage(0, 0, src)
    p.end()
    return image_to_png(flat)


def file_png(path: str) -> bytes:
    """Any image file Qt can read, as PNG (flattened onto white)."""
    img = QImage(path)
    if img.isNull():
        raise DocumentError(f"Could not read the image {path}.")
    flat = QImage(img.width(), img.height(), FMT_RGB32)
    flat.fill(QColor(255, 255, 255))
    p = QPainter(flat)
    p.drawImage(0, 0, img)
    p.end()
    return image_to_png(flat)


def reference_png(png: bytes) -> bytes:
    """Character Reference input: fitted inside the reference canvas closest to its
    aspect (1024x1536, 1536x1024 or 1472x1472), centred on black."""
    img = image_from_png(png)
    if img.isNull():
        raise DocumentError("Could not read the reference image.")
    aspect = img.width() / img.height()
    cw, ch = min(REFERENCE_CANVASES, key=lambda s: abs(s[0] / s[1] - aspect))
    scaled = img.scaled(cw, ch, KEEP_ASPECT, SMOOTH)
    canvas = QImage(cw, ch, FMT_RGB32)
    canvas.fill(QColor(0, 0, 0))
    p = QPainter(canvas)
    p.drawImage((cw - scaled.width()) // 2, (ch - scaled.height()) // 2, scaled)
    p.end()
    return image_to_png(canvas)
