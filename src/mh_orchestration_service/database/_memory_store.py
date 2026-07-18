from __future__ import annotations

from typing import Callable

from minimal_harness.memory import Memory

from mh_orchestration_service.adapters import SessionStoreProtocol
from mh_orchestration_service.database._session import Session, SessionSummary

MemoryFactory = Callable[[], Memory]

__all__ = [
    "MemoryFactory",
    "Session",
    "SessionStoreProtocol",
    "SessionSummary",
]
