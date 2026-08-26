#!/usr/bin/env python3
"""Sync locale files against en.json.

Adds every key that exists only in en.json to the other locale files, copying
the English value so files stay valid; translators replace the copies after.
Never deletes anything.

  python scripts/locale-scaffold.py                 # add missing keys
  python scripts/locale-scaffold.py --check         # exit 1 if anything missing
  python scripts/locale-scaffold.py --untranslated  # list copies still == en
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "locales"
SOURCE = "en"


def load(code: str):
    text = (LOCALES / f"{code}.json").read_text(encoding="utf-8")
    return json.loads(text, object_pairs_hook=collections.OrderedDict)


def flatten(obj, prefix=""):
    out = {}
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten(value, path))
        else:
            out[path] = value
    return out


def insert(tree, dotted, value):
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def prune_empty(node):
    changed = False
    for key in list(node):
        if isinstance(node[key], dict):
            changed |= prune_empty(node[key])
            if not node[key]:
                del node[key]
                changed = True
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--untranslated", action="store_true")
    args = ap.parse_args()

    english = flatten(load(SOURCE))
    status = 0
    for path in sorted(LOCALES.glob("*.json")):
        code = path.stem
        if code == SOURCE:
            continue
        tree = load(code)
        flat = flatten(tree)
        missing = [k for k in english if k not in flat]
        extra = [k for k in flat if k not in english]
        if args.untranslated:
            same = sorted(k for k in english
                          if k in flat and flat[k] == english[k] and english[k].strip())
            print(f"{code}: {len(same)} untranslated-looking keys")
            for key in same:
                print(f"  {key}")
            continue
        if extra:
            print(f"{code}: WARNING {len(extra)} keys absent from en.json (kept as-is)")
        for key in missing:
            insert(tree, key, english[key])
        while prune_empty(tree):
            pass
        if missing and not args.check:
            path.write_text(json.dumps(tree, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"{code}: {'MISSING ' if missing else 'ok '}"
              f"({len(missing)} missing, {len(extra)} extra)")
        if missing and args.check:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
