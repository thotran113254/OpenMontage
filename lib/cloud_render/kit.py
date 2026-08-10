"""Autoedit render-kit packager, split into two independent halves.

`build_composer_kit()` packages the Remotion composer itself
(`package.json`/`package-lock.json`/`tsconfig.json`/`src/`, ~623 KB) --
identical for every job, uploaded and `npm ci`-ed exactly once per rental
regardless of how many jobs share it (see `remote.render_batch`).
`build_job_kit()` packages one job's resolved props (renamed `props.json`)
and its tiny staged public dir -- the only per-job payload, uploaded into
that job's own remote subdirectory so N jobs can share one rental without
seeing each other's assets.

This is an explicit **allowlist**, not an exclude list: `remotion-composer/
public/` (349 MB measured, shared across every job) and `.env` are never
copied because they are simply never named. The rented box is untrusted
third-party hardware -- see the phase's Security section.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT

COMPOSER_DIR = REPO_ROOT / "remotion-composer"
# Only these composer paths ever enter a kit. `node_modules/` is deliberately
# excluded -- `npm ci` reinstalls it remotely from `package-lock.json`, which
# is exactly what proves the remote install matches the local one (see
# `remote.py`'s docstring on why `npm ci`, not `npm install`).
COMPOSER_ALLOWLIST = ("package.json", "package-lock.json", "tsconfig.json", "src")
PROPS_FILENAME = "props.json"
PUBLIC_DIRNAME = "public"
OUTPUT_RELATIVE_PATH = "out/final.mp4"
COMPOSITION_ID = "MonaTimeline"


class KitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComposerKitManifest:
    """The shared half of a kit -- uploaded once per rental, `npm ci`-ed
    once, and reused by every job in a batch."""
    kit_dir: Path
    kit_hash: str
    size_bytes: int
    file_count: int


@dataclass(frozen=True)
class JobKitManifest:
    """The per-job half of a kit -- one job's props + staged public dir,
    uploaded into its own remote subdirectory (`/root/kit/jobs/<job_id>/`)."""
    job_id: str
    kit_dir: Path
    kit_hash: str
    size_bytes: int
    file_count: int
    composition_id: str
    props_path: str            # relative to kit_dir, e.g. "props.json"
    public_dir: str            # relative to kit_dir, e.g. "public"
    expected_output_name: str  # relative to the job's remote dir, e.g. "out/final.mp4"
    estimated_render_seconds: float


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True)


def _iter_kit_files(kit_dir: Path):
    for path in sorted(kit_dir.rglob("*")):
        if path.is_file():
            yield path


def _hash_kit(kit_dir: Path) -> str:
    """sha256 over sorted relative-path + content -- the idempotency field
    used to detect "this exact kit already built/uploaded" (composer kits
    are effectively pinned by `package-lock.json`'s content since that
    dominates what changes between composer revisions)."""
    digest = hashlib.sha256()
    for path in _iter_kit_files(kit_dir):
        rel = path.relative_to(kit_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_composer_kit(*, composer_dir: Path = COMPOSER_DIR) -> ComposerKitManifest:
    """Allowlist-copy the composer's own files into a fresh
    `tempfile.mkdtemp()`. Contains no job data at all -- safe to build once
    and reuse across every job in a batch."""
    kit_dir = Path(tempfile.mkdtemp(prefix="openmontage-composer-kit-"))
    try:
        for name in COMPOSER_ALLOWLIST:
            source = composer_dir / name
            if not source.exists():
                raise KitError(f"Thiếu file composer cần cho kit: {source}")
            target = kit_dir / name
            if source.is_dir():
                _copy_tree(source, target)
            else:
                shutil.copyfile(source, target)
    except Exception:
        shutil.rmtree(kit_dir, ignore_errors=True)
        raise

    files = list(_iter_kit_files(kit_dir))
    return ComposerKitManifest(
        kit_dir=kit_dir,
        kit_hash=_hash_kit(kit_dir),
        size_bytes=sum(p.stat().st_size for p in files),
        file_count=len(files),
    )


def build_job_kit(job: Any, version: int, *,
                  render_seconds_per_video_second: float = 1.9) -> JobKitManifest:
    """Package one job's resolved props (renamed `props.json`) and its
    staged public dir into a fresh `tempfile.mkdtemp()`.

    Requires `stage_assets` to have already run for this version (the render
    stage always calls it before rendering locally, so a job that has ever
    rendered -- or is about to -- already has this staged dir).
    """
    props_path = job.props_path(version)
    if not props_path.exists():
        raise KitError(f"Chưa có props v{version} cho job {job.job_id}")

    staging_dir = job.render_public_dir
    if not staging_dir.exists():
        raise KitError(
            f"Chưa có staging public dir ({staging_dir}) -- chạy stage_assets trước")

    kit_dir = Path(tempfile.mkdtemp(prefix="openmontage-job-kit-"))
    try:
        shutil.copyfile(props_path, kit_dir / PROPS_FILENAME)
        _copy_tree(staging_dir, kit_dir / PUBLIC_DIRNAME)
    except Exception:
        shutil.rmtree(kit_dir, ignore_errors=True)
        raise

    props = json.loads(props_path.read_text(encoding="utf-8"))
    duration_seconds = float(props.get("durationSeconds") or 0.0)

    files = list(_iter_kit_files(kit_dir))
    return JobKitManifest(
        job_id=job.job_id,
        kit_dir=kit_dir,
        kit_hash=_hash_kit(kit_dir),
        size_bytes=sum(p.stat().st_size for p in files),
        file_count=len(files),
        composition_id=COMPOSITION_ID,
        props_path=PROPS_FILENAME,
        public_dir=PUBLIC_DIRNAME,
        expected_output_name=OUTPUT_RELATIVE_PATH,
        # Baseline estimate at a "reference core count" of 1x; render_now/
        # render_batch scale this by (reference_cores / offer_cores) once an
        # offer is chosen -- see lib/cloud_render/remote.py.
        estimated_render_seconds=round(duration_seconds * render_seconds_per_video_second, 1),
    )


def cleanup_kit(manifest: ComposerKitManifest | JobKitManifest) -> None:
    """Remove the temp kit dir (composer or job kit, either shape works via
    duck typing on `.kit_dir`). Best-effort: a stray temp dir costs disk, not
    correctness, so this never raises."""
    shutil.rmtree(manifest.kit_dir, ignore_errors=True)
