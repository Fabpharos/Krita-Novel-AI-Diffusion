"""Phase 9: the panel marker shared with manga_layout.py (Krita-free).

Runs the real script, reads the .ora's stack.xml into duck-typed nodes (the same
names, types and nesting Krita's .ora importer creates) and checks what the plugin
reads from it.
"""
import importlib.util
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from nai_krita import panels  # noqa: E402

SPEC = {"page": {"preset": "b5", "dpi": 350, "bleed_mm": 3, "rtl": True},
        "layout": {"split": "rows", "sizes": [1, 2], "slants": [0], "children": [
            {"chars": 0, "shot": "establishing", "note": "school rooftop"},
            {"split": "cols", "sizes": [2, 1], "slants": [8], "children": [
                {"chars": 2, "shot": "medium", "cast": "1girl, 1boy"},
                {"chars": 1, "shot": "close", "note": "BIG – she turns “Wait!”",
                 "bleed": ["bottom"]}]}]}}


class Node:
    def __init__(self, name, kind, children=(), visible=True):
        self._name, self._type, self._parent = name, kind, None
        self._children, self.visible = list(children), visible
        for c in self._children:
            c._parent = self

    def name(self): return self._name
    def type(self): return self._type
    def parentNode(self): return self._parent
    def childNodes(self): return self._children
    def setName(self, n): self._name = n


def _load_script():
    spec = importlib.util.spec_from_file_location("manga_layout", os.path.join(ROOT, "manga_layout.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ora_tree(path):
    """Krita-like node tree from an .ora: <stack> → group, <layer> → paint layer.
    Krita lists children bottom-first; the .ora lists them top-first."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("stack.xml"))

    def build(el):
        kids = [build(c) for c in el if c.tag in ("stack", "layer")]
        kind = "grouplayer" if el.tag == "stack" else "paintlayer"
        return Node(el.get("name", ""), kind, list(reversed(kids)),
                    el.get("visibility", "visible") == "visible")
    return build(root.find("stack"))


def _page():
    try:
        import PIL  # noqa: F401
    except ImportError:
        return None
    mod = _load_script()
    out = os.path.join(tempfile.mkdtemp(), "page")
    mod.build(SPEC, out)
    return _ora_tree(out + ".ora")


def _groups(root):
    return [c for c in root.childNodes() if c.type() == "grouplayer"]


def test_script_writes_readable_markers():
    root = _page()
    if root is None:
        return   # Pillow not installed
    groups = _groups(root)
    infos = sorted((panels.group_info(g) for g in groups), key=lambda i: i.id)
    assert [(i.id, i.chars, i.shot, i.source) for i in infos] == [
        (1, 0, "establishing", "name+marker"), (2, 2, "medium", "name+marker"),
        (3, 1, "close", "name+marker")]
    assert infos[0].note == "school rooftop" and infos[0].cast == ""
    assert infos[1].cast == "1girl, 1boy"
    assert infos[2].note == "BIG – she turns “Wait!”"     # dashes, quotes survive XML
    # marker is hidden, 1x1, at the bottom of the group; base layer still found
    for g in groups:
        marker = g.childNodes()[0]
        assert panels.is_marker_name(marker.name()) and not marker.visible
        assert panels.find_base_layer(g) is not None
    # prompts: cast tags when given, else "no humans" for an empty panel; shot tags
    assert [panels.default_panel_prompt(i) for i in infos] == [
        "no humans, very wide shot, scenery", "1girl, 1boy, upper body", "close-up, portrait"]


def test_renamed_group_falls_back_to_marker():
    root = _page()
    if root is None:
        return
    g = next(g for g in _groups(root) if g.name().startswith("P3"))
    g.setName("She turns")                       # the user renamed the group
    info = panels.group_info(g)
    assert (info.id, info.chars, info.shot, info.source) == (3, 1, "close", "marker")
    art = Node("sketch", "paintlayer")
    g._children.append(art)
    art._parent = g
    found, info = panels.find_panel_group(art)
    assert found is g and info.id == 3


def test_name_wins_over_marker():
    marker = Node(panels.marker_name({"id": 3, "chars": 1, "shot": "close", "cast": "1girl"}),
                  "paintlayer")
    g = Node("P4 – 2 char, wide – 900x700", "grouplayer", [marker])
    info = panels.group_info(g)
    # what the name shows wins; the marker adds the cast
    assert (info.id, info.chars, info.shot, info.cast) == (4, 2, "wide", "1girl")
    # a bare name takes the rest from the marker
    g = Node("P4", "grouplayer", [marker])
    info = panels.group_info(g)
    assert (info.id, info.chars, info.shot) == (4, 1, "close")


def test_old_pages_and_bad_markers():
    # no marker: unchanged behaviour
    g = Node("P2 – 2 char, medium – 1416x1987", "grouplayer",
             [Node("P2 base (paste above, Inherit Alpha)", "paintlayer")])
    info = panels.group_info(g)
    assert (info.id, info.chars, info.shot, info.source, info.cast) == (2, 2, "medium", "name", "")
    # unreadable marker on a renamed group: not a panel
    g = Node("Background", "grouplayer", [Node("nai-panel {broken", "paintlayer")])
    assert panels.group_info(g) is None
    # marker without an id on a renamed group: not a panel
    g = Node("Background", "grouplayer", [Node('nai-panel {"v": 1}', "paintlayer")])
    assert panels.group_info(g) is None
    # a future version is still read for the keys we know
    g = Node("x", "grouplayer", [Node('nai-panel {"v": 2, "id": 5, "shot": "full", "new": 1}',
                                      "paintlayer")])
    assert panels.group_info(g).id == 5


def test_marker_name_roundtrip():
    name = panels.marker_name({"id": 1, "chars": 0, "shot": "wide", "note": "", "cast": None})
    assert name == 'nai-panel {"v": 1, "id": 1, "chars": 0, "shot": "wide"}'
    assert panels.parse_marker(name) == {"v": 1, "id": 1, "chars": 0, "shot": "wide"}
    assert panels.parse_marker("P1 base") is None
