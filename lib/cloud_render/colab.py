"""Render a job's MP4 on Colab (account 1, TPU v6e-1: 44 vCPU EPYC) with no manual step.

The VPS only builds the kit, drives the browser and verifies the result:

1. Composer kit (content-addressed, reused) and job kit go to R2, as for Vast.ai.
2. A generated `run.sh` goes to R2 next to them. Colab gets one presigned GET
   for it; every URL the script needs is baked in and expires with the run.
   Colab never holds a credential, and no footage goes through a public host.
3. The browser opens the dedicated notebook, selects the runtime, connects and
   runs a single cell: `!curl -fsSL <run.sh> | bash`.
4. The script overwrites `status.json` in R2 every ~15 s; this side polls it,
   emits progress, and gives up on a deadline or a stall.
5. The MP4 lands in an R2 staging key and passes the same checks as a Vast.ai
   render (`remote.finalize_output`) before it may become final.mp4.

The runtime is always unassigned afterwards — success, failure or timeout —
because an idle TPU still burns compute units.
"""

from __future__ import annotations

import fcntl
import json
import shlex
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from lib.cloud_render import kit, transfer
from lib.cloud_render.colab_browser import ColabBrowser, ColabBrowserError
from lib.cloud_render.remote import CloudRenderError, finalize_output
from lib.talking_head_edit.job_store import REPO_ROOT
from lib.talking_head_edit.stages.render import build_remotion_command, stage_assets

CONFIG_PATH = REPO_ROOT / "config" / "colab-render.json"
# Machine state, not configuration: the notebook created on first use.
STATE_PATH = REPO_ROOT / "projects" / ".colab-render-state.json"
LOCK_PATH = REPO_ROOT / "projects" / ".colab-render.lock"
POLL_SECONDS = 15
URL_TTL_MARGIN_S = 1800
WORKERS_PLACEHOLDER = "$WORKERS"


class ColabRenderError(CloudRenderError):
    pass


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"enabled": False}


def _state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(**fields: Any) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({**_state(), **fields}, indent=2), encoding="utf-8")


@contextmanager
def single_flight() -> Iterator[None]:
    """One Colab render at a time: there is one account-1 runtime to share."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ColabRenderError("Đang có một render Colab khác chạy — thử lại sau") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def remotion_command(*, crf: int, jpeg_quality: int, x264_preset: str = "") -> str:
    """The same flag list as a local render, with Colab's own paths, a worker
    count the script derives from `nproc` on the runtime, and optionally a
    faster x264 preset (the encoder, not frame capture, bounds a 44-core box)."""
    args = build_remotion_command(
        entry="src/index.tsx", composition_id=kit.COMPOSITION_ID,
        out_path=f"../job/{kit.OUTPUT_RELATIVE_PATH}", props_path=f"../job/{kit.PROPS_FILENAME}",
        public_dir=f"../job/{kit.PUBLIC_DIRNAME}", workers=1, crf=crf, jpeg_quality=jpeg_quality)
    if x264_preset:
        args.append(f"--x264-preset={x264_preset}")
    return " ".join(f"--concurrency={WORKERS_PLACEHOLDER}" if a.startswith("--concurrency=")
                    else shlex.quote(a) for a in args)


def build_script(*, composer_url: str, composer_hash: str, job_url: str, status_url: str,
                 log_url: str, output_url: str, command: str, timeout_s: int) -> str:
    """The bash that runs on the Colab runtime. Status is plain JSON:
    {"state": running|done|failed, "stage", "percent", "message", "ts"}."""
    q = shlex.quote
    return f"""#!/usr/bin/env bash
set -uo pipefail
STATUS_URL={q(status_url)}
LOG_URL={q(log_url)}
W=/content/openmontage-render
mkdir -p "$W" && cd "$W"
: > run.log
status() {{
  local msg=${{4:-}}; msg=${{msg//\\"/\\'}}; msg=${{msg//$'\\n'/ }}
  printf '{{"state":"%s","stage":"%s","percent":%s,"message":"%s","ts":%s}}' \\
    "$1" "$2" "${{3:-0}}" "${{msg:0:300}}" "$(date +%s)" > status.json
  curl -fsS --retry 2 -X PUT --upload-file status.json "$STATUS_URL" >/dev/null || true
}}
push_log() {{
  {{ cat "$W/run.log"; [ -f "$W/render.out" ] && tail -c 60000 "$W/render.out"; }} > "$W/log.txt"
  curl -fsS --retry 2 -X PUT --upload-file "$W/log.txt" "$LOG_URL" >/dev/null || true
}}
fail() {{
  push_log
  status failed "$1" 0 "$( {{ tail -n 3 "$W/run.log"; [ -f "$W/render.out" ] && tail -n 3 "$W/render.out"; }} )"
  exit 1
}}
exec > >(tee -a run.log) 2>&1

status running setup 0 "$(nproc) vCPU"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  (curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs) >/dev/null || fail node
fi
if [ "$(cat composer/.kit_hash 2>/dev/null)" != {q(composer_hash)} ]; then
  rm -rf composer && mkdir composer
  curl -fsSL --retry 3 {q(composer_url)} | tar -xz -C composer || fail composer_kit
  (cd composer && npm ci --no-audit --no-fund --loglevel=error) || fail npm_ci
  echo {q(composer_hash)} > composer/.kit_hash
fi
rm -rf job && mkdir job
curl -fsSL --retry 3 {q(job_url)} | tar -xz -C job || fail job_kit
mkdir -p job/out

WORKERS=$(( $(nproc) > 8 ? $(nproc) - 4 : $(nproc) ))
status running render 0 "concurrency $WORKERS"
cd composer
timeout {timeout_s}s {command} > "$W/render.out" 2>&1 &
PID=$!
while kill -0 "$PID" 2>/dev/null; do
  P=$(grep -oE 'Rendered [0-9]+/[0-9]+' "$W/render.out" | tail -1 | awk -F'[ /]' '{{ if ($3 > 0) print int($2 * 100 / $3); else print 0 }}')
  status running render "${{P:-0}}"
  push_log
  sleep {POLL_SECONDS}
done
wait "$PID"; CODE=$?
[ "$CODE" -eq 0 ] || fail "render_exit_$CODE"
[ -s ../job/{kit.OUTPUT_RELATIVE_PATH} ] || fail no_output
status running upload 100
curl -fsS --retry 3 -X PUT --upload-file ../job/{kit.OUTPUT_RELATIVE_PATH} {q(output_url)} || fail upload
push_log
status done done 100 ok
"""


def parse_status(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        status = json.loads(raw)
    except ValueError:
        return None
    return status if isinstance(status, dict) and "state" in status else None


def render_job(job: Any, version: int, options: dict[str, Any], *,
               browser: ColabBrowser | None = None, settings: Any = None,
               config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render `version` of `job` on Colab and make the result its final.mp4."""
    from lib.r2_storage import presign
    from lib.r2_storage.config import resolve as r2_resolve

    config = config or load_config()
    if not config.get("enabled"):
        raise ColabRenderError("Render Colab đang tắt (config/colab-render.json)")
    settings = settings or r2_resolve()
    browser = browser or ColabBrowser(session=config.get("browser_session", "colab-cdp"),
                                      cdp_port=int(config.get("cdp_port", 9222)))
    deadline_s = int(config.get("max_runtime_minutes", 60)) * 60
    stall_s = int(config.get("stall_minutes", 6)) * 60
    rate = float(config.get("compute_units_per_hour", 0.0))

    with single_flight():
        props = json.loads(job.props_path(version).read_text(encoding="utf-8"))
        stage_assets(job, props)
        composer = kit.build_composer_kit()
        job_kit = kit.build_job_kit(job, version)
        run_id = f"{job.job_id}-{int(time.time())}"
        base = f"render-kits/colab/{run_id}"
        keys = {name: f"{base}/{name}" for name in
                ("job.tar.gz", "run.sh", "status.json", "render.log", "final.mp4")}
        composer_key = f"render-kits/composer/{composer.kit_hash}/kit.tar.gz"
        ttl = deadline_s + URL_TTL_MARGIN_S
        started = time.monotonic()
        connected_at: float | None = None
        try:
            transfer.push_kit(composer.kit_dir, composer_key, settings=settings)
            transfer.push_kit(job_kit.kit_dir, keys["job.tar.gz"], settings=settings)
            script = build_script(
                composer_url=presign.get_url(composer_key, ttl, settings=settings),
                composer_hash=composer.kit_hash,
                job_url=presign.get_url(keys["job.tar.gz"], ttl, settings=settings),
                status_url=presign.put_url(keys["status.json"], ttl, settings=settings),
                log_url=presign.put_url(keys["render.log"], ttl, settings=settings),
                output_url=presign.put_url(keys["final.mp4"], ttl, settings=settings),
                command=remotion_command(crf=int(options.get("render_crf", 17)),
                                         jpeg_quality=int(options.get("render_jpeg_quality", 100)),
                                         x264_preset=str(config.get("x264_preset") or "")),
                timeout_s=deadline_s)
            presign.put_text(keys["run.sh"], script, settings=settings)

            job.emit("log", "render", f"Colab: mở runtime {config.get('runtime')} trên tài khoản 1…")
            browser.ensure_chrome()
            browser.ensure_logged_in()
            notebook = browser.open_notebook(_state().get("notebook_url"))
            _save_state(notebook_url=notebook)
            browser.set_runtime(config.get("runtime", "v6e-1 TPU"))
            job.emit("log", "render", f"Colab: {browser.connect()}")
            connected_at = time.monotonic()
            run_url = presign.get_url(keys["run.sh"], ttl, settings=settings)
            browser.run_cell(f"!curl -fsSL '{run_url}' | bash")
            _wait_for_render(job, presign, keys["status.json"], settings,
                             deadline=started + deadline_s, stall_s=stall_s)
            result = finalize_output(job, job.job_id, keys["final.mp4"], Path(job_kit.kit_dir),
                                     float(props.get("durationSeconds") or 0) or None,
                                     settings=settings, started=started)
        except (ColabBrowserError, kit.KitError) as exc:
            raise ColabRenderError(str(exc)) from exc
        finally:
            _release(job, browser, connected_at, rate)
            _keep_log(job, presign, keys["render.log"], settings)
            for key in keys.values():
                try:
                    presign.delete_object(key, settings=settings)
                except Exception:  # noqa: BLE001 — a leftover temp key costs cents, not correctness
                    pass
            kit.cleanup_kit(composer)
            kit.cleanup_kit(job_kit)

    size_mb = round(result.size_bytes / 1e6, 2)
    job.emit("log", "render",
             f"Colab xong: final.mp4 {size_mb} MB, {result.duration_seconds:.1f}s, "
             f"tổng {result.wall_seconds:.0f}s")
    return {"output": "final.mp4", "output_path": str(job.final_path), "size_mb": size_mb,
            "scale": 1.0, "version": version, "location": "colab",
            "wall_seconds": result.wall_seconds}


def _wait_for_render(job: Any, presign: Any, status_key: str, settings: Any, *,
                     deadline: float, stall_s: int) -> None:
    last_seen = time.monotonic()
    last_ts: Any = None
    last_percent = -1
    while True:
        if time.monotonic() > deadline:
            raise ColabRenderError("Render Colab quá thời gian cho phép")
        status = parse_status(presign.read_text(status_key, settings=settings))
        if status and status.get("ts") != last_ts:
            last_ts, last_seen = status.get("ts"), time.monotonic()
            if status["state"] == "done":
                return
            if status["state"] == "failed":
                raise ColabRenderError(
                    f"Render Colab lỗi ở bước {status.get('stage')}: {status.get('message', '')}")
            percent = int(status.get("percent") or 0)
            if status.get("stage") == "render" and percent >= last_percent + 5:
                last_percent = percent - percent % 5
                job.emit("progress", "render", f"Render (Colab) {last_percent}%",
                         percent=last_percent)
            elif status.get("stage") != "render" and last_percent < 0:
                job.emit("log", "render", f"Colab: {status.get('stage')} {status.get('message', '')}")
        if time.monotonic() - last_seen > stall_s:
            raise ColabRenderError(f"Colab không báo tiến độ quá {stall_s // 60} phút")
        time.sleep(POLL_SECONDS)


def _keep_log(job: Any, presign: Any, log_key: str, settings: Any) -> None:
    """Colab's log is the only record of what ran there; keep it with the job."""
    try:
        text = presign.read_text(log_key, settings=settings)
    except Exception:  # noqa: BLE001 — a missing log must not mask the render's outcome
        text = None
    if text and getattr(job, "dir", None) is not None:
        path = Path(job.dir) / "logs" / "render_colab.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _release(job: Any, browser: ColabBrowser, connected_at: float | None, rate: float) -> None:
    """Always free the runtime, and say what it cost."""
    try:
        released = browser.unassign()
    except Exception as exc:  # noqa: BLE001 — reported loudly, the render result stands
        released = False
        job.emit("warning", "render", f"Colab: không xoá được runtime ({exc})")
    if not released:
        job.emit("warning", "render",
                 "Colab: runtime có thể vẫn chạy và tốn CU — kiểm tra Manage sessions")
    if connected_at is not None:
        minutes = (time.monotonic() - connected_at) / 60
        job.emit("log", "render",
                 f"Colab: runtime dùng {minutes:.1f} phút ≈ {minutes / 60 * rate:.2f} compute unit")
