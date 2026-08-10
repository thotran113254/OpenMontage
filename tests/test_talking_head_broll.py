"""B-roll overlays and speaker-aware framing.

Clip preparation runs against real ffmpeg on generated media, because the three
length cases (trim / speed / hold last frame) are exactly the kind of thing that
looks right in code and comes out the wrong duration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lib.talking_head_edit import resolve_broll as broll_mod
from lib.talking_head_edit.resolve_broll import (
    BrollError,
    prepare_clip,
    resolve_broll,
    speaker_aware_enabled,
    speaker_segments,
)
from lib.talking_head_edit.resolve_events import TimeMapper, apply_guards, resolve_events
from lib.talking_head_edit.resolve_spans import Span

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="cần ffmpeg")


def words(count=20, step=0.5, speaker=None):
    out = []
    for index in range(count):
        word = {"word": f"w{index}", "start": round(index * step, 3),
                "end": round(index * step + step * 0.8, 3), "src": "s0",
                "_orig_index": index}
        if speaker:
            word["speaker"] = speaker(index)
        out.append(word)
    return out


def make_clip(path: Path, seconds: float, colour: str = "red"):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c={colour}:size=320x568:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path), "-loglevel", "error"],
        check=True, capture_output=True,
    )
    return path


def duration_of(path: Path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip())


class TestResolveBrollEvent:
    def mapper(self):
        return TimeMapper(words(20), [Span("s0", Path("a.mp4"), 0.0, 10.0)],
                         new_duration=10.0, tempo=1.0)

    def test_broll_event_resolves_to_seconds_and_keeps_its_source_id(self):
        events, skipped = resolve_events(
            {"events": [{"type": "broll", "w0": 4, "w1": 12, "src": "s2"}]},
            self.mapper())
        assert skipped == []
        assert events[0]["type"] == "broll"
        assert events[0]["src"] == "s2", "phải giữ ID nguồn, không phải đường dẫn"
        assert events[0]["at"] < events[0]["end"]

    def test_a_very_short_overlay_is_dropped(self):
        """A 0.2s flash of different footage reads as a glitch, not an edit."""
        events, skipped = resolve_events(
            {"events": [{"type": "broll", "w0": 4, "w1": 4, "src": "s2"}]},
            self.mapper())
        assert events == []
        assert any("qua-ngan" in s for s in skipped)

    def test_reversed_indices_are_repaired(self):
        events, _ = resolve_events(
            {"events": [{"type": "broll", "w0": 12, "w1": 4, "src": "s2"}]},
            self.mapper())
        assert events and events[0]["at"] < events[0]["end"]

    def test_broll_fully_under_a_card_is_dropped(self):
        """A card is a full-screen panel, so an overlay beneath it is invisible —
        and paying an encode for an invisible clip is waste on top of wrong."""
        events = [
            {"type": "card", "at": 2.0, "end": 8.0, "kicker": "", "title": "t",
             "badge": "1", "bullets": []},
            {"type": "broll", "at": 3.0, "end": 5.0, "src": "s2"},
        ]
        kept, _ = apply_guards(events)
        assert not any(e["type"] == "broll" for e in kept)

    def test_broll_overlapping_a_card_is_trimmed_not_dropped(self):
        events = [
            {"type": "card", "at": 5.0, "end": 9.0, "kicker": "", "title": "t",
             "badge": "1", "bullets": []},
            {"type": "broll", "at": 2.0, "end": 7.0, "src": "s2"},
        ]
        kept, _ = apply_guards(events)
        overlay = next(e for e in kept if e["type"] == "broll")
        assert overlay["end"] == 5.0

    def test_broll_clear_of_cards_is_untouched(self):
        events = [
            {"type": "card", "at": 10.0, "end": 14.0, "kicker": "", "title": "t",
             "badge": "1", "bullets": []},
            {"type": "broll", "at": 2.0, "end": 6.0, "src": "s2"},
        ]
        kept, _ = apply_guards(events)
        overlay = next(e for e in kept if e["type"] == "broll")
        assert (overlay["at"], overlay["end"]) == (2.0, 6.0)


@needs_ffmpeg
class TestPrepareClip:
    def test_a_longer_clip_is_trimmed_to_the_span(self, tmp_path):
        source = make_clip(tmp_path / "long.mp4", 8.0)
        result = prepare_clip(source, 3.0, tmp_path / "out.mp4", 320, 568)
        assert result["mode"] == "trim"
        assert duration_of(result["path"]) == pytest.approx(3.0, abs=0.15)

    def test_a_slightly_short_clip_is_slowed_not_looped(self, tmp_path):
        """A loop is obvious the moment the clip has motion — the subject jumps
        back to the start."""
        source = make_clip(tmp_path / "short.mp4", 2.7)
        result = prepare_clip(source, 3.0, tmp_path / "out.mp4", 320, 568)
        assert result["mode"] == "slowed"
        assert duration_of(result["path"]) == pytest.approx(3.0, abs=0.2)
        assert result["warnings"] == []

    def test_a_much_shorter_clip_holds_its_last_frame_and_warns(self, tmp_path):
        source = make_clip(tmp_path / "tiny.mp4", 1.0)
        result = prepare_clip(source, 5.0, tmp_path / "out.mp4", 320, 568)
        assert result["mode"] == "hold_last_frame"
        assert result["warnings"], "phải cảnh báo, không im lặng giữ frame cuối"
        assert duration_of(result["path"]) == pytest.approx(5.0, abs=0.3)

    def test_a_matching_clip_is_left_alone(self, tmp_path):
        source = make_clip(tmp_path / "exact.mp4", 3.0)
        result = prepare_clip(source, 3.0, tmp_path / "out.mp4", 320, 568)
        assert result["mode"] == "as_is"

    def test_audio_is_dropped(self, tmp_path):
        """B-roll is a picture layer; mixing its sound would put the word clock
        back in play."""
        source = tmp_path / "withaudio.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:size=320x568:rate=30:duration=4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(source), "-loglevel", "error"],
            check=True, capture_output=True)
        result = prepare_clip(source, 3.0, tmp_path / "out.mp4", 320, 568)
        streams = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", str(result["path"])],
            capture_output=True, text=True).stdout
        assert "audio" not in streams

    def test_encoded_at_the_a_roll_pixel_size(self, tmp_path):
        source = make_clip(tmp_path / "s.mp4", 3.0)
        result = prepare_clip(source, 3.0, tmp_path / "out.mp4", 240, 426)
        size = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             str(result["path"])], capture_output=True, text=True).stdout.strip()
        assert size.startswith("240,426")

    def test_a_zero_length_span_is_refused(self, tmp_path):
        source = make_clip(tmp_path / "s.mp4", 3.0)
        with pytest.raises(BrollError):
            prepare_clip(source, 0.0, tmp_path / "out.mp4", 320, 568)


class TestResolveBrollBatch:
    def test_an_unknown_source_is_skipped_with_a_warning(self, tmp_path):
        clips, warnings = resolve_broll(
            [{"type": "broll", "at": 1.0, "end": 5.0, "src": "s9"}],
            [{"src": "s2", "path": str(tmp_path / "b.mp4"), "duration": 8.0}],
            tmp_path, 320, 568)
        assert clips == []
        assert any("overlay_pool" in w for w in warnings)

    def test_an_empty_pool_skips_everything(self, tmp_path):
        clips, warnings = resolve_broll(
            [{"type": "broll", "at": 1.0, "end": 5.0, "src": "s2"}], [], tmp_path,
            320, 568)
        assert clips == []
        assert warnings

    @needs_ffmpeg
    def test_props_carry_bare_filenames(self, tmp_path):
        """Preview and render must read identical bytes, which is why props hold a
        filename rather than a path."""
        source = make_clip(tmp_path / "broll.mp4", 6.0)
        clips, warnings = resolve_broll(
            [{"type": "broll", "at": 2.0, "end": 5.0, "src": "s2"}],
            [{"src": "s2", "path": str(source), "duration": 6.0}],
            tmp_path / "public", 320, 568)
        assert warnings == []
        assert len(clips) == 1
        assert "/" not in clips[0]["src"] and "\\" not in clips[0]["src"]
        assert clips[0]["start"] == 2.0
        assert clips[0]["duration"] == pytest.approx(3.0, abs=0.2)


class TestSpeakerSegments:
    def two_speakers(self):
        # 0-9 speaker_0, 10-19 speaker_1
        return words(20, speaker=lambda i: "s0:speaker_0" if i < 10 else "s0:speaker_1")

    def test_turns_are_detected(self):
        segments = speaker_segments(self.two_speakers(), lambda i: i * 0.5)
        assert len(segments) == 2
        assert segments[0]["speaker"].endswith("speaker_0")
        assert segments[1]["speaker"].endswith("speaker_1")

    def test_each_speaker_keeps_the_same_framing(self):
        alternating = words(40, speaker=lambda i: f"s0:speaker_{(i // 10) % 2}")
        segments = speaker_segments(alternating, lambda i: i * 0.5)
        by_speaker: dict[str, set[str]] = {}
        for segment in segments:
            by_speaker.setdefault(segment["speaker"], set()).add(segment["framing"])
        assert all(len(framings) == 1 for framings in by_speaker.values())

    def test_short_turns_are_folded_into_the_previous_one(self):
        """Fast back-and-forth would otherwise re-frame continuously, which looks
        worse than not re-framing at all."""
        # every 2 words alternates → each turn is 1s, below the 2.5s floor
        rapid = words(20, speaker=lambda i: f"s0:speaker_{(i // 2) % 2}")
        segments = speaker_segments(rapid, lambda i: i * 0.5, min_turn=2.5)
        assert len(segments) < 5, f"quá nhiều lượt: {len(segments)}"

    def test_no_speaker_labels_means_no_segments(self):
        assert speaker_segments(words(20), lambda i: i * 0.5) == []

    def test_empty_input(self):
        assert speaker_segments([], None) == []


class TestSpeakerAwareAuto:
    def test_off_when_explicitly_disabled(self):
        on, reason = speaker_aware_enabled(
            False, words(20, speaker=lambda i: f"s{i % 2}"))
        assert on is False
        assert "tắt" in reason

    def test_off_without_diarization(self):
        on, reason = speaker_aware_enabled("auto", words(20))
        assert on is False
        assert "diarization" in reason

    def test_off_for_a_monologue(self):
        on, _ = speaker_aware_enabled(
            "auto", words(20, speaker=lambda i: "s0:speaker_0"))
        assert on is False

    def test_off_when_the_second_speaker_is_a_sliver(self):
        """A label covering 5% of a monologue is diarization noise, and reframing
        for it is worse than ignoring it."""
        on, reason = speaker_aware_enabled(
            "auto", words(20, speaker=lambda i: "s0:speaker_1" if i == 0 else "s0:speaker_0"))
        assert on is False
        assert "nhiễu diarization" in reason

    def test_on_for_a_real_two_person_interview(self):
        on, reason = speaker_aware_enabled(
            "auto", words(20, speaker=lambda i: f"s0:speaker_{i // 10}"))
        assert on is True
        assert "2 người nói" in reason

    def test_manual_true_needs_at_least_two_labels(self):
        on, _ = speaker_aware_enabled(
            True, words(20, speaker=lambda i: "s0:speaker_0"))
        assert on is False


class TestAuditRejectsUnknownBroll:
    def test_a_source_not_in_the_pool_is_removed(self):
        from lib.talking_head_edit.stages.audit import audit_resources

        spec = {"events": [{"type": "broll", "w0": 2, "w1": 8, "src": "s9"}]}
        audited, removed = audit_resources(
            spec, 20, overlay_pool=[{"src": "s2", "duration": 8.0}])
        assert audited["events"] == []
        assert any("overlay_pool" in note for note in removed)

    def test_a_source_in_the_pool_survives(self):
        from lib.talking_head_edit.stages.audit import audit_resources

        spec = {"events": [{"type": "broll", "w0": 2, "w1": 8, "src": "s2"}]}
        audited, removed = audit_resources(
            spec, 20, overlay_pool=[{"src": "s2", "duration": 8.0}])
        assert len(audited["events"]) == 1
        assert removed == []

    def test_no_pool_means_no_broll_can_pass(self):
        from lib.talking_head_edit.stages.audit import audit_resources

        spec = {"events": [{"type": "broll", "w0": 2, "w1": 8, "src": "s2"}]}
        audited, removed = audit_resources(spec, 20, overlay_pool=None)
        assert audited["events"] == []
        assert removed


class TestVerifyBrollPresence:
    def test_identical_frames_mean_the_overlay_never_drew(self, monkeypatch):
        from lib.talking_head_edit.stages import verify as verify_stage

        monkeypatch.setattr(verify_stage, "frame_signature",
                            lambda video, at, work: "aabbcc")
        report = verify_stage.measure_broll(
            Path("final.mp4"), Path("src.mp4"),
            [{"src": "broll_00_s2.mp4", "start": 2.0, "duration": 3.0}],
            Path("work"))
        assert report["broll_present"] is False
        assert report["issues"]

    def test_different_frames_mean_it_drew(self, monkeypatch):
        from lib.talking_head_edit.stages import verify as verify_stage

        signatures = iter(["aaaaaa", "ffffff"])
        monkeypatch.setattr(verify_stage, "frame_signature",
                            lambda video, at, work: next(signatures))
        report = verify_stage.measure_broll(
            Path("final.mp4"), Path("src.mp4"),
            [{"src": "broll_00_s2.mp4", "start": 2.0, "duration": 3.0}],
            Path("work"))
        assert report["broll_present"] is True
        assert report["issues"] == []

    def test_no_clips_is_not_a_verdict(self, monkeypatch):
        from lib.talking_head_edit.stages import verify as verify_stage

        report = verify_stage.measure_broll(Path("f.mp4"), Path("s.mp4"), [],
                                           Path("work"))
        assert report["broll_present"] is None
        assert report["issues"] == []


def code_only(path: str) -> str:
    """Source with `//` comment lines removed.

    The comments deliberately NAME the API that must not be used, so a naive
    substring search would flag the very note warning against it.
    """
    return "\n".join(
        line for line in Path(path).read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//"))


class TestRendererContract:
    def test_broll_layer_uses_offthreadvideo_not_video(self):
        """`<Video>` renders through a canvas that ignores objectFit:cover and
        letterboxes the clip — already hit and reverted once for the A-roll."""
        source = code_only("remotion-composer/src/mona/BrollLayer.tsx")
        assert "<OffthreadVideo" in source
        assert "objectFit" in source
        assert "<Video" not in source

    def test_broll_sits_under_the_caption_layer(self):
        """Caption hidden behind an overlay is a subtitle that does not exist, so
        the ordering is not a preference."""
        timeline = code_only("remotion-composer/src/mona/MonaTimeline.tsx")
        assert timeline.index("<BrollLayer") < timeline.index("<CaptionLineView")

    def test_broll_sits_inside_the_a_roll_box(self):
        """So the frame preset's inset, radius and border apply to it too."""
        timeline = code_only("remotion-composer/src/mona/MonaTimeline.tsx")
        aroll_video = timeline.index("<OffthreadVideo src={resolveAsset(displaySrc)}")
        broll = timeline.index("<BrollLayer")
        brand_pill = timeline.index("{brandPill && (")
        assert aroll_video < broll < brand_pill
