"""Look preset store + API.

Every test that touches the filesystem monkeypatches `PRESETS_PATH` to a
`tmp_path` file — none of them may write to the repo's real
`config/look-presets.json`, which ships 3 seeded presets a developer relies on.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit import look_presets
from lib.talking_head_edit.look_presets import LookPresetError

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402


# ---- validate ----------------------------------------------------------

def test_validate_accepts_known_numeric_keys():
    assert look_presets.validate({"skin_smooth": 0.3}) == {"skin_smooth": 0.3}


def test_validate_rejects_sharpen():
    with pytest.raises(LookPresetError):
        look_presets.validate({"sharpen": 1.6})


def test_validate_rejects_clarity():
    with pytest.raises(LookPresetError):
        look_presets.validate({"clarity": 0.5})


def test_validate_rejects_string_value():
    with pytest.raises(LookPresetError):
        look_presets.validate({"skin_smooth": "0.3"})


def test_validate_rejects_bool_value():
    with pytest.raises(LookPresetError):
        look_presets.validate({"skin_smooth": True})


def test_validate_rejects_empty_dict():
    with pytest.raises(LookPresetError):
        look_presets.validate({})


def test_validate_rejects_unknown_key():
    with pytest.raises(LookPresetError):
        look_presets.validate({"totally_unknown_key": 1})


# ---- validate_name -------------------------------------------------------

@pytest.mark.parametrize("bad_name", ["../x", "a/b", "a\\b", "", "a" * 61])
def test_validate_name_rejects_bad_names(bad_name):
    with pytest.raises(LookPresetError):
        look_presets.validate_name(bad_name)


def test_validate_name_accepts_good_name():
    assert look_presets.validate_name(" da-nhe ") == "da-nhe"


# ---- save / load_all / delete -------------------------------------------

def test_save_same_name_twice_keeps_one_entry_with_new_value(tmp_path):
    path = tmp_path / "look-presets.json"
    look_presets.save("da-vua", {"skin_smooth": 0.2}, path=path)
    look_presets.save("da-vua", {"skin_smooth": 0.3}, path=path)
    presets = look_presets.load_all(path)
    assert len(presets) == 1
    assert presets[0]["grade"] == {"skin_smooth": 0.3}


def test_load_all_with_garbage_file_returns_empty_list(tmp_path):
    path = tmp_path / "look-presets.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert look_presets.load_all(path) == []


def test_load_all_missing_file_returns_empty_list(tmp_path):
    path = tmp_path / "does-not-exist.json"
    assert look_presets.load_all(path) == []


def test_delete_nonexistent_returns_false(tmp_path):
    path = tmp_path / "look-presets.json"
    look_presets.save("da-nhe", {"skin_smooth": 0.12}, path=path)
    assert look_presets.delete("khong-ton-tai", path=path) is False


def test_delete_existing_returns_true_and_removes(tmp_path):
    path = tmp_path / "look-presets.json"
    look_presets.save("da-nhe", {"skin_smooth": 0.12}, path=path)
    assert look_presets.delete("da-nhe", path=path) is True
    assert look_presets.load_all(path) == []


def test_save_writes_atomically_no_leftover_tmp_file(tmp_path):
    path = tmp_path / "look-presets.json"
    look_presets.save("da-nhe", {"skin_smooth": 0.12}, path=path)
    assert not (tmp_path / "look-presets.json.tmp").exists()
    assert path.exists()


# ---- API (TestClient) ----------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(look_presets, "PRESETS_PATH", tmp_path / "look-presets.json")

    from server.app import app

    return TestClient(app)


def test_post_then_get_then_delete_roundtrip(client):
    resp = client.post("/api/look-presets",
                       json={"name": "da-manh", "grade": {"skin_smooth": 0.45}})
    assert resp.status_code == 200
    saved = resp.json()
    assert saved["name"] == "da-manh"
    assert saved["grade"] == {"skin_smooth": 0.45}

    listed = client.get("/api/look-presets").json()
    assert len(listed) == 1
    assert listed[0]["name"] == "da-manh"

    deleted = client.delete("/api/look-presets/da-manh")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": "da-manh"}

    assert client.get("/api/look-presets").json() == []


def test_post_with_sharpen_key_rejected_400(client):
    resp = client.post("/api/look-presets",
                       json={"name": "bad", "grade": {"sharpen": 1.6}})
    assert resp.status_code == 400
    assert "sharpen" in resp.json()["detail"].lower() or "auto-sharpen" in resp.json()["detail"]


def test_delete_missing_preset_returns_404(client):
    resp = client.delete("/api/look-presets/khong-ton-tai")
    assert resp.status_code == 404


def test_real_config_file_untouched_after_api_roundtrip(client):
    """The `client` fixture monkeypatches `PRESETS_PATH` to `tmp_path`, so an API
    write here must leave the repo's real seeded file alone (name, size, mtime)."""
    from lib.talking_head_edit.job_store import REPO_ROOT

    real_path = REPO_ROOT / "config" / "look-presets.json"
    before = real_path.read_bytes() if real_path.exists() else None

    client.post("/api/look-presets", json={"name": "da-manh", "grade": {"skin_smooth": 0.45}})
    client.delete("/api/look-presets/da-manh")

    after = real_path.read_bytes() if real_path.exists() else None
    assert before == after
