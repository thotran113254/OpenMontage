"""Cloudflare R2 object storage -- config, client, sync engine.

Public surface: `resolve`, `validate`, `is_configured`, `build_client`.
Opt-in (see config/r2-storage.json's "enabled"); no network call at import.
"""

from lib.r2_storage.client import build_client
from lib.r2_storage.config import R2ConfigError, R2Settings, is_configured, resolve, validate

__all__ = [
    "R2ConfigError",
    "R2Settings",
    "build_client",
    "is_configured",
    "resolve",
    "validate",
]
