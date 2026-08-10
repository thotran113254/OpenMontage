"""Prompt registry, and the gate that makes the migration safe.

`TestByteIdenticalGate` is the whole point of this file. Translating five
prompts from f-strings to templates is a pure refactor, so the output must be
identical to the byte — "looks about right" would let a silently degraded prompt
through, and nothing downstream measures prompt quality.

The goldens in `tests/fixtures/prompts/` were captured from the f-string
implementations before they were replaced, so they are the real reference and not
a snapshot of the new code agreeing with itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.talking_head_edit import prompt_registry
from lib.talking_head_edit.prompt_registry import PromptError

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "prompts"

OPTIONS = {"topic": "3 sai lầm chatbot AI", "card_plan": "Đúng 4 card",
           "prompt": "giữ nhịp nhanh, đừng cắt ý",
           "bgm": True, "cold_open": True, "brand_pill": "MONA"}
PROFILE = {"notes": ["caption ngắn", "màu nóng cho cảnh báo"]}


def words(count, src="s0"):
    return [{"word": f"tu{i}", "start": round(i * 0.3, 3),
             "end": round(i * 0.3 + 0.2, 3), "src": src}
            for i in range(count)]


def golden(name: str) -> str:
    return (GOLDEN_DIR / f"{name}.golden.txt").read_text(encoding="utf-8")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Registry pointed at a temp dir, seeded with the real shipped templates.

    Override files and registry.json are written by these tests; doing that in
    the repo would leave the developer's own prompts modified.
    """
    prompts = tmp_path / "prompts"
    (prompts / "talking_head").mkdir(parents=True)
    (prompts / "overrides" / "talking_head").mkdir(parents=True)
    for path in (prompt_registry.TEMPLATE_DIR).glob("*.md"):
        (prompts / "talking_head" / path.name).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(prompt_registry, "PROMPTS_DIR", prompts)
    monkeypatch.setattr(prompt_registry, "TEMPLATE_DIR", prompts / "talking_head")
    monkeypatch.setattr(prompt_registry, "OVERRIDE_DIR",
                        prompts / "overrides" / "talking_head")
    monkeypatch.setattr(prompt_registry, "REGISTRY_PATH", prompts / "registry.json")
    return prompts


class TestByteIdenticalGate:
    """Each template must reproduce its f-string predecessor exactly."""

    def test_structure_single_source(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        assert build_structure_prompt(words(12), OPTIONS, PROFILE, version="v1") == \
            golden("structure_single")

    def test_structure_v2_is_identical_to_v1_without_broll(self):
        """v2 only adds a collapsible block, so a job with no b-roll source must
        get exactly the prompt it got before — that is what makes v2 safe as the
        default for every job rather than a per-job template choice."""
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        assert build_structure_prompt(words(12), OPTIONS, PROFILE, version="v2") == \
            golden("structure_single")

    def test_structure_v2_lists_only_real_broll_sources(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(
            words(12), OPTIONS, PROFILE, version="v2",
            overlay_pool=[{"src": "s2", "label": "bàn làm việc", "duration": 8.0}])
        assert '"type":"broll"' in prompt
        assert 'src="s2"' in prompt
        assert "bàn làm việc" in prompt

    def test_structure_v3_has_retention_gates_and_style_modes(self):
        """Director architecture: peak/valley retention + editorial modes, not
        metronome density. Still same 7 JSON keys and {{placeholders}} as v2."""
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(words(12), OPTIONS, PROFILE, version="v3")
        assert "4 CỔNG RETENTION" in prompt or "Cổng A" in prompt
        assert "edu" in prompt and "hot_take" in prompt
        assert "dead_air" in prompt and "soft_restart" in prompt
        assert "8-12s" not in prompt or "Không bắt buộc" in prompt
        assert "TRẢ VỀ DUY NHẤT JSON" in prompt
        # Same placeholder surface as v2 so prompt_structure.py needs no fork.
        for needle in ("Đúng 4 card", "giữ nhịp nhanh", "caption ngắn"):
            assert needle in prompt

    def test_structure_v3_is_shipped_default(self):
        assert prompt_registry.current_version("structure") == "v3"

    def test_structure_v3_keeps_broll_hook(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(
            words(12), OPTIONS, PROFILE, version="v3",
            overlay_pool=[{"src": "s2", "label": "bàn làm việc", "duration": 8.0}])
        assert '"type":"broll"' in prompt
        assert 'src="s2"' in prompt

    def test_structure_multi_source(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        assert build_structure_prompt(words(6) + words(6, src="s1"), OPTIONS,
                                     PROFILE, source_boundaries=[6],
                                     version="v1") == \
            golden("structure_multi")

    def test_structure_cross_source_cut_allowed(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        assert build_structure_prompt(words(6) + words(6, src="s1"), OPTIONS, PROFILE,
                                     source_boundaries=[6],
                                     cross_source_cut=True, version="v1") == \
            golden("structure_multi_cross")

    def test_structure_with_bgm_and_cold_open_off(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        assert build_structure_prompt(words(5), {"bgm": False, "cold_open": False},
                                     version="v1") == \
            golden("structure_minimal")

    def test_captions(self):
        from lib.talking_head_edit.prompt_captions import build_caption_prompt

        assert build_caption_prompt(words(40), 8, 24) == golden("captions")

    def test_cut_verify(self):
        """Pinned to v1: v2 deliberately adds the `unsure` verdict, so the gate
        checks the migration was faithful, not that nothing ever changes."""
        from lib.talking_head_edit.cut_verifier import build_prompt

        assert build_prompt([
            {"id": 0, "w": [4, 6], "before": "toi noi la", "cut": "a a a",
             "after": "chi phi cao"},
            {"id": 1, "w": [10, 11], "before": "khach hang", "cut": "ay",
             "after": "se roi di"},
        ], version="v1") == golden("cut_verify")

    def test_revise(self):
        from lib.talking_head_edit.stages.revise import build_revise_prompt

        spec = {"events": [
            {"type": "caption", "w0": 0, "w1": 5, "text": "Xin chào"},
            {"type": "card", "w0": 6, "w1": 20, "badge": "1", "title": "Sai lầm 1",
             "bullets": ["bot trả lời sai"]},
            {"type": "keyword", "atWord": 3, "text": "SỐC", "color": "#FF2E93"},
            {"type": "sfx", "atWord": 4, "name": "sfx_pop.mp3"},
            {"type": "flash", "atWord": 9},
        ]}
        assert build_revise_prompt(spec, words(30),
                                   "bỏ card 2, thêm keyword ở đầu") == golden("revise")

    def test_select_take(self):
        from lib.talking_head_edit.stages.select import build_select_prompt

        assert build_select_prompt(words(6) + words(6, src="s1"), [6]) == \
            golden("select_take")


class TestCardCountIsNotFormulaic:
    """The default card guidance (used whenever `card_plan` is empty) must ask the
    model to count real independent points in THIS video, not aim for a fixed
    number. A 20-word clip and a 2000-word clip need different answers, and
    neither answer is "3-5" — that was the old default this guards against.
    """

    def _card_bullet(self, n: int) -> str:
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(words(n), {})  # no card_plan -> default kicks in
        # card_guidance is spliced as one bullet under the cards schema. Prefer the
        # real guidance line (Ý CHÍNH) over incidental "card" mentions in style-mode
        # prose (structure.v3 lists "card nhiều hơn" under edu mode first).
        for line in prompt.splitlines():
            if "Ý CHÍNH" in line or "ý chính" in line.lower():
                return line
        for line in prompt.splitlines():
            if line.startswith("- ") and "card" in line.lower():
                return line
        raise AssertionError(f"Không tìm thấy dòng hướng dẫn card trong:\n{prompt}")

    def test_no_fixed_count_or_range_is_suggested(self):
        bullet = self._card_bullet(30)
        for banned in ("3-5", "3–5", "thường 3", "mặc định là"):
            assert banned not in bullet, f"vẫn còn mốc số cứng: {banned!r}"

    def test_asks_the_model_to_count_real_points_instead(self):
        bullet = self._card_bullet(30)
        assert "Ý CHÍNH" in bullet or "ý chính" in bullet.lower()
        assert "độc lập" in bullet.lower() or "riêng biệt" in bullet.lower()

    def test_explicitly_allows_very_few_cards_for_thin_content(self):
        bullet = self._card_bullet(20)
        assert "0-1" in bullet or "0-2" in bullet or "ít card" in bullet.lower()

    def test_explicitly_forbids_padding_a_single_idea_into_many_cards(self):
        """The failure mode a fixed '3-5' invites: splitting one point into
        three cards just to hit the range."""
        bullet = self._card_bullet(20)
        assert "chia nhỏ" in bullet.lower()

    def test_explicitly_forbids_compressing_distinct_ideas_into_one_card(self):
        """The opposite failure mode: a dense video capped at the same '3-5'."""
        bullet = self._card_bullet(200)
        assert "gộp" in bullet.lower()

    def test_word_count_is_shown_as_scale_context(self):
        """`n` is already in the spine header the model sees; repeating it here
        ties the decision to THIS video's actual size, not a generic instruction
        that reads the same for a 10-word clip and a 10,000-word one."""
        assert "20 từ" in self._card_bullet(20)
        assert "200 từ" in self._card_bullet(200)

    def test_an_explicit_card_plan_still_overrides_everything(self):
        """A human's explicit instruction is not a default — it must win outright,
        exactly as before this change."""
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(words(30), {"card_plan": "Đúng 4 card"})
        assert "Đúng 4 card" in prompt
        assert "Ý CHÍNH" not in prompt


class TestCardGuidanceRevert:
    """The exact capability this exists for: an original and an updated
    wording that can be switched between, without touching code.
    """

    def test_v1_is_the_original_fixed_wording(self):
        assert "3-5" in prompt_registry.render("card_guidance", {"n": 40}, version="v1")

    def test_v2_is_the_current_adaptive_wording(self):
        rendered = prompt_registry.render("card_guidance", {"n": 40}, version="v2")
        assert "3-5" not in rendered
        assert "40" in rendered

    def test_v2_is_the_shipped_default(self):
        assert prompt_registry.current_version("card_guidance") == "v2"

    def test_reverting_switches_the_rendered_default_back_to_v1(self, sandbox):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt_registry.set_current("card_guidance", "v1")
        try:
            prompt = build_structure_prompt(words(40), {})
            assert "thường 3-5 card" in prompt
            assert "Ý CHÍNH" not in prompt
        finally:
            prompt_registry.set_current("card_guidance", "v2")

    def test_a_card_guidance_edit_reruns_direct(self, tmp_path, sandbox):
        """The fingerprint direct's cache key hashes must name `card_guidance`
        explicitly — it is spliced INTO `structure`'s own text, so its bytes
        never show up in `structure`'s fingerprint on their own."""
        from lib.talking_head_edit import runner
        from lib.talking_head_edit.job_store import JobStore

        store = JobStore(root=tmp_path / "job-store")
        source = tmp_path / "footage.mp4"
        source.write_bytes(b"video")
        job = store.create(source, {}, title="cache test")
        job.spine_path.write_text('{"word_timestamps":[]}', encoding="utf-8")

        before, _ = runner.cache_signature("direct", job, {})
        prompt_registry.save_override("card_guidance", "bản thử nghiệm khác {{n}}")
        after, _ = runner.cache_signature("direct", job, {})
        assert before != after


class TestRender:
    def test_missing_variable_raises_before_any_model_call(self, sandbox):
        (sandbox / "talking_head" / "demo.v1.md").write_text(
            "xin chào {{ten}} và {{tuoi}}", encoding="utf-8")
        with pytest.raises(PromptError, match="tuoi"):
            prompt_registry.render("demo", {"ten": "A"})

    def test_leftover_placeholder_is_impossible_to_ship(self, sandbox):
        """A placeholder reaching the model as literal `{{spine}}` is the failure
        this check exists for."""
        (sandbox / "talking_head" / "demo.v1.md").write_text(
            "{{a}}", encoding="utf-8")
        # A variable whose *value* contains a placeholder must still be caught.
        with pytest.raises(PromptError, match="còn placeholder"):
            prompt_registry.render("demo", {"a": "{{b}}"})

    def test_json_braces_pass_through_untouched(self, sandbox):
        """The reason this is str.replace and not str.format."""
        (sandbox / "talking_head" / "demo.v1.md").write_text(
            'trả về {"w0":i,"w1":j} cho {{n}} từ', encoding="utf-8")
        assert prompt_registry.render("demo", {"n": 5}) == \
            'trả về {"w0":i,"w1":j} cho 5 từ'

    def test_missing_template_raises(self, sandbox):
        with pytest.raises(PromptError, match="Không có template"):
            prompt_registry.render("khong_co_that", {})

    def test_placeholders_are_listed(self, sandbox):
        found = prompt_registry.placeholders("structure")
        for expected in ("spine", "sfx_table", "card_rule", "n"):
            assert expected in found


class TestPathTraversal:
    @pytest.mark.parametrize("bad_id", ["../secrets", "a/b", "Structure", "a-b", ""])
    def test_bad_ids_are_refused(self, bad_id, sandbox):
        with pytest.raises(PromptError):
            prompt_registry.load(bad_id, "v1")

    @pytest.mark.parametrize("bad_version", ["../v1", "v", "1", "latest"])
    def test_bad_versions_are_refused(self, bad_version, sandbox):
        with pytest.raises(PromptError):
            prompt_registry.load("structure", bad_version)

    def test_no_version_means_the_current_one(self, sandbox):
        """`None`/empty is "use current", not a malformed version."""
        assert prompt_registry.load("structure", "") == \
            prompt_registry.load("structure", "v1")


class TestOverrides:
    def test_override_beats_the_shipped_template(self, sandbox):
        prompt_registry.save_override("captions", "bản của admin {{spine}}",
                                     version="v1")
        assert prompt_registry.load("captions", "v1").startswith("bản của admin")

    def test_new_version_number_auto_increments(self, sandbox):
        first = prompt_registry.save_override("captions", "a {{spine}}")
        second = prompt_registry.save_override("captions", "b {{spine}}")
        assert (first["version"], second["version"]) == ("v2", "v3")

    def test_saving_makes_it_current_and_records_a_changelog(self, sandbox):
        prompt_registry.save_override("captions", "x {{spine}}", note="thử ngắn hơn",
                                     author="tho")
        assert prompt_registry.current_version("captions") == "v2"
        entry = prompt_registry.registry()["captions"]
        assert entry["changelog"][-1]["note"] == "thử ngắn hơn"
        assert entry["changelog"][-1]["author"] == "tho"

    def test_dropping_the_json_line_warns(self, sandbox):
        """An override without it does not fail — it returns prose the parser
        rejects three minutes later."""
        result = prompt_registry.save_override("captions", "viết một bài thơ {{spine}}")
        assert any("TRẢ VỀ DUY NHẤT JSON" in w for w in result["warnings"])

    def test_dropping_a_placeholder_warns(self, sandbox):
        """Losing `{{body}}` means the words never reach the model at all."""
        result = prompt_registry.save_override(
            "captions", "TRẢ VỀ DUY NHẤT JSON — không có lời nói")
        assert any("body" in w for w in result["warnings"])

    def test_empty_body_is_refused(self, sandbox):
        with pytest.raises(PromptError):
            prompt_registry.save_override("captions", "   ")

    def test_deleting_an_override_restores_shipped_behaviour(self, sandbox):
        prompt_registry.save_override("captions", "tạm {{spine}}", version="v1")
        prompt_registry.delete_override("captions", "v1")
        assert "karaoke pill" in prompt_registry.load("captions", "v1")

    def test_deleting_the_current_version_falls_back_to_v1(self, sandbox):
        prompt_registry.save_override("captions", "x {{spine}}")   # v2, current
        prompt_registry.delete_override("captions", "v2")
        assert prompt_registry.current_version("captions") == "v1"

    def test_deleting_a_missing_override_is_an_error(self, sandbox):
        with pytest.raises(PromptError):
            prompt_registry.delete_override("captions", "v9")

    def test_set_current_requires_the_version_to_exist(self, sandbox):
        with pytest.raises(PromptError):
            prompt_registry.set_current("captions", "v7")

    def test_versions_lists_builtin_and_override(self, sandbox):
        prompt_registry.save_override("captions", "x {{spine}}")
        rows = prompt_registry.versions("captions")
        assert [r["version"] for r in rows] == ["v1", "v2"]
        assert rows[0]["source"] == "builtin"
        assert rows[1]["source"] == "override"
        assert rows[1]["is_current"] is True

    def test_diff_against_the_shipped_template(self, sandbox):
        prompt_registry.save_override("captions", "chỉ một dòng {{spine}}")
        text = prompt_registry.diff("captions", "v2")
        assert "chỉ một dòng" in text
        assert text.startswith("---")

    def test_catalog_lists_every_shipped_prompt(self, sandbox):
        ids = {row["id"] for row in prompt_registry.catalog()}
        for expected in ("captions", "cut_verify", "revise", "select_take", "card_guidance"):
            assert expected in ids


class TestShippedTemplates:
    def test_every_prompt_has_a_v1_on_disk(self):
        for prompt_id in ("structure", "captions", "cut_verify", "revise", "select_take",
                         "card_guidance"):
            assert (prompt_registry.TEMPLATE_DIR / f"{prompt_id}.v1.md").exists()

    def test_registry_json_is_valid_and_points_at_real_files(self):
        data = prompt_registry.registry()
        for prompt_id, entry in data.items():
            version = str(entry.get("current") or "v1")
            assert prompt_registry.template_path(prompt_id, version).exists(), \
                f"{prompt_id}.{version} không tồn tại"

    def test_every_template_keeps_its_json_marker(self):
        for prompt_id, markers in prompt_registry.REQUIRED_MARKERS.items():
            body = prompt_registry.load(prompt_id, "v1")
            for marker in markers:
                assert marker in body, f"{prompt_id}.v1 thiếu «{marker}»"


class TestCacheInvalidation:
    def test_editing_a_prompt_reruns_direct(self, tmp_path, monkeypatch, sandbox):
        """The easiest thing to forget in this whole phase: without hashing the
        rendered prompt, editing it changes nothing and the prompt looks useless."""
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.runner import cache_signature

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})
        job.spine_path.write_text(json.dumps({
            "schema": 3,
            "word_timestamps": words(20),
            "takes": [{"src": "s0", "w0": 0, "w1": 19, "take_group": "main", "order": 0}],
        }), encoding="utf-8")
        options = job.load()["options"]

        before, _ = cache_signature("direct", job, options)
        prompt_registry.save_override("structure", "prompt hoàn toàn khác {{spine}}")
        after, _ = cache_signature("direct", job, options)
        assert before != after

    def test_not_editing_a_prompt_keeps_the_cache(self, tmp_path, sandbox):
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.runner import cache_signature

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})
        job.spine_path.write_text(json.dumps({
            "schema": 3,
            "word_timestamps": words(20),
            "takes": [{"src": "s0", "w0": 0, "w1": 19, "take_group": "main", "order": 0}],
        }), encoding="utf-8")
        options = job.load()["options"]

        first, _ = cache_signature("direct", job, options)
        second, _ = cache_signature("direct", job, options)
        assert first == second
