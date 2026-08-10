"""Job workspace: state file, progress events, artifact paths.

Jobs live under `projects/autoedit-jobs/<job_id>/`, deliberately NOT inside
`remotion-composer/public/`: Remotion copies the entire public directory into
its bundle on every render (measured at 415 MB once a couple of jobs existed),
so job media there taxes every subsequent render. The render stage instead
stages a tiny per-job public dir, and the web server serves the same files to
the <Player>, so preview and render still read identical bytes.

State is two files, both append-safe:
  job.json        current snapshot (stages, options, versions)
  events.jsonl    append-only progress log — the server tails this, so a job
                  keeps reporting progress even with no UI attached.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
JOBS_ROOT = REPO_ROOT / "projects" / "autoedit-jobs"
# Projects keep their builds inside themselves. Spelled out here rather than
# imported from `project_store`, which imports this module — a path constant is
# a cheaper price than a circular import.
PROJECTS_ROOT = REPO_ROOT / "projects" / "autoedit"
SHARED_PUBLIC = REPO_ROOT / "remotion-composer" / "public"   # sfx_*.mp3, bgm_*.mp3

# `select` chooses between takes of the same content. It sits after transcribe
# because it needs the words, and before direct because the director must see
# only the words that survived. With one source it is a no-op that records why.
STAGES = ["probe", "transcribe", "select", "direct", "audit", "calibrate",
          "resolve", "render", "verify"]

DEFAULT_OPTIONS: dict[str, Any] = {
    "prompt": "",
    # Empty = fall through to director_client.default_model() (AUTOEDIT_DIRECTOR_MODEL
    # env var). A literal here used to shadow that env var for every job that didn't
    # pass --model explicitly, since a truthy dict value short-circuits the "or" in
    # `options.get("model") or default_model()` before the env var is ever read.
    "model": "",
    # Empty = reuse "model" for the audit-stage cut verifier too. Set separately to
    # route that narrow keep/remove judgment call to a cheaper/faster model without
    # touching the director's structure/caption calls.
    "verifier_model": "",
    "language": "vi",
    # Scribe is the default because it turns transcribe from a several-minute
    # local inference into one ~10-30s API call, and that latency is what makes
    # the prompt-iterate loop usable. `whisper_local` stays reachable for
    # offline work — and is what the fallback uses when Scribe is unreachable.
    "asr_provider": "elevenlabs_scribe",
    "asr_model": "scribe_v2",
    "whisper_model": "medium",     # the fallback branch's model, not Scribe's
    # Empty = mine `topic` + `card_plan`. Vietnamese ASR mishears brand names
    # and jargon far more than ordinary words, and this is the cheap fix.
    "keyterms": [],
    # Job-level layer of the assembly config. Empty = inherit project, then the
    # global `config/autoedit-defaults.json`. See assembly_config.resolve.
    "assembly": {},
    "tempo": 1.06,
    "cold_open": True,
    "bgm": True,
    "topic": "",
    "brand_pill": "",
    "card_plan": "",
    "style_profile": "styles/user-edit-profile.json",
    "frame_preset": "dark",
    "intermediate_preset": "medium",   # detail retention beats encode speed here
    # The intermediate never leaves the machine, so compressing it hard buys
    # nothing and costs sharpness: at 12 instead of 17 the deliverable measured
    # 2.85 vs 2.65 on a face crop, for 1 MB per 5 seconds of scratch space.
    # `render_crf` is the one that decides the deliverable's size — 12 there
    # gains a further ~16% but triples the file.
    "intermediate_crf": 12,
    "render_crf": 17,
    # Remotion's own default is 80, which softens the frame before x264 ever
    # sees it. This intermediate is thrown away, so don't compress it.
    "render_jpeg_quality": 100,
    # Measure each video's own softness and set the sharpening from it,
    # rather than reusing a number tuned on one clip. Skipped when a human
    # has set `sharpen` explicitly in grade_overrides.
    "auto_sharpen": True,
    # Measure this footage's own exposure and colour cast instead of trusting
    # the director's guess. Skipped per-key when a human has set that exact
    # key (brightness/gamma/warmth) in grade_overrides — same rule as
    # auto_sharpen. See grade_calibrate.py.
    "auto_grade": True,
    "audio_preset": "shotgun",   # off | voice | voice_strong | shotgun | shotgun_dry
    # Measure this take's own room decay to pick shotgun vs shotgun_dry,
    # overriding `audio_preset` above. Set False to pick one of the 5 presets
    # by hand instead (voice/voice_strong/off are not part of this decision).
    # See audio_calibrate.py.
    "auto_audio_preset": True,
    # Off by default since a blind test showed the stage was reading its own
    # labels, not the media (see stages/calibrate.py). Turn on only after
    # `audio_hearing_check` passes for the model in use.
    "calibrate_grade": False,
    "calibrate_audio": False,
    "calibrate_at": None,        # giây trong footage gốc; None = giữa lời nói
    "width": 1080,
    "height": 1920,
    "fps": 30,
}


def option_enabled(options: dict[str, Any], name: str, default: bool = True) -> bool:
    """Read a boolean option, treating an explicit null as "not set".

    `options.get(name, True)` looks equivalent but is not: it returns None when
    the key exists holding null, and None is falsy, so the feature turns itself
    off with nothing in any log to say why. A JSON client or an HTML form sends
    `{"bgm": null}` far more readily than it omits the key — `POST /run` merges
    whatever it is given straight into the stored options — so the null path is
    the likely one, not the exotic one.
    """
    value = options.get(name)
    return default if value is None else bool(value)


def job_input_paths(state: dict[str, Any]) -> list[Path]:
    """Every source path of a job, old and new layouts alike.

    A job created before multi-source has a single `input_path`; one created
    after has `input_paths`. Wrapping the fallback here — rather than writing
    `state.get("input_paths") or [state["input_path"]]` in a dozen places —
    is what keeps old jobs openable without a migration.
    """
    paths = state.get("input_paths")
    if paths:
        return [Path(p) for p in paths]
    single = state.get("input_path")
    return [Path(single)] if single else []


def primary_input_path(state: dict[str, Any]) -> Path:
    """The first speaking source — what single-source code paths mean by "the
    input". Kept so preview/calibrate need no multi-source awareness."""
    paths = job_input_paths(state)
    if not paths:
        raise ValueError("Job không có nguồn nào (thiếu input_paths/input_path)")
    return paths[0]


def slugify(text: str, max_len: int = 40) -> str:
    """ASCII kebab slug; Vietnamese diacritics folded so paths stay portable."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].strip("-") or "job")


class Job:
    """One auto-edit run. All paths derive from the job directory."""

    def __init__(self, job_id: str, dir_path: Path):
        self.job_id = job_id
        self.dir = dir_path

    # ---- paths -----------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.dir / "job.json"

    @property
    def events_path(self) -> Path:
        return self.dir / "events.jsonl"

    @property
    def spine_path(self) -> Path:
        return self.dir / "spine.json"

    @property
    def src_path(self) -> Path:
        return self.dir / "src.mp4"

    @property
    def preview_src_path(self) -> Path:
        """A lightweight re-encode of `src_path`, for the browser Player only.

        `src_path` is intermediate_crf (12 by default — near-lossless, tens of
        Mbps) because that quality is what the renderer reads frames from.
        Streaming that same file to a `<video>` tag over HTTP is a different
        job: Chrome's decoder falls behind a 50-70 Mbps 1080x1920 stream,
        `OffthreadVideo`'s `pauseWhenBuffering` default keeps stalling to
        rebuffer, and audio — tied to the same paused state — drops out with
        it. The real render never reads this file (`stage_assets` stages
        `src_path`, not this), so render quality is unaffected.
        """
        return self.dir / "preview_src.mp4"

    @property
    def final_path(self) -> Path:
        return self.dir / "final.mp4"

    def spec_path(self, version: int) -> Path:
        return self.dir / f"spec_v{version}.json"

    def props_path(self, version: int) -> Path:
        return self.dir / f"props_v{version}.json"

    def log_path(self, stage: str) -> Path:
        return self.dir / "logs" / f"{stage}.log"

    @property
    def render_public_dir(self) -> Path:
        """Tiny staged public dir handed to Remotion via --public-dir."""
        return self.dir / "render_public"

    def rel(self, path: Path) -> str:
        """Path relative to the job dir — how the API and UI refer to artifacts."""
        return path.resolve().relative_to(self.dir.resolve()).as_posix()

    # ---- state -----------------------------------------------------------
    def load(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: dict[str, Any]) -> None:
        state["updated_at"] = time.time()
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def update(self, **fields: Any) -> dict[str, Any]:
        state = self.load()
        state.update(fields)
        self.save(state)
        return state

    def set_stage(self, stage: str, **fields: Any) -> dict[str, Any]:
        state = self.load()
        entry = state["stages"].setdefault(stage, {})
        entry.update(fields)
        self.save(state)
        return state

    # ---- progress --------------------------------------------------------
    def emit(self, kind: str, stage: str = "", message: str = "", **extra: Any) -> None:
        """Append one progress event. Never raises — progress must not kill a job."""
        event = {"ts": round(time.time(), 3), "type": kind, "stage": stage, "message": message}
        event.update(extra)
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def read_events(self, offset: int = 0) -> Iterator[dict[str, Any]]:
        if not self.events_path.exists():
            return iter(())

        def gen() -> Iterator[dict[str, Any]]:
            with open(self.events_path, encoding="utf-8") as fh:
                for index, line in enumerate(fh):
                    if index < offset or not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

        return gen()


class JobStore:
    """Creates and lists jobs. One directory per job, no database."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else JOBS_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, input_path: str | Path | list[str | Path],
               options: dict[str, Any] | None = None,
               title: str = "", project_id: str | None = None) -> Job:
        """One job over one or more sources.

        `input_path` still accepts a single path so existing callers (CLI, API,
        tests) keep working; a list makes a multi-source job. Both write
        `input_paths`, and `input_path` is written too so that a job created here
        stays readable by any code that has not been updated yet.
        """
        raw = input_path if isinstance(input_path, list) else [input_path]
        paths = [str(Path(p).resolve()) for p in raw]
        if not paths:
            raise ValueError("Job cần ít nhất một nguồn")

        opts = {**DEFAULT_OPTIONS, **(options or {})}
        stamp = time.strftime("%y%m%d-%H%M%S")
        stem = Path(paths[0]).stem
        job_id = f"{slugify(title or stem)}-{stamp}"
        job = Job(job_id, self.root / job_id)
        (job.dir / "logs").mkdir(parents=True, exist_ok=True)
        job.save({
            "job_id": job_id,
            "title": title or stem,
            "created_at": time.time(),
            "project_id": project_id,
            "input_paths": paths,
            # Kept in sync with input_paths[0] for readers that predate
            # multi-source; never the source of truth (see job_input_paths).
            "input_path": paths[0],
            "options": opts,
            "status": "queued",
            "current_version": 0,
            "versions": [],
            "stages": {name: {"status": "pending"} for name in STAGES},
            "cost_usd": 0.0,
        })
        count = f" ({len(paths)} nguồn)" if len(paths) > 1 else ""
        job.emit("job_created", message=f"Job {job_id} tạo xong{count}")
        return job

    def get(self, job_id: str) -> Job:
        job = Job(job_id, self.root / job_id)
        if not job.state_path.exists():
            raise FileNotFoundError(f"Job không tồn tại: {job_id}")
        return job

    def list(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*/job.json"), reverse=True):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out


def job_roots(legacy_root: Path | None = None,
              projects_root: Path | None = None) -> list[Path]:
    """Every directory that can contain job folders.

    Legacy jobs stay where they are: migrating a directory of finished renders to
    gain tidiness risks data for nothing, so both layouts are simply read.
    """
    roots = [Path(legacy_root) if legacy_root else JOBS_ROOT]
    projects = Path(projects_root) if projects_root else PROJECTS_ROOT
    if projects.exists():
        roots += sorted(p for p in projects.glob("*/jobs") if p.is_dir())
    return [r for r in roots if r.exists()]


def find_job(job_id: str, legacy_root: Path | None = None,
             projects_root: Path | None = None) -> Job:
    """A job by id, wherever it lives.

    Every entry point that takes a job id — the CLI, the queue worker, the API —
    goes through here, so a build inside a project is reachable by the same id the
    UI shows without any caller knowing which layout it is in.
    """
    for root in job_roots(legacy_root, projects_root):
        candidate = Job(job_id, root / job_id)
        if candidate.state_path.exists():
            return candidate
    raise FileNotFoundError(f"Job không tồn tại: {job_id}")


def list_all_jobs(legacy_root: Path | None = None,
                  projects_root: Path | None = None) -> list[dict[str, Any]]:
    """Job states from every root, newest first, each tagged with where it lives.

    Ids are unique in practice (they end in a timestamp), but the layout is
    reported anyway: two roots merged into one list is exactly where a duplicate
    id would become an impossible bug to read.
    """
    out: list[dict[str, Any]] = []
    for root in job_roots(legacy_root, projects_root):
        legacy = root == (Path(legacy_root) if legacy_root else JOBS_ROOT)
        for path in root.glob("*/job.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            state.setdefault("project_id", None)
            state["is_legacy"] = legacy
            out.append(state)
    return sorted(out, key=lambda s: float(s.get("created_at") or 0), reverse=True)
