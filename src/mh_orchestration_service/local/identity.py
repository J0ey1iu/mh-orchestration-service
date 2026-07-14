"""Locale parsing and display-name resolution.

Pure functions.  No HTTP, no FastAPI, no Pydantic.
"""

from __future__ import annotations

import json

_SUPPORTED_LOCALES = ("zh", "en")
_DEFAULT_LOCALE = "zh"


def parse_locale(accept_language: str | None = None) -> str:
    """Extract the primary locale tag from an ``Accept-Language`` header.

    Returns one of ``"zh"``, ``"en"``; defaults to ``"zh"``.
    """
    if accept_language:
        lang = accept_language.split(",")[0].split(";")[0].strip().lower()
        if lang in _SUPPORTED_LOCALES:
            return lang
    return _DEFAULT_LOCALE


def resolve_locale(
    value: str,
    value_locale: str | None,
    locale: str,
) -> str:
    if locale and value_locale:
        try:
            locale_map = json.loads(value_locale)
            if isinstance(locale_map, dict) and locale in locale_map:
                return str(locale_map[locale])
        except (json.JSONDecodeError, TypeError):
            pass
    return value


def parse_locale_json(raw: str | None) -> dict[str, str] | None:
    """Parse a locale JSON blob (``{"en": "...", "zh": "..."}``)."""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {k: str(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def resolve_display_name(
    display_name: str,
    display_name_locale: str | None,
    locale: str,
) -> str:
    """Return the locale-appropriate display name, falling back to *display_name*."""
    return resolve_locale(display_name, display_name_locale, locale)


def resolve_description(
    description: str,
    description_locale: str | None,
    locale: str,
) -> str:
    """Return the locale-appropriate description, falling back to *description*."""
    return resolve_locale(description, description_locale, locale)


__all__ = [
    "parse_locale",
    "parse_locale_json",
    "resolve_description",
    "resolve_display_name",
    "resolve_locale",
]
