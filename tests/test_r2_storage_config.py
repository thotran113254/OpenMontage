"""Tests for lib/r2_storage/config.py -- layering, `_clean`, credential-key
rejection, presign clamp, bucket resolution, `is_configured`. Zero network:
credentials are read from env but never used to build a client here.
"""

from __future__ import annotations

import json

import pytest

from lib.r2_storage import config as r2_config

_ENV_NAMES = (
    "CLOUDFLARE_R2_ACCOUNT_ID", "CLOUDFLARE_R2_ACCESS_KEY_ID",
    "CLOUDFLARE_R2_SECRET_ACCESS_KEY", "CLOUDFLARE_R2_ENDPOINT_URL",
    "CLOUDFLARE_R2_BUCKET", "CLOUDFLARE_R2_PUBLIC_BASE_URL",
)


@pytest.fixture()
def clean_r2_env(monkeypatch):
    """Every test starts from a known-empty env, not whatever the real `.env`
    has -- `load_env()` is blocked outright so deleting a var here can't be
    silently undone by re-reading the real `.env` file."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(r2_config, "load_env", lambda *a, **k: None)


class TestBuiltinDefaults:
    def test_resolve_on_a_clean_checkout_is_disabled(self, clean_r2_env, tmp_path):
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert settings.enabled is False

    def test_resolve_never_reprs_a_secret(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "sk-shouldnotappear")
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert "sk-shouldnotappear" not in repr(settings)


class TestLayering:
    def test_json_overrides_builtin_defaults(self, clean_r2_env, tmp_path):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"prefix": "custom", "max_concurrency": 9}))
        settings = r2_config.resolve(path=path)
        assert settings.prefix == "custom"
        assert settings.max_concurrency == 9

    def test_none_value_in_json_does_not_override(self, clean_r2_env, tmp_path):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"prefix": None}))
        settings = r2_config.resolve(path=path)
        assert settings.prefix == "projects"

    def test_unknown_json_key_is_ignored_not_raised(self, clean_r2_env, tmp_path):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"totally_unknown_key": "x"}))
        r2_config.resolve(path=path)

    def test_bucket_falls_back_to_env_when_json_empty(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "from-env")
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert settings.bucket == "from-env"

    def test_json_bucket_wins_over_env(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "from-env")
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"bucket": "from-json"}))
        settings = r2_config.resolve(path=path)
        assert settings.bucket == "from-json"

    def test_endpoint_derived_from_account_id_when_no_explicit_url(
            self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_ACCOUNT_ID", "abc123")
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert settings.endpoint_url == "https://abc123.r2.cloudflarestorage.com"

    def test_explicit_endpoint_url_wins_over_account_id(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_ACCOUNT_ID", "abc123")
        monkeypatch.setenv("CLOUDFLARE_R2_ENDPOINT_URL", "https://explicit.example.com")
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert settings.endpoint_url == "https://explicit.example.com"


class TestCredentialKeyRejection:
    @pytest.mark.parametrize("key", [
        "secret_access_key", "SECRET", "access_key_id", "ACCESS-KEY", "api_token", "password",
    ])
    def test_credential_shaped_key_in_json_raises(self, clean_r2_env, tmp_path, key):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({key: "x"}))
        with pytest.raises(r2_config.R2ConfigError):
            r2_config.resolve(path=path)

    def test_ordinary_key_does_not_raise(self, clean_r2_env, tmp_path):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"prefix": "ok"}))
        r2_config.resolve(path=path)


class TestPresignClamp:
    @pytest.mark.parametrize("requested,expected", [
        (-5, 1), (0, 1), (10, 10), (604800, 604800), (10_000_000, 604800),
    ])
    def test_clamped_to_1_through_7_days(self, clean_r2_env, tmp_path, requested, expected):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"presign_expiry_seconds": requested}))
        settings = r2_config.resolve(path=path)
        assert settings.presign_expiry_seconds == expected


class TestIsConfigured:
    def test_missing_both_credentials_reports_both(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setattr(r2_config, "GLOBAL_CONFIG_PATH", tmp_path / "missing.json")
        configured, missing = r2_config.is_configured()
        assert configured is False
        assert "CLOUDFLARE_R2_ACCESS_KEY_ID" in missing
        assert "CLOUDFLARE_R2_SECRET_ACCESS_KEY" in missing

    def test_fully_configured_reports_true_empty_missing(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setattr(r2_config, "GLOBAL_CONFIG_PATH", tmp_path / "missing.json")
        monkeypatch.setenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "k")
        monkeypatch.setenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "s")
        monkeypatch.setenv("CLOUDFLARE_R2_ENDPOINT_URL", "https://example.com")
        configured, missing = r2_config.is_configured()
        assert configured is True
        assert missing == []

    def test_does_not_import_botocore_as_a_side_effect(self, clean_r2_env, monkeypatch, tmp_path):
        import sys

        monkeypatch.setattr(r2_config, "GLOBAL_CONFIG_PATH", tmp_path / "missing.json")
        was_present = "botocore" in sys.modules
        r2_config.is_configured()
        assert ("botocore" in sys.modules) == was_present


class TestValidate:
    def test_enabled_true_with_empty_bucket_raises(self, clean_r2_env, tmp_path):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"enabled": True}))
        with pytest.raises(r2_config.R2ConfigError):
            r2_config.resolve(path=path)

    def test_enabled_false_with_empty_bucket_does_not_raise(self, clean_r2_env, tmp_path):
        path = tmp_path / "r2.json"
        path.write_text(json.dumps({"enabled": False}))
        r2_config.resolve(path=path)


class TestPublicBaseUrl:
    def test_non_https_raises(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_PUBLIC_BASE_URL", "http://insecure.example.com")
        with pytest.raises(r2_config.R2ConfigError):
            r2_config.resolve(path=tmp_path / "missing.json")

    def test_trailing_slash_stripped(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com/")
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert settings.public_base_url == "https://cdn.example.com"

    def test_public_url_helper_joins_key(self, clean_r2_env, monkeypatch, tmp_path):
        monkeypatch.setenv("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert settings.public_url("a/b.mp4") == "https://cdn.example.com/a/b.mp4"

    def test_public_url_none_when_unset(self, clean_r2_env, tmp_path):
        settings = r2_config.resolve(path=tmp_path / "missing.json")
        assert settings.public_url("a.mp4") is None
