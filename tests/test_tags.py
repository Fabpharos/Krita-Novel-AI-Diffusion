"""Phase 8: tag autocomplete core (Krita-free)."""
import gzip
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nai_krita import tags  # noqa: E402

ROWS = [   # most used first, like the data file
    ("1girl", "general", 8_600_000, ["1girls"]),
    ("long hair", "general", 6_400_000, ["very long hair alias"]),
    ("smile", "general", 5_000_000, ["smiling"]),
    ("long sleeves", "general", 2_000_000, []),
    ("hair ornament", "general", 1_900_000, []),
    ("twintails", "general", 1_200_000, ["twin tails", "pigtails"]),
    ("very long hair", "general", 1_100_000, []),
    ("hatsune miku", "character", 150_000, ["miku"]),
    ("vocaloid", "copyright", 180_000, []),
    ("kaname madoka (cosplay)", "character", 500, []),
    ("very aesthetic", "nai", 0, []),
    ("lo", "general", 10, []),
]


def idx():
    return tags.TagIndex(ROWS)


def names(matches):
    return [m.name for m in matches]


def test_prefix_ranked_by_use():
    assert names(idx().search("long")) == ["long hair", "long sleeves", "very long hair"]
    assert names(idx().search("LONG_h")) == ["long hair", "very long hair"]
    # a popular tag matched on a later word beats a rare tag that starts with the text
    assert names(idx().search("miku")) == ["hatsune miku"]
    i = tags.TagIndex(ROWS + [("miku day", "meta", 300, [])])
    assert names(i.search("miku")) == ["hatsune miku", "miku day"]


def test_exact_first_then_word_matches():
    r = idx().search("lo")
    assert r[0].name == "lo" and r[0].kind == tags.EXACT
    assert names(r)[1:3] == ["long hair", "long sleeves"]
    r = idx().search("hair")
    # direct prefix (hair ornament) before word matches (long hair, very long hair)
    assert names(r) == ["hair ornament", "long hair", "very long hair"]
    assert [m.kind for m in r] == [tags.PREFIX, tags.WORD, tags.WORD]


def test_aliases():
    r = idx().search("twin t")
    assert names(r) == ["twintails"] and r[0].alias == "twin tails"
    r = idx().search("pig")
    assert names(r) == ["twintails"] and r[0].alias == "pigtails"
    # a name match beats an alias of a more popular tag
    assert idx().search("miku")[0].name == "hatsune miku"


def test_word_match_after_parenthesis_and_exclusion():
    assert names(idx().search("cosplay")) == ["kaname madoka (cosplay)"]
    assert names(idx().search("long", exclude={"long hair"})) == ["long sleeves", "very long hair"]
    assert idx().search("l") == []        # too short


def test_load_from_file():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.tsv.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("# header\n1girl\tgeneral\t10\t1girls\nsmile\tgeneral\t5\t\n")
    i = tags.TagIndex.load(path)
    assert len(i) == 2 and names(i.search("1gi")) == ["1girl"]
    assert i.search("1girls")[0].alias == "1girls"


def test_bundled_data_file():
    if not os.path.exists(tags.DATA_FILE):
        return   # built by tools/build_tags.py
    i = tags.TagIndex.load()
    assert len(i) > 10_000
    assert names(i.search("long h"))[0] == "long hair"
    assert names(i.search("twin"))[0] == "twintails"
    assert "very aesthetic" in names(i.search("very aes"))


def tok(marked):
    """current_token on text with | marking the cursor."""
    pos = marked.index("|")
    text = marked.replace("|", "", 1)
    s, e, prefix = tags.current_token(text, pos)
    return text[s:e], prefix


def test_current_token():
    assert tok("1girl, long h|") == ("long h", "long h")
    assert tok("long h|") == ("long h", "long h")
    # the tag ends at the cursor; nothing after it belongs to it
    assert tok("1girl, lo|ng hair, smile") == ("lo", "lo")
    assert tok("1girl, lo|smile") == ("lo", "lo")
    assert tok("1girl, lo| smile") == ("lo", "lo")
    assert tok("lo|, smile") == ("lo", "lo")
    assert tok("{{long h|}}") == ("long h", "long h")
    assert tok("1girl, [sm|]") == ("sm", "sm")
    assert tok("1.3::long h|") == ("long h", "long h")
    assert tok("1.3::long hair::, sm|") == ("sm", "sm")
    assert tok("-2::sm|::") == ("sm", "sm")
    assert tok("girl, red hair\nsm|") == ("sm", "sm")
    assert tok("1girl, |") == ("", "")


def test_apply_completion():
    text = "1girl, long h"
    s, e, _ = tags.current_token(text, len(text))
    assert tags.apply_completion(text, s, e, "long hair") == ("1girl, long hair, ", 18)
    text = "lo, smile"
    s, e, _ = tags.current_token(text, 2)
    assert tags.apply_completion(text, s, e, "long hair") == ("long hair, smile", 9)
    text = "lo smile"
    s, e, _ = tags.current_token(text, 2)
    assert tags.apply_completion(text, s, e, "long hair") == ("long hair, smile", 10)
    text = "lo(smile)"
    s, e, _ = tags.current_token(text, 2)
    assert tags.apply_completion(text, s, e, "long hair") == ("long hair, (smile)", 11)
    text = "{{lo}}"
    s, e, _ = tags.current_token(text, 4)
    assert tags.apply_completion(text, s, e, "long hair") == ("{{long hair}}", 11)
    text = "1girl, lo  "
    s, e, _ = tags.current_token(text, 9)
    assert tags.apply_completion(text, s, e, "long hair") == ("1girl, long hair, ", 18)



def complete(idx_, marked, pick="long hair"):
    """Type up to | then accept the suggestion named `pick`; returns the text with | at the cursor."""
    pos = marked.index("|")
    text = marked.replace("|", "", 1)
    found = tags.find(idx_, text, pos)
    assert found, marked
    start, end, matches = found
    assert pick in names(matches), (marked, names(matches))
    new, cur = tags.apply_completion(text, start, end, pick)
    return new[:cur] + "|" + new[cur:]


def test_completion_leaves_the_rest_alone():
    i = idx()
    # reported bug: the following tag, or a sentence, was deleted
    assert complete(i, "1girl, lo| smile") == "1girl, long hair,| smile"
    assert complete(i, "lo|\nA girl walking in the rain.") == "long hair,|\nA girl walking in the rain."
    assert complete(i, "lo| A girl walking in the rain.") == "long hair,| A girl walking in the rain."
    # a sentence before the tag with no comma: only the typed word is replaced
    assert complete(i, "A girl walking in the rain. lo|") == "A girl walking in the rain. long hair, |"
    assert complete(i, "She smiles. very long h|", "very long hair") == \
        "She smiles. very long hair, |"
    assert complete(i, "Rain! smi|", "smile") == "Rain! smile, |"
    # reported bug: a multi-word phrase that isn't a tag must not restart suggestions
    # at each new word ("people in background" → people…, in…, background…)
    rows = ROWS + [("people", "general", 900_000, []), ("background", "general", 800_000, []),
                   ("indoors", "general", 700_000, [])]
    j = tags.TagIndex(rows)
    assert names(tags.find(j, "peop", 4)[2]) == ["people"]      # first word: suggestions
    for typed in ("people i", "people in", "people in backg", "people in background"):
        assert tags.find(j, "1girl, " + typed, len("1girl, " + typed)) is None, typed
    assert tags.find(j, "1girl smi", 9) is None and tags.find(j, "She has very long h", 19) is None
    # the longest stretch that is a tag wins
    assert complete(i, "1girl, long h|") == "1girl, long hair, |"
    # before a comma or closing bracket: no extra comma
    assert complete(i, "lo|, smile") == "long hair|, smile"
    assert complete(i, "{{lo|}}, smile") == "{{long hair|}}, smile"
    # typing straight in front of an existing tag (no space) keeps that tag
    assert complete(i, "1girl, lo|smile, blush") == "1girl, long hair, |smile, blush"
    assert complete(i, "lo|A girl walking in the rain.") == "long hair, |A girl walking in the rain."


def test_tags_in():
    assert tags.tags_in("1girl, {{long_hair}}, 1.2::smile::\n[blush]") == \
        {"1girl", "long hair", "smile", "blush"}


def test_format_count():
    assert [tags.format_count(n) for n in (0, 950, 1500, 34_000, 6_400_000)] == \
        ["", "950", "1.5k", "34k", "6.4M"]
