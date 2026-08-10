"""Pipeline sync hooks: mirror a finished job/project directory into R2.

Sync fires at job end only (Q2, locked 2026-08-07) -- no per-stage call site,
so the round-trip cost never lands inside the interactive prompt-iterate
loop the pipeline is tuned for. `auto_sync` stays `false` on a clean
checkout; flipping that one config key is what turns syncing on.

Every hook call is wrapped in a blanket try/except: `run_stage` re-raises on
failure, so a hook that raises would turn an R2 outage into a lost render.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

_cached_settings: Any = None
_cache_loaded = False


def reset_cache() -> None:
    """Force the next _gate() call to re-read config/r2-storage.json + env."""
    global _cached_settings, _cache_loaded
    _cached_settings = None
    _cache_loaded = False


def _gate() -> Any | None:
    """Load settings once per process. Returns None fast when disabled -- no
    file I/O on the hot (disabled) path after the first call.

    `resolve()` can raise `R2ConfigError` on a malformed value (e.g. a typo'd
    `CLOUDFLARE_R2_PUBLIC_BASE_URL`) -- unconditionally, regardless of
    `enabled`. That must degrade to "sync skipped", never propagate: this is
    the one place a config error could otherwise turn a finished render into
    a raised exception, which is exactly the contract this module exists to
    prevent (see module docstring).
    """
    global _cached_settings, _cache_loaded
    if not _cache_loaded:
        try:
            from lib.r2_storage.config import resolve

            _cached_settings = resolve()
        except Exception as exc:  # noqa: BLE001 -- see docstring: must never propagate
            _logger.warning("R2 config lỗi, bỏ qua sync lần này: %s", exc)
            _cached_settings = None
        _cache_loaded = True
    settings = _cached_settings
    if settings is None or not settings.enabled or not settings.auto_sync:
        return None
    return settings


def maybe_sync_job(job: Any, trigger: str = "job_end") -> None:
    """No-op (zero network) unless enabled+auto_sync. Only "job_end" is wired
    (Q2) -- `trigger` is kept in the signature so a stage hook could be added
    later without reshaping callers."""
    settings = _gate()
    if settings is None:
        return
    try:
        from lib.r2_storage.sync import apply_sync, plan_sync

        prefix = f"{settings.prefix}/autoedit-jobs/{job.job_id}"
        plan = plan_sync(job.dir, prefix, settings)
        result = apply_sync(plan, settings)
        job.emit("r2_sync", message=f"R2 sync: {result.uploaded} uploaded, {result.skipped} skipped",
                  uploaded=result.uploaded, skipped=result.skipped, bytes=result.bytes, prefix=prefix)
    except Exception as exc:  # noqa: BLE001 -- a sync failure must never fail a finished render
        job.emit("warning", message=f"R2 sync thất bại: {exc.__class__.__name__}: {exc}")


def maybe_sync_project(project: Any) -> None:
    """Mirrors a project's directory to `<prefix>/autoedit/<project_id>`."""
    settings = _gate()
    if settings is None:
        return
    try:
        from lib.r2_storage.sync import apply_sync, plan_sync

        prefix = f"{settings.prefix}/autoedit/{project.project_id}"
        plan = plan_sync(project.dir, prefix, settings)
        apply_sync(plan, settings)
    except Exception as exc:  # noqa: BLE001 -- same contract as maybe_sync_job
        _logger.warning("R2 project sync thất bại (%s): %s", project.project_id, exc)
