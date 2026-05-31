from pydantic import BaseModel


class ConfigMapping(BaseModel):
    key_mapping: dict[str, str] = {}
    sensitive_keys: set[str] = set()
