"""Wildcard-aware permission matching.

Pure function.  Re-exported from ``mh_orchestration_service.auth`` so
the HTTP layer keeps importing through the same public surface.
"""

from __future__ import annotations


def match_permission(user_permissions: list[str], target: str) -> bool:
    """Return True iff *user_permissions* grants *target*.

    Each permission is a ``:``-separated triple (``action:resource:target``).
    ``*`` in any segment acts as a wildcard.
    """
    target_parts = target.split(":", maxsplit=2)
    if len(target_parts) != 3:
        return False
    for p in user_permissions:
        parts = p.split(":", maxsplit=2)
        if len(parts) != 3:
            continue
        if all(parts[i] == target_parts[i] or parts[i] == "*" for i in range(3)):
            return True
    return False


__all__ = ["match_permission"]
