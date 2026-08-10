"""ASR provider abstraction: spine v2, spacing filter, fallback classification.

Every test here mocks the provider call. Two reasons: no API key is needed to
run the suite, and the cases that matter most (a `spacing` token reaching the
spine, a fatal error being papered over by a fallback) are impossible to trigger
on demand against a live service.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.talking_head_edit import asr
from lib.talking_head_edit.asr import base as asr_base
from lib.talking_head_edit.asr import elevenlabs_scribe as scribe_provider
from lib.talking_head_edit.cache import (
    find_cached_transcript,
    legacy_transcript_cache_path,
    transcript_cache_path,
)


class FakeResult:
    def __init__(self, success: bool, data=None, error: str = ""):
        self.success = success
        self.data = data or {}
        self.error = error


def scribe_words():
    """The shape Scribe actually returns: words interleaved with spacing, plus a
    non-speech event."""
    return [
        {"text": "Xin", "start": 0.10, "end": 0.30, "type": "word", "speaker_id": "speaker_0"},
        {"text": " ", "start": 0.30, "end": 0.31, "type": "spacing", "speaker_id": "speaker_0"},
        {"text": "chào", "start": 0.31, "end": 0.62, "type": "word", "speaker_id": "speaker_0"},
        {"text": "(laughter)", "start": 0.70, "end": 1.20, "type": "audio_event"},
        {"text": "mọi", "start": 1.30, "end": 1.50, "type": "word", "speaker_id": "speaker_1"},
        {"text": "người", "start": 1.50, "end": 1.50, "type": "word", "speaker_id": "speaker_1"},
    ]


@pytest.fixture
def scribe_ok(monkeypatch):
    """Patch the tool class the provider imports, not the provider itself."""
    calls: list[dict] = []

    class FakeTool:
        def execute(self, inputs):
            calls.append(inputs)
            return FakeResult(True, {
                "text": "Xin chào mọi người",
                "words": scribe_words(),
                "language": "vi",
                "duration_seconds": 1.5,
                "model_id": inputs.get("model_id"),
            })

    import tools.analysis.elevenlabs_scribe as tool_module

    monkeypatch.setattr(tool_module, "ElevenLabsScribe", FakeTool)
    return calls


class TestNormalise:
    def test_spacing_tokens_never_reach_the_spine(self):
        """The single most damaging failure mode: a spacing token shifts every
        index the director sees, so every caption lands on the wrong word."""
        words = asr_base.normalise_words(scribe_words())
        assert [w["word"] for w in words] == ["Xin", "chào", "mọi", "người"]
        assert all(w["word"].strip() for w in words)

    def test_audio_events_leave_the_spine_but_are_kept(self):
        words = asr_base.normalise_words(scribe_words())
        assert "(laughter)" not in [w["word"] for w in words]
        events = asr_base.extract_audio_events(scribe_words())
        assert events == [{"kind": "laughter", "start": 0.7, "end": 1.2}]

    def test_zero_length_word_is_nudged(self):
        words = asr_base.normalise_words(scribe_words())
        last = words[-1]
        assert last["end"] > last["start"]

    def test_probability_is_dropped(self):
        """Only one engine has it; a field that exists half the time invites
        code that assumes it always does."""
        words = asr_base.normalise_words(
            [{"word": "a", "start": 0.0, "end": 0.2, "probability": 0.9}])
        assert "probability" not in words[0]

    def test_whisper_words_pass_through_without_a_type_field(self):
        words = asr_base.normalise_words([{"word": "một", "start": 1.0, "end": 1.2}])
        assert words == [{"word": "một", "start": 1.0, "end": 1.2}]

    def test_speakers_keep_first_heard_order(self):
        words = asr_base.normalise_words(scribe_words())
        assert asr_base.speakers_of(words) == ["speaker_0", "speaker_1"]


class TestScribeProvider:
    def test_builds_spine_v2(self, scribe_ok, tmp_path):
        spine = scribe_provider.transcribe(tmp_path / "clip.mp4", {"language": "vi"})
        assert spine["schema"] == 2
        assert spine["provider"] == "elevenlabs_scribe"
        assert spine["model"] == "scribe_v2"
        assert [w["word"] for w in spine["word_timestamps"]] == \
            ["Xin", "chào", "mọi", "người"]
        assert spine["speakers"] == ["speaker_0", "speaker_1"]
        assert spine["audio_events"][0]["kind"] == "laughter"

    def test_keyterms_and_diarize_are_forwarded(self, scribe_ok, tmp_path):
        scribe_provider.transcribe(tmp_path / "clip.mp4",
                                   {"keyterms": ["Zalo OA"], "diarize": True})
        assert scribe_ok[0]["keyterms"] == ["Zalo OA"]
        assert scribe_ok[0]["diarize"] is True

    def test_empty_transcript_is_fatal_not_transient(self, monkeypatch, tmp_path):
        """A request that succeeded and found no speech cannot be improved by a
        different engine — falling back would just cost minutes."""
        class Empty:
            def execute(self, inputs):
                return FakeResult(True, {"words": [], "language": "vi"})

        import tools.analysis.elevenlabs_scribe as tool_module
        monkeypatch.setattr(tool_module, "ElevenLabsScribe", Empty)
        with pytest.raises(asr.AsrFatal):
            scribe_provider.transcribe(tmp_path / "clip.mp4", {})


class TestErrorClassification:
    @pytest.mark.parametrize("error", [
        "401 Unauthorized", "429 rate limit exceeded", "503 Service Unavailable",
        "Connection timed out", "Thiếu ELEVENLABS_API_KEY trong môi trường (.env)",
        "Chưa cài SDK. Chạy: pip install elevenlabs",
    ])
    def test_transient_errors_allow_fallback(self, error):
        assert isinstance(scribe_provider.classify(error), asr.AsrTransient)

    @pytest.mark.parametrize("error", [
        "422 Unprocessable Entity: model_id invalid",
        "400 Bad Request: unsupported parameter",
    ])
    def test_bad_request_errors_must_not_fall_back(self, error):
        assert isinstance(scribe_provider.classify(error), asr.AsrFatal)


class TestFallback:
    def _patch(self, monkeypatch, scribe_raises, whisper_spine):
        def fake_scribe(path, options=None, log_dir=None):
            raise scribe_raises

        def fake_whisper(path, options=None, log_dir=None):
            return dict(whisper_spine)

        monkeypatch.setitem(asr.PROVIDERS, "elevenlabs_scribe", fake_scribe)
        monkeypatch.setitem(asr.PROVIDERS, "whisper_local", fake_whisper)

    def test_transient_failure_falls_back_and_warns(self, monkeypatch, tmp_path):
        whisper = asr_base.build_spine(
            [{"word": "a", "start": 0.0, "end": 0.2}],
            provider="whisper_local", model="medium", language="vi",
            duration_seconds=0.2)
        self._patch(monkeypatch, asr.AsrTransient("429 rate limit"), whisper)
        warnings: list[str] = []

        spine = asr.transcribe(tmp_path / "c.mp4", {}, on_warning=warnings.append)

        assert spine["provider"] == "whisper_local"
        assert spine["asr_fallback"] is True
        assert spine["asr_fallback_from"] == "elevenlabs_scribe"
        assert warnings and "429" in warnings[0]

    def test_fatal_failure_does_not_fall_back(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, asr.AsrFatal("422 invalid model_id"), {})
        with pytest.raises(asr.AsrFatal):
            asr.transcribe(tmp_path / "c.mp4", {})

    def test_unknown_provider_is_rejected_before_any_call(self):
        with pytest.raises(asr.AsrFatal):
            asr.resolve_provider({"asr_provider": "deepgram"})

    def test_default_provider_is_scribe(self):
        assert asr.resolve_provider({}) == "elevenlabs_scribe"
        assert asr.resolve_provider(None) == "elevenlabs_scribe"


class TestTranscriptCache:
    def test_provider_is_part_of_the_key(self):
        a = transcript_cache_path("abc123", "scribe_v2", "vi", "elevenlabs_scribe")
        b = transcript_cache_path("abc123", "medium", "vi", "whisper_local")
        assert a != b

    def test_legacy_whisper_cache_still_hits(self, monkeypatch, tmp_path):
        """Transcripts cached before the provider split must not be thrown away —
        re-running Whisper medium costs minutes for nothing."""
        import lib.talking_head_edit.cache as cache_module
        monkeypatch.setattr(cache_module, "TRANSCRIPT_CACHE_DIR", tmp_path)

        legacy = legacy_transcript_cache_path("abc123", "medium", "vi")
        legacy.write_text(json.dumps({"word_timestamps": []}), encoding="utf-8")

        assert find_cached_transcript("abc123", "whisper_local", "medium", "vi") == legacy
        # Scribe must NOT read Whisper's cached transcript.
        assert find_cached_transcript("abc123", "elevenlabs_scribe", "scribe_v2", "vi") is None


class TestScribeToolContract:
    def test_registered_with_analysis_capability(self):
        from tools.analysis.elevenlabs_scribe import ElevenLabsScribe

        tool = ElevenLabsScribe()
        assert tool.capability == "analysis"
        assert tool.provider == "elevenlabs"
        assert "ELEVENLABS_API_KEY" in tool.install_instructions

    def test_missing_file_is_reported_not_raised(self):
        from tools.analysis.elevenlabs_scribe import ElevenLabsScribe

        result = ElevenLabsScribe().execute({"input_path": "/nonexistent/clip.mp4"})
        assert result.success is False
        assert "Không tìm thấy" in (result.error or "")

    def test_too_many_keyterms_is_refused(self, tmp_path):
        from tools.analysis.elevenlabs_scribe import ElevenLabsScribe

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"0")
        result = ElevenLabsScribe().execute({
            "input_path": str(clip), "keyterms": [f"t{i}" for i in range(101)],
        })
        assert result.success is False
        assert "100" in (result.error or "")

    def test_discovered_by_the_registry(self):
        from tools.tool_registry import registry

        registry.discover()
        names = {tool.name for tool in registry.get_by_capability("analysis")}
        assert "elevenlabs_scribe" in names


class TestKeytermMining:
    def test_explicit_keyterms_win(self):
        from lib.talking_head_edit.stages.transcribe import keyterms_for

        assert keyterms_for({"keyterms": ["A"], "topic": "Zalo OA"}) == ["A"]

    def test_mined_from_topic_and_card_plan(self):
        from lib.talking_head_edit.stages.transcribe import keyterms_for

        terms = keyterms_for({"topic": "3 sai lầm khi dùng chatbot AI",
                              "card_plan": "4 card về CRM và Zalo OA"})
        assert "chatbot" not in terms, "từ thường không đáng chiếm slot"
        for expected in ("CRM", "Zalo", "OA", "AI"):
            assert expected in terms

    def test_capped_at_provider_limit(self):
        from lib.talking_head_edit.stages.transcribe import keyterms_for

        assert len(keyterms_for({"keyterms": [f"T{i}" for i in range(200)]})) == 100


class TestTranscribeStage:
    def test_cached_spine_is_reused_without_calling_the_engine(self, monkeypatch, tmp_path):
        import lib.talking_head_edit.cache as cache_module
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.stages import transcribe as stage

        monkeypatch.setattr(cache_module, "TRANSCRIPT_CACHE_DIR", tmp_path / "cache")
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(source, {})
        job.update(input_sha256="deadbeefdeadbeef")

        spine = {"schema": 2, "provider": "elevenlabs_scribe", "model": "scribe_v2",
                 "word_timestamps": [{"word": w, "start": i * 0.2, "end": i * 0.2 + 0.1}
                                     for i, w in enumerate("abcdef")]}
        transcript_cache_path("deadbeefdeadbeef", "scribe_v2", "vi",
                              "elevenlabs_scribe").write_text(
            json.dumps(spine), encoding="utf-8")

        def explode(*args, **kwargs):
            raise AssertionError("cache hit must not call the ASR engine")

        monkeypatch.setattr(stage, "asr_transcribe", explode)
        result = stage.run(job, {"language": "vi", "asr_provider": "elevenlabs_scribe"})
        assert result["cached"] is True
        assert result["words"] == 6
        assert result["provider"] == "elevenlabs_scribe"

    def test_too_few_words_fails_loudly(self, monkeypatch, tmp_path):
        import lib.talking_head_edit.cache as cache_module
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.stages import transcribe as stage

        monkeypatch.setattr(cache_module, "TRANSCRIPT_CACHE_DIR", tmp_path / "cache")
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(source, {})
        job.update(input_sha256="cafe")

        monkeypatch.setattr(stage, "asr_transcribe", lambda *a, **k: asr_base.build_spine(
            [{"word": "a", "start": 0.0, "end": 0.1}],
            provider="elevenlabs_scribe", model="scribe_v2",
            language="vi", duration_seconds=0.1))
        with pytest.raises(stage.TranscribeError, match="1 từ"):
            stage.run(job, {"language": "vi"})

    def test_fallback_caches_under_the_engine_that_ran(self, monkeypatch, tmp_path):
        """Otherwise the next run reads Whisper's spine out of the Scribe slot."""
        import lib.talking_head_edit.cache as cache_module
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.stages import transcribe as stage

        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(cache_module, "TRANSCRIPT_CACHE_DIR", cache_dir)
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(source, {})
        job.update(input_sha256="feedface")

        fell_back = asr_base.build_spine(
            [{"word": w, "start": i * 0.2, "end": i * 0.2 + 0.1} for i, w in
             enumerate(["a", "b", "c", "d", "e", "f"])],
            provider="whisper_local", model="medium", language="vi",
            duration_seconds=1.2)
        fell_back["asr_fallback"] = True
        monkeypatch.setattr(stage, "asr_transcribe", lambda *a, **k: fell_back)

        stage.run(job, {"language": "vi", "asr_provider": "elevenlabs_scribe"})

        assert transcript_cache_path("feedface", "medium", "vi", "whisper_local").exists()
        assert not transcript_cache_path("feedface", "scribe_v2", "vi",
                                         "elevenlabs_scribe").exists()
        assert job.load().get("asr_fallback") is True
