"""boto3 S3 client factory for Cloudflare R2.

R2 rejects two things AWS S3 clients do by default:
  - a non-"auto" region (produces a 301 redirect)
  - the CRC32 integrity checksum botocore >= 1.36 computes on every
    PutObject/UploadPart (request_checksum_calculation must be
    "when_required", verified empirically against botocore 1.43.66 as a
    top-level `Config` kwarg -- not nested under `s3={...}`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from lib.r2_storage.config import R2Settings

# botocore DEBUG logging prints the SigV4 Authorization header. Pin to at
# least WARNING (raise DEBUG/INFO/NOTSET up to it) but never lower a level a
# caller explicitly set stricter than WARNING (e.g. ERROR/CRITICAL).
_botocore_logger = logging.getLogger("botocore")
if _botocore_logger.level < logging.WARNING:
    _botocore_logger.setLevel(logging.WARNING)

_ENDPOINT_MASK_RE = re.compile(r"https://[^.]+\.r2\.cloudflarestorage\.com")


def mask_endpoint(url: str) -> str:
    """Hide the account id embedded in the endpoint host for log lines."""
    if not url:
        return url
    return _ENDPOINT_MASK_RE.sub("https://<account>.r2.cloudflarestorage.com", url)


def build_client(settings: R2Settings) -> Any:
    """Return a configured boto3 S3 client for R2. Lazy boto3 import."""
    import boto3
    from botocore.config import Config

    access_key, secret_key = settings._credentials()
    config = Config(
        region_name="auto",
        signature_version="s3v4",
        retries={"mode": "adaptive", "max_attempts": 5},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=config,
    )


def transfer_config(settings: R2Settings) -> Any:
    """boto3.s3.transfer.TransferConfig sized from the resolved settings."""
    from boto3.s3.transfer import TransferConfig

    return TransferConfig(
        multipart_threshold=settings.multipart_threshold_mb * 1024 * 1024,
        multipart_chunksize=settings.multipart_chunksize_mb * 1024 * 1024,
        max_concurrency=settings.max_concurrency,
    )
