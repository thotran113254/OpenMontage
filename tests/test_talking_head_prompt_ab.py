"""A/B comparison: mechanical verdict rules, isolation, and the cost preview.

The model calls are stubbed. What is being tested is the part that must not be a
model's opinion: which branch wins and why. A model scoring two of its own
prompts is a loop with no independent signal in it, so the rules live in code —
and therefore in tests.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit import prompt_ab, prompt_registry
from lib.talking_head_edit.job_store import JobStore
from lib.talking_head_edit.prompt_ab import AbError, card_target, verdict


def words(count=30):
    return [{"word": f"tu{i}", "start": round(i * 0.5, 3),
             "end": round(i * 0.5 + 0.4, 3), "src": "s0"}
            for i in range(count)]


def branch(**overrides):
    base = {"version": "v1", "cards": 4, "caption_coverage": 1.0,
            "captions_over_9w": 0, "keyword_in_card": 0, "events": 20,
            "cuts_proposed": 6, "cuts_rejected_by_verifier": 1,
            "removed_resources": 0, "tokens": {"in": 48000, "out": 7400},
            "cost_usd": 0.0}
    return {**base, **overrides}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    prompts = tmp_path / "prompts"
    (prompts / "talking_head").mkdir(parents=True)
    (prompts / "overrides" / "talking_head").mkdir(parents=True)
    for path in prompt_registry.TEMPLATE_DIR.glob("*.md"):
        (prompts / "talking_head" / path.name).write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(prompt_registry, "PROMPTS_DIR", prompts)
    monkeypatch.setattr(prompt_registry, "TEMPLATE_DIR", prompts / "talking_head")
    monkeypatch.setattr(prompt_registry, "OVERRIDE_DIR",
                        prompts / "overrides" / "talking_head")
    monkeypatch.setattr(prompt_registry, "REGISTRY_PATH", prompts / "registry.json")
    return prompts


@pytest.fixture
def job(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0")
    job = JobStore(tmp_path / "jobs").create(clip, {"card_plan": "Đúng 4 card"})
    job.spine_path.write_text(json.dumps({
        "schema": 3, "word_timestamps": words(),
        "takes": [{"src": "s0", "w0": 0, "w1": 29, "take_group": "main", "order": 0}],
        "sources": [{"id": "s0", "path": str(clip), "role": "aroll", "order": 0}],
    }, ensure_ascii=False), encoding="utf-8")
    return job


class TestCardTarget:
    @pytest.mark.parametrize("plan,expected", [
        ("Đúng 4 card", 4),
        ("3-5 card, mỗi card một ý", 3),
        ("làm 6 card", 6),
        ("số card do bạn quyết", None),
        ("", None),
        ("999 card", None),
    ])
    def test_number_is_read_from_the_brief(self, plan, expected):
        assert card_target(plan) == expected


class TestVerdictRules:
    def test_card_count_against_the_brief_wins_first(self):
        """A missing card is a missing section of the video — it outranks
        everything else that could differ."""
        result = verdict(branch(cards=4, caption_coverage=0.9),
                        branch(cards=3, caption_coverage=1.0), card_goal=4)
        assert result["winner"] == "a"
        assert "card" in result["reason"]

    def test_no_card_goal_means_card_count_is_not_scored(self):
        result = verdict(branch(cards=4), branch(cards=7), card_goal=None)
        assert result["winner"] == "tie"

    def test_coverage_beats_caption_length(self):
        result = verdict(branch(caption_coverage=1.0, captions_over_9w=3),
                        branch(caption_coverage=0.90, captions_over_9w=0))
        assert result["winner"] == "a"
        assert "phủ caption" in result["reason"]

    def test_tiny_coverage_difference_is_noise_not_a_verdict(self):
        result = verdict(branch(caption_coverage=1.0, captions_over_9w=0),
                        branch(caption_coverage=0.99, captions_over_9w=2))
        assert result["winner"] == "a"
        assert "quá dài" in result["reason"], "1% lệch phủ là nhiễu, phải xét tiêu chí sau"

    def test_long_captions_beat_hidden_keywords(self):
        result = verdict(branch(captions_over_9w=0, keyword_in_card=2),
                        branch(captions_over_9w=4, keyword_in_card=0))
        assert result["winner"] == "a"
        assert "quá dài" in result["reason"]

    def test_hidden_keywords_are_scored(self):
        result = verdict(branch(keyword_in_card=0), branch(keyword_in_card=3))
        assert result["winner"] == "a"
        assert "card che" in result["reason"]

    def test_safer_cuts_are_the_last_tiebreak(self):
        result = verdict(branch(cuts_rejected_by_verifier=0),
                        branch(cuts_rejected_by_verifier=4))
        assert result["winner"] == "a"
        assert "verifier" in result["reason"]

    def test_identical_branches_are_an_honest_tie(self):
        """Inventing a winner from noise is worse than saying "look at it"."""
        result = verdict(branch(), branch(version="v2"))
        assert result["winner"] == "tie"
        assert "bằng mắt" in result["reason"]

    def test_b_can_win(self):
        result = verdict(branch(cards=2), branch(cards=4), card_goal=4)
        assert result["winner"] == "b"


class TestRunIsolation:
    def _stub_chat(self, cards_by_version):
        calls: list[str] = []

        def chat(prompt, model=None, temperature=0.25, max_tokens=None,
                 raw_dump=None, **kwargs):
            # Which version produced this prompt is recoverable from its text
            # because the override bodies below are distinctive.
            version = "v2" if "BẢN V2" in prompt else "v1"
            calls.append(version)
            count = cards_by_version[version]
            return ({
                "cards": [{"w0": i * 5, "w1": i * 5 + 4, "title": f"c{i}",
                           "bullets": ["x"], "badge": str(i + 1)}
                          for i in range(count)],
                "events": [], "cut_remove": [], "grade": {},
                "bgm": None, "cold_open": None, "endcard": {"title": "chốt"},
            }, {"prompt_tokens": 1000, "completion_tokens": 200}, "")

        return chat, calls

    def test_ab_does_not_touch_the_job_spec(self, job, sandbox, monkeypatch):
        """An experiment must not be able to damage a build someone approved."""
        job.spec_path(1).write_text('{"events": [], "approved": true}', encoding="utf-8")
        job.update(current_version=1)
        prompt_registry.save_override(
            "structure", "BẢN V2 {{spine}} TRẢ VỀ DUY NHẤT JSON", version="v2",
            make_current=False)
        monkeypatch.setattr(prompt_ab, "verify_cuts",
                            lambda *a, **k: ([], [], {}))

        chat, _ = self._stub_chat({"v1": 4, "v2": 3})
        prompt_ab.run(job, "v1", "v2", chat=chat)

        assert json.loads(job.spec_path(1).read_text(encoding="utf-8"))["approved"] is True
        assert not job.spec_path(2).exists()

    def test_both_branches_write_into_their_own_directory(self, job, sandbox, monkeypatch):
        prompt_registry.save_override(
            "structure", "BẢN V2 {{spine}} TRẢ VỀ DUY NHẤT JSON", version="v2",
            make_current=False)
        monkeypatch.setattr(prompt_ab, "verify_cuts", lambda *a, **k: ([], [], {}))
        chat, calls = self._stub_chat({"v1": 4, "v2": 3})

        result = prompt_ab.run(job, "v1", "v2", chat=chat)

        assert sorted(calls) == ["v1", "v2"]
        assert result["a"]["cards"] == 4 and result["b"]["cards"] == 3
        assert result["verdict"]["winner"] == "a", "brief đòi 4 card"

        from pathlib import Path

        comparison = Path(result["dir"]) / "comparison.json"
        assert comparison.exists()
        assert json.loads(comparison.read_text(encoding="utf-8"))["words"] == 30

    def test_same_version_twice_is_refused(self, job, sandbox):
        with pytest.raises(AbError, match="giống nhau"):
            prompt_ab.run(job, "v1", "v1")

    def test_missing_version_is_refused_before_spending_tokens(self, job, sandbox):
        with pytest.raises(AbError, match="v9"):
            prompt_ab.run(job, "v1", "v9")

    def test_no_spine_is_refused(self, tmp_path, sandbox):
        prompt_registry.save_override(
            "structure", "BẢN V2 {{spine}} TRẢ VỀ DUY NHẤT JSON", version="v2",
            make_current=False)
        clip = tmp_path / "c.mp4"
        clip.write_bytes(b"0")
        empty = JobStore(tmp_path / "jobs2").create(clip, {})
        empty.spine_path.write_text('{"word_timestamps": []}', encoding="utf-8")
        with pytest.raises(AbError, match="spine"):
            prompt_ab.run(empty, "v1", "v2")


class TestCostPreview:
    def test_estimate_is_reported_before_running(self, job, sandbox):
        prompt_registry.save_override(
            "structure", "BẢN V2 {{spine}} TRẢ VỀ DUY NHẤT JSON", version="v2",
            make_current=False)
        preview = prompt_ab.preview_cost(job, job.load()["options"], ("v1", "v2"))
        assert preview["words"] == 30
        assert preview["calls"] >= 4, "hai nhánh, mỗi nhánh ít nhất 1 khung + 1 caption"
        assert preview["estimated_tokens_in"] > 0

    def test_estimate_roughly_doubles_a_single_run(self, job, sandbox):
        """The whole objection to A/B is that it doubles a director call, so the
        estimate has to reflect that rather than hide it."""
        prompt_registry.save_override(
            "structure", "BẢN V2 {{spine}} TRẢ VỀ DUY NHẤT JSON", version="v2",
            make_current=False)
        both = prompt_ab.preview_cost(job, job.load()["options"], ("v1", "v2"))
        single = prompt_ab.preview_cost(job, job.load()["options"], ("v1", "v1"))
        # Same spine, so the per-branch cost is the same; the total is 2x either way.
        assert both["estimated_tokens_in"] == pytest.approx(
            single["estimated_tokens_in"], rel=0.35)
