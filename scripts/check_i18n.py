#!/usr/bin/env python3
"""Check i18n key completeness between en.json and ar.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "src" / "dashboard" / "i18n"


def flatten(node, prefix=""):
    keys = set()
    if isinstance(node, dict):
        for key, value in node.items():
            full = f"{prefix}.{key}" if prefix else key
            keys.add(full)
            keys.update(flatten(value, full))
    return keys


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    en_path = I18N_DIR / "en.json"
    ar_path = I18N_DIR / "ar.json"

    if not en_path.exists() or not ar_path.exists():
        print("Missing i18n files.")
        return 2

    en = load_json(en_path)
    ar = load_json(ar_path)
    en_keys = flatten(en)
    ar_keys = flatten(ar)

    missing_in_ar = sorted(en_keys - ar_keys)
    extra_in_ar = sorted(ar_keys - en_keys)

    has_error = False
    if missing_in_ar:
        has_error = True
        print("Missing keys in ar.json:")
        for key in missing_in_ar:
            print(f"  - {key}")
    if extra_in_ar:
        print("Orphan keys only in ar.json:")
        for key in extra_in_ar:
            print(f"  - {key}")

    if has_error:
        return 1
    print("i18n check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

