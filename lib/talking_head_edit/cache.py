"""Stage-level caching.

Why it matters: the director call costs money and the resolve/render stages
cost minutes. Re-running a job after a prompt tweak must only redo the stages
whose inputs actually changed, otherwise the prompt-iterate loop is unusable.

A stage is cached when the sha256 of its declared inputs is unchanged AND all
its declared outputs still exist on disk.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lib.talking_head_edit.job_store import REPO_ROOT, Job

# Transcripts are keyed by the source file's content hash, not by job, so the
# same footage re-submitted (new prompt, new job) never re-runs Whisper.
TRANSCRIPT_CACHE_DIR = REPO_ROOT / "output" / "transcript_cache"


def file_sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def hash_inputs(payload: Any) -> str:
    """Stable hash of a JSON-serialisable stage input set."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StageCache:
    def __init__(self, job: Job):
        self.path = job.dir / "cache.json"

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def is_fresh(self, stage: str, input_hash: str, outputs: list[Path]) -> bool:
        if self._load().get(stage) != input_hash:
            return False
        return all(Path(p).exists() for p in outputs)

    def mark(self, stage: str, input_hash: str) -> None:
        data = self._load()
        data[stage] = input_hash
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def invalidate(self, *stages: str) -> None:
        data = self._load()
        for stage in stages:
            data.pop(stage, None)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def transcript_cache_path(input_sha: str, model_size: str, language: str,
                          provider: str = "whisper_local") -> Path:
    """Where a transcript for this (file, provider, model, language) lives.

    `provider` joined the key when a second ASR engine arrived: two engines
    transcribing the same file produce different word boundaries, so one cache
    slot for both would serve Scribe's spine to a job that asked for Whisper's.
    """
    TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return (TRANSCRIPT_CACHE_DIR /
            f"{input_sha[:16]}_{provider}_{model_size}_{language}.json")


def legacy_transcript_cache_path(input_sha: str, model_size: str, language: str) -> Path:
    """The pre-provider filename. Read-only — nothing writes this layout now."""
    TRANSCRIPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TRANSCRIPT_CACHE_DIR / f"{input_sha[:16]}_{model_size}_{language}.json"


def find_cached_transcript(input_sha: str, provider: str, model_size: str,
                           language: str) -> Path | None:
    """Existing transcript for this request, or None.

    Falls back to the pre-provider filename for Whisper so transcripts cached
    before the ASR split still hit — re-running Whisper `medium` on footage that
    was already transcribed costs minutes for nothing.
    """
    current = transcript_cache_path(input_sha, model_size, language, provider)
    if current.exists():
        return current
    if provider == "whisper_local":
        legacy = legacy_transcript_cache_path(input_sha, model_size, language)
        if legacy.exists():
            return legacy
    return None
