from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from mh_orchestration_service.services.database import get_db

logger = logging.getLogger("orchestration.health")

health_router = APIRouter(tags=["health"])


@health_router.get("/health")
async def health():
    return {"status": "ok"}


@health_router.get("/ready")
async def ready():
    try:
        db = get_db()
        await db.execute("SELECT 1")
    except Exception as e:
        logger.warning("Readiness check failed: %s", e)
        raise HTTPException(status_code=503, detail="Not ready") from e
    return {"status": "ready"}
