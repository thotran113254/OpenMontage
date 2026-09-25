"""Projects: streaming uploads, source metadata, config inheritance, guards.

The upload path is tested with real bytes through the real code (a 4 MB file, so
several 1 MB chunks) because the two things that go wrong there — a truncated
file left behind by a dead upload, and a hash computed from a second read — are
both invisible in a mocked test.
"""

from __future__ import annotations

import io
import json

import pytest

from lib.talking_head_edit.project_store import ProjectError, ProjectStore
from lib.talking_head_edit.project_uploads import UploadError, safe_filename


class FakeProbe:
    """Stands in for ffprobe + the volume reading."""

    def __init__(self, duration=12.0, has_audio=True, volume=-20.0,
                 width=1080, height=1920):
        self.payload = {"duration": duration, "width": width, "height": height,
                        "fps": 30.0, "video_codec": "h264",
                        "audio_codec": "aac" if has_audio else None,
                        "audio_channels": 2 if has_audio else None,
                        "has_audio": has_audio, "mean_volume_db": volume,
                        "size_bytes": 1024}

    def __call__(self, path):
        return dict(self.payload)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from lib.talking_head_edit import project_store as module
    from lib.talking_head_edit import sources as sources_mod

    monkeypatch.setattr(sources_mod, "probe_source", FakeProbe())
    # Thumbnails are an ffmpeg call; the spec field is what matters here.
    monkeypatch.setattr(module, "thumbnail",
                        lambda source, out, at_seconds=1.0: (out.write_bytes(b"jpg"), out)[1])
    return ProjectStore(tmp_path / "autoedit")


def upload(project, name="take-1.mp4", megabytes=4, label=""):
    """A real multi-chunk stream, so the 1 MB loop is genuinely exercised."""
    payload = b"\0" * (megabytes * (1 << 20))
    return project.add_source(io.BytesIO(payload), name, label=label)


class TestSafeFilename:
    def test_strips_directories(self):
        assert safe_filename("../../etc/passwd.mp4") == "passwd.mp4"

    def test_folds_vietnamese_diacritics(self):
        """These paths get handed to ffmpeg and Node."""
        assert safe_filename("Buổi quay đầu.mp4") == "Buoi-quay-dau.mp4"

    def test_refuses_an_unsupported_extension(self):
        with pytest.raises(UploadError):
            safe_filename("script.exe")

    def test_refuses_a_missing_extension(self):
        with pytest.raises(UploadError):
            safe_filename("nofileextension")

    def test_long_names_are_truncated_but_keep_the_suffix(self):
        name = safe_filename("x" * 300 + ".mp4")
        assert name.endswith(".mp4")
        assert len(name) <= 64


class TestUpload:
    def test_hash_is_computed_in_the_same_pass_as_the_write(self, store):
        import hashlib

        project = store.create("Buổi quay 1")
        spec = upload(project, megabytes=4)
        expected = hashlib.sha256(b"\0" * (4 << 20)).hexdigest()
        assert spec["sha256"] == expected
        assert spec["size_bytes"] == 4 << 20

    def test_file_lands_inside_the_project(self, store):
        project = store.create("P")
        spec = upload(project)
        path = project.source_path(spec)
        assert path.exists()
        assert path.parent == project.sources_dir
        assert spec["file"].startswith("sources/")

    def test_no_part_file_is_left_behind(self, store):
        project = store.create("P")
        upload(project)
        assert list(project.sources_dir.glob("*.part")) == []

    def test_a_dying_upload_leaves_no_truncated_file(self, store):
        """A truncated mp4 is worse than no file: ffprobe accepts it and every
        later stage misreads it."""
        project = store.create("P")

        class Exploding(io.BytesIO):
            def read(self, size=-1):
                raise OSError("kết nối đứt")

        with pytest.raises(OSError):
            project.add_source(Exploding(b"x"), "take.mp4")
        assert list(project.sources_dir.glob("*")) == []

    def test_an_empty_upload_is_refused(self, store):
        project = store.create("P")
        with pytest.raises(ProjectError):
            project.add_source(io.BytesIO(b""), "take.mp4")

    def test_ids_increment_and_never_collide(self, store):
        project = store.create("P")
        ids = [upload(project, name=f"t{i}.mp4")["id"] for i in range(3)]
        assert ids == ["s0", "s1", "s2"]

    def test_removed_id_is_reused_rather_than_left_as_a_hole(self, store):
        project = store.create("P")
        upload(project, name="a.mp4")
        upload(project, name="b.mp4")
        project.remove_source("s0")
        assert upload(project, name="c.mp4")["id"] == "s0"

    def test_unprobeable_file_is_rejected_and_deleted(self, store, monkeypatch):
        from lib.talking_head_edit import sources as sources_mod

        project = store.create("P")

        def explode(path):
            raise RuntimeError("moov atom not found")

        monkeypatch.setattr(sources_mod, "probe_source", explode)
        with pytest.raises(ProjectError, match="ffprobe"):
            upload(project)
        assert list(project.sources_dir.glob("*.mp4")) == []


class TestSourceClassification:
    def test_a_silent_file_is_registered_as_broll(self, store, monkeypatch):
        from lib.talking_head_edit import sources as sources_mod

        project = store.create("P")
        monkeypatch.setattr(sources_mod, "probe_source",
                            FakeProbe(has_audio=False, volume=None))
        spec = upload(project, name="broll-desk.mp4")
        assert spec["role"] == "broll"
        assert spec["speech"] is False
        assert spec["warnings"]

    def test_a_speaking_file_is_aroll(self, store):
        project = store.create("P")
        assert upload(project)["role"] == "aroll"

    def test_a_human_can_override_the_role(self, store):
        project = store.create("P")
        upload(project)
        spec = project.update_source("s0", role="broll")
        assert spec["role"] == "broll"
        assert spec["speech"] is False

    def test_only_human_owned_fields_are_editable(self, store):
        """Letting a client overwrite a measurement puts a guess where a fact was."""
        project = store.create("P")
        upload(project)
        with pytest.raises(ProjectError, match="duration"):
            project.update_source("s0", duration=999.0)

    def test_workspace_fields_tags_notes_meta_bgm(self, store):
        project = store.create("P")
        upload(project)
        spec = project.update_source(
            "s0",
            tags=["tiktok", "tuan-33"],
            notes="hook mạnh",
            meta={"posted": True, "views": 1200},
            bgm_name="bgm_tech_pulse.mp3",
            bgm_volume=0.14,
        )
        assert spec["tags"] == ["tiktok", "tuan-33"]
        assert spec["notes"] == "hook mạnh"
        assert spec["meta"]["views"] == 1200
        assert spec["bgm_name"] == "bgm_tech_pulse.mp3"
        assert spec["bgm_volume"] == 0.14

    def test_create_job_inherits_source_bgm_for_single_clip(self, store):
        project = store.create("P")
        upload(project)
        project.update_source("s0", bgm_name="bgm_lofi_chill.mp3", bgm_volume=0.12)
        job = project.create_job(source_ids=["s0"])
        options = job.load()["options"]
        assert options["bgm_name"] == "bgm_lofi_chill.mp3"
        assert options["bgm_volume"] == 0.12
        assert options["bgm"] is True

    def test_an_invalid_role_is_refused(self, store):
        project = store.create("P")
        upload(project)
        with pytest.raises(ProjectError):
            project.update_source("s0", role="cinematic")

    def test_updating_a_missing_source_is_an_error(self, store):
        project = store.create("P")
        with pytest.raises(ProjectError):
            project.update_source("s9", label="x")


class TestConfigInheritance:
    def test_build_inherits_defaults_keyterms_and_assembly(self, store):
        project = store.create(
            "P", assembly={"mode": "best_take", "speaker_aware": True},
            keyterms=["Zalo OA"],
            defaults={"topic": "chatbot", "frame_preset": "light"})
        upload(project)
        job = project.create_job()

        options = job.load()["options"]
        assert options["topic"] == "chatbot"
        assert options["frame_preset"] == "light"
        assert options["keyterms"] == ["Zalo OA"]
        assert options["assembly"]["mode"] == "best_take"

    def test_job_assembly_merges_rather_than_replaces(self, store):
        """`{"mode": "sequential"}` from a job must not drop the project's
        speaker_aware setting."""
        project = store.create("P", assembly={"mode": "best_take",
                                              "speaker_aware": True})
        upload(project)
        job = project.create_job({"assembly": {"mode": "sequential"}})
        assembly = job.load()["options"]["assembly"]
        assert assembly == {"mode": "sequential", "speaker_aware": True}

    def test_explicit_options_beat_project_defaults(self, store):
        project = store.create("P", defaults={"topic": "cũ"})
        upload(project)
        job = project.create_job({"topic": "mới"})
        assert job.load()["options"]["topic"] == "mới"

    def test_build_takes_every_source_with_aroll_first(self, store, monkeypatch):
        """B-roll must reach the build: `spine_build` routes silent sources to
        `overlay_pool`, so leaving them out is what silently drops every overlay.

        A-roll first because `primary_input_path` means "the footage".
        """
        from lib.talking_head_edit import sources as sources_mod

        project = store.create("P")
        upload(project, name="take-1.mp4")
        upload(project, name="take-2.mp4")
        monkeypatch.setattr(sources_mod, "probe_source",
                            FakeProbe(has_audio=False, volume=None))
        upload(project, name="broll.mp4")

        job = project.create_job()
        paths = job.load()["input_paths"]
        assert len(paths) == 3
        assert "take-1" in paths[0] and "take-2" in paths[1]
        assert "broll" in paths[2]
        # input_path (the legacy single-source field) must be a speaking source.
        assert "take-1" in job.load()["input_path"]

    def test_build_hands_down_the_source_classification(self, store):
        """probe must keep the human's role/take_group instead of re-guessing."""
        project = store.create("P")
        upload(project)
        project.update_source("s0", take_group="mo-dau", label="take 1")
        job = project.create_job()
        rows = job.load()["sources"]
        assert rows[0]["take_group"] == "mo-dau"
        assert rows[0]["label"] == "take 1"

    def test_a_project_with_no_speech_cannot_build(self, store, monkeypatch):
        from lib.talking_head_edit import sources as sources_mod

        project = store.create("P")
        monkeypatch.setattr(sources_mod, "probe_source",
                            FakeProbe(has_audio=False, volume=None))
        upload(project, name="broll.mp4")
        with pytest.raises(ProjectError, match="lời nói"):
            project.create_job()

    def test_job_records_the_project_it_belongs_to(self, store):
        project = store.create("P")
        upload(project)
        job = project.create_job()
        assert job.load()["project_id"] == project.project_id


class TestSourceReuse:
    def test_two_builds_share_one_copy_of_the_footage(self, store):
        """The whole point of the project layer: today a second build means
        uploading the file again."""
        project = store.create("P")
        spec = upload(project)
        first = project.create_job()
        second = project.create_job()

        assert first.load()["input_paths"] == second.load()["input_paths"]
        assert len(list(project.sources_dir.glob("*.mp4"))) == 1
        assert project.source_path(spec).exists()

    def test_builds_are_listed_with_their_status(self, store):
        project = store.create("P")
        upload(project)
        job = project.create_job()
        builds = project.builds()
        assert [b["job_id"] for b in builds] == [job.job_id]
        assert builds[0]["has_final"] is False
        assert builds[0]["stages"]["select"] == "pending"


class TestDeletionGuards:
    def test_a_source_used_by_a_build_is_not_deleted(self, store):
        project = store.create("P")
        upload(project)
        job = project.create_job()
        with pytest.raises(ProjectError) as exc:
            project.remove_source("s0")
        assert job.job_id in str(exc.value)

    def test_force_deletes_it_and_reports_the_damage(self, store):
        project = store.create("P")
        spec = upload(project)
        job = project.create_job()
        result = project.remove_source("s0", force=True)
        assert result["was_used_by"] == [job.job_id]
        assert not project.source_path(spec).exists()

    def test_an_unused_source_deletes_cleanly(self, store):
        project = store.create("P")
        spec = upload(project)
        project.remove_source("s0")
        assert not project.source_path(spec).exists()
        assert project.load()["sources"] == []

    def test_deleting_a_project_removes_its_builds_too(self, store):
        project = store.create("P")
        upload(project)
        project.create_job()
        result = store.delete(project.project_id)
        assert result["jobs_deleted"] == 1
        assert not project.dir.exists()


class TestListing:
    def test_summary_never_reads_the_sources_directory(self, store):
        project = store.create("Buổi quay tháng 8")
        upload(project, name="a.mp4")
        upload(project, name="b.mp4")
        row = store.list()[0]
        assert row["source_count"] == 2
        assert row["aroll_count"] == 2
        assert row["total_seconds"] == 24.0
        assert row["thumb"]
        assert row["folder"] == ""
        assert row["ready_count"] == 0
        assert row["pending_count"] == 2

    def test_malformed_project_json_is_skipped_not_fatal(self, store):
        good = store.create("Tốt")
        broken = store.root / "broken-260804-000000"
        broken.mkdir(parents=True)
        (broken / "project.json").write_text("{ not json", encoding="utf-8")
        assert [r["project_id"] for r in store.list()] == [good.project_id]

    def test_missing_project_raises_file_not_found(self, store):
        with pytest.raises(FileNotFoundError):
            store.get("khong-co-that")


class TestFolderAndClipScope:
    def test_folder_is_stored_on_the_project(self, store):
        project = store.create("Kênh An", folder="Studio")
        assert project.load()["folder"] == "Studio"
        assert store.list()[0]["folder"] == "Studio"

    def test_a_clip_build_uses_only_that_source(self, store):
        project = store.create("Kênh An")
        upload(project, name="clip-a.mp4")
        upload(project, name="clip-b.mp4")
        job = project.create_job(source_ids=["s1"], include_broll=False)
        state = job.load()
        assert state["source_ids"] == ["s1"]
        assert len(state["input_paths"]) == 1
        assert "clip-b" in state["input_paths"][0]
        builds = project.builds()
        assert builds[0]["source_ids"] == ["s1"]
        assert builds[0]["source_labels"] == ["clip-b"]
        assert builds[0]["has_final"] is False
        assert builds[0]["media_base"] == f"/api/media/{job.job_id}/"

    def test_ready_count_follows_finished_mp4(self, store):
        project = store.create("An")
        upload(project, name="clip-a.mp4")
        upload(project, name="clip-b.mp4")
        job = project.create_job(source_ids=["s0"], include_broll=False)
        assert store.list()[0]["ready_count"] == 0
        (project.jobs_dir / job.job_id / "final.mp4").write_bytes(b"mp4")
        row = store.list()[0]
        assert row["ready_count"] == 1
        assert row["pending_count"] == 1

    def test_per_clip_build_can_still_pull_project_broll(self, store, monkeypatch):
        from lib.talking_head_edit import sources as sources_mod

        project = store.create("Kênh An")
        upload(project, name="clip-a.mp4")
        upload(project, name="clip-b.mp4")
        monkeypatch.setattr(sources_mod, "probe_source",
                            FakeProbe(has_audio=False, volume=None))
        upload(project, name="broll.mp4")
        job = project.create_job(source_ids=["s0"], include_broll=True)
        paths = job.load()["input_paths"]
        assert len(paths) == 2
        assert "clip-a" in paths[0]
        assert "broll" in paths[1]
        assert "clip-b" not in "".join(paths)

    def test_unknown_source_id_is_refused(self, store):
        project = store.create("P")
        upload(project)
        with pytest.raises(ProjectError, match="Không có nguồn"):
            project.create_job(source_ids=["s9"])

    def test_empty_source_ids_is_refused(self, store):
        project = store.create("P")
        upload(project)
        with pytest.raises(ProjectError, match="Chưa chọn"):
            project.create_job(source_ids=[])


class TestPartialUploadCleanup:
    def test_leftover_part_files_are_removed(self, store):
        project = store.create("P")
        project.sources_dir.mkdir(parents=True, exist_ok=True)
        stale = project.sources_dir / "s0_take.mp4.part"
        stale.write_bytes(b"half a video")
        assert store.clean_partial_uploads() == [str(stale)]
        assert not stale.exists()

    def test_real_sources_are_left_alone(self, store):
        project = store.create("P")
        spec = upload(project)
        store.clean_partial_uploads()
        assert project.source_path(spec).exists()


class TestTwoRootJobLookup:
    def test_a_build_inside_a_project_is_found_by_id(self, store):
        from lib.talking_head_edit.job_store import find_job

        project = store.create("P")
        upload(project)
        job = project.create_job()

        import lib.talking_head_edit.job_store as job_store_module

        # Point the project scan at this test's root.
        original = job_store_module.PROJECTS_ROOT
        job_store_module.PROJECTS_ROOT = store.root
        try:
            found = find_job(job.job_id, legacy_root=store.root / "_nonexistent")
            assert found.dir == job.dir
        finally:
            job_store_module.PROJECTS_ROOT = original

    def test_legacy_and_project_jobs_appear_in_one_list(self, store, tmp_path):
        from lib.talking_head_edit.job_store import JobStore, list_all_jobs
        import lib.talking_head_edit.job_store as job_store_module

        legacy_root = tmp_path / "autoedit-jobs"
        clip = tmp_path / "old.mp4"
        clip.write_bytes(b"0")
        legacy_job = JobStore(legacy_root).create(clip, {})

        project = store.create("P")
        upload(project)
        project_job = project.create_job()

        original = job_store_module.PROJECTS_ROOT
        job_store_module.PROJECTS_ROOT = store.root
        try:
            rows = list_all_jobs(legacy_root=legacy_root)
        finally:
            job_store_module.PROJECTS_ROOT = original

        by_id = {r["job_id"]: r for r in rows}
        assert legacy_job.job_id in by_id and project_job.job_id in by_id
        assert by_id[legacy_job.job_id]["is_legacy"] is True
        assert by_id[legacy_job.job_id]["project_id"] is None
        assert by_id[project_job.job_id]["project_id"] == project.project_id
