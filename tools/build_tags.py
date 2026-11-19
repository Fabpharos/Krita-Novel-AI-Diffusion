"""Build nai_krita/data/tags.tsv.gz, the tag list for prompt autocomplete.

    python tools/build_tags.py [--min-posts 100]

Source: Danbooru's public JSON API (tags ordered by post count, plus active tag
aliases). Only tag names, categories, post counts and alias names are kept; these
are plain facts, not anyone's creative work. A few NovelAI-only tags (quality and
aesthetic tags, "no text", year tags) are added as category "nai".

Output, one tag per line, sorted by post count:
    name<TAB>category<TAB>post_count<TAB>alias|alias|...
with underscores turned into spaces, as NovelAI expects.
"""
import argparse
import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "nai_krita", "data", "tags.tsv.gz")
API = "https://danbooru.donmai.us"
UA = "nai-krita-tagbuilder/1.0"
DELAY = 0.8   # seconds between requests; Danbooru allows more, be gentle

CATEGORIES = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}
# Meta tags that describe the upload, not the picture: useless in a prompt.
META_SKIP = re.compile(r"commentary|translat|request|bad id|bad .* id|md5|duplicate|"
                       r"check |revision|third-party|source|artist name|sample|"
                       r"resized|upscaled|scan|photoshop|paid reward|patreon|fanbox|"
                       r"tagme|has .* revision|image|animated|video|audio|sound|"
                       r"lowres|jpeg|transparent|alternate", re.I)
META_KEEP = {"highres", "absurdres", "incredibly absurdres", "lowres", "jpeg artifacts",
             "transparent background"}

NAI_TAGS = [  # NovelAI-specific tags the models were trained with (quality, era, text)
    ("masterpiece", 0), ("best quality", 0), ("amazing quality", 0), ("great quality", 0),
    ("good quality", 0), ("normal quality", 0), ("bad quality", 0), ("worst quality", 0),
    ("very aesthetic", 0), ("aesthetic", 0), ("displeasing", 0), ("very displeasing", 0),
    ("no text", 0), ("text", 0), ("english text", 0),
] + [(f"year {y}", 0) for y in range(2005, 2026)]


def get(path, params):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            print("  retry", attempt + 1, e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed: {url}")


def fetch_tags(min_posts):
    tags, page = {}, 1
    while True:
        rows = get("tags.json", {"search[order]": "count", "search[hide_empty]": "true",
                                 "limit": 1000, "page": page,
                                 "only": "name,category,post_count"})
        if not rows:
            break
        for r in rows:
            if r["post_count"] < min_posts:
                return tags
            cat = CATEGORIES.get(r["category"])
            name = r["name"].replace("_", " ")
            if cat is None:
                continue
            if cat == "meta" and name not in META_KEEP and META_SKIP.search(name):
                continue
            tags[name] = [cat, r["post_count"], []]
        print(f"  tags page {page}: {len(tags)} kept, last count {rows[-1]['post_count']}")
        page += 1
        time.sleep(DELAY)
    return tags


def fetch_aliases(tags):
    page, kept, last_id = 0, 0, None
    while True:
        params = {"search[status]": "active", "limit": 1000,
                  "only": "id,antecedent_name,consequent_name"}
        params["page"] = f"b{last_id}" if last_id else 1
        rows = get("tag_aliases.json", params)
        if not rows:
            break
        for r in rows:
            target = r["consequent_name"].replace("_", " ")
            if target in tags:
                alias = r["antecedent_name"].replace("_", " ")
                if alias not in tags and "|" not in alias:
                    tags[target][2].append(alias)
                    kept += 1
        last_id = min(r["id"] for r in rows)
        page += 1
        if page % 10 == 0:
            print(f"  alias page {page}: {kept} kept")
        time.sleep(DELAY)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-posts", type=int, default=100)
    a = ap.parse_args()
    print("Fetching tags…")
    tags = fetch_tags(a.min_posts)
    print("Fetching aliases…")
    n_alias = fetch_aliases(tags)
    for name, _ in NAI_TAGS:
        if name not in tags:
            tags[name] = ["nai", 0, []]
        elif tags[name][0] == "general":
            tags[name][0] = "nai"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows = sorted(tags.items(), key=lambda kv: (kv[1][0] != "nai", -kv[1][1], kv[0]))
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(f"# Danbooru tags with >= {a.min_posts} posts, fetched "
                f"{time.strftime('%Y-%m-%d')}; see tools/build_tags.py\n")
        for name, (cat, count, aliases) in rows:
            f.write(f"{name}\t{cat}\t{count}\t{'|'.join(sorted(set(aliases)))}\n")
    print(f"wrote {OUT}: {len(tags)} tags, {n_alias} aliases, "
          f"{os.path.getsize(OUT) // 1024} KB")


if __name__ == "__main__":
    main()
