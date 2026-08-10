"""Multi-source spine, assembly config, role detection, index round-trip.

The single most important test in this file is
`TestSingleSourceRegression::test_one_source_gives_identical_words_and_indices`.
Multi-source is a rewrite of the clock, and the clock is what every later stage
trusts. If a one-source job stops producing the same word list at the same
indices, everything measured on this pipeline — sharpness, LUFS, cut quality —
was measured on a different edit.
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit import assembly_config, spine_build
from lib.talking_head_edit import sources as sources_mod
from lib.talking_head_edit.asr.base import build_spine
from lib.talking_head_edit.job_store import (
    JobStore,
    job_input_paths,
    primary_input_path,
)


def words_of(texts, base=0.0, step=0.25):
    return [{"word": text, "start": round(base + i * step, 3),
             "end": round(base + i * step + 0.2, 3)}
            for i, text in enumerate(texts)]


def spine_v2(texts, provider="elevenlabs_scribe"):
    return build_spine(words_of(texts), provider=provider, model="scribe_v2",
                       language="vi", duration_seconds=len(texts) * 0.25)


def spec(source_id, *, role="aroll", order=0, take_group="main", duration=10.0,
         speech=True, path=None):
    return {"id": source_id, "path": path or f"C:/f/{source_id}.mp4", "role": role,
            "order": order, "take_group": take_group, "duration": duration,
            "speech": speech, "label": source_id, "sha256": f"sha-{source_id}"}


class TestAssemblyConfigLayers:
    def test_builtin_defaults_when_nothing_configured(self, tmp_path):
        config = assembly_config.resolve(global_path=tmp_path / "missing.json")
        assert config["mode"] == "auto"
        assert config["speaker_aware"] == "auto"
        assert config["broll_overlay"] is True
        assert config["cross_source_cut"] is False
        assert config["take_detect"] == "suggest"

    def test_job_beats_project_beats_global(self, tmp_path):
        global_path = tmp_path / "defaults.json"
        global_path.write_text(json.dumps(
            {"assembly": {"mode": "sequential", "broll_overlay": False,
                          "cross_source_cut": True}}), encoding="utf-8")

        config = assembly_config.resolve(
            project={"mode": "best_take", "broll_overlay": True},
            job={"mode": "auto"},
            global_path=global_path)

        assert config["mode"] == "auto", "job thắng"
        assert config["broll_overlay"] is True, "project thắng global"
        assert config["cross_source_cut"] is True, "global còn hiệu lực khi không ai ghi đè"

    def test_null_is_not_a_value(self, tmp_path):
        """A JSON client sends {"broll_overlay": null} far more readily than it
        omits the key; treating null as False turns a feature off silently."""
        config = assembly_config.resolve(
            project={"broll_overlay": None, "mode": None},
            global_path=tmp_path / "missing.json")
        assert config["broll_overlay"] is True
        assert config["mode"] == "auto"

    def test_unknown_keys_are_ignored(self, tmp_path):
        config = assembly_config.resolve(job={"mode": "sequential", "nonsense": 1},
                                         global_path=tmp_path / "missing.json")
        assert "nonsense" not in config

    def test_invalid_mode_is_rejected(self, tmp_path):
        with pytest.raises(assembly_config.AssemblyConfigError):
            assembly_config.resolve(job={"mode": "magic"},
                                    global_path=tmp_path / "missing.json")

    def test_invalid_speaker_aware_is_rejected(self, tmp_path):
        with pytest.raises(assembly_config.AssemblyConfigError):
            assembly_config.resolve(job={"speaker_aware": "maybe"},
                                    global_path=tmp_path / "missing.json")

    def test_malformed_global_file_falls_back_to_builtins(self, tmp_path):
        broken = tmp_path / "defaults.json"
        broken.write_text("{ not json", encoding="utf-8")
        assert assembly_config.resolve(global_path=broken)["mode"] == "auto"

    def test_shipped_global_config_is_valid(self):
        assert assembly_config.global_defaults()["mode"] in assembly_config.MODES

    def test_origin_reports_which_layer_won(self, tmp_path):
        config = assembly_config.resolve(project={"mode": "best_take"},
                                         job={"broll_overlay": False},
                                         global_path=tmp_path / "missing.json")
        origin = assembly_config.sources_of(
            config, project={"mode": "best_take"}, job={"broll_overlay": False},
            global_path=tmp_path / "missing.json")
        assert origin["mode"] == "project"
        assert origin["broll_overlay"] == "job"
        assert origin["cross_source_cut"] == "global"


class TestRoleDetection:
    def test_no_audio_stream_is_broll(self):
        role, speech, warnings = sources_mod.classify_role(
            {"has_audio": False, "mean_volume_db": None})
        assert (role, speech) == ("broll", False)
        assert warnings

    def test_quiet_track_is_broll(self):
        role, speech, _ = sources_mod.classify_role(
            {"has_audio": True, "mean_volume_db": -58.0})
        assert (role, speech) == ("broll", False)

    def test_speech_level_track_is_aroll(self):
        role, speech, _ = sources_mod.classify_role(
            {"has_audio": True, "mean_volume_db": -21.0})
        assert (role, speech) == ("aroll", True)

    def test_unmeasurable_volume_defaults_to_speech(self):
        """Wrongly calling A-roll b-roll loses the speaker entirely; the reverse
        shows up as nonsense in the transcript immediately."""
        role, speech, warnings = sources_mod.classify_role(
            {"has_audio": True, "mean_volume_db": None})
        assert (role, speech) == ("aroll", True)
        assert warnings


class TestTakeSuggestions:
    def _specs(self, names, durations=None):
        durations = durations or [10.0] * len(names)
        return [sources_mod.SourceSpec(id=f"s{i}", path=f"C:/f/{n}.mp4",
                                      duration=d, order=i)
                for i, (n, d) in enumerate(zip(names, durations))]

    def test_take_markers_are_stripped_before_comparing(self):
        found = sources_mod.suggest_take_groups(
            self._specs(["intro-take1", "intro-take2", "intro_3"]))
        assert len(found) == 1
        assert set(found[0]["sources"]) == {"s0", "s1", "s2"}

    def test_similar_durations_raise_confidence(self):
        similar = sources_mod.suggest_take_groups(
            self._specs(["a-take1", "a-take2"], [30.0, 33.0]))
        different = sources_mod.suggest_take_groups(
            self._specs(["a-take1", "a-take2"], [30.0, 90.0]))
        assert similar[0]["confidence"] > different[0]["confidence"]

    def test_unrelated_names_are_not_grouped(self):
        assert sources_mod.suggest_take_groups(
            self._specs(["intro", "outro", "broll-desk"])) == []

    def test_suggestion_never_applied_by_default(self):
        """`take_detect: suggest` is the default precisely because a wrong group
        makes select delete real content."""
        specs = self._specs(["a-take1", "a-take2"])
        assert all(s.take_group == "main" for s in specs)


class TestSpineBuild:
    def test_indices_are_global_and_continuous(self):
        spine = spine_build.build(
            {"s0": spine_v2(["một", "hai"]), "s1": spine_v2(["ba", "bốn", "năm"])},
            [spec("s0", order=0), spec("s1", order=1)])
        assert [w["word"] for w in spine["word_timestamps"]] == \
            ["một", "hai", "ba", "bốn", "năm"]
        assert [w["src"] for w in spine["word_timestamps"]] == \
            ["s0", "s0", "s1", "s1", "s1"]
        assert spine["takes"] == [
            {"src": "s0", "w0": 0, "w1": 1, "take_group": "main", "order": 0},
            {"src": "s1", "w0": 2, "w1": 4, "take_group": "main", "order": 1},
        ]

    def test_sources_are_joined_in_order_not_dict_order(self):
        spine = spine_build.build(
            {"s1": spine_v2(["sau"]), "s0": spine_v2(["truoc"])},
            [spec("s1", order=1), spec("s0", order=0)])
        assert [w["word"] for w in spine["word_timestamps"]] == ["truoc", "sau"]

    def test_timestamps_stay_in_each_source_own_time_base(self):
        """Deliberate: converting to joined-timeline seconds would mean
        regenerating the whole spine every time the order changes."""
        spine = spine_build.build(
            {"s0": spine_v2(["a", "b"]), "s1": spine_v2(["c"])},
            [spec("s0", order=0), spec("s1", order=1)])
        assert spine["word_timestamps"][2]["start"] == 0.0

    def test_broll_never_enters_the_spine(self):
        spine = spine_build.build(
            {"s0": spine_v2(["noi"])},
            [spec("s0"), spec("s2", role="broll", order=2, speech=False, duration=8.0),
             spec("s3", role="broll", order=3, speech=False, duration=4.0)])
        assert [w["word"] for w in spine["word_timestamps"]] == ["noi"]
        assert len(spine["overlay_pool"]) == 2
        assert spine["overlay_pool"][0]["duration"] == 8.0

    def test_speaker_labels_are_namespaced_across_sources(self):
        """The same "speaker_0" in two files is probably two different people."""
        a = spine_v2(["a"])
        a["word_timestamps"][0]["speaker"] = "speaker_0"
        a["speakers"] = ["speaker_0"]
        b = spine_v2(["b"])
        b["word_timestamps"][0]["speaker"] = "speaker_0"
        b["speakers"] = ["speaker_0"]

        spine = spine_build.build({"s0": a, "s1": b},
                                  [spec("s0", order=0), spec("s1", order=1)])
        assert spine["speakers"] == ["s0:speaker_0", "s1:speaker_0"]
        assert [w["speaker"] for w in spine["word_timestamps"]] == \
            ["s0:speaker_0", "s1:speaker_0"]

    def test_mixed_providers_are_reported_as_mixed(self):
        spine = spine_build.build(
            {"s0": spine_v2(["a"], provider="elevenlabs_scribe"),
             "s1": spine_v2(["b"], provider="whisper_local")},
            [spec("s0", order=0), spec("s1", order=1)])
        assert spine["provider"] == "mixed"

    def test_boundaries_mark_every_source_after_the_first(self):
        spine = spine_build.build(
            {"s0": spine_v2(["a", "b"]), "s1": spine_v2(["c"]), "s2": spine_v2(["d"])},
            [spec("s0", order=0), spec("s1", order=1), spec("s2", order=2)])
        assert spine_build.source_boundaries(spine) == [2, 3]


class TestSingleSourceRegression:
    def test_one_source_gives_identical_words_and_indices(self):
        """The gate. Spine v3 over one source must be the same clock as v2."""
        texts = ["xin", "chào", "mọi", "người", "hôm", "nay"]
        v2 = spine_v2(texts)
        v3 = spine_build.build({"s0": v2}, [spec("s0")])

        assert len(v3["word_timestamps"]) == len(v2["word_timestamps"])
        for index, (before, after) in enumerate(
                zip(v2["word_timestamps"], v3["word_timestamps"])):
            assert after["word"] == before["word"], f"từ {index} đổi"
            assert after["start"] == before["start"], f"start của từ {index} đổi"
            assert after["end"] == before["end"], f"end của từ {index} đổi"

    def test_one_source_needs_no_boundary_marker(self):
        v3 = spine_build.build({"s0": spine_v2(["a", "b"])}, [spec("s0")])
        assert spine_build.source_boundaries(v3) == []

    def test_one_source_speaker_label_is_not_namespaced(self):
        one = spine_v2(["a"])
        one["word_timestamps"][0]["speaker"] = "speaker_0"
        one["speakers"] = ["speaker_0"]
        v3 = spine_build.build({"s0": one}, [spec("s0")])
        assert v3["speakers"] == ["speaker_0"]

    def test_legacy_v2_spine_upgrades_without_moving_a_word(self):
        v2 = spine_v2(["một", "hai", "ba"])
        upgraded = spine_build.upgrade_v2(v2)
        assert upgraded["schema"] == 3
        assert [w["word"] for w in upgraded["word_timestamps"]] == ["một", "hai", "ba"]
        assert all(w["src"] == "s0" for w in upgraded["word_timestamps"])
        assert upgraded["takes"][0] == {"src": "s0", "w0": 0, "w1": 2,
                                        "take_group": "main", "order": 0}


class TestIndexRoundTrip:
    def test_every_word_maps_back_to_its_original_index(self):
        """The worst failure this pipeline can have is cutting the wrong second of
        the wrong file, and that is exactly what a broken mapping causes."""
        words = words_of([f"w{i}" for i in range(50)])
        ranges = [[5, 12], [30, 41]]
        filtered = spine_build.filter_words(words, ranges)

        assert len(filtered) == 8 + 12
        for filtered_index, word in enumerate(filtered):
            original = word["_orig_index"]
            assert words[original]["word"] == word["word"]
            assert spine_build.to_filtered_index(filtered)[original] == filtered_index

    def test_no_ranges_keeps_everything_in_order(self):
        words = words_of(["a", "b", "c"])
        filtered = spine_build.filter_words(words, None)
        assert [w["word"] for w in filtered] == ["a", "b", "c"]
        assert spine_build.orig_indices(filtered) == [0, 1, 2]

    def test_out_of_order_ranges_are_sorted(self):
        words = words_of([f"w{i}" for i in range(10)])
        filtered = spine_build.filter_words(words, [[6, 7], [1, 2]])
        assert spine_build.orig_indices(filtered) == [1, 2, 6, 7]

    def test_adjacent_ranges_do_not_duplicate_words(self):
        words = words_of([f"w{i}" for i in range(10)])
        filtered = spine_build.filter_words(words, [[0, 4], [5, 9]])
        assert spine_build.orig_indices(filtered) == list(range(10))

    def test_coverage_is_the_fraction_kept(self):
        assert spine_build.coverage([[0, 4]], 10) == 0.5
        assert spine_build.coverage(None, 10) == 1.0
        assert spine_build.coverage([[0, 9]], 10) == 1.0


class TestJobInputPaths:
    def test_legacy_single_input_path_still_reads(self, tmp_path):
        state = {"input_path": str(tmp_path / "one.mp4")}
        assert job_input_paths(state) == [tmp_path / "one.mp4"]
        assert primary_input_path(state) == tmp_path / "one.mp4"

    def test_multi_source_job_reads_all_paths(self, tmp_path):
        clips = [tmp_path / f"c{i}.mp4" for i in range(3)]
        for clip in clips:
            clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clips, {})
        state = job.load()
        assert job_input_paths(state) == [c.resolve() for c in clips]
        # input_path stays in sync for readers that predate multi-source
        assert state["input_path"] == str(clips[0].resolve())

    def test_single_path_argument_still_accepted(self, tmp_path):
        clip = tmp_path / "one.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})
        assert job.load()["input_paths"] == [str(clip.resolve())]

    def test_select_is_in_the_stage_list_after_transcribe(self, tmp_path):
        from lib.talking_head_edit.job_store import STAGES

        assert STAGES.index("select") == STAGES.index("transcribe") + 1
        assert STAGES.index("select") < STAGES.index("direct")

        clip = tmp_path / "one.mp4"
        clip.write_bytes(b"0")
        job = JobStore(tmp_path / "jobs").create(clip, {})
        assert job.load()["stages"]["select"] == {"status": "pending"}


class TestCompactSpineMarkers:
    def test_no_marker_for_a_single_source(self):
        from lib.talking_head_edit.prompt_structure import compact_spine

        text = compact_spine(words_of(["a", "b", "c"]), [])
        assert "NGUỒN" not in text
        assert text == "0:a 1:b 2:c"

    def test_marker_sits_at_the_boundary(self):
        from lib.talking_head_edit.prompt_structure import compact_spine

        text = compact_spine(words_of(["a", "b", "c", "d"]), [2])
        assert "--- NGUỒN 2 ---" in text
        assert text.index("1:b") < text.index("NGUỒN 2") < text.index("2:c")

    def test_indices_are_unchanged_by_markers(self):
        from lib.talking_head_edit.prompt_structure import compact_spine

        text = compact_spine(words_of(["a", "b", "c"]), [1])
        for index, word in enumerate(["a", "b", "c"]):
            assert f"{index}:{word}" in text

    def test_cut_across_boundary_is_forbidden_by_default(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(words_of(["a", "b", "c"]), {},
                                        source_boundaries=[2])
        assert "TUYỆT ĐỐI không đặt một cut" in prompt

    def test_cross_source_cut_flips_the_rule(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(words_of(["a", "b", "c"]), {},
                                        source_boundaries=[2],
                                        cross_source_cut=True)
        assert "Được phép đặt cut" in prompt

    def test_single_source_prompt_says_nothing_about_sources(self):
        """The director's job does not change when footage arrives in one file,
        so neither should its prompt."""
        from lib.talking_head_edit.prompt_structure import build_structure_prompt

        prompt = build_structure_prompt(words_of(["a", "b"]), {})
        assert "NGUỒN" not in prompt
