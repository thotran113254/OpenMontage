"""The `select` stage and its guards.

Every guard here exists because the failure it prevents is invisible: a bad
`kept_word_ranges` does not crash anything, it silently removes something the
speaker said. So the tests are mostly about what the stage REFUSES to do, and
about it always falling back to keeping everything rather than guessing.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit.asr.base import build_spine
from lib.talking_head_edit.job_store import JobStore
from lib.talking_head_edit.stages import select


def words_of(count, base=0.0):
    return [{"word": f"w{i}", "start": round(base + i * 0.25, 3),
             "end": round(base + i * 0.25 + 0.2, 3), "src": "s0"}
            for i in range(count)]


def two_take_spine(per_take=100):
    """Two sources of the same content — the case `select` exists for."""
    words = []
    for source_id in ("s0", "s1"):
        for index in range(per_take):
            words.append({"word": f"{source_id}w{index}",
                          "start": round(index * 0.25, 3),
                          "end": round(index * 0.25 + 0.2, 3),
                          "src": source_id})
    return {
        "schema": 3, "provider": "elevenlabs_scribe", "model": "scribe_v2",
        "language": "vi", "duration_seconds": per_take * 0.25,
        "word_timestamps": words,
        "takes": [
            {"src": "s0", "w0": 0, "w1": per_take - 1, "take_group": "main", "order": 0},
            {"src": "s1", "w0": per_take, "w1": per_take * 2 - 1,
             "take_group": "main", "order": 1},
        ],
        "sources": [
            {"id": "s0", "path": "C:/f/a-take1.mp4", "role": "aroll", "order": 0,
             "take_group": "main", "speech": True, "duration": per_take * 0.25},
            {"id": "s1", "path": "C:/f/a-take2.mp4", "role": "aroll", "order": 1,
             "take_group": "main", "speech": True, "duration": per_take * 0.25},
        ],
        "overlay_pool": [], "audio_events": [], "speakers": [],
    }


@pytest.fixture
def job(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0")
    return JobStore(tmp_path / "jobs").create(clip, {})


def write_spine(job, spine):
    job.spine_path.write_text(json.dumps(spine, ensure_ascii=False), encoding="utf-8")


class TestNoOpPaths:
    def test_single_source_keeps_everything_without_a_model_call(self, job, monkeypatch):
        spine = build_spine(words_of(20), provider="elevenlabs_scribe",
                            model="scribe_v2", language="vi", duration_seconds=5.0)
        spine["takes"] = [{"src": "s0", "w0": 0, "w1": 19, "take_group": "main",
                           "order": 0}]
        write_spine(job, spine)

        monkeypatch.setattr(select, "chat_json", lambda *a, **k: pytest.fail(
            "một nguồn thì không được gọi model"))

        result = select.run(job, {})
        assert result["mode_resolved"] == "sequential"
        assert result["kept_word_ranges"] == [[0, 19]]
        assert result["coverage"] == 1.0

    def test_sequential_mode_skips_the_model_entirely(self, job, monkeypatch):
        write_spine(job, two_take_spine(20))
        job.update(assembly_resolved={"mode": "sequential", "speaker_aware": "auto",
                                      "broll_overlay": True,
                                      "cross_source_cut": False,
                                      "take_detect": "suggest"})
        monkeypatch.setattr(select, "chat_json", lambda *a, **k: pytest.fail(
            "sequential thì không được gọi model"))

        result = select.run(job, {})
        assert result["mode_resolved"] == "sequential"
        assert result["kept_word_ranges"] == [[0, 39]]

    def test_selection_is_written_to_disk_and_job_state(self, job, monkeypatch):
        write_spine(job, two_take_spine(10))
        job.update(assembly_resolved={"mode": "sequential", "speaker_aware": "auto",
                                      "broll_overlay": True,
                                      "cross_source_cut": False,
                                      "take_detect": "suggest"})
        select.run(job, {})
        assert (job.dir / "selection_v1.json").exists()
        assert job.load()["selection"]["mode_resolved"] == "sequential"


class TestBestTake:
    def _answer(self, monkeypatch, payload):
        monkeypatch.setattr(select, "chat_json",
                            lambda *a, **k: (payload, {"total_tokens": 100}, ""))

    def test_accepts_a_clean_proposal(self, job, monkeypatch):
        write_spine(job, two_take_spine(100))
        self._answer(monkeypatch, {
            "no_overlap": False,
            "segments": [{"content": "mở đầu", "chosen": {"w": [100, 199]},
                          "reason": "take 2 nói liền mạch"}],
            "kept_word_ranges": [[100, 199]],
        })
        result = select.run(job, {})
        assert result["mode_resolved"] == "best_take"
        assert result["kept_word_ranges"] == [[100, 199]]
        assert result["dropped_words"] == 100

    def test_no_overlap_answer_becomes_sequential(self, job, monkeypatch):
        write_spine(job, two_take_spine(50))
        self._answer(monkeypatch, {"no_overlap": True, "kept_word_ranges": [[0, 99]]})
        result = select.run(job, {})
        assert result["mode_resolved"] == "sequential"
        assert result["kept_word_ranges"] == [[0, 99]]

    def test_model_failure_falls_back_instead_of_stopping_the_job(self, job, monkeypatch):
        write_spine(job, two_take_spine(50))

        def explode(*args, **kwargs):
            raise select.DirectorError("gateway 500")

        monkeypatch.setattr(select, "chat_json", explode)
        result = select.run(job, {})
        assert result["mode_resolved"] == "sequential"
        assert any("không gọi được model" in w for w in result["warnings"])

    def test_garbage_answer_falls_back_with_a_reason(self, job, monkeypatch):
        write_spine(job, two_take_spine(50))
        self._answer(monkeypatch, {"kept_word_ranges": "không phải danh sách"})
        result = select.run(job, {})
        assert result["mode_resolved"] == "sequential"
        assert result["warnings"]


class TestRangeGuards:
    def takes(self, per_take=100):
        return two_take_spine(per_take)["takes"]

    def test_overlapping_ranges_are_refused(self):
        _, problems = select.check_ranges([[0, 50], [40, 90]], 200, self.takes())
        assert any("chồng nhau" in p for p in problems)

    def test_out_of_bounds_range_is_refused(self):
        _, problems = select.check_ranges([[0, 500]], 200, self.takes())
        assert any("ra ngoài dãy" in p for p in problems)

    def test_empty_proposal_is_refused(self):
        _, problems = select.check_ranges([], 200, self.takes())
        assert problems

    def test_non_numeric_range_is_refused(self):
        _, problems = select.check_ranges([["a", "b"]], 200, self.takes())
        assert problems

    def test_reversed_range_is_repaired_not_refused(self):
        clean, problems = select.check_ranges([[199, 100]], 200, self.takes())
        assert clean == [[100, 199]]
        assert problems == []

    def test_keeping_too_little_overall_is_refused(self):
        """A proposal that throws away two thirds of the words has mistaken two
        different passages for takes of the same thing."""
        _, problems = select.check_ranges([[0, 10]], 200, self.takes())
        assert any("quá ít" in p for p in problems)

    def test_take_group_must_keep_most_of_one_take(self):
        # 200 words, two takes of 100. Keeping 50 from each passes total coverage
        # but leaves neither take substantially intact.
        _, problems = select.check_ranges([[0, 49], [100, 149]], 200, self.takes())
        assert any("nhóm take" in p for p in problems)

    def test_a_whole_take_kept_passes_every_guard(self):
        clean, problems = select.check_ranges([[100, 199]], 200, self.takes())
        assert clean == [[100, 199]]
        assert problems == []


class TestMarkedSpine:
    def test_marker_is_inserted_at_the_take_boundary(self):
        spine = two_take_spine(3)
        text = select.marked_spine(spine["word_timestamps"], [3])
        assert "--- NGUỒN 2 ---" in text
        assert text.index("2:s0w2") < text.index("NGUỒN 2") < text.index("3:s1w0")

    def test_no_filenames_or_seconds_reach_the_model(self):
        spine = two_take_spine(3)
        text = select.marked_spine(spine["word_timestamps"], [3])
        assert ".mp4" not in text
        assert "0.25" not in text


class TestDirectorSeesFilteredSpine:
    def test_director_words_are_reindexed_from_zero(self, job):
        from lib.talking_head_edit.stages.direct import director_words

        write_spine(job, two_take_spine(100))
        job.update(selection={"kept_word_ranges": [[100, 199]]})

        words, boundaries = director_words(job)
        assert len(words) == 100
        assert words[0]["word"] == "s1w0"
        assert words[0]["_orig_index"] == 100
        assert boundaries == [], "một take còn lại thì không còn ranh giới nguồn"

    def test_boundaries_are_recomputed_after_filtering(self, job):
        from lib.talking_head_edit.stages.direct import director_words

        spine = two_take_spine(10)
        write_spine(job, spine)
        job.update(selection={"kept_word_ranges": [[0, 4], [10, 14]]})

        words, boundaries = director_words(job)
        assert [w["src"] for w in words] == ["s0"] * 5 + ["s1"] * 5
        assert boundaries == [5]

    def test_no_selection_means_the_whole_spine(self, job):
        from lib.talking_head_edit.stages.direct import director_words

        write_spine(job, two_take_spine(10))
        words, boundaries = director_words(job)
        assert len(words) == 20
        assert boundaries == [10]
