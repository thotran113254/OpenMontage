"""Audit stage: ground-truth checks and the LLM cut verifier's contract.

The verifier is stubbed — these tests pin down what happens around it, which
is where safety lives: a verdict the model didn't give, or a verifier that
fell over, must never result in footage being cut.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit import cut_verifier
from lib.talking_head_edit.director_client import DirectorError
from lib.talking_head_edit.stages.audit import audit_resources, measure_quality


def words(count: int = 40) -> list[dict[str, object]]:
    return [{"word": f"w{i}", "start": float(i), "end": i + 0.8} for i in range(count)]


class TestCutVerifier:
    def test_accepts_only_what_the_verifier_marks_remove(self, monkeypatch):
        def fake_chat(prompt, **kwargs):
            return {"verdicts": [
                {"id": 0, "decision": "remove", "reason": "ê a"},
                {"id": 1, "decision": "keep", "reason": "có thông tin"},
            ]}, {"total_tokens": 10}, ""

        monkeypatch.setattr(cut_verifier, "chat_json", fake_chat)
        accepted, decisions, _ = cut_verifier.verify_cuts(
            [{"w": [3, 3]}, {"w": [10, 12]}], words(), cold_open=None
        )
        assert accepted == [[3, 3]]
        assert [d["decision"] for d in decisions] == ["remove", "keep"]

    def test_verifier_failure_keeps_everything(self, monkeypatch):
        def boom(prompt, **kwargs):
            raise DirectorError("gateway down")

        monkeypatch.setattr(cut_verifier, "chat_json", boom)
        accepted, decisions, _ = cut_verifier.verify_cuts([{"w": [3, 3]}], words(), None)
        assert accepted == [], "không kiểm được thì tuyệt đối không cắt"
        assert decisions[0]["source"] == "verifier-failed"

    def test_missing_verdict_keeps_that_cut(self, monkeypatch):
        monkeypatch.setattr(cut_verifier, "chat_json",
                            lambda prompt, **kw: ({"verdicts": []}, {}, ""))
        accepted, decisions, _ = cut_verifier.verify_cuts([{"w": [5, 6]}], words(), None)
        assert accepted == []
        assert decisions[0]["decision"] == "keep"

    def test_cut_overlapping_cold_open_never_reaches_the_verifier(self, monkeypatch):
        called = []
        monkeypatch.setattr(cut_verifier, "chat_json",
                            lambda prompt, **kw: (called.append(1), ({"verdicts": []}, {}, ""))[1])
        accepted, decisions, _ = cut_verifier.verify_cuts(
            [{"w": [10, 12]}], words(), cold_open={"w0": 8, "w1": 15}
        )
        assert accepted == []
        assert decisions[0]["source"] == "mechanical"
        assert not called, "không tốn call cho cut đã loại bằng sự thật cơ học"

    def test_indices_are_clamped_and_ordered(self, monkeypatch):
        seen = {}

        def fake_chat(prompt, **kwargs):
            seen["prompt"] = prompt
            return {"verdicts": [{"id": 0, "decision": "keep", "reason": ""}]}, {}, ""

        monkeypatch.setattr(cut_verifier, "chat_json", fake_chat)
        cut_verifier.verify_cuts([{"w": [999, -5]}], words(10), None)
        payload = seen["prompt"]
        assert '"w": [' in payload.replace("'", '"')  # candidate made it through
        assert "999" not in payload.split("ỨNG VIÊN")[1].split("TRẢ VỀ")[0]


class TestResourceAudit:
    def test_drops_sfx_that_has_no_file(self):
        spec = {"events": [
            {"type": "sfx", "name": "sfx_does_not_exist.mp3", "atWord": 1},
            {"type": "sfx", "name": "sfx_pop.mp3", "atWord": 2},
        ]}
        out, removed = audit_resources(spec, word_count=10)
        names = [e["name"] for e in out["events"]]
        assert names == ["sfx_pop.mp3"]
        assert any("sfx_does_not_exist.mp3" in r for r in removed)

    def test_drops_event_types_the_renderer_does_not_implement(self):
        spec = {"events": [{"type": "zoom_blur", "atWord": 1}]}
        out, removed = audit_resources(spec, word_count=10)
        assert out["events"] == []
        assert removed

    def test_drops_events_pointing_outside_the_spine(self):
        spec = {"events": [
            {"type": "keyword", "atWord": 500, "text": "X"},
            {"type": "keyword", "atWord": 5, "text": "OK"},
        ]}
        out, removed = audit_resources(spec, word_count=10)
        assert [e["text"] for e in out["events"]] == ["OK"]
        assert removed

    def test_drops_unknown_bgm_and_out_of_range_cold_open(self):
        spec = {"events": [], "bgm": {"name": "bgm_nope.mp3"},
                "cold_open": {"w0": 2, "w1": 999}}
        out, removed = audit_resources(spec, word_count=10)
        assert out["bgm"] is None
        assert out["cold_open"] is None
        assert len(removed) == 2

    def test_keeps_a_real_bgm(self):
        spec = {"events": [], "bgm": {"name": "bgm_tech_pulse.mp3", "volume": 0.22}}
        out, removed = audit_resources(spec, word_count=10)
        assert out["bgm"]["name"] == "bgm_tech_pulse.mp3"
        assert removed == []


class TestQualityMeasurement:
    def test_reports_full_coverage(self):
        spec = {"events": [{"type": "caption", "w0": 0, "w1": 9, "text": "x"}]}
        quality = measure_quality(spec, words(10))
        assert quality["caption_coverage"] == 1.0
        assert quality["uncovered_words"] == []

    def test_reports_gaps_without_fixing_them(self):
        spec = {"events": [{"type": "caption", "w0": 0, "w1": 4, "text": "x"}]}
        quality = measure_quality(spec, words(10))
        assert quality["caption_coverage"] == pytest.approx(0.5)
        assert quality["uncovered_words"] == [5, 6, 7, 8, 9]

    def test_flags_keyword_hidden_by_a_card(self):
        spec = {"events": [
            {"type": "card", "w0": 5, "w1": 15, "title": "C"},
            {"type": "keyword", "atWord": 8, "text": "HIDDEN"},
        ]}
        assert measure_quality(spec, words(20))["keyword_in_card"] == ["HIDDEN"]

    def test_counts_captions_over_nine_words(self):
        spec = {"events": [{"type": "caption", "w0": 0, "w1": 12, "text": "dài"}]}
        assert measure_quality(spec, words(20))["captions_over_9_words"] == 1


class TestBgmNormalisation:
    """A revise pass really did answer with a bare string; the music vanished."""

    def test_bare_string_becomes_the_object_form(self):
        from lib.talking_head_edit.stages.audit import normalise_bgm
        bgm, error = normalise_bgm("bgm_energy_drive.mp3")
        assert error == ""
        assert bgm == {"name": "bgm_energy_drive.mp3", "volume": 0.22}

    def test_object_form_keeps_its_volume(self):
        from lib.talking_head_edit.stages.audit import normalise_bgm
        bgm, _ = normalise_bgm({"name": "bgm_lofi_chill.mp3", "volume": 0.3})
        assert bgm == {"name": "bgm_lofi_chill.mp3", "volume": 0.3}

    def test_none_stays_none_without_complaint(self):
        from lib.talking_head_edit.stages.audit import normalise_bgm
        assert normalise_bgm(None) == (None, "")

    def test_unusable_shape_is_reported_not_swallowed(self):
        from lib.talking_head_edit.stages.audit import normalise_bgm
        bgm, error = normalise_bgm(["bgm_lofi_chill.mp3"])
        assert bgm is None
        assert error, "mất nhạc thì phải có cảnh báo"

    def test_normalize_cold_open_snaps_to_sentence_end(self):
        from lib.talking_head_edit.stages.audit import normalize_cold_open

        words = [
            {"word": "Một"}, {"word": "câu"}, {"word": "hook"}, {"word": "đắt."},
            {"word": "Tiếp"}, {"word": "theo"},
        ]
        cold, notes = normalize_cold_open({"w0": 1, "w1": 2, "caption": "x"}, words)
        assert cold is not None
        assert cold["w0"] == 0
        assert cold["w1"] == 3
        assert any("biên câu" in n for n in notes)

    def test_normalize_cold_open_drops_too_short(self):
        from lib.talking_head_edit.stages.audit import normalize_cold_open

        words = [{"word": "a"}, {"word": "b."}]
        cold, notes = normalize_cold_open({"w0": 0, "w1": 0, "caption": "x"}, words)
        assert cold is None
        assert any("cắt cụt" in n for n in notes)

    def test_string_bgm_survives_the_full_resource_audit(self):
        spec = {"events": [], "bgm": "bgm_energy_drive.mp3"}
        out, removed = audit_resources(spec, word_count=10)
        assert out["bgm"]["name"] == "bgm_energy_drive.mp3"
        assert removed == []
