"""The user's request must take effect — or be reported as not done.

Pinned here because each of these once failed silently on a real job: a revise
asked for cuts, a full frame and more keywords, and got only emoji — the cuts
were verified then dropped by a duration check measuring the wrong thing, the
frame was an option revise could not touch, and nothing told the user.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from lib.talking_head_edit import cpu_budget, cut_verifier
from lib.talking_head_edit.job_store import JobStore
from lib.talking_head_edit.prompt_captions import build_caption_prompt
from lib.talking_head_edit.prompt_structure import build_structure_prompt, prompt_version_number
from lib.talking_head_edit.spec_patch import apply_patch
from lib.talking_head_edit.stages import revise as revise_stage
from lib.talking_head_edit.stages.audit import audit_cuts, record_outcome


def speech(*tokens: str, gap: float = 0.05) -> list[dict[str, object]]:
    """Words back to back with `gap` of silence; a token "~1.0" widens the gap before the next."""
    words, clock = [], 0.0
    for token in tokens:
        if token.startswith("~"):
            clock += float(token[1:])
            continue
        words.append({"word": token, "start": round(clock, 3), "end": round(clock + 0.3, 3)})
        clock += 0.3 + gap
    return words


def verifier_says(monkeypatch, decision: str) -> list[str]:
    seen: list[str] = []

    def fake(prompt, **kwargs):
        seen.append(prompt)
        return {"verdicts": [{"id": i, "decision": decision, "reason": "x"}
                             for i in range(20)]}, {}, ""

    monkeypatch.setattr(cut_verifier, "chat_json", fake)
    return seen


class TestAuditCuts:
    def test_a_filler_inside_a_pause_is_applied(self, monkeypatch):
        words = speech("ví", "dụ", "như,", "~0.9", "ờ...", "~0.5", "sản", "phẩm")
        words[3]["end"] = words[3]["start"] + 0.06      # ASR gives "ờ" 60ms
        verifier_says(monkeypatch, "remove")
        result = audit_cuts([{"w": [3, 3], "ly_do": "filler"}], words, None, "normal")
        assert result["applied"] == [[3, 3]]
        assert result["blocked"] == []

    def test_every_cut_not_applied_says_why(self, monkeypatch):
        words = speech("đến", "và", "và", "từ", "đó")
        verifier_says(monkeypatch, "remove")
        result = audit_cuts([{"w": [1, 1], "ly_do": "repeat"}], words, None, "normal")
        assert result["applied"] == []
        assert result["blocked"][0]["by"] == "an_toan"
        assert result["blocked"][0]["text"] == "và"

    def test_the_users_own_range_skips_the_verifier(self, monkeypatch):
        words = speech(*[f"w{i}" for i in range(12)], gap=0.2)
        seen = verifier_says(monkeypatch, "keep")
        result = audit_cuts([{"w": [4, 6], "nguon": "khach"}], words, None, "light")
        assert result["applied"] == [[4, 6]]
        assert seen == [], "cut khách tự đánh dấu không đi qua verifier"

    def test_outcome_merges_into_the_version_entry(self):
        state = {"versions": [{"version": 2, "outcome": {"not_done": ["x"]}}]}
        record_outcome(state, 2, {"cuts_applied": 1})
        assert state["versions"][0]["outcome"] == {"not_done": ["x"], "cuts_applied": 1}


class TestPatchCuts:
    SPEC = {"events": [], "cut_remove": [[3, 3], [10, 12]]}

    def test_adding_a_cut_keeps_the_approved_ones(self):
        new, report = apply_patch(self.SPEC, {"cut_add": [{"w": [20, 21], "ly_do": "repeat"}]})
        assert [p["w"] for p in new["cut_proposed"]] == [[3, 3], [10, 12], [20, 21]]
        assert report["cuts_added"] == 1
        assert "cut_remove" in report["top_level_changed"]

    def test_restoring_a_range_drops_only_overlapping_cuts(self):
        new, report = apply_patch(self.SPEC, {"cut_restore": [[11, 11]]})
        assert [p["w"] for p in new["cut_proposed"]] == [[3, 3]]
        assert report["cuts_restored"] == 1

    def test_a_full_replacement_resets_the_proposals_too(self):
        new, _ = apply_patch({**self.SPEC, "cut_proposed": [{"w": [1, 1]}]},
                             {"set": {"cut_remove": [[5, 5]]}})
        assert [p["w"] for p in new["cut_proposed"]] == [[5, 5]]


class TestReviseOptions:
    def test_only_whitelisted_valid_options_apply(self):
        changed, refused = revise_stage.apply_option_changes(
            {"frame_preset": "none", "tempo": 3, "cut_level": "tight", "width": 720},
            {}, {"frame_preset": "dark", "cut_level": "normal"})
        assert changed == {"frame_preset": "none", "cut_level": "tight"}
        assert len(refused) == 2

    def test_a_revised_bgm_is_written_back_to_the_options(self, monkeypatch):
        """Otherwise audit re-applies the template's bed and undoes the change."""
        monkeypatch.setattr(revise_stage, "usable_bgm", lambda: ["bgm_chill.mp3"])
        changed, _ = revise_stage.apply_option_changes(
            {}, {"bgm": {"name": "bgm_chill.mp3", "volume": 0.14}},
            {"bgm": True, "bgm_name": "bgm_tech_pulse.mp3"})
        assert changed == {"bgm_name": "bgm_chill.mp3", "bgm_volume": 0.14}

    def test_a_made_up_bgm_is_refused_not_persisted(self, monkeypatch):
        monkeypatch.setattr(revise_stage, "usable_bgm", lambda: ["bgm_chill.mp3"])
        changed, refused = revise_stage.apply_option_changes(
            {}, {"bgm": {"name": "bgm_invented.mp3", "volume": 0.4}}, {"bgm": True})
        assert changed == {} and "bgm_invented.mp3" in refused[0]

    def test_run_persists_options_and_records_what_was_not_done(self, tmp_path, monkeypatch):
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {"frame_preset": "dark"})
        job.spine_path.write_text(json.dumps({"word_timestamps": speech("a", "b", "c")}),
                                  encoding="utf-8")
        job.spec_path(1).write_text(json.dumps({"events": [], "cut_remove": []}),
                                    encoding="utf-8")
        job.update(current_version=1, versions=[{"version": 1, "kind": "director"}])
        patch = {"options": {"frame_preset": "none"}, "cut_add": [{"w": [1, 1]}],
                 "not_done": ["đổi font — không hỗ trợ"]}
        monkeypatch.setattr(revise_stage, "chat_json", lambda *a, **k: (patch, {}, ""))

        result = revise_stage.run(job, {**job.load()["options"],
                                        "revise_instruction": "full khung, cắt chữ b"})

        state = job.load()
        assert state["options"]["frame_preset"] == "none"
        outcome = state["versions"][-1]["outcome"]
        assert outcome["options_changed"] == {"frame_preset": "none"}
        assert outcome["not_done"] == ["đổi font — không hỗ trợ"]
        assert result["report"]["cuts_added"] == 1

    def test_the_prompt_shows_the_whole_take_and_current_cuts(self):
        words = speech(*[f"t{i}" for i in range(900)])
        prompt = revise_stage.build_revise_prompt({"events": [], "cut_remove": [[5, 6]]},
                                                  words, "cắt kỹ")
        assert "899:t899" in prompt, "trước đây chỉ gửi 400 từ đầu"
        assert 'w5-6: "t5 t6"' in prompt


class TestPrompts:
    def test_the_users_request_comes_before_the_technique_hints(self):
        prompt = build_structure_prompt(speech("xin", "chào"), {"prompt": "không card"},
                                        version="v8")
        assert prompt.index("không card") < prompt.index("GỢI Ý KỸ THUẬT")

    @pytest.mark.parametrize("level,marker", [
        ("light", "MỨC CẮT NHẸ"), ("normal", "MỨC CẮT VỪA"), ("tight", "MỨC CẮT KỸ")])
    def test_the_cut_rule_follows_the_level(self, level, marker):
        prompt = build_structure_prompt(speech("a"), {"cut_level": level}, version="v8")
        assert marker in prompt

    def test_captions_see_the_request_and_brand_spelling(self):
        prompt = build_caption_prompt(speech("acmi", "bot"), 0, 1, version="v3",
                                      options={"prompt": "viết hoa đầu từ",
                                               "keyterms": ["Acmebot"]})
        assert "viết hoa đầu từ" in prompt and "Acmebot" in prompt

    def test_version_gates_compare_numbers(self):
        assert prompt_version_number("v10") > prompt_version_number("v9") >= 5


class TestCpuBudget:
    def test_defaults_to_a_share_of_the_cores(self, monkeypatch):
        monkeypatch.setattr(cpu_budget, "available_cores", lambda: 10)
        assert cpu_budget.cpu_budget() == 6
        assert cpu_budget.ffmpeg_threads(4) == ["-threads", "1", "-filter_threads", "2"]
        assert cpu_budget.ffmpeg_decode_threads(4) == ["-threads", "2"]
        assert cpu_budget.ffmpeg_decode_threads(1) == ["-threads", "6"], "never above budget"

    def test_env_is_honoured_but_never_above_the_cores(self, monkeypatch):
        monkeypatch.setattr(cpu_budget, "available_cores", lambda: 10)
        monkeypatch.setenv("AUTOEDIT_CPU_BUDGET", "64")
        assert cpu_budget.cpu_budget() == 10

    def test_nice_zero_turns_the_prefix_off(self, monkeypatch):
        monkeypatch.setenv("AUTOEDIT_NICE", "0")
        assert cpu_budget.low_priority_prefix() == []

    @pytest.mark.skipif(not cpu_budget.low_priority_prefix(), reason="no nice here")
    def test_the_prefix_runs_a_command_with_its_own_flags(self):
        """uutils nice refuses `nice -n 10 python -c …` without a `--`."""
        result = subprocess.run([*cpu_budget.low_priority_prefix(), "python3", "-c",
                                 "import os; print(os.nice(0))"],
                                capture_output=True, text=True)
        assert result.returncode == 0 and int(result.stdout) >= 1


def _ffmpeg_ok() -> bool:
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _ffmpeg_ok(), reason="needs ffmpeg")
def test_cold_open_is_joined_without_re_encoding_the_timeline(tmp_path):
    from lib.talking_head_edit.resolve_cut import (
        _SEGMENT_FORMAT, prepend_teaser, probe_duration, video_stream_bitrate,
    )

    base = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=4",
                    "-f", "lavfi", "-i", "sine=f=440:d=4", "-c:v", "libx264", "-crf", "30",
                    *_SEGMENT_FORMAT, "-r", "30", str(base), "-loglevel", "error"], check=True)
    bitrate = video_stream_bitrate(base)

    teaser = prepend_teaser(base, 1.0, 2.0, fps=30, preset="ultrafast", crf=30)

    assert teaser == pytest.approx(5.0, abs=0.15)
    assert probe_duration(base) == pytest.approx(5.0, abs=0.15)
    assert video_stream_bitrate(base) == pytest.approx(bitrate, rel=0.35)


class TestVisionScoring:
    """The blind vision check's arithmetic — no model call."""

    @staticmethod
    def pairs(tmp_path):
        from lib.talking_head_edit.vision_quality_check import ALL_DEFECTS
        clean, bad = tmp_path / "clean.jpg", tmp_path / "bad.jpg"
        out = []
        for index, defect in enumerate(ALL_DEFECTS):
            out.append({"loi": defect, "a": clean, "b": bad, "dung": "a", "cap": f"c{2 * index}"})
            out.append({"loi": defect, "a": bad, "b": clean, "dung": "b", "cap": f"c{2 * index + 1}"})
        return out

    def test_a_model_that_sees_picks_the_clean_frame_both_ways(self, tmp_path):
        from lib.talking_head_edit.vision_quality_check import score_preference
        pairs = self.pairs(tmp_path)
        result = score_preference([{"cap": p["cap"], "tot_hon": p["dung"]} for p in pairs], pairs)
        assert result["ty_le_dung"] == 1.0 and "CHỌN ĐÚNG" in result["ket_luan"]

    def test_always_picking_a_is_exposed_as_guessing(self, tmp_path):
        from lib.talking_head_edit.vision_quality_check import score_preference
        pairs = self.pairs(tmp_path)
        result = score_preference([{"cap": p["cap"], "tot_hon": "a"} for p in pairs], pairs)
        assert result["ty_le_dung"] == 0.5 and result["ty_le_chon_a"] == 1.0
        assert "KHÔNG" in result["ket_luan"]

    def test_filters_scale_toward_neutral(self):
        from lib.talking_head_edit.vision_quality_check import defect_filter
        assert defect_filter("da_cam", 0.5) == "eq=saturation=1.25,colorbalance=rm=0.06:bm=-0.04"
        assert defect_filter("qua_toi", 0.25) == "eq=brightness=-0.04"


def test_a_dry_run_chat_does_not_leak_option_changes(tmp_path, monkeypatch):
    from lib.talking_head_edit import chat_revise

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0")
    job = JobStore(tmp_path / "jobs").create(clip, {"frame_preset": "dark"})
    job.spine_path.write_text(json.dumps({"word_timestamps": speech("a", "b")}), encoding="utf-8")
    job.spec_path(1).write_text(json.dumps({"events": [], "cut_remove": []}), encoding="utf-8")
    job.update(current_version=1, versions=[{"version": 1, "kind": "director"}])
    monkeypatch.setattr(revise_stage, "chat_json",
                        lambda *a, **k: ({"options": {"frame_preset": "none"}}, {}, ""))

    chat_revise.chat(job, "full khung", dry_run=True)

    state = job.load()
    assert state["options"]["frame_preset"] == "dark"
    assert state["current_version"] == 1


def _job_with_spec(tmp_path, spec, options=None):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0")
    job = JobStore(tmp_path / "jobs").create(clip, options or {})
    job.spine_path.write_text(json.dumps({"word_timestamps": speech(*"abcdefgh")}),
                              encoding="utf-8")
    job.spec_path(1).write_text(json.dumps(spec), encoding="utf-8")
    job.update(current_version=1, versions=[{"version": 1, "kind": "director"}])
    return job


class TestUserCutsAndRollback:
    def test_keeping_the_last_proposed_cut_does_not_bring_it_back(self):
        from lib.talking_head_edit.cut_safety import current_proposals
        new, _ = apply_patch({"events": [], "cut_remove": [[5, 6]]}, {"cut_restore": [[5, 6]]})
        assert current_proposals(new) == []

    def test_a_model_cannot_label_its_cuts_as_the_users(self):
        new, _ = apply_patch({"events": []}, {"cut_add": [{"w": [2, 3], "nguon": "khach"}]})
        assert new["cut_proposed"][0]["nguon"] == "ai"

    def test_a_user_cut_upgrades_the_directors_proposal_on_the_same_range(self):
        spec = {"events": [], "cut_proposed": [{"w": [2, 3], "nguon": "ai"}]}
        new, report = apply_patch(spec, {"cut_add": [{"w": [2, 3], "nguon": "khach"}]},
                                  trust_user_cuts=True)
        assert [(p["w"], p["nguon"]) for p in new["cut_proposed"]] == [([2, 3], "khach")]
        assert report["cuts_added"] == 1

    def test_marked_ranges_become_user_cuts_without_a_model(self, tmp_path):
        from lib.talking_head_edit.versions import apply_user_cuts
        job = _job_with_spec(tmp_path, {"events": [], "cut_remove": [[1, 1]]})
        result = apply_user_cuts(job, cut=[[4, 5]], keep=[[1, 1]])
        spec = json.loads(job.spec_path(result["version"]).read_text(encoding="utf-8"))
        assert spec["cut_proposed"] == [{"w": [4, 5], "ly_do": "khach_danh_dau", "nguon": "khach",
                                         "moi": True}]

    def test_rollback_puts_back_the_options_a_revise_changed(self, tmp_path, monkeypatch):
        from lib.talking_head_edit.versions import rollback_to
        monkeypatch.setattr(revise_stage, "usable_bgm", lambda: ["bgm_a.mp3", "bgm_b.mp3"])
        job = _job_with_spec(tmp_path, {"events": [], "cut_remove": []},
                             {"frame_preset": "dark", "bgm_name": "bgm_a.mp3"})
        patch = {"options": {"frame_preset": "none"},
                 "set": {"bgm": {"name": "bgm_b.mp3", "volume": 0.15}}}
        monkeypatch.setattr(revise_stage, "chat_json", lambda *a, **k: (patch, {}, ""))
        revise_stage.run(job, {**job.load()["options"], "revise_instruction": "x"})
        assert job.load()["options"]["frame_preset"] == "none"

        result = rollback_to(job, 1)

        options = job.load()["options"]
        assert (options["frame_preset"], options["bgm_name"]) == ("dark", "bgm_a.mp3")
        assert result["version"] == 3 and set(result["options_changed"]) >= {"frame_preset"}

    def test_an_out_of_range_user_cut_is_reported(self, monkeypatch):
        verifier_says(monkeypatch, "keep")
        result = audit_cuts([{"w": [40, 41], "nguon": "khach"}], speech("a", "b"), None, "normal")
        assert result["applied"] == [] and result["blocked"][0]["by"] == "ngoai_pham_vi"


def test_cancelling_marks_the_stage_that_was_running(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0")
    job = JobStore(tmp_path / "jobs").create(clip, {})
    job.set_stage("render", status="running")
    state = job.mark_cancelled()
    assert state["status"] == "cancelled" and state["stages"]["render"]["status"] == "cancelled"
