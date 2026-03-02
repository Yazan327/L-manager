#!/usr/bin/env python3
"""Detect new hardcoded UI literals that should be localized.

Uses a baseline file so existing legacy literals do not block CI. CI fails only
when new issues are introduced.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "src" / "dashboard" / "templates"
BASELINE_PATH = ROOT / "scripts" / "i18n" / "hardcoded_baseline.txt"

ALPHA = re.compile(r"[A-Za-z]")


def check_file(path: Path):
    issues = []
    text = path.read_text(encoding="utf-8", errors="ignore")

    # 1) alert/confirm with inline string literal (unless already routed through i18nT)
    js_call = re.compile(r"""\b(alert|confirm)\(\s*(['"])(.*?)\2""", re.DOTALL)
    for match in js_call.finditer(text):
        raw = " ".join(match.group(3).split())
        if not raw or not ALPHA.search(raw):
            continue
        if "i18nT(" in raw:
            continue
        issues.append(f"{path}: {match.group(1)} literal -> {raw}")

    # 2) placeholder/title literals with alphabetic chars not wrapped in t()
    attr = re.compile(r'(placeholder|title)\s*=\s*"([^"]+)"')
    for match in attr.finditer(text):
        raw = " ".join(match.group(2).split())
        if not raw or not ALPHA.search(raw):
            continue
        if "{{" in raw or "{%" in raw or "t(" in raw:
            continue
        issues.append(f"{path}: {match.group(1)} literal -> {raw}")

    return issues


def load_baseline() -> set[str]:
    if not BASELINE_PATH.exists():
        return set()
    return {line.strip() for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_baseline(entries: list[str]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text("\n".join(sorted(set(entries))) + "\n", encoding="utf-8")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true", help="Update hardcoded baseline from current scan")
    args = parser.parse_args()

    all_issues = []
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        all_issues.extend(check_file(path))

    if args.update_baseline:
        save_baseline(all_issues)
        print(f"Baseline updated: {BASELINE_PATH} ({len(set(all_issues))} entries).")
        return 0

    baseline = load_baseline()
    current = set(all_issues)
    new_issues = sorted(current - baseline)

    if new_issues:
        print("New hardcoded localization issues found:")
        for issue in new_issues:
            print(f"  - {issue}")
        return 1

    print(f"Hardcoded localization check passed (baseline={len(baseline)}, current={len(current)}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
