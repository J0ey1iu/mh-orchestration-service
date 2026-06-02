from mh_orchestration_service.database._opengauss import OpenGaussDatabase
from mh_orchestration_service.database._protocol import DatabaseProtocol
from mh_orchestration_service.database._registry import DatabaseBackend
from mh_orchestration_service.database._sqlite import SqliteDatabase

DatabaseBackend.register("sqlite", SqliteDatabase)
DatabaseBackend.register("opengauss", OpenGaussDatabase)

__all__ = [
    "DatabaseProtocol",
    "DatabaseBackend",
    "SqliteDatabase",
    "OpenGaussDatabase",
]
