"""Tests for lib/r2_storage/client.py -- checksum mode, `region_name="auto"`,
`mask_endpoint`, botocore logger level. Zero network: `build_client` only
constructs a boto3 client object, never calls the API.
"""

from __future__ import annotations

import logging

import pytest

from lib.r2_storage import client as r2_client
from lib.r2_storage.config import R2Settings


def _settings(**overrides) -> R2Settings:
    base = dict(
        enabled=False, bucket="test-bucket", prefix="projects", auto_sync=False,
        sync_after_stage=False, exclude=(), multipart_threshold_mb=64,
        multipart_chunksize_mb=64, max_concurrency=4, presign_expiry_seconds=3600,
        max_upload_mb_per_sync=5000, endpoint_url="https://acct123.r2.cloudflarestorage.com",
        public_base_url=None,
    )
    base.update(overrides)
    return R2Settings(**base)


@pytest.fixture()
def fake_credentials(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")


class TestBuildClient:
    def test_region_is_auto(self, fake_credentials):
        client = r2_client.build_client(_settings())
        assert client.meta.config.region_name == "auto"

    def test_checksum_calculation_is_when_required(self, fake_credentials):
        client = r2_client.build_client(_settings())
        assert client.meta.config.request_checksum_calculation == "when_required"
        assert client.meta.config.response_checksum_validation == "when_required"

    def test_signature_version_is_s3v4(self, fake_credentials):
        client = r2_client.build_client(_settings())
        assert client.meta.config.signature_version == "s3v4"

    def test_endpoint_url_is_passed_through(self, fake_credentials):
        client = r2_client.build_client(_settings(endpoint_url="https://x.r2.cloudflarestorage.com"))
        assert client.meta.endpoint_url == "https://x.r2.cloudflarestorage.com"

    def test_no_network_call_is_made(self, fake_credentials):
        import socket

        original_connect = socket.socket.connect

        def _boom(*a, **k):
            raise AssertionError("build_client made a socket connection -- it must not")

        socket.socket.connect = _boom
        try:
            r2_client.build_client(_settings())
        finally:
            socket.socket.connect = original_connect


class TestTransferConfig:
    def test_sizes_come_from_settings_in_bytes(self, fake_credentials):
        tc = r2_client.transfer_config(_settings(multipart_threshold_mb=1, multipart_chunksize_mb=2,
                                                 max_concurrency=3))
        assert tc.multipart_threshold == 1 * 1024 * 1024
        assert tc.multipart_chunksize == 2 * 1024 * 1024
        assert tc.max_concurrency == 3


class TestMaskEndpoint:
    def test_masks_account_id(self):
        masked = r2_client.mask_endpoint("https://40ec8915d8a69aa9dc9cc0dd51f820c7.r2.cloudflarestorage.com")
        assert "40ec8915d8a69aa9dc9cc0dd51f820c7" not in masked
        assert masked == "https://<account>.r2.cloudflarestorage.com"

    def test_empty_string_passes_through(self):
        assert r2_client.mask_endpoint("") == ""

    def test_non_r2_url_passes_through_unmodified(self):
        assert r2_client.mask_endpoint("https://example.com/x") == "https://example.com/x"


class TestBotocoreLoggerLevel:
    def test_pinned_to_at_least_warning(self):
        logger = logging.getLogger("botocore")
        assert logger.level == 0 or logger.level >= logging.WARNING

    def test_does_not_lower_a_level_explicitly_raised_by_the_caller(self):
        import importlib

        logger = logging.getLogger("botocore")
        logger.setLevel(logging.CRITICAL)
        try:
            importlib.reload(r2_client)
            assert logger.level == logging.CRITICAL
        finally:
            logger.setLevel(logging.WARNING)
            importlib.reload(r2_client)
