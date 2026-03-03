#!/usr/bin/env python3
"""Build runtime text localization maps from templates.

Adds/updates `runtime_strings` in src/dashboard/i18n/en.json and ar.json.
English values are identity; Arabic values are machine-translated when missing.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Set

from bs4 import BeautifulSoup, Comment

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "src" / "dashboard" / "templates"
I18N_DIR = ROOT / "src" / "dashboard" / "i18n"
EN_PATH = I18N_DIR / "en.json"
AR_PATH = I18N_DIR / "ar.json"

ALPHA_RE = re.compile(r"[A-Za-z]")
SCRIPT_LIT_RE = re.compile(r"\b(?:alert|confirm)\(\s*(['\"])(.*?)\1", re.DOTALL)

SKIP_SUBSTRINGS = (
    "x-text",
    "x-show",
    "x-model",
    "@click",
    "class=",
    "=>",
    "{{",
    "}}",
    "{%",
    "%}",
    "/api/",
    "&&",
    "||",
    "?.",
    "document.",
    "console.",
    "function(",
)

SKIP_CHARS_RE = re.compile(r"[<>{}\[\]=@`]")


def normalize_spaces(value: str) -> str:
    return " ".join((value or "").split())


def should_keep(text: str) -> bool:
    value = normalize_spaces(text)
    if not value:
        return False
    if len(value) < 2 or len(value) > 180:
        return False
    if not ALPHA_RE.search(value):
        return False
    lower = value.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return False
    if SKIP_CHARS_RE.search(value):
        return False
    if any(token in value for token in SKIP_SUBSTRINGS):
        return False
    if value.startswith(("#", ".", "/", "$")):
        return False
    if value.count(":") > 4:
        return False
    if value.count("\"") > 4 or value.count("'") > 6:
        return False
    # very code-like tokens
    if re.search(r"^[a-z_][a-z0-9_]{20,}$", lower):
        return False
    return True


def extract_template_strings(path: Path) -> Set[str]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    candidates: Set[str] = set()

    # JS alerts/confirms are always user-facing.
    for match in SCRIPT_LIT_RE.finditer(raw):
        text = normalize_spaces(match.group(2))
        if should_keep(text):
            candidates.add(text)

    cleaned = re.sub(r"\{#.*?#\}", " ", raw, flags=re.DOTALL)
    cleaned = re.sub(r"\{%.*?%\}", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\{\{.*?\}\}", " ", cleaned, flags=re.DOTALL)

    soup = BeautifulSoup(cleaned, "lxml")

    # Remove comments/scripts/styles from visible text scan.
    for node in soup.find_all(string=lambda t: isinstance(t, Comment)):
        node.extract()
    for node in soup(["script", "style"]):
        node.decompose()

    for text_node in soup.find_all(string=True):
        text = normalize_spaces(str(text_node))
        if should_keep(text):
            candidates.add(text)

    for tag in soup.find_all(True):
        for attr in ("placeholder", "title", "aria-label"):
            value = tag.attrs.get(attr)
            if isinstance(value, str):
                text = normalize_spaces(value)
                if should_keep(text):
                    candidates.add(text)

    return candidates


def extract_all_strings() -> Set[str]:
    all_candidates: Set[str] = set()
    for template_path in sorted(TEMPLATES_DIR.glob("*.html")):
        all_candidates.update(extract_template_strings(template_path))
    return all_candidates


def _translate_batch_en_to_ar(lines: Iterable[str]) -> Dict[str, str]:
    items = [line for line in lines if line]
    if not items:
        return {}
    joined = "\n".join(items)
    url = (
        "https://translate.googleapis.com/translate_a/single?"
        + urllib.parse.urlencode(
            {
                "client": "gtx",
                "sl": "en",
                "tl": "ar",
                "dt": "t",
                "q": joined,
            }
        )
    )
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    translated_parts = []
    for chunk in payload[0] if payload and isinstance(payload, list) else []:
        if isinstance(chunk, list) and chunk:
            translated_parts.append(chunk[0] or "")
    translated_text = "".join(translated_parts)
    translated_lines = translated_text.split("\n")
    if len(translated_lines) < len(items):
        translated_lines.extend([""] * (len(items) - len(translated_lines)))
    mapping: Dict[str, str] = {}
    for original, translated in zip(items, translated_lines):
        mapping[original] = (translated or original).strip() or original
    return mapping


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    en_data = load_json(EN_PATH)
    ar_data = load_json(AR_PATH)

    en_runtime = dict(en_data.get("runtime_strings") or {})
    ar_runtime = dict(ar_data.get("runtime_strings") or {})

    candidates = sorted(extract_all_strings())

    new_count = 0
    translated_count = 0

    for text in candidates:
        if text not in en_runtime:
            en_runtime[text] = text
            new_count += 1

    missing_ar = [text for text in candidates if not str(ar_runtime.get(text, "")).strip()]
    batch_size = 40
    for idx in range(0, len(missing_ar), batch_size):
        chunk = missing_ar[idx: idx + batch_size]
        try:
            translated = _translate_batch_en_to_ar(chunk)
        except Exception:
            translated = {text: text for text in chunk}
        for text in chunk:
            ar_runtime[text] = translated.get(text, text)
            translated_count += 1

    en_data["runtime_strings"] = dict(sorted(en_runtime.items(), key=lambda kv: kv[0].lower()))
    ar_data["runtime_strings"] = dict(sorted(ar_runtime.items(), key=lambda kv: kv[0].lower()))

    save_json(EN_PATH, en_data)
    save_json(AR_PATH, ar_data)

    print(f"Candidates: {len(candidates)}")
    print(f"EN runtime strings: {len(en_runtime)} (new {new_count})")
    print(f"AR translated this run: {translated_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
