"""Edit-style packs + BGM lock."""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit import edit_styles
from lib.talking_head_edit.edit_styles import EditStyleError
from lib.talking_head_edit.stages.audit import apply_chosen_bgm

fastapi = pytest.importorskip("fastapi", reason="server deps chưa cài")
from fastapi.testclient import TestClient  # noqa: E402


def test_clean_options_clamps_volume():
    out = edit_styles.clean_options({"bgm_volume": 0.9, "bgm": True})
    assert out["bgm_volume"] == 0.22
    assert out["bgm"] is True


def test_save_and_load(tmp_path):
    path = tmp_path / "edit-styles.json"
    saved = edit_styles.save(
        "Bán hàng",
        {"prompt": "giữ giọng", "bgm_name": "bgm_tech_pulse.mp3", "bgm_volume": 0.15},
        path=path,
    )
    listed = edit_styles.load_all(path)
    assert len(listed) == 1
    assert listed[0]["id"] == saved["id"]
    assert listed[0]["options"]["bgm_volume"] == 0.15


def test_title_too_short(tmp_path):
    with pytest.raises(EditStyleError):
        edit_styles.save("x", {}, path=tmp_path / "s.json")


def test_apply_chosen_bgm_locks_name(monkeypatch):
    monkeypatch.setattr(
        "lib.talking_head_edit.stages.audit.usable_bgm",
        lambda: ["bgm_tech_pulse.mp3"],
    )
    spec = {"bgm": {"name": "bgm_lofi_chill.mp3", "volume": 0.16}}
    removed: list[str] = []
    out = apply_chosen_bgm(
        spec,
        {"bgm": True, "bgm_name": "bgm_tech_pulse.mp3", "bgm_volume": 0.15},
        removed,
    )
    assert out["bgm"]["name"] == "bgm_tech_pulse.mp3"
    assert out["bgm"]["volume"] == 0.15


def test_apply_chosen_bgm_off():
    spec = {"bgm": {"name": "bgm_tech_pulse.mp3", "volume": 0.16}}
    out = apply_chosen_bgm(spec, {"bgm": False}, [])
    assert out["bgm"] is None


def test_api_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(edit_styles, "STYLES_PATH", tmp_path / "edit-styles.json")
    from server.app import app

    client = TestClient(app)
    created = client.post(
        "/api/edit-styles",
        json={"title": "Polo đỏ", "options": {"bgm_name": "bgm_tech_pulse.mp3", "bgm_volume": 0.15}},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    listed = client.get("/api/edit-styles").json()
    assert any(item["id"] == body["id"] for item in listed)
    deleted = client.delete(f"/api/edit-styles/{body['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/edit-styles").json() == []


def test_bgm_list_endpoint():
    from server.app import app

    client = TestClient(app)
    resp = client.get("/api/bgm")
    assert resp.status_code == 200
    data = resp.json()
    names = [t["name"] for t in data["tracks"]]
    assert "bgm_tech_pulse.mp3" in names
