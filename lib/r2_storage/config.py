"""Cloudflare R2 config: built-in defaults -> config/r2-storage.json -> env (creds only).

Mirrors `lib/cloud_render/config.py` (`BUILTIN_DEFAULTS` / `_read_json` /
`_clean` trio). Credentials and deployment-specific values (endpoint, public
base url) never live in the committed JSON -- only in `.env`, read at call
time so a logged/serialized `R2Settings` cannot leak a secret.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from lib.env_loader import get_env, load_env

REPO_ROOT = Path(__file__).resolve().parents[2]
GLOBAL_CONFIG_PATH = REPO_ROOT / "config" / "r2-storage.json"

BUILTIN_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "bucket": "",
    "prefix": "projects",
    "auto_sync": False,
    "sync_after_stage": False,
    "exclude": ["*.log", "*.tmp", "render_public/**", "__pycache__/**", ".r2sync.json"],
    "multipart_threshold_mb": 64,
    "multipart_chunksize_mb": 64,
    "max_concurrency": 4,
    "presign_expiry_seconds": 604800,
    "max_upload_mb_per_sync": 5000,
}

KEYS = tuple(BUILTIN_DEFAULTS)

# Any JSON key whose name matches this must never live in the committed
# config -- credentials belong in .env only.
_CREDENTIAL_KEY_RE = re.compile(r"(secret|access[_-]?key|token|password)", re.IGNORECASE)

_PRESIGN_MIN_SECONDS = 1
_PRESIGN_MAX_SECONDS = 604800  # 7 days -- R2/SigV4 hard ceiling


class R2ConfigError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean(layer: Any) -> dict[str, Any]:
    """Keep only known keys with a real value; `None` is dropped, not an override."""
    if not isinstance(layer, dict):
        return {}
    return {key: value for key, value in layer.items()
            if key in BUILTIN_DEFAULTS and value is not None}


def _reject_credential_keys(raw: dict[str, Any]) -> None:
    for key in raw:
        if _CREDENTIAL_KEY_RE.search(key):
            raise R2ConfigError(
                f"config/r2-storage.json không được chứa key credential ({key!r}) -- "
                "credentials chỉ đọc từ .env")


@dataclass(frozen=True)
class R2Settings:
    enabled: bool
    bucket: str
    prefix: str
    auto_sync: bool
    sync_after_stage: bool
    exclude: tuple[str, ...]
    multipart_threshold_mb: int
    multipart_chunksize_mb: int
    max_concurrency: int
    presign_expiry_seconds: int
    max_upload_mb_per_sync: int
    endpoint_url: str
    public_base_url: str | None

    def _credentials(self) -> tuple[str | None, str | None]:
        """Read from os.environ at call time -- never stored as an attribute."""
        return (get_env("CLOUDFLARE_R2_ACCESS_KEY_ID"),
                get_env("CLOUDFLARE_R2_SECRET_ACCESS_KEY"))

    def public_url(self, key: str) -> str | None:
        if not self.public_base_url:
            return None
        return f"{self.public_base_url}/{quote(key)}"


def _resolve_endpoint_url() -> str:
    explicit = get_env("CLOUDFLARE_R2_ENDPOINT_URL")
    if explicit:
        return explicit.rstrip("/")
    account_id = get_env("CLOUDFLARE_R2_ACCOUNT_ID")
    if account_id:
        return f"https://{account_id}.r2.cloudflarestorage.com"
    return ""


def _resolve_public_base_url() -> str | None:
    raw = get_env("CLOUDFLARE_R2_PUBLIC_BASE_URL")
    if not raw:
        return None
    value = raw.rstrip("/")
    if not value.startswith("https://"):
        raise R2ConfigError(
            f"CLOUDFLARE_R2_PUBLIC_BASE_URL phải bắt đầu bằng https://, nhận {raw!r}")
    return value


def resolve(path: Path | None = None) -> R2Settings:
    """built-in defaults -> config/r2-storage.json -> env (bucket fallback only)."""
    load_env()
    raw = _read_json(path or GLOBAL_CONFIG_PATH)
    _reject_credential_keys(raw)
    merged = {**BUILTIN_DEFAULTS, **_clean(raw)}

    bucket = merged.get("bucket") or get_env("CLOUDFLARE_R2_BUCKET", "") or ""
    presign_expiry = _clamp(
        merged.get("presign_expiry_seconds"), _PRESIGN_MIN_SECONDS, _PRESIGN_MAX_SECONDS,
        BUILTIN_DEFAULTS["presign_expiry_seconds"])

    settings = R2Settings(
        enabled=bool(merged.get("enabled", False)),
        bucket=bucket,
        prefix=str(merged.get("prefix", "projects")),
        auto_sync=bool(merged.get("auto_sync", False)),
        sync_after_stage=bool(merged.get("sync_after_stage", False)),
        exclude=tuple(merged.get("exclude", [])),
        multipart_threshold_mb=int(merged.get("multipart_threshold_mb", 64)),
        multipart_chunksize_mb=int(merged.get("multipart_chunksize_mb", 64)),
        max_concurrency=int(merged.get("max_concurrency", 4)),
        presign_expiry_seconds=presign_expiry,
        max_upload_mb_per_sync=int(merged.get("max_upload_mb_per_sync", 5000)),
        endpoint_url=_resolve_endpoint_url(),
        public_base_url=_resolve_public_base_url(),
    )
    return validate(settings)


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def validate(settings: R2Settings) -> R2Settings:
    """Fail loud on malformed settings -- never silently widen a guard."""
    if not settings.enabled:
        # Bucket may legitimately be empty on a clean, disabled checkout.
        return settings
    if not settings.bucket or not isinstance(settings.bucket, str):
        raise R2ConfigError("bucket rỗng hoặc không hợp lệ -- đặt CLOUDFLARE_R2_BUCKET hoặc "
                             "'bucket' trong config/r2-storage.json")
    for size_key in ("multipart_threshold_mb", "multipart_chunksize_mb", "max_concurrency",
                     "max_upload_mb_per_sync"):
        value = getattr(settings, size_key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise R2ConfigError(f"{size_key} phải là số nguyên > 0, nhận {value!r}")
    return settings


def is_configured() -> tuple[bool, list[str]]:
    """No network call. Reports which required env vars are missing."""
    load_env()
    required = ("CLOUDFLARE_R2_ACCESS_KEY_ID", "CLOUDFLARE_R2_SECRET_ACCESS_KEY")
    missing = [name for name in required if not get_env(name)]
    settings = resolve()
    if not settings.endpoint_url:
        missing.append("CLOUDFLARE_R2_ENDPOINT_URL")
    return (len(missing) == 0, missing)
