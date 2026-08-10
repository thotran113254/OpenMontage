"""Action implementations for the `cloudflare_r2` BaseTool.

Split out of `tools/storage/cloudflare_r2.py` to keep that file under the
repo's 200 LOC modularization rule. Pure functions: (client, settings,
inputs[, transfer_config]) -> dict, no ToolResult wrapping -- the caller
(the tool) owns error translation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lib.r2_storage.manifest import file_md5

_PRESIGN_MIN_SECONDS = 1
_PRESIGN_MAX_SECONDS = 604800  # 7 days -- SigV4 hard ceiling


def upload(client: Any, settings: Any, inputs: dict[str, Any], transfer_config: Any) -> dict[str, Any]:
    local_path = Path(inputs["local_path"])
    key = inputs["remote_key"]
    # Chunked (8 MiB) hashing -- a multi-GB render must not load whole into RAM.
    md5 = file_md5(local_path)
    extra_args: dict[str, Any] = {"Metadata": {"local-md5": md5}}
    if inputs.get("content_type"):
        extra_args["ContentType"] = inputs["content_type"]
    size = local_path.stat().st_size
    client.upload_file(str(local_path), settings.bucket, key,
                        Config=transfer_config(settings), ExtraArgs=extra_args)
    head = client.head_object(Bucket=settings.bucket, Key=key)
    multipart = size > settings.multipart_threshold_mb * 1024 * 1024
    return {"key": key, "bytes": size, "etag": head.get("ETag", "").strip('"'), "multipart": multipart}


def download(client: Any, settings: Any, inputs: dict[str, Any], transfer_config: Any) -> dict[str, Any]:
    key = inputs["remote_key"]
    local_path = Path(inputs["local_path"])
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(settings.bucket, key, str(local_path), Config=transfer_config(settings))
    return {"local_path": str(local_path), "bytes": local_path.stat().st_size}


def list_objects(client: Any, settings: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    max_keys = min(int(inputs.get("max_keys", 1000)), 1000)
    response = client.list_objects_v2(Bucket=settings.bucket, Prefix=inputs.get("prefix", ""),
                                       MaxKeys=max_keys)
    objects = [{"key": o["Key"], "size": o["Size"], "last_modified": o["LastModified"].isoformat(),
                "etag": o["ETag"].strip('"')} for o in response.get("Contents", [])]
    return {"objects": objects, "truncated": bool(response.get("IsTruncated"))}


def delete(client: Any, settings: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    key = inputs["remote_key"]
    if "*" in key or key.endswith("/"):
        raise ValueError(f"remote_key phải là một key chính xác, nhận {key!r}")
    client.delete_object(Bucket=settings.bucket, Key=key)
    return {"key": key, "deleted": True}


def _clamp_expiry(settings: Any, inputs: dict[str, Any]) -> int:
    requested = inputs.get("expires_in", settings.presign_expiry_seconds)
    try:
        value = int(requested)
    except (TypeError, ValueError):
        value = settings.presign_expiry_seconds
    return max(_PRESIGN_MIN_SECONDS, min(_PRESIGN_MAX_SECONDS, value))


def presigned(client: Any, settings: Any, inputs: dict[str, Any], method: str) -> dict[str, Any]:
    key = inputs["remote_key"]
    expires_in = _clamp_expiry(settings, inputs)
    url = client.generate_presigned_url(
        method, Params={"Bucket": settings.bucket, "Key": key}, ExpiresIn=expires_in)
    return {"url": url, "expires_at": time.time() + expires_in, "key": key}


def delivery_url(client: Any, settings: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    key = inputs["remote_key"]
    public = settings.public_url(key)
    if public:
        return {"url": public, "kind": "public", "key": key}
    result = presigned(client, settings, inputs, "get_object")
    result["kind"] = "presigned"
    result["warning"] = ("Chưa cấu hình CLOUDFLARE_R2_PUBLIC_BASE_URL -- trả về presigned URL tạm "
                          "thời. Bật custom domain / Public Development URL trong Cloudflare dashboard "
                          "rồi điền env var để có link chia sẻ vĩnh viễn.")
    return result
