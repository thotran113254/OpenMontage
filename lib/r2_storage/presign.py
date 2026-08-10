"""Presigned URL + server-side copy helpers for the Vast.ai transfer path.

Kept separate from `sync.py` so `lib.cloud_render` imports one small module
rather than the whole sync engine. `generate_presigned_url` is a local SigV4
computation -- no network call -- so building a fresh client per call here
costs nothing extra; only `object_exists`/`copy_object` actually hit R2.
"""

from __future__ import annotations

from typing import Any


def _client_and_bucket(settings: Any | None) -> tuple[Any, str]:
    from lib.r2_storage.client import build_client
    from lib.r2_storage.config import resolve

    settings = settings or resolve()
    return build_client(settings), settings.bucket


def get_url(key: str, ttl: int, *, settings: Any | None = None) -> str:
    client, bucket = _client_and_bucket(settings)
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl)


def put_url(key: str, ttl: int, *, settings: Any | None = None) -> str:
    """No extra signed params (e.g. ContentType): a signed header the remote
    `curl -X PUT` never sends would make R2 reject the request with a 403."""
    client, bucket = _client_and_bucket(settings)
    return client.generate_presigned_url(
        "put_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl)


def object_exists(key: str, *, settings: Any | None = None) -> bool:
    import botocore.exceptions as botocore_exceptions

    client, bucket = _client_and_bucket(settings)
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except botocore_exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise


def copy_object(src_key: str, dst_key: str, *, settings: Any | None = None) -> None:
    """Server-side copy within the same bucket -- no bytes pass through local,
    so a failed render's staging object never risks the archive key."""
    client, bucket = _client_and_bucket(settings)
    client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src_key}, Key=dst_key)


def delete_object(key: str, *, settings: Any | None = None) -> None:
    client, bucket = _client_and_bucket(settings)
    client.delete_object(Bucket=bucket, Key=key)


def download(key: str, local_path: Any, *, settings: Any | None = None) -> None:
    from pathlib import Path

    client, bucket = _client_and_bucket(settings)
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, key, str(local_path))
