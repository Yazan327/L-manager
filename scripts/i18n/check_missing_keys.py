#!/usr/bin/env python3
"""Fail when translation keys used by templates are missing from en/ar dictionaries."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "src" / "dashboard" / "templates"
I18N_DIR = ROOT / "src" / "dashboard" / "i18n"


def flatten_keys(node, prefix=""):
    keys = set()
    if isinstance(node, dict):
        for key, value in node.items():
            full = f"{prefix}.{key}" if prefix else key
            keys.add(full)
            keys.update(flatten_keys(value, full))
    return keys


def collect_template_keys() -> set[str]:
    keys: set[str] = set()
    patterns = [
        re.compile(r"""t\(\s*['"]([^'"]+)['"]"""),
        re.compile(r"""i18nT\(\s*['"]([^'"]+)['"]"""),
    ]
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            keys.update(pattern.findall(text))
    # only canonical dotted keys are checked; literal fallback strings are ignored
    canonical = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)+$")
    return {key for key in keys if canonical.match(key)}


def load_dict(lang: str):
    path = I18N_DIR / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    used_keys = sorted(collect_template_keys())
    en_keys = flatten_keys(load_dict("en"))
    ar_keys = flatten_keys(load_dict("ar"))

    missing_en = [k for k in used_keys if k not in en_keys]
    missing_ar = [k for k in used_keys if k not in ar_keys]

    has_error = False
    if missing_en:
        has_error = True
        print("Missing keys in en.json:")
        for key in missing_en:
            print(f"  - {key}")
    if missing_ar:
        has_error = True
        print("Missing keys in ar.json:")
        for key in missing_ar:
            print(f"  - {key}")

    if has_error:
        return 1

    print(f"i18n key check passed ({len(used_keys)} keys verified).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
