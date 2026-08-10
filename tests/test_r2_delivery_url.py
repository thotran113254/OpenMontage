"""Tests for the `cloudflare_r2` tool's `delivery_url` action -- public vs
presigned fallback + warning; `presigned_url`/`presigned_put_url` stay signed
even when a public base is configured. Zero network -- `generate_presigned_url`
is a local SigV4 computation, and `delivery_url`'s public branch never calls
the client at all.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.r2_storage import actions


def _settings(public_base_url=None, presign_expiry_seconds=3600, bucket="test-bucket"):
    return SimpleNamespace(bucket=bucket, public_base_url=public_base_url,
                           presign_expiry_seconds=presign_expiry_seconds,
                           public_url=lambda key: (f"{public_base_url}/{key}"
                                                    if public_base_url else None))


class _FakeClient:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        self.calls.append((method, Params, ExpiresIn))
        return f"https://fake.example/{Params['Key']}?X-Amz-Signature=deadbeef"


class TestDeliveryUrl:
    def test_returns_public_url_when_configured(self):
        settings = _settings(public_base_url="https://cdn.example.com")
        client = _FakeClient()
        result = actions.delivery_url(client, settings, {"remote_key": "a/b.mp4"})
        assert result["kind"] == "public"
        assert result["url"] == "https://cdn.example.com/a/b.mp4"
        assert "X-Amz-Signature" not in result["url"]
        assert client.calls == []  # public path never touches the client

    def test_falls_back_to_presigned_with_warning_when_unconfigured(self):
        settings = _settings(public_base_url=None)
        client = _FakeClient()
        result = actions.delivery_url(client, settings, {"remote_key": "a/b.mp4"})
        assert result["kind"] == "presigned"
        assert "X-Amz-Signature" in result["url"]
        assert result["warning"]
        assert "CLOUDFLARE_R2_PUBLIC_BASE_URL" in result["warning"]


class TestPresignedActionsStaySignedRegardlessOfPublicConfig:
    @pytest.mark.parametrize("method_name,sdk_method", [
        ("presigned", "get_object"),
        ("presigned", "put_object"),
    ])
    def test_presigned_helper_always_signs(self, method_name, sdk_method):
        settings = _settings(public_base_url="https://cdn.example.com")
        client = _FakeClient()
        result = actions.presigned(client, settings, {"remote_key": "a/b.mp4"}, sdk_method)
        assert "X-Amz-Signature" in result["url"]
        assert client.calls[0][0] == sdk_method

    def test_expires_in_is_clamped_to_seven_days(self):
        settings = _settings()
        client = _FakeClient()
        actions.presigned(client, settings, {"remote_key": "k", "expires_in": 10_000_000},
                          "get_object")
        assert client.calls[0][2] == 604800

    def test_expires_in_defaults_to_settings_value(self):
        settings = _settings(presign_expiry_seconds=900)
        client = _FakeClient()
        actions.presigned(client, settings, {"remote_key": "k"}, "get_object")
        assert client.calls[0][2] == 900

    def test_non_integer_expires_in_falls_back_to_settings_default(self):
        settings = _settings(presign_expiry_seconds=900)
        client = _FakeClient()
        actions.presigned(client, settings, {"remote_key": "k", "expires_in": "not-a-number"},
                          "get_object")
        assert client.calls[0][2] == 900
