"""Projects: sources uploaded once, reused by many builds.

Deliberately no database. One directory per entity with a JSON file in it, the
same shape as `JobStore` — a dev machine holds a few dozen projects, and a schema
plus migrations would be pure cost.

```
projects/autoedit/<project_id>/
├── project.json
├── sources/  s0_take-1.mp4  s0.transcript.json  s0.thumb.jpg
└── jobs/<job_id>/          exactly the job layout, unchanged
```

The one thing worth building here is that sources belong to the PROJECT: today a
second build of the same footage means uploading the file again, and while the
transcript cache is shared by content hash, the video itself is duplicated on
disk.

Legacy jobs under `projects/autoedit-jobs/` are read, never moved. Migrating
files would risk data to gain tidiness.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from lib.talking_head_edit.job_store import (
    REPO_ROOT,
    STAGES,
    JobStore,
    slugify,
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
           "ProjectStore"]


class ProjectError(RuntimeError):
    pass


class Project:
    """One shoot: its sources, its shared settings, and its builds."""

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
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)

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
        }
        state.setdefault("sources", []).append(spec)
        self.save(state)
        return spec

    def update_source(self, source_id: str, **fields: Any) -> dict[str, Any]:
        """Change the human-owned metadata of one source.

        Only these four: everything else is measured, and letting a client
        overwrite a measurement would put a guess where a fact was.
        """
        allowed = {"role", "order", "take_group", "label"}
        unknown = set(fields) - allowed
        if unknown:
            raise ProjectError(
                f"Không sửa được: {', '.join(sorted(unknown))}. "
                f"Chỉ sửa được: {', '.join(sorted(allowed))}")

        state = self.load()
        for spec in state.get("sources", []):
            if str(spec.get("id")) == source_id:
                spec.update({k: v for k, v in fields.items() if v is not None})
                if "role" in fields and fields["role"] is not None:
                    if fields["role"] not in ("aroll", "broll"):
                        raise ProjectError("role phải là 'aroll' hoặc 'broll'")
                    # A human calling something b-roll overrides the measurement.
                    spec["speech"] = fields["role"] == "aroll" and spec.get("speech", True)
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

    def create_job(self, options: dict[str, Any] | None = None,
                   title: str = "") -> Any:
        """A build of this project. Sources come from the project, in order."""
        state = self.load()
        from lib.talking_head_edit import sources as sources_mod

        rows = [{**spec, "path": str(self.source_path(spec))}
                for spec in state.get("sources", [])]
        speaking = sources_mod.aroll(rows)
        overlays = sources_mod.broll(rows)
        if not speaking:
            raise ProjectError(
                "Project chưa có nguồn nào có lời nói. Pipeline neo theo lời nói "
                "nên cần ít nhất một nguồn A-roll.")

        # B-roll goes in too. `probe` classifies each source and `spine_build`
        # routes the silent ones to `overlay_pool` instead of the word spine — so
        # handing over only the speaking sources is what would silently drop every
        # b-roll clip from the build.
        #
        # A-roll first, because `primary_input_path` (used by grade preview and
        # calibrate) means "the footage", and that has to be a speaking source.
        job = self.job_store().create(
            [row["path"] for row in speaking + overlays],
            self.job_options(options),
            title=title or state.get("title", ""),
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
        ], project=state)

        state.setdefault("jobs", []).append(job.job_id)
        self.save(state)
        return job

    def builds(self) -> list[dict[str, Any]]:
        """Every build with just enough to compare them in a list."""
        out: list[dict[str, Any]] = []
        for job_state in self.job_store().list():
            job_id = str(job_state.get("job_id"))
            job_dir = self.jobs_dir / job_id
            out.append({
                "job_id": job_id,
                "title": job_state.get("title"),
                "status": job_state.get("status"),
                "created_at": job_state.get("created_at"),
                "current_version": job_state.get("current_version", 0),
                "cost_usd": job_state.get("cost_usd", 0.0),
                "has_final": (job_dir / "final.mp4").exists(),
                "prompt": (job_state.get("options") or {}).get("prompt", ""),
                "stages": {name: (job_state.get("stages") or {}).get(name, {}).get("status")
                           for name in STAGES},
            })
        return out


class ProjectStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else PROJECTS_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, title: str, assembly: dict[str, Any] | None = None,
               keyterms: list[str] | None = None,
               defaults: dict[str, Any] | None = None) -> Project:
        stamp = time.strftime("%y%m%d-%H%M%S")
        project_id = f"{slugify(title or 'project')}-{stamp}"
        project = Project(project_id, self.root / project_id)
        project.sources_dir.mkdir(parents=True, exist_ok=True)
        project.jobs_dir.mkdir(parents=True, exist_ok=True)
        project.save({
            "project_id": project_id,
            "title": title or project_id,
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
            first_thumb = next((s.get("thumb") for s in sources if s.get("thumb")), None)
            out.append({
                "project_id": state.get("project_id"),
                "title": state.get("title"),
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "source_count": len(sources),
                "aroll_count": sum(1 for s in sources if s.get("role", "aroll") == "aroll"),
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
