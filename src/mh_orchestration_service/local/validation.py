"""Provider / model name validation.

Pure functions.  The TUI imports these at startup to catch typos in
``MH_PROVIDER`` against the live ``/management/providers`` list returned
by the orch-service.
"""

from __future__ import annotations


class UnknownProviderError(ValueError):
    """Raised when a configured provider name is not in the live registry."""


def validate_provider_name(
    name: str,
    available: list[str],
) -> None:
    """Raise :class:`UnknownProviderError` if *name* is not in *available*.

    Empty / whitespace-only *name* is treated as missing — raises too,
    because every LLM-backed agent must declare a non-empty provider.
    """
    if not name or not name.strip():
        raise UnknownProviderError(
            "Provider name is empty; set MH_PROVIDER or define provider "
            "per-agent in agents.json"
        )
    if name not in available:
        rendered = ", ".join(available) if available else "(none)"
        raise UnknownProviderError(
            f"Unknown provider '{name}' — backend reports: {rendered}"
        )


def validate_model_name(
    name: str,
    available: list[str] | None = None,
) -> None:
    """Raise :class:`UnknownProviderError` if *name* is empty.

    Model catalogs are not currently exposed by the orch-service; *available*
    is accepted for forward-compatibility but currently unused.
    """
    if available is not None and available and name not in available:
        rendered = ", ".join(available)
        raise UnknownProviderError(
            f"Unknown model '{name}' — backend reports: {rendered}"
        )
    if not name or not name.strip():
        raise UnknownProviderError(
            "Model name is empty; set MH_MODEL or define model per-agent"
        )


__all__ = [
    "UnknownProviderError",
    "validate_provider_name",
    "validate_model_name",
]
