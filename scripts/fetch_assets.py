"""Download externally licensed model assets and verify their exact baseline hash."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["brain", "edge"])
    args = parser.parse_args()
    for asset in json.loads((ROOT / "config/assets.json").read_text()):
        if asset["role"] != args.role:
            continue
        dest = ROOT / "assets/models" / asset["path"]
        if dest.exists() and digest(dest) == asset["sha256"]:
            print("Verified", asset["path"])
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".partial")
        print("Downloading", asset["path"], flush=True)
        with urllib.request.urlopen(asset["url"], timeout=120) as source, partial.open("wb") as out:
            while block := source.read(1024 * 1024):
                out.write(block)
        if digest(partial) != asset["sha256"]:
            raise RuntimeError(f"Checksum mismatch: {partial}; existing model preserved")
        partial.replace(dest)


if __name__ == "__main__":
    main()
