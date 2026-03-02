#!/usr/bin/env python3
"""Extract probable user-facing strings from templates for i18n inventory."""

from __future__ import annotations

import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "src" / "dashboard" / "templates"
OUT_PATH = ROOT / "documentation" / "i18n-inventory.csv"

ALPHA_RE = re.compile(r"[A-Za-z]")
T_CALL_RE = re.compile(r"(?:\{\{\s*)?t\(\s*['\"]")
JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)


def slugify(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return value[:48] or "text"


def should_keep(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if len(text) <= 1:
        return False
    if not ALPHA_RE.search(text):
        return False
    if text.startswith("http://") or text.startswith("https://"):
        return False
    return True


def proposed_key(file_stem: str, text: str, kind: str) -> str:
    prefix_map = {
        "alert": "alerts",
        "confirm": "confirm",
        "placeholder": "placeholders",
        "text": "ui",
    }
    namespace = prefix_map.get(kind, "ui")
    return f"{file_stem}.{namespace}.{slugify(text)}"


def extract_rows(path: Path) -> list[dict[str, str]]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    rows: list[dict[str, str]] = []
    stem = path.stem

    # JS alert/confirm string literals
    for kind in ("alert", "confirm"):
        pattern = re.compile(rf"\b{kind}\(\s*([\"'])(.*?)\1", re.DOTALL)
        for match in pattern.finditer(content):
            raw = " ".join(match.group(2).split())
            if not should_keep(raw):
                continue
            rows.append(
                {
                    "file": str(path.relative_to(ROOT)),
                    "kind": kind,
                    "text": raw,
                    "proposed_key": proposed_key(stem, raw, kind),
                    "status": "todo",
                }
            )

    # Placeholder/title literals in attributes
    attr_pattern = re.compile(r'(placeholder|title)\s*=\s*"([^"]+)"')
    for match in attr_pattern.finditer(content):
        raw = " ".join(match.group(2).split())
        if "t(" in raw or "{{" in raw or "{%" in raw:
            continue
        if not should_keep(raw):
            continue
        rows.append(
            {
                "file": str(path.relative_to(ROOT)),
                "kind": "placeholder",
                "text": raw,
                "proposed_key": proposed_key(stem, raw, "placeholder"),
                "status": "todo",
            }
        )

    # Visible text nodes (best-effort)
    cleaned = JINJA_RE.sub("", content)
    text_pattern = re.compile(r">([^<]+)<")
    for match in text_pattern.finditer(cleaned):
        raw = " ".join(match.group(1).split())
        if not should_keep(raw):
            continue
        if T_CALL_RE.search(raw):
            continue
        rows.append(
            {
                "file": str(path.relative_to(ROOT)),
                "kind": "text",
                "text": raw,
                "proposed_key": proposed_key(stem, raw, "text"),
                "status": "todo",
            }
        )

    return rows


def main() -> int:
    rows: list[dict[str, str]] = []
    for template in sorted(TEMPLATES_DIR.glob("*.html")):
        rows.extend(extract_rows(template))

    # de-duplicate by file+kind+text
    seen = set()
    deduped = []
    for row in rows:
        key = (row["file"], row["kind"], row["text"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["file", "kind", "text", "proposed_key", "status"],
        )
        writer.writeheader()
        writer.writerows(deduped)

    print(f"Wrote {len(deduped)} rows to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
