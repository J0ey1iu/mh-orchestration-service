from fastapi import APIRouter

from mh_orchestration_service.api.agents import router as agents_router
from mh_orchestration_service.api.auth_routes import auth_router
from mh_orchestration_service.api.chat import router as chat_router
from mh_orchestration_service.api.guide import router as guide_router
from mh_orchestration_service.api.management import router as management_router
from mh_orchestration_service.api.runtime_tools import router as runtime_tools_router
from mh_orchestration_service.api.scenarios import router as scenarios_router
from mh_orchestration_service.api.sessions import router as sessions_router
from mh_orchestration_service.api.tools import router as tools_router
from mh_orchestration_service.monitoring.api import metrics_router
from mh_orchestration_service.monitoring.health import health_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(chat_router)
router.include_router(scenarios_router)
router.include_router(sessions_router)
router.include_router(guide_router)
router.include_router(agents_router)
router.include_router(tools_router)
router.include_router(runtime_tools_router)
router.include_router(management_router)
router.include_router(health_router)
router.include_router(metrics_router)
