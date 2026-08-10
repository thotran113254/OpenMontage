"""Stage cache: what a prompt edit must and must not re-run.

This is the whole economics of the iterate loop — if a prompt tweak re-ran
Whisper and the render, nobody would iterate.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit.cache import StageCache, file_sha256, hash_inputs
from lib.talking_head_edit.job_store import JobStore
from lib.talking_head_edit.runner import cache_signature, stages_from


@pytest.fixture()
def job(tmp_path):
    source = tmp_path / "footage.mp4"
    source.write_bytes(b"not really a video")
    store = JobStore(root=tmp_path / "jobs")
    job = store.create(source, {"prompt": "bản đầu", "model": "m1"}, title="thu nghiem")
    job.spine_path.write_text(json.dumps({"word_timestamps": [
        {"word": "a", "start": 0.0, "end": 0.5}]}), encoding="utf-8")
    return job


class TestHashing:
    def test_same_payload_same_hash_regardless_of_key_order(self):
        assert hash_inputs({"a": 1, "b": 2}) == hash_inputs({"b": 2, "a": 1})

    def test_different_payload_different_hash(self):
        assert hash_inputs({"a": 1}) != hash_inputs({"a": 2})

    def test_file_hash_tracks_content(self, tmp_path):
        path = tmp_path / "f.bin"
        path.write_bytes(b"one")
        first = file_sha256(path)
        path.write_bytes(b"two")
        assert file_sha256(path) != first


class TestStageCache:
    def test_not_fresh_until_marked(self, job):
        cache = StageCache(job)
        assert not cache.is_fresh("direct", "sig", [])

    def test_fresh_only_while_outputs_exist(self, job):
        cache = StageCache(job)
        output = job.dir / "out.json"
        output.write_text("{}", encoding="utf-8")
        cache.mark("resolve", "sig")
        assert cache.is_fresh("resolve", "sig", [output])
        output.unlink()
        assert not cache.is_fresh("resolve", "sig", [output]), \
            "mất file đầu ra thì không được coi là còn cache"

    def test_invalidate_clears_only_named_stages(self, job):
        cache = StageCache(job)
        cache.mark("direct", "a")
        cache.mark("resolve", "b")
        cache.invalidate("direct")
        assert not cache.is_fresh("direct", "a", [])
        assert cache.is_fresh("resolve", "b", [])


class TestCacheSignatures:
    def test_prompt_change_invalidates_direct(self, job):
        options = job.load()["options"]
        before, _ = cache_signature("direct", job, options)
        after, _ = cache_signature("direct", job, {**options, "prompt": "sửa lại"})
        assert before != after

    def test_prompt_change_does_not_invalidate_transcribe(self, job):
        options = job.load()["options"]
        before, _ = cache_signature("transcribe", job, options)
        after, _ = cache_signature("transcribe", job, {**options, "prompt": "sửa lại"})
        assert before == after, "đổi prompt không được bắt chạy lại Whisper"

    def test_whisper_model_change_invalidates_transcribe(self, job):
        options = {**job.load()["options"], "asr_provider": "whisper_local"}
        before, _ = cache_signature("transcribe", job, {**options, "whisper_model": "small"})
        after, _ = cache_signature("transcribe", job, {**options, "whisper_model": "medium"})
        assert before != after

    def test_asr_provider_change_invalidates_transcribe(self, job):
        """Two engines give different word boundaries for the same audio, so one
        cache slot for both would serve the wrong spine."""
        options = job.load()["options"]
        scribe, _ = cache_signature("transcribe", job,
                                    {**options, "asr_provider": "elevenlabs_scribe"})
        whisper, _ = cache_signature("transcribe", job,
                                     {**options, "asr_provider": "whisper_local"})
        assert scribe != whisper

    def test_keyterms_change_invalidates_transcribe(self, job):
        options = job.load()["options"]
        before, _ = cache_signature("transcribe", job, options)
        after, _ = cache_signature("transcribe", job, {**options, "keyterms": ["Zalo OA"]})
        assert before != after, "đổi từ khoá gợi ý làm ASR nghe khác → phải chạy lại"

    def test_tempo_change_invalidates_resolve(self, job):
        options = job.load()["options"]
        before, _ = cache_signature("resolve", job, options)
        after, _ = cache_signature("resolve", job, {**options, "tempo": 1.2})
        assert before != after

    def test_render_tracks_props_and_scale(self, job):
        options = job.load()["options"]
        job.update(current_version=1)
        job.props_path(1).write_text('{"events": []}', encoding="utf-8")
        before, outputs = cache_signature("render", job, options)
        assert outputs == [job.final_path]
        after, _ = cache_signature("render", job, {**options, "render_scale": 0.5})
        assert before != after


class TestStageOrdering:
    def test_stage_from_returns_the_tail(self):
        assert stages_from("resolve") == ["resolve", "render", "verify"]

    def test_unknown_stage_is_rejected(self):
        with pytest.raises(ValueError):
            stages_from("khong-ton-tai")
