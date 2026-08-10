"""Durable batch queue + one-rental flush.

Batch exists for one reason: the fixed overhead (boot ~60 s + apt + `npm ci`
~10 s + transfers) measured at roughly 5-7 min of a manual run's 7-8 min
lifetime. On a single 15-second job the overhead *is* the cost; amortized
over 5 jobs it is ~1 min each.

`server/queue_worker.py`'s in-process `queue.Queue` is deliberately not
reused here: restarting the server loses every pending item, so a batch that
can wait hours for a threshold needs an on-disk queue instead. This module
also executes renders on a *rented* box, never as a local CLI subprocess.

The flush *decision* is not Python's: `flush_check()` returns facts
(`job_count`, `estimated_render_minutes`, `oldest_age_minutes`, which
thresholds are met) and never rents anything as a side effect. Whether to
call `flush()` is a policy call for the caller (agent skill), per
AGENT_GUIDE's "Python = tools + persistence" rule.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lib.cloud_render import kit, ledger, onstart, remote, setup_key, vast_client
from lib.cloud_render.config import REPO_ROOT
from lib.talking_head_edit.job_store import find_job

STATE_DIR = REPO_ROOT / "projects" / "cloud-render"
QUEUE_PATH = STATE_DIR / "batch-queue.json"
LOCK_PATH = STATE_DIR / "batch-queue.lock"

# Three writers exist for the queue file (CLI, FastAPI server, agent) --
# a lock file with a stale timeout beats last-write-wins, which can
# silently drop a concurrent enqueue.
LOCK_STALE_SECONDS = 30.0
LOCK_ACQUIRE_TIMEOUT_S = 10.0
LOCK_POLL_INTERVAL_S = 0.05

STATUS_PENDING = "pending"
STATUS_RENDERING = "rendering"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"

MAX_ATTEMPTS = 3

# Measured fixed cost of one rental's boot + apt + `npm ci`, in minutes --
# the number the whole batch amortization argument rests on (see module
# docstring). Used both for the deadline estimate and the cost comparison.
OVERHEAD_MINUTES = 6.0
# Safety multiplier applied to the batch's own render-time estimate when
# sizing the deadline -- estimates run low more often than high.
RENDER_TIME_SAFETY_FACTOR = 1.5


class QueueLockError(RuntimeError):
    pass


class FlushError(RuntimeError):
    """Nothing could be rented/rendered at all (no eligible offer, or every
    job's kit failed to build) -- distinct from a partial batch, which is a
    correct outcome represented by `BatchResult.pending`/`.failed`."""


@dataclass
class QueueEntry:
    job_id: str
    project_id: str | None
    version_at_enqueue: int
    enqueued_at: str
    estimated_render_seconds: float
    duration_seconds: float
    note: str
    status: str = STATUS_PENDING
    attempts: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueueEntry":
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass(frozen=True)
class BatchResult:
    rendered: list[str]
    failed: dict[str, str]
    pending: list[str]
    blocked: list[str]
    instance_id: int | None
    offer_id: int | None
    actual_usd: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_epoch(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                    .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Lock file (stale-timeout) -- guards the read-modify-write below, never held
# across a rental's lifetime.
# ---------------------------------------------------------------------------

@contextmanager
def _locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_ACQUIRE_TIMEOUT_S
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
            except OSError:
                age = LOCK_STALE_SECONDS + 1  # lock vanished mid-check -- treat as gone, retry
            if age > LOCK_STALE_SECONDS:
                LOCK_PATH.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise QueueLockError(
                    f"không lấy được lock {LOCK_PATH} sau {LOCK_ACQUIRE_TIMEOUT_S}s "
                    "(một writer khác đang giữ lock)")
            time.sleep(LOCK_POLL_INTERVAL_S)
    try:
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Atomic read/write of batch-queue.json -- same temp+os.replace shape as the
# ledger, so a crash mid-write never corrupts the previously-committed file.
# ---------------------------------------------------------------------------

def _read_state() -> dict[str, Any]:
    if not QUEUE_PATH.exists():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"version": 1, "entries": []}
    return data


def _write_state(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = QUEUE_PATH.with_name(QUEUE_PATH.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, QUEUE_PATH)


# ---------------------------------------------------------------------------
# Public queue API
# ---------------------------------------------------------------------------

def enqueue(job_id: str, *, version: int, estimated_render_seconds: float,
           duration_seconds: float = 0.0, note: str = "",
           project_id: str | None = None) -> QueueEntry:
    """Upsert on `job_id` -- a re-enqueue (e.g. after `--revise`) updates the
    existing entry in place rather than duplicating it. `enqueued_at` is only
    set for a genuinely new entry, so re-enqueuing a revised job does not
    reset its place in the `max_wait_minutes` queue."""
    with _locked():
        data = _read_state()
        entries = data["entries"]
        for raw in entries:
            if raw.get("job_id") == job_id:
                raw.update({
                    "version_at_enqueue": version,
                    "estimated_render_seconds": estimated_render_seconds,
                    "duration_seconds": duration_seconds,
                    "note": note,
                    "project_id": project_id,
                })
                _write_state(data)
                return QueueEntry.from_dict(raw)

        entry = QueueEntry(
            job_id=job_id, project_id=project_id, version_at_enqueue=version,
            enqueued_at=_now_iso(), estimated_render_seconds=estimated_render_seconds,
            duration_seconds=duration_seconds, note=note)
        entries.append(entry.to_dict())
        _write_state(data)
        return entry


def list_entries(*, prune_missing: bool = True) -> list[QueueEntry]:
    """Every queued entry, oldest ledger-order (insertion order) preserved.

    A queue entry whose job directory has disappeared is dropped here (with
    a warning) rather than left to explode at flush time.
    """
    with _locked():
        data = _read_state()
        raw_entries = data["entries"]
        if not prune_missing:
            return [QueueEntry.from_dict(r) for r in raw_entries]

        kept: list[dict[str, Any]] = []
        dropped = False
        for raw in raw_entries:
            job_id = raw.get("job_id")
            try:
                find_job(job_id)
            except FileNotFoundError:
                dropped = True
                warnings.warn(
                    f"batch queue: job_id={job_id!r} không còn thư mục job -- "
                    "loại khỏi queue", stacklevel=2)
                continue
            kept.append(raw)
        if dropped:
            _write_state({"version": 1, "entries": kept})
        return [QueueEntry.from_dict(r) for r in kept]


def remove(job_id: str) -> bool:
    with _locked():
        data = _read_state()
        entries = data["entries"]
        remaining = [r for r in entries if r.get("job_id") != job_id]
        if len(remaining) == len(entries):
            return False
        _write_state({"version": 1, "entries": remaining})
        return True


def clear() -> None:
    with _locked():
        _write_state({"version": 1, "entries": []})


def _set_status(job_ids: list[str], status: str) -> None:
    if not job_ids:
        return
    with _locked():
        data = _read_state()
        for raw in data["entries"]:
            if raw.get("job_id") in job_ids:
                raw["status"] = status
        _write_state(data)


def _record_failures(failed: dict[str, str]) -> list[str]:
    """Increment `attempts` for each failed job_id and set `last_error`;
    at `MAX_ATTEMPTS` the entry becomes `blocked` instead of retried forever.
    Only touches entries that exist in the queue (a force-flushed job that
    was never enqueued has nothing to update). Returns job_ids newly
    `blocked`."""
    if not failed:
        return []
    blocked_now: list[str] = []
    with _locked():
        data = _read_state()
        for raw in data["entries"]:
            job_id = raw.get("job_id")
            if job_id not in failed:
                continue
            attempts = int(raw.get("attempts") or 0) + 1
            raw["attempts"] = attempts
            raw["last_error"] = failed[job_id]
            if attempts >= MAX_ATTEMPTS:
                raw["status"] = STATUS_BLOCKED
                blocked_now.append(job_id)
            else:
                raw["status"] = STATUS_FAILED
        _write_state(data)
    return blocked_now


def flush_check(config: dict[str, Any]) -> dict[str, Any]:
    """Pure computation: thresholds + cost comparison, zero network calls,
    zero instances created. The flush *decision* belongs to the caller."""
    entries = list_entries()
    job_count = len(entries)
    total_render_seconds = sum(e.estimated_render_seconds for e in entries)
    estimated_render_minutes = round(total_render_seconds / 60.0, 2)

    now = time.time()
    oldest_age_minutes = 0.0
    if entries:
        epochs = [_parse_iso_epoch(e.enqueued_at) for e in entries]
        oldest_epoch = min(epoch for epoch in epochs if epoch is not None) if any(
            epoch is not None for epoch in epochs) else now
        oldest_age_minutes = round(max(0.0, now - oldest_epoch) / 60.0, 1)

    batch_cfg = config.get("batch") or {}
    thresholds = {
        "min_jobs": batch_cfg.get("min_jobs", 3),
        "min_total_render_minutes": batch_cfg.get("min_total_render_minutes", 20),
        "max_wait_minutes": batch_cfg.get("max_wait_minutes", 240),
    }
    thresholds_met = []
    if job_count >= thresholds["min_jobs"]:
        thresholds_met.append("min_jobs")
    if estimated_render_minutes >= thresholds["min_total_render_minutes"]:
        thresholds_met.append("min_total_render_minutes")
    if entries and oldest_age_minutes >= thresholds["max_wait_minutes"]:
        thresholds_met.append("max_wait_minutes")

    dph = float(config.get("max_dph_usd", 0.15))
    if job_count == 0:
        estimated_cost_usd = 0.0
        estimated_cost_if_separate = 0.0
    else:
        overhead_hours = OVERHEAD_MINUTES / 60.0
        render_hours = estimated_render_minutes / 60.0
        estimated_cost_usd = round(dph * (overhead_hours + render_hours), 4)
        estimated_cost_if_separate = round(dph * (overhead_hours * job_count + render_hours), 4)

    amortization_note = (
        f"overhead ~{OVERHEAD_MINUTES:g} phút trả 1 lần thay vì {job_count}x"
        if job_count else "queue rỗng -- chưa có gì để amortize")

    return {
        "job_count": job_count,
        "estimated_render_minutes": estimated_render_minutes,
        "oldest_age_minutes": oldest_age_minutes,
        "thresholds": thresholds,
        "thresholds_met": thresholds_met,
        "estimated_cost_usd": estimated_cost_usd,
        "estimated_cost_if_rendered_separately_usd": estimated_cost_if_separate,
        "amortization_note": amortization_note,
    }


# ---------------------------------------------------------------------------
# flush -- rent once, render_batch, destroy in `finally`, dequeue only what
# actually rendered.
# ---------------------------------------------------------------------------

def flush(job_ids: list[str], *, config: dict[str, Any],
         cost_hooks: dict[str, Callable[..., None]] | None = None,
         max_total_usd: float | None = None) -> BatchResult:
    """Force flush IS this function with an explicit job list -- there is no
    separate code path. Offer selection follows `remote.render_now`'s own
    convention (search + pick cheapest eligible offer internally) rather
    than taking a pre-chosen `offer_id`, for the same reason `render_now`
    does it that way: one convention, not two.

    `max_total_usd`, if given, bounds the whole batch rental's deadline via
    `remote._clamp_deadline_minutes` (mechanical ceiling enforcement, same
    as `render_now` -- see that function's docstring for why this must not
    be validated-then-discarded). `config["enabled"]` is re-checked here as
    defense-in-depth even though `VastCloudRender.execute()` already checks
    it -- `flush()` must never rent for real even if called directly.
    """
    if not config.get("enabled", False):
        raise remote.CloudRenderError(
            "cloud render dang tat (enabled: false trong config/cloud-render.json)")
    ledger.reap()

    jobs: dict[str, Any] = {}
    for job_id in job_ids:
        try:
            jobs[job_id] = find_job(job_id)
        except FileNotFoundError:
            warnings.warn(f"flush: job_id={job_id!r} không còn thư mục job -- bỏ qua")

    render_seconds_per_video_second = float(config.get("render_seconds_per_video_second", 1.9))
    queue_entries = {e.job_id: e for e in list_entries(prune_missing=False)}

    job_manifests: dict[str, kit.JobKitManifest] = {}
    build_errors: dict[str, str] = {}
    for job_id, job in jobs.items():
        try:
            state = job.load()
            current_version = int(state.get("current_version") or 0)
            entry = queue_entries.get(job_id)
            if entry is not None and current_version != entry.version_at_enqueue:
                warnings.warn(
                    f"flush: job {job_id} đã sang version {current_version} "
                    f"(enqueue lúc v{entry.version_at_enqueue}) -- render version hiện tại")
            job_manifests[job_id] = kit.build_job_kit(
                job, current_version,
                render_seconds_per_video_second=render_seconds_per_video_second)
        except Exception as exc:  # noqa: BLE001 -- one bad kit must not abort the whole batch
            build_errors[job_id] = str(exc)

    # Record kit-build failures against the queue immediately -- regardless
    # of whether renting later succeeds, these specific jobs already used up
    # one attempt (a bad props/staging dir is a job-side problem, not a
    # rental-side one, so it must count even if nothing gets rented at all).
    build_blocked = _record_failures(build_errors)

    if not job_manifests:
        raise FlushError(
            f"Không job nào build được kit -- không có gì để render: {build_errors}")

    composer_manifest = kit.build_composer_kit()

    def _cleanup_all() -> None:
        kit.cleanup_kit(composer_manifest)
        for manifest in job_manifests.values():
            kit.cleanup_kit(manifest)

    try:
        total_render_minutes = sum(m.estimated_render_seconds
                                   for m in job_manifests.values()) / 60.0
        max_batch_minutes = float(config.get("max_batch_runtime_minutes",
                                             config.get("max_runtime_minutes", 60)))
        requested_minutes = min(
            OVERHEAD_MINUTES + total_render_minutes * RENDER_TIME_SAFETY_FACTOR,
            max_batch_minutes)
        now = int(time.time())

        offers = vast_client.search(config["offer_query"], mode=config["pricing_mode"])
        eligible = [offer for offer in offers if offer.dph <= config["max_dph_usd"]]
        if not eligible:
            raise remote.CloudRenderError(
                f"Không có offer nào <= ${config['max_dph_usd']}/h khớp query "
                f"{config['offer_query']!r}")
        offer = min(eligible, key=lambda offer: offer.dph)
        if cost_hooks and cost_hooks.get("on_offer_selected"):
            cost_hooks["on_offer_selected"](offer)

        # Clamp AFTER the offer is known (not before) -- `max_total_usd` is
        # a $ ceiling, so it must be converted to minutes using the real
        # offer.dph, not a placeholder. Same enforcement as `render_now`.
        deadline_minutes = remote._clamp_deadline_minutes(
            requested_minutes, offer.dph, max_total_usd)
        deadline_epoch = now + int(deadline_minutes * 60)

        key_path = Path(config.get("ssh_key_path", "~/.ssh/openmontage_cloud_render")).expanduser()
        pubkey = setup_key.public_key_text(key_path)
        intent_id = uuid.uuid4().hex[:6]
        onstart_script = onstart.build(pubkey, list(config["apt_packages"]), deadline_epoch,
                                       now_epoch=now)
        bid_price = offer.dph if config["pricing_mode"] == "bid" else None

        rental = vast_client.rent(
            offer.id, offer.dph, intent_id=intent_id, deadline_epoch=deadline_epoch,
            ceiling_dph=config["max_dph_usd"], image=config["image"], disk_gb=config["disk_gb"],
            onstart=onstart_script, bid_price=bid_price)
    except Exception:
        _cleanup_all()
        raise

    start = time.monotonic()
    instance_id = rental.id
    rendered: list[str] = []
    render_failed: dict[str, str] = {}
    pending: list[str] = []

    def _on_output(item: remote.BatchItem, result: remote.RemoteRenderResult) -> None:
        # `_render_one_item` already wrote `job.final_path` (it is the sole
        # writer -- a second copy here would touch its mtime again and make
        # the `record_external_upload` manifest entry it just wrote stale).
        rendered.append(item.job_id)
        remove(item.job_id)  # dequeue immediately -- incremental, survives a later crash/preemption

    try:
        instance = vast_client.wait_running(instance_id,
                                            timeout_s=remote.DEFAULT_WAIT_RUNNING_TIMEOUT_S)
        max_concurrency = remote._remote_concurrency(offer.cpu_cores_effective)

        items: list[remote.BatchItem] = []
        for job_id, manifest in job_manifests.items():
            job = jobs[job_id]
            options = job.load().get("options") or {}
            flags = {
                "crf": options.get("render_crf", 17),
                "jpeg_quality": options.get("render_jpeg_quality", 100),
                "scale": 1.0,
            }
            items.append(remote.BatchItem(
                job_id=job_id, kit=manifest, flags=flags,
                timeout_s=max(60.0, manifest.estimated_render_seconds * RENDER_TIME_SAFETY_FACTOR),
                job=job))

        _set_status([item.job_id for item in items], STATUS_RENDERING)

        results = remote.render_batch(
            instance, composer_manifest, items, key_path=key_path,
            max_concurrency=max_concurrency, deadline_epoch=deadline_epoch,
            on_output=_on_output)

        for item_result in results:
            if item_result.status == "pending":
                pending.append(item_result.job_id)
            elif item_result.status == "failed":
                render_failed[item_result.job_id] = item_result.error or "unknown error"
            # "done" already handled incrementally by _on_output
    finally:
        try:
            vast_client.destroy(instance_id)
        except Exception as exc:  # noqa: BLE001 -- must never swallow a destroy failure silently
            warnings.warn(
                f"flush: không destroy được instance {instance_id}: {exc} -- "
                "để nguyên ledger active, reaper sẽ dọn sau")
        else:
            elapsed = time.monotonic() - start
            ledger.close(intent_id, actual_usd=round(offer.dph * elapsed / 3600, 4),
                        duration_seconds=round(elapsed, 1))
        _cleanup_all()

    _set_status(pending, STATUS_PENDING)
    render_blocked = _record_failures(render_failed)

    return BatchResult(
        rendered=rendered, failed={**build_errors, **render_failed},
        pending=pending, blocked=build_blocked + render_blocked,
        instance_id=instance_id, offer_id=offer.id,
        actual_usd=round(offer.dph * (time.monotonic() - start) / 3600, 4))
