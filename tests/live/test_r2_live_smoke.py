"""Opt-in live smoke test against the real Cloudflare R2 bucket.

Never runs automatically -- `pytest.ini` deselects everything marked
`@pytest.mark.live` by default, and `tests/test_r2_live_gate.py` (which does
run in the default suite) fails the build if `R2_LIVE_TEST` is ever set
while `CI` is set. Writes only under a `livetest/` key prefix and deletes
what it creates in a `finally` -- never touches `projects/` or `render-kits/`.

Run with:

    R2_LIVE_TEST=1 python -m pytest tests/live/test_r2_live_smoke.py -v -m live
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.live

_SKIP_REASON = (
    "R2_LIVE_TEST not set -- see tests/live/README.md to run this against the "
    "real bucket (put/list/presign/get/delete a 1 KB object)")


@pytest.mark.skipif(not os.environ.get("R2_LIVE_TEST"), reason=_SKIP_REASON)
class TestR2LiveSmoke:
    def test_put_list_presign_get_delete_a_real_object(self):
        from lib.r2_storage.client import build_client
        from lib.r2_storage.config import is_configured, resolve

        configured, missing = is_configured()
        assert configured, f"R2 not configured -- missing {missing}"

        settings = resolve()
        client = build_client(settings)
        key = f"livetest/{uuid.uuid4().hex[:12]}.txt"
        body = b"r2 live smoke test"

        try:
            client.put_object(Bucket=settings.bucket, Key=key, Body=body)

            listing = client.list_objects_v2(Bucket=settings.bucket, Prefix="livetest/")
            keys = {o["Key"] for o in listing.get("Contents", [])}
            assert key in keys

            url = client.generate_presigned_url(
                "get_object", Params={"Bucket": settings.bucket, "Key": key}, ExpiresIn=60)
            assert "X-Amz-Signature" in url

            fetched = client.get_object(Bucket=settings.bucket, Key=key)
            assert fetched["Body"].read() == body
        finally:
            client.delete_object(Bucket=settings.bucket, Key=key)
