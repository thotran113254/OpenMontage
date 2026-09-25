"""Projects: a shared studio of creator folders, each holding many independent clips.

Deliberately no database. One directory per entity with a JSON file in it, the
same shape as `JobStore`. Physical layout stays flat so existing projects do not
move; grouping is a `folder` field (creator / team) on `project.json`.

```
Studio (UI)
└── folder / người          e.g. 5 creators
    └── project             one workspace per person
        ├── sources/        each talking-head clip is its own video
        └── jobs/           one build uses selected source_ids, not every clip

projects/autoedit/<project_id>/
├── project.json            folder, defaults (style/BGM), sources[], jobs[]
├── sources/                s0_clip.mp4  s0.transcript.json  s0.thumb.jpg
└── jobs/<job_id>/
```

A project is a creator workspace, not one shoot: 10 clips a day stay in the
same project and each render picks its own source. Omitting `source_ids` still
uses every source (legacy multi-take assembly).

Legacy jobs under `projects/autoedit-jobs/` are read, never moved.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from lib.talking_head_edit.job_store import (
    REPO_ROOT,
    STAGES,
    JobStore,
    slugify,
    write_json_atomic,
)
from lib.talking_head_edit.project_uploads import (
    UPLOAD_SUFFIXES,
    UploadError,
    safe_filename,
    stream_to_file,
    thumbnail,
)

PROJECTS_ROOT = REPO_ROOT / "projects" / "autoedit"

__all__ = ["PROJECTS_ROOT", "UPLOAD_SUFFIXES", "Project", "ProjectError",
           "ProjectStore", "clean_folder"]


class ProjectError(RuntimeError):
    pass


def clean_folder(value: Any) -> str:
    return str(value or "").strip()[:80]


class Project:
    """One creator workspace: many clips, shared style, independent builds."""

    def __init__(self, project_id: str, dir_path: Path):
        self.project_id = project_id
        self.dir = dir_path

    # ---- paths -----------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.dir / "project.json"

    @property
    def sources_dir(self) -> Path:
        return self.dir / "sources"

    @property
    def jobs_dir(self) -> Path:
        return self.dir / "jobs"

    def source_path(self, spec: dict[str, Any]) -> Path:
        return self.dir / str(spec["file"])

    # ---- state -----------------------------------------------------------
    def load(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = time.time()
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.state_path, state)

    def update(self, **fields: Any) -> dict[str, Any]:
        state = self.load()
        state.update(fields)
        self.save(state)
        return state

    # ---- sources ---------------------------------------------------------
    def next_source_id(self) -> str:
        used = {str(s.get("id")) for s in self.load().get("sources", [])}
        index = 0
        while f"s{index}" in used:
            index += 1
        return f"s{index}"

    def add_source(self, stream: BinaryIO, filename: str,
                   label: str = "") -> dict[str, Any]:
        """Stream an upload into `sources/` and record it."""
        source_id = self.next_source_id()
        try:
            target = self.sources_dir / f"{source_id}_{safe_filename(filename)}"
            sha256, size = stream_to_file(stream, target)
        except UploadError as exc:
            raise ProjectError(str(exc)) from exc
        # The label defaults to the name the USER uploaded, not the name on disk:
        # the stored file carries an `s0_` prefix, and take-group detection
        # compares names — the prefix would make two takes of the same content
        # look like two different subjects.
        return self.register_source(target, source_id=source_id,
                                    label=label or Path(filename).stem,
                                    sha256=sha256, size_bytes=size)

    def register_source(self, path: Path, source_id: str | None = None,
                        label: str = "", sha256: str = "",
                        size_bytes: int = 0) -> dict[str, Any]:
        """Probe a file already inside `sources/` and record it in project.json."""
        from lib.talking_head_edit import sources as sources_mod

        source_id = source_id or self.next_source_id()
        try:
            probe = sources_mod.probe_source(path)
        except Exception as exc:  # noqa: BLE001 — a bad upload must not 500
            path.unlink(missing_ok=True)
            raise ProjectError(
                f"{path.name}: không đọc được bằng ffprobe ({str(exc)[:160]}). "
                "File có thể bị hỏng hoặc chưa upload xong.") from exc

        role, speech, warnings = sources_mod.classify_role(probe)
        thumb = thumbnail(path, self.sources_dir / f"{source_id}.thumb.jpg",
                          at_seconds=min(1.0, max(0.0, probe["duration"] / 2)))

        state = self.load()
        order = max((int(s.get("order", 0)) for s in state.get("sources", [])),
                    default=-1) + 1
        spec = {
            "id": source_id,
            "file": path.relative_to(self.dir).as_posix(),
            "sha256": sha256 or "",
            "size_bytes": size_bytes or path.stat().st_size,
            "duration": probe["duration"],
            "width": probe["width"],
            "height": probe["height"],
            "fps": probe["fps"],
            "has_audio": probe["has_audio"],
            "mean_volume_db": probe["mean_volume_db"],
            "role": role,
            "speech": speech,
            "order": order,
            "take_group": "main",
            "label": label or path.stem,
            "thumb": thumb.relative_to(self.dir).as_posix() if thumb else None,
            "transcript": f"sources/{source_id}.transcript.json",
            "warnings": warnings,
            "added_at": time.time(),
            # Human workspace fields — optional, never measured by probe.
            "tags": [],
            "notes": "",
            "meta": {},
            "bgm_name": "",
            "bgm_volume": None,
        }
        state.setdefault("sources", []).append(spec)
        self.save(state)
        return spec

    def update_source(self, source_id: str, **fields: Any) -> dict[str, Any]:
        """Change the human-owned metadata of one source.

        Measured fields (duration, size, loudness…) stay read-only. Workspace
        fields (tags, notes, meta, preferred BGM) are free-form so long-lived
        clip libraries stay manageable across months.
        """
        allowed = {
            "role", "order", "take_group", "label",
            "tags", "notes", "meta", "bgm_name", "bgm_volume",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ProjectError(
                f"Không sửa được: {', '.join(sorted(unknown))}. "
                f"Chỉ sửa được: {', '.join(sorted(allowed))}")

        cleaned: dict[str, Any] = {}
        for key, value in fields.items():
            if value is None and key != "bgm_volume":
                continue
            if key == "tags":
                if not isinstance(value, list):
                    raise ProjectError("tags phải là danh sách chuỗi")
                cleaned[key] = [str(item).strip() for item in value if str(item).strip()]
            elif key == "notes":
                cleaned[key] = str(value)
            elif key == "meta":
                if not isinstance(value, dict):
                    raise ProjectError("meta phải là object JSON")
                cleaned[key] = value
            elif key == "bgm_name":
                cleaned[key] = str(value or "").strip()
            elif key == "bgm_volume":
                if value is None or value == "":
                    cleaned[key] = None
                else:
                    try:
                        vol = float(value)
                    except (TypeError, ValueError) as exc:
                        raise ProjectError("bgm_volume phải là số") from exc
                    cleaned[key] = max(0.0, min(1.0, vol))
            else:
                cleaned[key] = value

        state = self.load()
        for spec in state.get("sources", []):
            if str(spec.get("id")) == source_id:
                # Backfill workspace keys on older project.json rows.
                spec.setdefault("tags", [])
                spec.setdefault("notes", "")
                spec.setdefault("meta", {})
                spec.setdefault("bgm_name", "")
                spec.setdefault("bgm_volume", None)
                spec.update(cleaned)
                if "role" in cleaned:
                    if cleaned["role"] not in ("aroll", "broll"):
                        raise ProjectError("role phải là 'aroll' hoặc 'broll'")
                    # A human calling something b-roll overrides the measurement.
                    spec["speech"] = cleaned["role"] == "aroll" and spec.get("speech", True)
                self.save(state)
                return spec
        raise ProjectError(f"Không có nguồn {source_id} trong project {self.project_id}")

    def jobs_using(self, source_id: str) -> list[str]:
        """Job ids whose input list still contains this source's file."""
        state = self.load()
        spec = next((s for s in state.get("sources", []) if str(s["id"]) == source_id), None)
        if not spec:
            return []
        target = str(self.source_path(spec).resolve())
        used: list[str] = []
        for job_state in JobStore(self.jobs_dir).list():
            paths = job_state.get("input_paths") or [job_state.get("input_path")]
            if any(p and str(Path(p).resolve()) == target for p in paths):
                used.append(str(job_state.get("job_id")))
        return used

    def remove_source(self, source_id: str, force: bool = False) -> dict[str, Any]:
        blocking = self.jobs_using(source_id)
        if blocking and not force:
            raise ProjectError(
                f"Nguồn {source_id} đang được dùng bởi: {', '.join(blocking)}. "
                "Xoá vẫn được với force=true, nhưng các bản dựng đó sẽ không chạy lại được.")

        state = self.load()
        spec = next((s for s in state.get("sources", []) if str(s["id"]) == source_id), None)
        if not spec:
            raise ProjectError(f"Không có nguồn {source_id}")
        for key in ("file", "thumb", "transcript"):
            relative = spec.get(key)
            if relative:
                (self.dir / relative).unlink(missing_ok=True)
        state["sources"] = [s for s in state["sources"] if str(s["id"]) != source_id]
        self.save(state)
        return {"removed": source_id, "was_used_by": blocking}

    # ---- jobs ------------------------------------------------------------
    def job_store(self) -> JobStore:
        return JobStore(self.jobs_dir)

    def job_options(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Project defaults + assembly + keyterms, then the caller's overrides.

        Assembly merges rather than replaces: a job saying `{"mode": "sequential"}`
        should not silently drop the project's `speaker_aware`.
        """
        state = self.load()
        options: dict[str, Any] = {**(state.get("defaults") or {})}
        if state.get("keyterms"):
            options["keyterms"] = list(state["keyterms"])
        project_assembly = state.get("assembly") or {}
        given = dict(overrides or {})
        options.update({k: v for k, v in given.items() if k != "assembly"})
        options["assembly"] = {**project_assembly, **(given.get("assembly") or {})}
        return options

    def _pick_sources(self, source_ids: list[str] | None,
                      include_broll: bool) -> tuple[list[dict[str, Any]], list[str]]:
        """Resolve which clips this build owns.

        `source_ids=None` keeps the old behaviour (every source) so multi-take
        assembly and existing tests still work. A daily clip render passes one id.
        """
        state = self.load()
        all_specs = list(state.get("sources") or [])
        by_id = {str(spec.get("id")): spec for spec in all_specs}

        if source_ids is None:
            chosen = list(all_specs)
            requested = [str(spec.get("id")) for spec in all_specs]
        else:
            requested = [str(item).strip() for item in source_ids if str(item).strip()]
            if not requested:
                raise ProjectError("Chưa chọn video nào để dựng")
            missing = [item for item in requested if item not in by_id]
            if missing:
                raise ProjectError(
                    f"Không có nguồn {', '.join(missing)} trong project {self.project_id}")
            chosen = [by_id[item] for item in requested]
            if include_broll:
                have = {str(spec.get("id")) for spec in chosen}
                for spec in all_specs:
                    if spec.get("role") == "broll" and str(spec.get("id")) not in have:
                        chosen.append(spec)

        rows = [{**spec, "path": str(self.source_path(spec))} for spec in chosen]
        return rows, requested

    def create_job(self, options: dict[str, Any] | None = None,
                   title: str = "",
                   source_ids: list[str] | None = None,
                   include_broll: bool = True) -> Any:
        """A build of selected clips. Default: every source in the project."""
        state = self.load()
        from lib.talking_head_edit import sources as sources_mod

        rows, requested = self._pick_sources(source_ids, include_broll)
        speaking = sources_mod.aroll(rows)
        overlays = sources_mod.broll(rows)
        if not speaking:
            raise ProjectError(
                "Chưa có video có lời nói trong phần đã chọn. Pipeline neo theo "
                "lời nói nên cần ít nhất một nguồn A-roll.")

        # Prefer per-clip BGM when building one talking-head and the caller did
        # not pin bgm_name — keeps long-lived clip libraries self-describing.
        given = dict(options or {})
        if len(speaking) == 1 and "bgm_name" not in given:
            pref_name = str(speaking[0].get("bgm_name") or "").strip()
            if pref_name:
                given["bgm_name"] = pref_name
                if "bgm" not in given:
                    given["bgm"] = True
                if "bgm_volume" not in given and speaking[0].get("bgm_volume") is not None:
                    given["bgm_volume"] = speaking[0].get("bgm_volume")

        # B-roll goes in too. `probe` classifies each source and `spine_build`
        # routes the silent ones to `overlay_pool` instead of the word spine — so
        # handing over only the speaking sources is what would silently drop every
        # b-roll clip from the build.
        #
        # A-roll first, because `primary_input_path` (used by grade preview and
        # calibrate) means "the footage", and that has to be a speaking source.
        speaking_labels = [str(row.get("label") or row.get("id")) for row in speaking]
        default_title = speaking_labels[0] if len(speaking) == 1 else (
            title or state.get("title", ""))
        job = self.job_store().create(
            [row["path"] for row in speaking + overlays],
            self.job_options(given),
            title=title or default_title,
            project_id=self.project_id,
        )
        # Hand the classification down so probe keeps the human's decisions
        # instead of re-guessing role and take_group.
        job.update(sources=[
            {k: v for k, v in row.items() if k in
             ("id", "path", "role", "order", "take_group", "label", "speech",
              "duration", "width", "height", "fps", "sha256", "has_audio",
              "mean_volume_db")}
            for row in sorted(rows, key=lambda r: int(r.get("order", 0)))
        ], project=state, source_ids=requested, include_broll=bool(include_broll))

        state.setdefault("jobs", []).append(job.job_id)
        self.save(state)
        return job

    def _source_ids_for_job(self, job_state: dict[str, Any],
                            specs: list[dict[str, Any]]) -> list[str]:
        stored = job_state.get("source_ids")
        if isinstance(stored, list) and stored:
            return [str(item) for item in stored]
        paths = {str(Path(p).resolve())
                 for p in (job_state.get("input_paths") or []) if p}
        if not paths:
            return []
        return [str(spec["id"]) for spec in specs
                if str(self.source_path(spec).resolve()) in paths]

    def builds(self) -> list[dict[str, Any]]:
        """Every build with just enough to compare them in a list."""
        specs = list(self.load().get("sources") or [])
        labels = {str(spec.get("id")): str(spec.get("label") or spec.get("id"))
                  for spec in specs}
        out: list[dict[str, Any]] = []
        for job_state in self.job_store().list():
            job_id = str(job_state.get("job_id"))
            job_dir = self.jobs_dir / job_id
            source_ids = self._source_ids_for_job(job_state, specs)
            out.append({
                "job_id": job_id,
                "title": job_state.get("title"),
                "status": job_state.get("status"),
                "created_at": job_state.get("created_at"),
                "current_version": job_state.get("current_version", 0),
                "cost_usd": job_state.get("cost_usd", 0.0),
                "has_final": (job_dir / "final.mp4").exists(),
                "has_thumbnail": (job_dir / "thumbnail.jpg").exists(),
                "media_base": f"/api/media/{job_id}/",
                "prompt": (job_state.get("options") or {}).get("prompt", ""),
                "source_ids": source_ids,
                "source_labels": [labels.get(sid, sid) for sid in source_ids],
                "stages": {name: (job_state.get("stages") or {}).get(name, {}).get("status")
                           for name in STAGES},
            })
        return out


def ready_source_ids(project_dir: Path) -> set[str]:
    """Source ids that already have a finished MP4. Only opens job.json of
    jobs that actually produced final.mp4 — the list page needs a count, not a
    full builds() walk.
    """
    ready: set[str] = set()
    jobs = project_dir / "jobs"
    if not jobs.exists():
        return ready
    for final in jobs.glob("*/final.mp4"):
        state_path = final.parent / "job.json"
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ids = data.get("source_ids")
        if isinstance(ids, list) and ids:
            ready.update(str(item) for item in ids)
    return ready


class ProjectStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else PROJECTS_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, title: str, assembly: dict[str, Any] | None = None,
               keyterms: list[str] | None = None,
               defaults: dict[str, Any] | None = None,
               folder: str = "") -> Project:
        stamp = time.strftime("%y%m%d-%H%M%S")
        project_id = f"{slugify(title or 'project')}-{stamp}"
        project = Project(project_id, self.root / project_id)
        project.sources_dir.mkdir(parents=True, exist_ok=True)
        project.jobs_dir.mkdir(parents=True, exist_ok=True)
        project.save({
            "project_id": project_id,
            "title": title or project_id,
            "folder": clean_folder(folder),
            "created_at": time.time(),
            "sources": [],
            "assembly": assembly or {},
            "keyterms": keyterms or [],
            "defaults": defaults or {},
            "jobs": [],
        })
        return project

    def get(self, project_id: str) -> Project:
        project = Project(project_id, self.root / project_id)
        if not project.state_path.exists():
            raise FileNotFoundError(f"Project không tồn tại: {project_id}")
        return project

    def list(self) -> list[dict[str, Any]]:
        """Summaries only — never walks `sources/`, so the list stays fast."""
        out: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*/project.json"), reverse=True):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sources = state.get("sources") or []
            aroll_ids = [str(s.get("id")) for s in sources
                         if s.get("role", "aroll") == "aroll"]
            ready_ids = ready_source_ids(path.parent)
            ready_count = sum(1 for sid in aroll_ids if sid in ready_ids)
            first_thumb = next((s.get("thumb") for s in sources if s.get("thumb")), None)
            out.append({
                "project_id": state.get("project_id"),
                "title": state.get("title"),
                "folder": state.get("folder") or "",
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "source_count": len(sources),
                "aroll_count": len(aroll_ids),
                "ready_count": ready_count,
                "pending_count": max(0, len(aroll_ids) - ready_count),
                "total_seconds": round(sum(float(s.get("duration") or 0) for s in sources), 1),
                "build_count": len(state.get("jobs") or []),
                "thumb": first_thumb,
                "assembly": state.get("assembly") or {},
            })
        return out

    def delete(self, project_id: str) -> dict[str, Any]:
        """Removes the project directory INCLUDING its builds. Caller confirms."""
        project = self.get(project_id)
        state = project.load()
        shutil.rmtree(project.dir, ignore_errors=True)
        return {"deleted": project_id, "path": str(project.dir),
                "jobs_deleted": len(state.get("jobs") or [])}

    def clean_partial_uploads(self) -> list[str]:
        """Delete leftover `.part` files. Called at server start.

        An upload killed mid-flight leaves one behind, and they are large.
        """
        removed: list[str] = []
        for part in self.root.glob("*/sources/*.part"):
            try:
                part.unlink()
                removed.append(str(part))
            except OSError:
                continue
        return removed

    def iter_projects(self) -> Iterator[Project]:
        for path in sorted(self.root.glob("*/project.json")):
            yield Project(path.parent.name, path.parent)
