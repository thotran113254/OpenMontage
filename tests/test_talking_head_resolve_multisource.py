"""Per-span cutting across several sources.

Two gates in here are the reason this phase is the riskiest in the plan, and
both are asserted rather than hoped for:

1. **Video is encoded exactly once.** The obvious implementation — encode spans,
   concat, re-encode to run loudnorm — encodes twice and throws away every
   sharpness gain this pipeline measured (2.85 → ~1.5 on a face crop). The master
   audio step must copy the video stream, and `apply_master_audio` proves it did
   by comparing the video stream's bitrate before and after.
2. **The loudness chain does not run per span.** `loudnorm` measures over a
   window, so per-span it gives each span its own gain and the level steps at
   every edit. The span chain is asserted to contain nothing but fade and tempo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.talking_head_edit import resolve_cut, resolve_media
from lib.talking_head_edit.resolve_events import TimeMapper
from lib.talking_head_edit.resolve_media import build_audio_chain, build_audio_master_chain, build_audio_span_chain
from lib.talking_head_edit.resolve_cut import can_copy_span
from lib.talking_head_edit.resolve_spans import Span, merge_adjacent_spans, plan_spans, source_runs


def words(count, src="s0", step=1.0, base=0.0, orig_from=0):
    return [{"word": f"w{i}", "start": round(base + i * step, 3),
             "end": round(base + i * step + step * 0.8, 3), "src": src,
             "_orig_index": orig_from + i}
            for i in range(count)]


class TestAudioChainSplit:
    def test_master_chain_has_the_loudness_processing(self):
        chain = build_audio_master_chain("voice")
        for stage in ("acompressor", "alimiter", "loudnorm", "aresample"):
            assert stage in chain

    def test_master_chain_has_no_tempo(self):
        """Tempo must be per span so video and audio leave each span the same
        length; in the master chain it would stretch the joined file instead."""
        assert "atempo" not in build_audio_master_chain("voice")

    def test_span_chain_has_only_fade_and_tempo(self):
        """The point of the split: nothing context-dependent runs per span."""
        chain = build_audio_span_chain(1.06, duration=5.0)
        assert "afade=t=in" in chain
        assert "afade=t=out" in chain
        assert "atempo=1.06" in chain
        for forbidden in ("loudnorm", "acompressor", "alimiter", "afftdn", "highpass"):
            assert forbidden not in chain, f"{forbidden} không được chạy per-span"

    def test_span_chain_skips_the_out_fade_on_a_very_short_span(self):
        chain = build_audio_span_chain(1.0, duration=0.03)
        assert "afade=t=out" not in chain

    def test_old_single_pass_chain_is_unchanged(self):
        """The equivalence gate. The single-pass chain must still be exactly
        master + tempo, so a LUFS comparison between the old and new paths is
        comparing identical filters in identical order."""
        assert build_audio_chain("shotgun", 1.06) == \
            build_audio_master_chain("shotgun") + ",atempo=1.06"

    def test_cleanup_still_leads_the_chain(self):
        """Denoising after the compressor would amplify noise first, then try to
        remove what the compressor pulled level with the voice."""
        chain = build_audio_master_chain("voice")
        assert chain.index("highpass") < chain.index("acompressor")
        assert chain.index("afftdn") < chain.index("loudnorm")

    def test_unknown_preset_falls_back_to_voice(self):
        assert build_audio_master_chain("khong-co-that") == \
            build_audio_master_chain("voice")


class TestSourceRuns:
    def test_single_source_is_one_run(self):
        assert source_runs(words(10)) == [("s0", 0, 9)]

    def test_split_where_the_source_changes(self):
        joined = words(5) + words(5, src="s1", orig_from=5)
        assert source_runs(joined) == [("s0", 0, 4), ("s1", 5, 9)]

    def test_split_where_select_dropped_words_inside_one_source(self):
        """Same file, but a gap in the original indices means the material is not
        contiguous — cutting across that gap would join two unrelated moments."""
        kept = words(3) + words(3, base=10.0, orig_from=40)
        assert source_runs(kept) == [("s0", 0, 2), ("s0", 3, 5)]

    def test_empty_input(self):
        assert source_runs([]) == []


class TestPlanSpans:
    def paths(self, *ids):
        return {src: Path(f"C:/f/{src}.mp4") for src in ids}

    def test_no_cuts_gives_one_span_per_run(self):
        spans, removes = plan_spans(words(10), [], self.paths("s0"))
        assert len(spans) == 1
        assert spans[0].src_id == "s0"
        assert removes == []

    def test_each_source_becomes_its_own_span(self):
        joined = words(5) + words(5, src="s1", orig_from=5)
        spans, _ = plan_spans(joined, [], self.paths("s0", "s1"))
        assert [s.src_id for s in spans] == ["s0", "s1"]
        assert spans[0].path == Path("C:/f/s0.mp4")
        assert spans[1].path == Path("C:/f/s1.mp4")

    def test_a_cut_splits_its_run_in_two(self):
        spans, removes = plan_spans(words(10), [[4, 5]], self.paths("s0"))
        assert len(spans) == 2
        assert len(removes) == 1
        assert removes[0]["src"] == "s0"

    def test_cut_indices_are_local_to_each_run(self):
        """A cut at global index 7 in a two-run spine belongs to the second run
        as its index 2 — getting this wrong cuts the wrong words in the wrong
        file, which is the worst failure this pipeline has."""
        joined = words(5) + words(5, src="s1", orig_from=5)
        spans, removes = plan_spans(joined, [[7, 7]], self.paths("s0", "s1"))
        assert removes[0]["src"] == "s1"
        assert [s.src_id for s in spans] == ["s0", "s1", "s1"]

    def test_a_cut_straddling_a_boundary_is_clipped_to_each_run(self):
        joined = words(5) + words(5, src="s1", orig_from=5)
        _, removes = plan_spans(joined, [[3, 7]], self.paths("s0", "s1"))
        assert {r["src"] for r in removes} == {"s0", "s1"}

    def test_missing_source_path_is_fatal(self):
        """Better to stop than to cut some other file."""
        with pytest.raises(resolve_media.ResolveError, match="Không tra được file nguồn"):
            plan_spans(words(4, src="s9"), [], self.paths("s0"))

    def test_cutting_a_whole_run_keeps_it_rather_than_emptying_the_video(self):
        """`kept_spans` never returns empty by design: a cut list that would
        remove everything is far more likely a bad proposal than a request for a
        zero-length video."""
        few = [{"word": "a", "start": 0.0, "end": 5.0, "src": "s0", "_orig_index": 0}]
        spans, _ = plan_spans(few, [[0, 0]], self.paths("s0"))
        assert len(spans) == 1
        assert spans[0].start == 0.0
        assert spans[0].end >= 5.0

    def test_spans_use_their_own_source_time_base(self):
        joined = words(5) + words(5, src="s1", base=100.0, orig_from=5)
        spans, _ = plan_spans(joined, [], self.paths("s0", "s1"))
        assert spans[1].start == pytest.approx(100.0, abs=0.1), \
            "span của nguồn 2 phải dùng giây của nguồn 2, không phải giây timeline"


class TestRunWindows:
    """Where each run's window starts and ends.

    This rule came out of a real measurement, not from taste: word-tight windows
    turned a 90.8 s edit into an 89.0 s one by dropping the head and tail room
    tone the single-filtergraph path kept.
    """

    def paths(self, *ids):
        return {src: Path(f"C:/f/{src}.mp4") for src in ids}

    def test_first_run_starts_at_second_zero(self):
        spans, _ = plan_spans(words(5, base=3.0), [], self.paths("s0"), {"s0": 20.0})
        assert spans[0].start == 0.0, "đầu video phải giữ nguyên như đường cũ"

    def test_last_run_keeps_the_tail_of_its_source(self):
        # words end at 4.8s but the file runs to 20s
        spans, _ = plan_spans(words(5), [], self.paths("s0"), {"s0": 20.0})
        assert spans[-1].end == pytest.approx(20.0, abs=0.01), \
            "đuôi file phải giữ — cắt ở từ cuối làm mất tiếng phòng"

    def test_single_source_window_matches_the_old_full_file_window(self):
        spans, _ = plan_spans(words(10), [], self.paths("s0"), {"s0": 30.0})
        assert (spans[0].start, spans[0].end) == (0.0, 30.0)

    def test_internal_run_is_word_tight_with_a_small_pad(self):
        """A seam between two takes must not carry one take's trailing silence
        into the next take's opening word."""
        joined = words(5) + words(5, src="s1", base=100.0, orig_from=5)
        spans, _ = plan_spans(joined, [], self.paths("s0", "s1"),
                              {"s0": 60.0, "s1": 60.0})
        # s0 is the first run: keeps its head, but its tail is word-tight
        assert spans[0].start == 0.0
        assert spans[0].end < 10.0, "đuôi của take giữa không được lấy hết file"
        # s1 is the last run: word-tight head, full tail
        assert spans[1].start == pytest.approx(100.0, abs=0.1)
        assert spans[1].end == pytest.approx(104.88, abs=0.2)

    def test_unknown_source_length_falls_back_to_the_last_word(self):
        spans, _ = plan_spans(words(5), [], self.paths("s0"))
        assert spans[-1].end == pytest.approx(4.88, abs=0.01)


class TestSpanMerging:
    def _spans(self, count):
        return [Span("s0", Path("a.mp4"), i * 1.0, i * 1.0 + 1.0) for i in range(count)]

    def test_under_the_limit_nothing_is_merged(self):
        spans = self._spans(10)
        assert merge_adjacent_spans(spans, limit=80) == spans

    def test_over_the_limit_contiguous_spans_merge(self):
        merged = merge_adjacent_spans(self._spans(100), limit=80)
        assert len(merged) == 1
        assert merged[0].start == 0.0 and merged[0].end == 100.0

    def test_a_real_gap_is_never_merged_away(self):
        """Merging across a gap would undo a cut the director asked for."""
        spans = self._spans(100)
        spans[50] = Span("s0", Path("a.mp4"), 60.0, 61.0)     # gap before this one
        merged = merge_adjacent_spans(spans, limit=80)
        assert len(merged) > 1

    def test_different_sources_never_merge(self):
        spans = [Span("s0", Path("a.mp4"), 0.0, 1.0),
                 Span("s1", Path("b.mp4"), 1.0, 2.0)] * 60
        merged = merge_adjacent_spans(spans, limit=80)
        assert all(m.src_id in ("s0", "s1") for m in merged)
        assert len(merged) > 1


class TestFastCopyPath:
    def span(self):
        return Span("s0", Path("a.mp4"), 0.0, 5.0)

    def test_copy_allowed_when_nothing_transforms_the_picture(self):
        assert can_copy_span(self.span(), "", 1.0, (1080, 1920),
                             {"width": 1080, "height": 1920, "video_codec": "h264"})

    def test_grade_blocks_the_copy(self):
        assert not can_copy_span(self.span(), "eq=brightness=0.02", 1.0, (1080, 1920),
                                 {"width": 1080, "height": 1920, "video_codec": "h264"})

    def test_tempo_blocks_the_copy(self):
        assert not can_copy_span(self.span(), "", 1.06, (1080, 1920),
                                 {"width": 1080, "height": 1920, "video_codec": "h264"})

    def test_wrong_size_blocks_the_copy(self):
        assert not can_copy_span(self.span(), "", 1.0, (1080, 1920),
                                 {"width": 720, "height": 1280, "video_codec": "h264"})

    def test_wrong_codec_blocks_the_copy(self):
        assert not can_copy_span(self.span(), "", 1.0, (1080, 1920),
                                 {"width": 1080, "height": 1920, "video_codec": "hevc"})

    def test_no_probe_blocks_the_copy(self):
        assert not can_copy_span(self.span(), "", 1.0, (1080, 1920), None)


class TestMultiSourceTimeMapper:
    def spans(self):
        # 10s of s0 then 10s of s1, s1's own clock starting at 100s
        return [Span("s0", Path("a.mp4"), 0.0, 10.0),
                Span("s1", Path("b.mp4"), 100.0, 110.0)]

    def mapper(self, tempo=1.0):
        joined = words(10, src="s0") + words(10, src="s1", base=100.0, orig_from=10)
        return TimeMapper(joined, self.spans(), new_duration=20.0 / tempo, tempo=tempo)

    def test_first_source_maps_to_itself(self):
        assert self.mapper().at(3) == pytest.approx(3.0, abs=0.01)

    def test_second_source_maps_past_the_first(self):
        """Word 13 sits at second 103 of file two, which is second 13 of the
        joined timeline — this is the mapping single-source code cannot express."""
        assert self.mapper().at(13) == pytest.approx(13.0, abs=0.01)

    def test_tempo_compresses_the_whole_timeline(self):
        assert self.mapper(tempo=2.0).at(13) == pytest.approx(6.5, abs=0.01)

    def test_seams_are_reported_on_the_output_timeline(self):
        assert self.mapper().seams() == [10.0]

    def test_src_of_a_word_is_reported(self):
        mapper = self.mapper()
        assert mapper.src_of(3) == "s0"
        assert mapper.src_of(13) == "s1"

    def test_a_word_in_a_cut_clamps_to_the_nearest_kept_edge(self):
        joined = words(10, src="s0")
        mapper = TimeMapper(joined, [Span("s0", Path("a.mp4"), 0.0, 3.0),
                                    Span("s0", Path("a.mp4"), 6.0, 10.0)],
                            new_duration=7.0, tempo=1.0)
        # word 4 starts at 4.0, inside the removed 3.0-6.0 window
        assert mapper.at(4) == pytest.approx(3.0, abs=0.01)

    def test_event_from_a_dropped_source_does_not_land_at_zero(self):
        """Anchoring to 0 would silently move the event to the top of the video;
        `at` returns 0.0 only after the caller has no source to map to."""
        joined = words(5, src="s7")
        mapper = TimeMapper(joined, self.spans(), new_duration=20.0, tempo=1.0)
        with pytest.raises(KeyError):
            mapper.map_time("s7", 1.0)

    def test_never_exceeds_the_new_duration(self):
        mapper = TimeMapper(words(10, src="s0"),
                            [Span("s0", Path("a.mp4"), 0.0, 10.0)],
                            new_duration=3.0, tempo=1.0)
        assert mapper.at(9, use_end=True) <= 3.0


class TestCopyAssertion:
    """`apply_master_audio` must refuse to accept a re-encoded video stream."""

    def test_bitrate_drift_over_the_tolerance_raises(self, tmp_path, monkeypatch):
        joined = tmp_path / "joined.mp4"
        out = tmp_path / "out.mp4"
        joined.write_bytes(b"x")

        readings = iter([4_000_000.0, 1_200_000.0])   # 70% smaller = re-encoded
        monkeypatch.setattr(resolve_cut, "video_stream_bitrate",
                            lambda path: next(readings))
        monkeypatch.setattr(resolve_cut.subprocess, "run",
                            lambda *a, **k: _ok(out))

        with pytest.raises(resolve_media.ResolveError, match="encode lại"):
            resolve_cut.apply_master_audio(joined, out)

    def test_a_true_copy_passes(self, tmp_path, monkeypatch):
        joined = tmp_path / "joined.mp4"
        out = tmp_path / "out.mp4"
        joined.write_bytes(b"x")

        readings = iter([4_000_000.0, 4_010_000.0])   # 0.25% — container overhead
        monkeypatch.setattr(resolve_cut, "video_stream_bitrate",
                            lambda path: next(readings))
        monkeypatch.setattr(resolve_cut.subprocess, "run",
                            lambda *a, **k: _ok(out))
        monkeypatch.setattr(resolve_cut, "probe_duration", lambda path: 42.0)

        assert resolve_cut.apply_master_audio(joined, out) == 42.0

    def test_the_command_really_asks_for_copy(self, tmp_path, monkeypatch):
        joined = tmp_path / "joined.mp4"
        out = tmp_path / "out.mp4"
        joined.write_bytes(b"x")
        seen: dict[str, list[str]] = {}

        def capture(command, *args, **kwargs):
            seen["command"] = list(command)
            return _ok(out)

        monkeypatch.setattr(resolve_cut, "video_stream_bitrate", lambda path: 0.0)
        monkeypatch.setattr(resolve_cut.subprocess, "run", capture)
        monkeypatch.setattr(resolve_cut, "probe_duration", lambda path: 1.0)
        resolve_cut.apply_master_audio(joined, out)

        command = seen["command"]
        assert "-c:v" in command and command[command.index("-c:v") + 1] == "copy"
        assert "libx264" not in command


class _ok:
    """Minimal stand-in for a successful CompletedProcess."""

    def __init__(self, out_path: Path):
        out_path.write_bytes(b"out")
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""


class TestConcatDurationCheck:
    def test_short_joined_file_is_refused(self, tmp_path, monkeypatch):
        """Partial concat is a real failure mode, and a truncated video would
        otherwise sail through to the renderer."""
        segments = [tmp_path / f"seg_{i}.mp4" for i in range(3)]
        for segment in segments:
            segment.write_bytes(b"x")
        out = tmp_path / "joined.mp4"

        monkeypatch.setattr(resolve_cut.subprocess, "run", lambda *a, **k: _ok(out))
        monkeypatch.setattr(resolve_cut, "probe_duration",
                            lambda path: 2.0 if path != out else 3.0)

        with pytest.raises(resolve_media.ResolveError, match="concat"):
            resolve_cut.concat_segments(segments, out)

    def test_matching_duration_passes(self, tmp_path, monkeypatch):
        segments = [tmp_path / f"seg_{i}.mp4" for i in range(2)]
        for segment in segments:
            segment.write_bytes(b"x")
        out = tmp_path / "joined.mp4"

        monkeypatch.setattr(resolve_cut.subprocess, "run", lambda *a, **k: _ok(out))
        monkeypatch.setattr(resolve_cut, "probe_duration",
                            lambda path: 4.0 if path == out else 2.0)
        assert resolve_cut.concat_segments(segments, out) == 4.0

    def test_empty_segment_list_is_refused(self, tmp_path):
        with pytest.raises(resolve_media.ResolveError):
            resolve_cut.concat_segments([], tmp_path / "joined.mp4")


class TestGradeChainPerSource:
    def test_flat_overrides_apply_to_every_source(self, tmp_path, monkeypatch):
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.stages import resolve as stage

        clip = tmp_path / "a.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})
        monkeypatch.setattr(stage, "calibrate", lambda *a, **k: (_ for _ in ()).throw(
            stage.SharpenCalibrationError("bỏ qua trong test")))

        sources = [{"id": "s0", "path": str(clip), "width": 720},
                   {"id": "s1", "path": str(clip), "width": 1080}]
        chains, grades = stage.grade_chains_for(
            job, {"grade_overrides": {"brightness": 0.05}, "auto_sharpen": False},
            {"contrast": 1.05}, words(4), sources, (1012, 1800))

        assert set(chains) == {"s0", "s1"}
        assert grades["s0"]["brightness"] == 0.05
        assert grades["s1"]["brightness"] == 0.05

    def test_keyed_overrides_target_one_source(self, tmp_path, monkeypatch):
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.stages import resolve as stage

        clip = tmp_path / "a.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})

        sources = [{"id": "s0", "path": str(clip), "width": 720},
                   {"id": "s1", "path": str(clip), "width": 720}]
        _, grades = stage.grade_chains_for(
            job,
            {"grade_overrides": {"__all__": {"warmth": 4}, "s1": {"brightness": 0.03}},
             "auto_sharpen": False},
            {}, words(4), sources, (1012, 1800))

        assert grades["s0"]["warmth"] == 4
        assert "brightness" not in grades["s0"]
        assert grades["s1"]["brightness"] == 0.03
        assert grades["s1"]["warmth"] == 4, "lớp __all__ vẫn áp cho nguồn có override riêng"

    def test_different_sources_get_different_chains(self, tmp_path):
        from lib.talking_head_edit.job_store import JobStore
        from lib.talking_head_edit.stages import resolve as stage

        clip = tmp_path / "a.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})
        sources = [{"id": "s0", "path": str(clip), "width": 720},
                   {"id": "s1", "path": str(clip), "width": 1080}]
        chains, _ = stage.grade_chains_for(job, {"auto_sharpen": False}, {},
                                          words(4), sources, (1012, 1800))
        assert chains["s0"] != chains["s1"], \
            "nguồn phóng to nhiều hơn phải được làm nét mạnh hơn"
