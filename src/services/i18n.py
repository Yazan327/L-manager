"""
Localization helpers for EN/AR UI translation and message localization.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


LOGGER = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "ar")
DEFAULT_LANGUAGE = "en"

_I18N_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "i18n"


def normalize_language(lang: Optional[str]) -> str:
    value = (lang or "").strip().lower()
    if value in SUPPORTED_LANGUAGES:
        return value
    if value.startswith("ar"):
        return "ar"
    if value.startswith("en"):
        return "en"
    return DEFAULT_LANGUAGE


def get_direction(lang: Optional[str]) -> str:
    return "rtl" if normalize_language(lang) == "ar" else "ltr"


@lru_cache(maxsize=8)
def load_dictionary(lang: str) -> Dict[str, Any]:
    normalized = normalize_language(lang)
    path = _I18N_DIR / f"{normalized}.json"
    if not path.exists():
        LOGGER.warning("i18n dictionary not found for lang=%s at %s", normalized, path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("failed to load i18n dictionary lang=%s: %s", normalized, exc)
        return {}


def get_dictionary(lang: Optional[str]) -> Dict[str, Any]:
    return load_dictionary(normalize_language(lang))


def _lookup_key(data: Dict[str, Any], key: str) -> Any:
    if not isinstance(data, dict):
        return None
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def translate(
    key: str,
    lang: Optional[str] = None,
    default: Optional[str] = None,
    **vars: Any,
) -> str:
    normalized = normalize_language(lang)
    value = _lookup_key(get_dictionary(normalized), key)
    if value is None and normalized != DEFAULT_LANGUAGE:
        value = _lookup_key(get_dictionary(DEFAULT_LANGUAGE), key)
    if not isinstance(value, str):
        value = default if default is not None else key

    if vars:
        try:
            value = value.format(**vars)
        except Exception:
            # Keep untranslated value if template variables mismatch.
            pass
    return value


def detect_accept_language(header_value: Optional[str]) -> str:
    raw = (header_value or "").lower()
    if not raw:
        return DEFAULT_LANGUAGE
    # Favor Arabic when explicitly preferred.
    if re.search(r"\bar\b", raw):
        return "ar"
    return "en"


def get_language(user=None, session_obj=None, request_obj=None) -> str:
    if session_obj is not None:
        candidate = normalize_language(session_obj.get("ui_lang"))
        if candidate in SUPPORTED_LANGUAGES:
            return candidate

    if user is not None:
        preferred = normalize_language(getattr(user, "preferred_language", None))
        if preferred in SUPPORTED_LANGUAGES:
            return preferred

    if request_obj is not None:
        accept = detect_accept_language(request_obj.headers.get("Accept-Language"))
        if accept in SUPPORTED_LANGUAGES:
            return accept

    return DEFAULT_LANGUAGE


def set_language(user, lang: str) -> str:
    normalized = normalize_language(lang)
    if user is not None and hasattr(user, "preferred_language"):
        user.preferred_language = normalized
    return normalized


def _legacy_entry(message: str, lang: Optional[str]) -> Optional[Any]:
    dictionary = get_dictionary(lang)
    legacy = dictionary.get("legacy_messages") if isinstance(dictionary, dict) else None
    if isinstance(legacy, dict):
        return legacy.get(message)
    return None


def localize_legacy_message(message: str, lang: Optional[str]) -> str:
    if not message:
        return message
    entry = _legacy_entry(message, lang)
    if entry is None and normalize_language(lang) != DEFAULT_LANGUAGE:
        entry = _legacy_entry(message, DEFAULT_LANGUAGE)
    if isinstance(entry, dict):
        return str(entry.get("text") or message)
    if isinstance(entry, str):
        return entry
    return message


def get_error_code_for_legacy_message(message: str) -> Optional[str]:
    if not message:
        return None
    entry = _legacy_entry(message, DEFAULT_LANGUAGE)
    if isinstance(entry, dict):
        code = (entry.get("code") or "").strip().lower()
        if code:
            return code
    return None


def _slugify_error_code(message: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (message or "").strip().lower()).strip("_")
    return base[:64] if base else "error"


def translate_with_fallback(key: str, lang: Optional[str], fallback: str, **vars: Any) -> str:
    return translate(key=key, lang=lang, default=fallback, **vars)


def localize_error_payload(payload: Dict[str, Any], lang: Optional[str]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    error_text = payload.get("error")
    if isinstance(error_text, str) and error_text.strip():
        code = payload.get("code") or get_error_code_for_legacy_message(error_text)
        if not code:
            code = _slugify_error_code(error_text)
        if not payload.get("code"):
            payload["code"] = code
        payload["error"] = localize_legacy_message(error_text, lang)
    return payload
