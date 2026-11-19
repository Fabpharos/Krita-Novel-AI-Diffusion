"""Build dist/nai_krita.zip for Tools > Scripts > Import Python Plugin from File.

    python tools/package.py
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", "nai_krita.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(ROOT, "nai_krita.desktop"), "nai_krita.desktop")
        z.write(os.path.join(ROOT, "LICENSE"), "nai_krita/LICENSE")
        for folder, dirs, files in os.walk(os.path.join(ROOT, "nai_krita")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            # Krita's importer finds the plugin by its directory entry
            # ("nai_krita/"), so every folder needs one, not just its files.
            rel = os.path.relpath(folder, ROOT).replace(os.sep, "/") + "/"
            z.writestr(zipfile.ZipInfo(rel), b"")
            for f in sorted(files):
                if f.endswith((".py", ".tsv.gz")):
                    z.write(os.path.join(folder, f), rel + f)
    print("wrote", out)


if __name__ == "__main__":
    main()
