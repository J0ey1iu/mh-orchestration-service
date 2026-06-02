from __future__ import annotations


class DatabaseBackend:
    _drivers: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, driver_cls: type) -> None:
        cls._drivers[name] = driver_cls

    @classmethod
    def get(cls, name: str) -> type:
        if name not in cls._drivers:
            raise ValueError(
                f"Unknown database backend: {name!r}. "
                f"Available backends: {list(cls._drivers)}"
            )
        return cls._drivers[name]
