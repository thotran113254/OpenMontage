"""Repo-wide pytest fixtures.

Holds the shared fake-`vastai` injection for the cloud-render suite
(`tests/fixtures/fake_vastai.py`) and the shared R2 test-double fixtures
(`tests/test_r2_*.py`). Kept at the top level (not a per-feature
`tests/<feature>/conftest.py`) because every feature area's tests live flat
under `tests/`, matching this repo's existing layout.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from tests.fixtures import fake_vastai


@pytest.fixture()
def fake_vastai_sdk(monkeypatch):
    """Install a fresh `FakeVastAI` as `sys.modules["vastai"]` for one test
    and yield it so the test can seed offers/instances, inspect `.calls`, or
    script a failure via `fail_on=`.

    Teardown asserts nothing left `sys.modules["vastai"]` pointing at a
    *different* module than the one this fixture installed -- i.e. no test
    swapped in the real SDK (or some other stand-in) without going through
    `monkeypatch`, which would let the next test in the session silently
    inherit it. This assertion runs before `monkeypatch`'s own revert (fixture
    teardown is LIFO: `monkeypatch` was set up first here, so it tears down
    last), so it catches a violation `monkeypatch`'s automatic cleanup alone
    would paper over.
    """
    account = fake_vastai.install(monkeypatch)
    installed_module = sys.modules["vastai"]
    yield account
    current = sys.modules.get("vastai")
    assert current is None or current is installed_module, (
        "a test replaced sys.modules['vastai'] with something other than the "
        "fake this fixture installed -- this could be the real SDK, which "
        "means a later test could accidentally make a real network call")


@pytest.fixture()
def r2_moto_server(monkeypatch):
    """A real `http://127.0.0.1:PORT` S3 endpoint (`moto[server]`), so R2
    client code (multipart, sigv4, `region_name="auto"`) runs through its
    actual code path instead of gambling on moto's `@mock_aws` endpoint
    patching. The bucket is created via a throwaway `region_name="us-east-1"`
    admin client -- moto's `CreateBucket` op validates the region against the
    endpoint and rejects `"auto"` specifically there; every other operation
    this repo's code actually calls (Put/Get/List/Copy/Delete/presign) works
    fine with `"auto"` once the bucket already exists, which is the only
    thing that matters since production code never calls `CreateBucket`.
    """
    import boto3
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0)
    server.start()
    _, port = server.get_host_and_port()
    endpoint_url = f"http://127.0.0.1:{port}"
    bucket = "test-bucket"

    monkeypatch.setenv("CLOUDFLARE_R2_ENDPOINT_URL", endpoint_url)
    monkeypatch.setenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret-key")
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", bucket)
    monkeypatch.delenv("CLOUDFLARE_R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_R2_PUBLIC_BASE_URL", raising=False)

    admin = boto3.client("s3", endpoint_url=endpoint_url, region_name="us-east-1",
                        aws_access_key_id="test-access-key", aws_secret_access_key="test-secret-key")
    admin.create_bucket(Bucket=bucket)

    try:
        yield SimpleNamespace(endpoint_url=endpoint_url, bucket=bucket)
    finally:
        server.stop()


@pytest.fixture()
def counting_client():
    """`(client) -> counter` -- registers a botocore `before-send` event hook
    on `client` and returns a mutable `{"n": int}` counter, for asserting
    "this operation issues zero/N HTTP requests" without moto/network access
    of its own (it wraps whatever client the test already built)."""
    def _wrap(client):
        counter = {"n": 0}
        client.meta.events.register(
            "before-send.s3", lambda **kw: counter.__setitem__("n", counter["n"] + 1))
        return counter

    return _wrap


# Machine settings from the repo's `.env` must not leak into tests: a real API
# token turns every endpoint test into a 401, and a real public host changes
# every URL a test asserts on. Set to "" rather than deleted — the `.env`
# loaders run again at call time and only fill keys that are absent.
_MACHINE_ENV = ("AUTOEDIT_API_TOKEN", "AUTOEDIT_PUBLIC_HOST", "AUTOEDIT_CPU_BUDGET",
                "AUTOEDIT_NICE", "OPENMONTAGE_RENDER_MAX_CONCURRENCY")


@pytest.fixture(autouse=True)
def _isolate_machine_env(monkeypatch):
    for name in _MACHINE_ENV:
        monkeypatch.setenv(name, "")
