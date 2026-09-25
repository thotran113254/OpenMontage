"""Single-rental, N-job remote render: rent -> wait ready -> upload the
shared composer kit -> `npm ci` ONCE -> per job: upload its props+public ->
run the SAME Remotion command as local -> download -> verify -> guaranteed
destroy.

One code path serves both a lone job (`render_now`) and a batch
(`lib.cloud_render.queue.flush`) -- `render_batch` with a single-item list
IS `render_now`'s render step. There is deliberately no separate
"render one job" function: two render code paths is exactly how the cloud
deliverable would drift from what a batch produces.

Fidelity: the remote render command comes from
`lib.talking_head_edit.stages.render.build_remotion_command` -- the exact
same function the local render path calls -- with only paths and concurrency
substituted. Cloud must never build a second, independently-maintained flag
list; that is exactly how the cloud deliverable would drift visibly from the
local one (different crf/jpeg-quality/scale reads as a different-looking
video, not a bug report).

Cleanup ordering: download-then-verify-then-destroy. Destroying before the
local file is confirmed to exist and be non-trivial in size loses the render
entirely for a rental that has already been paid for.

Batch isolation: a failing job's kit records its error and the batch
continues with the rest -- a bad props file must not cost the other jobs
their shared rental. The one exception is a failure in the *shared* setup
(missing ssh_host, `_wait_ready` timeout, composer upload, `npm ci`): those
block every job equally, so they raise and the caller destroys the rental
without attempting any job.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lib.cloud_render import kit, ledger, onstart, setup_key, transfer, vast_client
from lib.r2_storage import manifest as r2_manifest
from lib.r2_storage import presign as r2_presign
from lib.talking_head_edit.resolve_media import ResolveError, probe_duration
from lib.talking_head_edit.stages.render import build_remotion_command, parse_progress_line

# Presigned GET TTL for a kit -- used once, right after readiness / right
# before an item's render, never re-signed once expired mid-batch.
_KIT_PRESIGN_TTL_S = 1800
# Presigned PUT TTL headroom on top of an item's own render timeout -- must
# survive exactly one render, clamped well under the 7-day SigV4 ceiling.
_RESULT_PUT_TTL_MARGIN_S = 600
_RESULT_PUT_TTL_MAX_S = 6 * 3600

REMOTE_KIT_DIR = "/root/kit"
REMOTE_JOBS_DIR = f"{REMOTE_KIT_DIR}/jobs"
MIN_OUTPUT_BYTES = 100_000
DURATION_TOLERANCE_RATIO = 0.01
DEFAULT_READY_TIMEOUT_S = 240.0
DEFAULT_READY_POLL_S = 5.0
DEFAULT_WAIT_RUNNING_TIMEOUT_S = 300.0
DEFAULT_UPLOAD_DOWNLOAD_TIMEOUT_S = 300.0
# How far ahead of a batch deadline a job's own estimated timeout must fit
# before `render_batch` will even start it -- stopping cleanly and leaving
# the remainder `pending` beats starting a render that gets killed mid-way
# by the instance's own onstart watchdog.
DEFAULT_DEADLINE_SAFETY_MARGIN_S = 60.0
# Same shape as render.py's local default ((cores - 2), capped) but with the
# remote core count and a much higher cap -- 8 would waste most of a rented
# 32+ core box.
MAX_REMOTE_CONCURRENCY = 32


class CloudRenderError(RuntimeError):
    """Base error for the remote render path. Every raise site here happens
    inside `render_now`'s `try`, so `finally` still destroys the rental."""


class CloudRenderUnsupported(CloudRenderError):
    """Structured refusal for a props shape this phase does not support
    (the `video_compose` pipeline's absolute `file:///` URIs) -- never a
    partial/best-effort render."""

    def __init__(self, reason: str, options: list[str]):
        super().__init__(reason)
        self.reason = reason
        self.options = options


@dataclass(frozen=True)
class RemoteRenderResult:
    local_output_path: Path
    size_bytes: int
    duration_seconds: float
    wall_seconds: float


@dataclass(frozen=True)
class BatchItem:
    """One job's slot in a `render_batch` call. `job` is optional (only used
    for progress `emit` calls, same as `render_one`'s old `job` kwarg)."""
    job_id: str
    kit: "kit.JobKitManifest"
    flags: dict[str, Any] = field(default_factory=dict)
    timeout_s: float = DEFAULT_UPLOAD_DOWNLOAD_TIMEOUT_S
    expected_duration_seconds: float | None = None
    job: Any = None


@dataclass(frozen=True)
class BatchItemResult:
    """Outcome of one `BatchItem` inside a batch. `status` is one of
    `"done"` (rendered, downloaded, verified), `"failed"` (this job's error
    did not stop the rest of the batch), or `"pending"` (never started --
    the deadline guard tripped before it, so it stays in the queue)."""
    job_id: str
    status: str
    result: RemoteRenderResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class CloudRenderResult:
    local_output_path: Path
    size_bytes: int
    duration_seconds: float
    wall_seconds: float
    actual_usd: float
    instance_id: int
    offer_id: int


def render_pipeline(*_args: Any, **_kwargs: Any) -> None:
    """The `video_compose` pipeline shape is refused, not adapted, in this
    phase -- its props carry absolute local `file:///` URIs (rewritten at
    `tools/video/video_compose.py:1697-1704`), which need asset collection
    and a path rewrite that is roughly another phase of work."""
    raise CloudRenderUnsupported(
        "props carry absolute local file:// URIs generated at "
        "video_compose.py:1697-1704; asset collection + path rewrite not implemented",
        options=["render locally", "use the autoedit pipeline instead",
                "open a follow-up plan for pipeline-shape cloud render"],
    )


def _remote_concurrency(cpu_cores_effective: float | None,
                        max_concurrency: int = MAX_REMOTE_CONCURRENCY) -> int:
    if not cpu_cores_effective:
        return 8
    return max(1, min(int(cpu_cores_effective) - 2, max_concurrency))


def _masked(exc: Exception, settings: Any) -> str:
    """`botocore.exceptions.EndpointConnectionError` (and similar) embed the
    full R2 endpoint URL -- including the account id -- in their message.
    Mask it before the text reaches `CloudRenderError`/`job.emit`/`events.jsonl`."""
    from lib.r2_storage.client import mask_endpoint

    text = str(exc)
    endpoint = getattr(settings, "endpoint_url", None)
    if endpoint:
        text = text.replace(endpoint, mask_endpoint(endpoint))
    return text


def _call_hook(hooks: dict[str, Callable[..., None]] | None, name: str, *args: Any) -> None:
    if not hooks:
        return
    callback = hooks.get(name)
    if callback is not None:
        callback(*args)


def _wait_ready(host: str, port: int, key_path: str | Path, *,
                timeout_s: float = DEFAULT_READY_TIMEOUT_S,
                poll_interval_s: float = DEFAULT_READY_POLL_S) -> None:
    """Poll for `/root/.openmontage-ready` -- written by the onstart script
    only once `apt-get install` succeeds. Absence within the timeout means
    the box has no Chrome deps and every render would fail after the account
    already paid for boot."""
    deadline = time.monotonic() + timeout_s
    command = f"test -f {onstart.READY_MARKER}"
    while True:
        result = transfer.ssh(host, port, key_path, command, timeout=30.0)
        if result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise CloudRenderError(
                f"instance chưa sẵn sàng (thiếu {onstart.READY_MARKER}) sau {timeout_s}s")
        time.sleep(poll_interval_s)


def _stream_command(args: list[str], *, timeout_s: float,
                    on_line: Callable[[str], None]) -> int:
    """Run `args` in the foreground (never `nohup`/background) and feed every
    stdout line to `on_line` as it arrives, so a paid rental's progress is
    never a silent multi-minute gap. Bounded by `timeout_s` in addition to
    the remote `timeout <s>` wrapper the caller already added -- defense in
    depth against a hung headless Chrome."""
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    deadline = time.monotonic() + timeout_s
    assert process.stdout is not None
    try:
        for line in process.stdout:
            on_line(line)
            if time.monotonic() > deadline:
                process.kill()
                raise CloudRenderError(f"remote render vượt timeout {timeout_s}s, đã kill")
        return process.wait(timeout=max(1.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        raise CloudRenderError(
            f"remote render vượt timeout {timeout_s}s khi chờ exit code, đã kill") from None


def finalize_output(job: Any, job_id: str, staging_key: str, work_dir: Path,
                    expected_duration_seconds: float | None, *, settings: Any,
                    started: float) -> RemoteRenderResult:
    """Download a rendered MP4 from its R2 staging key, verify it, and make it
    the job's final.mp4. Shared by every remote render path (Vast.ai, Colab):
    whatever rendered it, the same size and duration checks decide whether it
    may replace `final.mp4`, and the staging object is always deleted."""
    local_tmp = Path(work_dir) / "downloaded_final.mp4"
    try:
        r2_presign.download(staging_key, local_tmp, settings=settings)

        size_bytes = local_tmp.stat().st_size
        if size_bytes <= MIN_OUTPUT_BYTES:
            raise CloudRenderError(f"file tải về quá nhỏ ({size_bytes} bytes) -- render có thể đã lỗi")

        try:
            actual_duration = probe_duration(local_tmp)
        except ResolveError as exc:
            raise CloudRenderError(f"không đọc được thời lượng file tải về: {exc}") from exc

        if expected_duration_seconds:
            drift_ratio = (abs(actual_duration - expected_duration_seconds)
                           / expected_duration_seconds)
            if drift_ratio > DURATION_TOLERANCE_RATIO:
                raise CloudRenderError(
                    f"thời lượng lệch {drift_ratio:.1%} (mong {expected_duration_seconds:.2f}s, "
                    f"đo được {actual_duration:.2f}s)")

        # Verified -- write the job's local final.mp4 HERE (the only writer:
        # callers must not also copy result.local_output_path themselves,
        # since a second copy would touch final.mp4's mtime again and make
        # the record_external_upload stat below stale before it is even
        # saved) before promoting staging to the durable archive with a
        # server-side copy (no bytes through local) and recording it so the
        # job-end sync hook skips re-uploading the same bytes from home.
        # `item.job` is documented as "only used for progress emit calls"
        # (BatchItem docstring) -- a caller's job double may not have `.dir`.
        job_dir = getattr(job, "dir", None)
        if job_dir is not None:
            final_path = Path(job_dir) / "final.mp4"
            final_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_tmp, final_path)

        archive_key = f"{settings.prefix}/autoedit-jobs/{job_id}/final.mp4"
        r2_presign.copy_object(staging_key, archive_key, settings=settings)
        if job_dir is not None:
            r2_manifest.record_external_upload(job_dir, "final.mp4", archive_key, settings)
    finally:
        r2_presign.delete_object(staging_key, settings=settings)

    return RemoteRenderResult(
        local_output_path=local_tmp, size_bytes=size_bytes,
        duration_seconds=actual_duration, wall_seconds=round(time.monotonic() - started, 1))


def _render_one_item(host: str, port: int, key_path: str | Path, item: BatchItem, *,
                     max_concurrency: int, settings: Any = None) -> RemoteRenderResult:
    """Upload this job's props+public (via R2), render, download the result
    (via R2), verify. Raises `CloudRenderError` at the first failing step --
    the caller (`render_batch`) decides whether that failure is fatal to the
    whole batch (shared setup) or just to this one item (this function is
    only ever called for the per-item half)."""
    from lib.r2_storage.config import resolve as r2_resolve

    settings = settings or r2_resolve()
    start = time.monotonic()
    remote_job_dir = f"{REMOTE_JOBS_DIR}/{item.job_id}"
    job_kit_key = f"render-kits/jobs/{item.job_id}/kit.tar.gz"

    try:
        transfer.push_kit(item.kit.kit_dir, job_kit_key, settings=settings)
    except Exception as exc:  # noqa: BLE001 -- surfaced as a CloudRenderError, same contract as scp before
        raise CloudRenderError(f"push kit job lên R2 thất bại: {_masked(exc, settings)}") from exc

    fetch = transfer.fetch_to_remote(host, port, key_path, job_kit_key, remote_job_dir,
                                     timeout=DEFAULT_UPLOAD_DOWNLOAD_TIMEOUT_S,
                                     ttl=_KIT_PRESIGN_TTL_S, settings=settings)
    if fetch.returncode != 0:
        raise CloudRenderError(
            f"tải kit job từ R2 vào rental thất bại: "
            f"{transfer._curl_error(fetch.returncode, fetch.stderr)}")

    remote_out = f"{remote_job_dir}/{item.kit.expected_output_name}"
    remote_props = f"{remote_job_dir}/{item.kit.props_path}"
    remote_public = f"{remote_job_dir}/{item.kit.public_dir}"
    command = build_remotion_command(
        entry="src/index.tsx", composition_id=item.kit.composition_id, out_path=remote_out,
        props_path=remote_props, public_dir=remote_public, workers=max_concurrency,
        crf=int(item.flags.get("crf", 17)), jpeg_quality=int(item.flags.get("jpeg_quality", 100)),
        scale=float(item.flags.get("scale", 1.0)))
    shell_command = (
        f"cd {REMOTE_KIT_DIR} && timeout {max(1, int(item.timeout_s))}s {' '.join(command)}")

    last_percent = -1

    def on_line(line: str) -> None:
        nonlocal last_percent
        if item.job is None:
            return
        percent = parse_progress_line(line)
        if percent is not None and percent != last_percent and percent % 5 == 0:
            last_percent = percent
            item.job.emit("progress", "render", f"Render {percent}%", percent=percent)

    ssh_args = ["ssh", "-i", str(key_path), "-p", str(port), *transfer._SSH_OPTS,
               f"root@{host}", shell_command]
    code = _stream_command(ssh_args, timeout_s=item.timeout_s, on_line=on_line)
    if code != 0:
        raise CloudRenderError(f"remote render lỗi (exit {code})")

    # Staging, not the archive key directly: a failed verification below must
    # never overwrite `projects/autoedit-jobs/<id>/final.mp4` with garbage.
    staging_key = f"render-kits/jobs/{item.job_id}/out/final.mp4"
    put_ttl = min(item.timeout_s + _RESULT_PUT_TTL_MARGIN_S, _RESULT_PUT_TTL_MAX_S)
    push = transfer.push_from_remote(host, port, key_path, remote_out, staging_key,
                                     ttl=put_ttl, timeout=DEFAULT_UPLOAD_DOWNLOAD_TIMEOUT_S,
                                     settings=settings)
    if push.returncode != 0:
        raise CloudRenderError(
            f"đẩy kết quả render lên R2 thất bại: "
            f"{transfer._curl_error(push.returncode, push.stderr)}")

    return finalize_output(item.job, item.job_id, staging_key, Path(item.kit.kit_dir),
                           item.expected_duration_seconds, settings=settings, started=start)


def render_batch(rental: Any, composer: "kit.ComposerKitManifest", items: list[BatchItem], *,
                 key_path: str | Path, max_concurrency: int,
                 ready_timeout_s: float = DEFAULT_READY_TIMEOUT_S,
                 deadline_epoch: int | None = None,
                 safety_margin_s: float = DEFAULT_DEADLINE_SAFETY_MARGIN_S,
                 on_output: Callable[[BatchItem, RemoteRenderResult], None] | None = None,
                 ) -> list[BatchItemResult]:
    """Wait for readiness -> upload the shared composer kit -> `npm ci` ONCE
    -> per item: upload -> render (streaming progress) -> download -> verify
    -> `on_output` -> mark done. One item's failure is recorded and the batch
    continues; a shared-setup failure (missing ssh_host, readiness timeout,
    composer upload, `npm ci`) raises `CloudRenderError` because no item
    could possibly render without it.

    `deadline_epoch`, if given, is checked before *starting* each item: if
    `now + item.timeout_s` would run past `deadline_epoch - safety_margin_s`,
    that item and every item after it are returned as `"pending"` without
    being touched -- a partial batch is a correct outcome, not a failure.
    `render_batch` itself never raises for a per-item or deadline outcome;
    only the shared-setup steps can raise.
    """
    from lib.r2_storage.config import is_configured as r2_is_configured
    from lib.r2_storage.config import resolve as r2_resolve

    host, port = rental.ssh_host, rental.ssh_port
    if not host or not port:
        raise CloudRenderError(f"rental {rental.id} chưa có ssh_host/ssh_port")

    configured, missing = r2_is_configured()
    if not configured:
        raise CloudRenderError(
            f"R2 chưa cấu hình đủ để chuyển kit/kết quả (thiếu env: {', '.join(missing)}) -- "
            "render cloud đi qua R2, không còn scp trực tiếp")
    settings = r2_resolve()

    composer_key = f"render-kits/composer/{composer.kit_hash}/kit.tar.gz"
    try:
        transfer.push_kit(composer.kit_dir, composer_key, settings=settings)
    except Exception as exc:  # noqa: BLE001 -- shared-setup failure, fatal to the whole batch
        raise CloudRenderError(f"push kit composer lên R2 thất bại: {_masked(exc, settings)}") from exc

    _wait_ready(host, port, key_path, timeout_s=ready_timeout_s)

    fetch = transfer.fetch_to_remote(host, port, key_path, composer_key, REMOTE_KIT_DIR,
                                     timeout=DEFAULT_UPLOAD_DOWNLOAD_TIMEOUT_S,
                                     ttl=_KIT_PRESIGN_TTL_S, settings=settings)
    if fetch.returncode != 0:
        raise CloudRenderError(
            f"tải kit composer từ R2 vào rental thất bại: "
            f"{transfer._curl_error(fetch.returncode, fetch.stderr)}")

    npm_ci = transfer.ssh(host, port, key_path,
                          f"cd {REMOTE_KIT_DIR} && npm ci --no-audit --no-fund",
                          timeout=max(60.0, DEFAULT_UPLOAD_DOWNLOAD_TIMEOUT_S))
    if npm_ci.returncode != 0:
        raise CloudRenderError(
            f"npm ci thất bại (exit {npm_ci.returncode}): "
            f"{(npm_ci.stdout or '')[-300:]}{(npm_ci.stderr or '')[-300:]}")

    results: list[BatchItemResult] = []
    for index, item in enumerate(items):
        if deadline_epoch is not None and time.time() + item.timeout_s > deadline_epoch - safety_margin_s:
            for remaining in items[index:]:
                results.append(BatchItemResult(job_id=remaining.job_id, status="pending"))
            break
        try:
            result = _render_one_item(host, port, key_path, item,
                                      max_concurrency=max_concurrency, settings=settings)
        except Exception as exc:  # noqa: BLE001 -- one bad job must not cost the rest their rental
            results.append(BatchItemResult(job_id=item.job_id, status="failed", error=str(exc)))
            continue
        if on_output is not None:
            on_output(item, result)
        results.append(BatchItemResult(job_id=item.job_id, status="done", result=result))

    return results


# A rental this short cannot even finish booting -- if `max_total_usd`
# clamps the deadline below this floor, the ceiling is refused rather than
# silently producing a rental doomed to time out after already paying for
# boot. Chosen well under the measured ~60s boot + ~9s npm ci baseline's
# safety margin.
MIN_VIABLE_RUNTIME_MINUTES = 5.0


def _clamp_deadline_minutes(requested_minutes: float, dph: float,
                            max_total_usd: float | None) -> float:
    """The mechanical enforcement of `max_total_usd`: it is not enough to
    validate the input once and discard it (that was a real gap found in
    review) -- the ceiling must bound the actual rental duration, since cost
    = dph x elapsed and elapsed is bounded only by the deadline. Returns the
    smaller of the requested runtime and what `max_total_usd` can actually
    afford at `dph`. Raises if that leaves less than
    `MIN_VIABLE_RUNTIME_MINUTES`."""
    if max_total_usd is None or dph <= 0:
        return requested_minutes
    affordable_minutes = (float(max_total_usd) / dph) * 60.0
    effective_minutes = min(requested_minutes, affordable_minutes)
    if effective_minutes < MIN_VIABLE_RUNTIME_MINUTES:
        raise CloudRenderError(
            f"max_total_usd=${max_total_usd} qua thap cho gia ${dph}/h "
            f"(chi du {effective_minutes:.1f} phut, can >= "
            f"{MIN_VIABLE_RUNTIME_MINUTES} phut) -- chon offer re hon hoac tang ceiling")
    return effective_minutes


def render_now(job: Any, version: int, *, config: dict[str, Any],
              cost_hooks: dict[str, Callable[..., None]] | None = None,
              max_total_usd: float | None = None) -> CloudRenderResult:
    """rent + `render_batch` (single item) + destroy in `finally`,
    ledger-closed in `finally`, regardless of exception/timeout/
    `KeyboardInterrupt`. One rental, one job, guaranteed destroy.

    `max_total_usd`, if given, bounds the rental's own deadline (see
    `_clamp_deadline_minutes`) -- this is the mechanical ceiling enforcement,
    not just an input-validation formality. The `config["enabled"]` check
    below is defense-in-depth: `VastCloudRender.execute()` already checks it,
    but `render_now` must never rent for real even if called directly."""
    if not config.get("enabled", False):
        raise CloudRenderError(
            "cloud render dang tat (enabled: false trong config/cloud-render.json)")
    ledger.reap()

    props_path = job.props_path(version)
    if not props_path.exists():
        raise CloudRenderError(f"Chưa có props v{version} cho job {job.job_id}")
    props = json.loads(props_path.read_text(encoding="utf-8"))
    expected_duration = float(props.get("durationSeconds") or 0.0)

    render_seconds_per_video_second = float(config.get("render_seconds_per_video_second", 1.9))
    # The kit ships the staging dir: stage the delivery-quality cut, not the
    # draft resolve writes for previews.
    from lib.talking_head_edit.stages.render import RenderError, deliverable_video, stage_assets
    try:
        stage_assets(job, props,
                     deliverable_video(job, version, job.load().get("options") or {}))
    except RenderError as exc:
        raise CloudRenderError(str(exc)) from exc
    composer_manifest = kit.build_composer_kit()
    try:
        job_manifest = kit.build_job_kit(
            job, version, render_seconds_per_video_second=render_seconds_per_video_second)
    except Exception:
        kit.cleanup_kit(composer_manifest)
        raise

    try:
        offers = vast_client.search(config["offer_query"], mode=config["pricing_mode"])
        eligible = [offer for offer in offers if offer.dph <= config["max_dph_usd"]]
        if not eligible:
            raise CloudRenderError(
                f"Không có offer nào <= ${config['max_dph_usd']}/h khớp query "
                f"{config['offer_query']!r}")
        offer = min(eligible, key=lambda offer: offer.dph)
        _call_hook(cost_hooks, "on_offer_selected", offer)

        key_path = Path(config.get("ssh_key_path", "~/.ssh/openmontage_cloud_render")).expanduser()
        pubkey = setup_key.public_key_text(key_path)

        intent_id = uuid.uuid4().hex[:6]
        now = int(time.time())
        effective_minutes = _clamp_deadline_minutes(
            float(config["max_runtime_minutes"]), offer.dph, max_total_usd)
        deadline_epoch = now + int(effective_minutes * 60)
        onstart_script = onstart.build(pubkey, list(config["apt_packages"]), deadline_epoch,
                                       now_epoch=now)
        bid_price = offer.dph if config["pricing_mode"] == "bid" else None

        rental = vast_client.rent(
            offer.id, offer.dph, intent_id=intent_id, deadline_epoch=deadline_epoch,
            ceiling_dph=config["max_dph_usd"], image=config["image"], disk_gb=config["disk_gb"],
            onstart=onstart_script, bid_price=bid_price)
    except Exception:
        kit.cleanup_kit(composer_manifest)
        kit.cleanup_kit(job_manifest)
        raise

    start = time.monotonic()
    instance_id = rental.id
    try:
        instance = vast_client.wait_running(instance_id, timeout_s=DEFAULT_WAIT_RUNNING_TIMEOUT_S)

        max_concurrency = _remote_concurrency(offer.cpu_cores_effective)
        wall_budget = max(60.0, min(float(config["max_runtime_minutes"]) * 60,
                                    deadline_epoch - now))
        job_options = job.load().get("options") or {}
        flags = {
            "crf": job_options.get("render_crf", 17),
            "jpeg_quality": job_options.get("render_jpeg_quality", 100),
            "scale": 1.0,
        }
        item = BatchItem(job_id=job.job_id, kit=job_manifest, flags=flags,
                         timeout_s=wall_budget, expected_duration_seconds=expected_duration,
                         job=job)

        batch_results = render_batch(
            instance, composer_manifest, [item], key_path=key_path,
            max_concurrency=max_concurrency, ready_timeout_s=DEFAULT_READY_TIMEOUT_S)
        item_result = batch_results[0]
        if item_result.status != "done" or item_result.result is None:
            raise CloudRenderError(item_result.error or "render batch item không hoàn thành")
        result = item_result.result
        # `_render_one_item` already wrote `job.final_path` (it is the sole
        # writer -- a second copy here would touch its mtime again and make
        # the `record_external_upload` manifest entry it just wrote stale).

        return CloudRenderResult(
            local_output_path=job.final_path, size_bytes=result.size_bytes,
            duration_seconds=result.duration_seconds, wall_seconds=result.wall_seconds,
            actual_usd=round(offer.dph * (time.monotonic() - start) / 3600, 4),
            instance_id=instance_id, offer_id=offer.id)
    finally:
        try:
            vast_client.destroy(instance_id)
        except Exception as exc:  # noqa: BLE001 -- must never swallow a destroy failure silently
            job.emit("warning", "render",
                     f"Không destroy được instance {instance_id}: {exc} -- "
                     "để nguyên ledger active, reaper sẽ dọn sau")
        else:
            elapsed = time.monotonic() - start
            ledger.close(intent_id, actual_usd=round(offer.dph * elapsed / 3600, 4),
                        duration_seconds=round(elapsed, 1))
        kit.cleanup_kit(composer_manifest)
        kit.cleanup_kit(job_manifest)
