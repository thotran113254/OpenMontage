"""Stage 6 — render the timeline with Remotion.

Two things this stage works around, both observed on this machine:

* Remotion copies the whole `--public-dir` into its bundle before rendering.
  Pointing it at the shared public folder meant copying 415 MB per render and
  growing. Instead each job stages only the assets its props reference, linked
  rather than copied where the filesystem allows it.
* The compositor intermittently fails with "No frame found at position" under
  high concurrency, while the same frame renders fine at lower concurrency.
  One retry at half concurrency, and only for that error.

The composition reads its length from props (calculateMetadata), so no
--frames range is needed and the render cannot leave a black tail.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from lib.talking_head_edit.cpu_budget import available_cores, cpu_budget
from lib.talking_head_edit.job_store import REPO_ROOT, SHARED_PUBLIC

COMPOSER_DIR = REPO_ROOT / "remotion-composer"
COMPOSITION_ID = "MonaTimeline"
FRAME_SEEK_ERROR = "No frame found at position"
# Hard ceiling for local renders; OPENMONTAGE_RENDER_MAX_CONCURRENCY overrides
# it. The effective default is also held to the machine's CPU budget.
_DEFAULT_MAX_CONCURRENCY = 8


def _configured_max_concurrency() -> int:
    """The env override, else the default — either way held to the CPU budget."""
    raw = os.environ.get("OPENMONTAGE_RENDER_MAX_CONCURRENCY", "").strip()
    try:
        cap = int(raw)
    except ValueError:
        cap = _DEFAULT_MAX_CONCURRENCY
    return max(1, min(cap, cpu_budget()))


# Hard default used by tests. Runtime workers come from
# `_configured_max_concurrency()` so a later `load_env()` can raise the cap.
MAX_CONCURRENCY = _DEFAULT_MAX_CONCURRENCY


class RenderError(RuntimeError):
    pass


def _concurrency(options: dict[str, Any] | None = None,
                 max_concurrency: int | None = None) -> int:
    """Workers for the Remotion render.

    `render_concurrency` may be a number or the string `"half"`. `"half"` exists
    for the judder remedy: the existing in-render retry only fires on
    FRAME_SEEK_ERROR, which is a hard failure — judder is a *successful* render
    that repeated frames, so it needs the lower concurrency from the first frame
    rather than after a crash that never comes.

    `max_concurrency` defaults to the local-machine cap (`MAX_CONCURRENCY`).
    The cloud render path (`lib/cloud_render/remote.py`) passes a much higher
    cap derived from the rented box's core count.
    """
    if max_concurrency is None:
        max_concurrency, cores = _configured_max_concurrency(), available_cores()
    else:   # a rented box: its own cores, minus headroom for the compositor
        cores = (os.cpu_count() or 4) - 2
    default = max(1, min(max_concurrency, cores))
    requested = (options or {}).get("render_concurrency")
    if requested is None:
        return default
    if isinstance(requested, str):
        if requested.strip().lower() == "half":
            return max(1, default // 2)
        try:
            requested = int(requested)
        except ValueError:
            return default
    return max(1, min(max_concurrency, int(requested)))


def _link_or_copy(source: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)          # same volume: no bytes copied
    except (OSError, NotImplementedError):
        shutil.copyfile(source, target)


def stage_assets(job, props: dict[str, Any]) -> Path:
    """Build the per-job public dir: the cut footage plus the audio it uses."""
    staging = job.render_public_dir
    staging.mkdir(parents=True, exist_ok=True)

    _link_or_copy(job.src_path, staging / Path(props["videoSrc"]).name)

    wanted = {event["name"] for event in props.get("events", []) if event.get("type") == "sfx"}
    if props.get("bgm", {}).get("name"):
        wanted.add(props["bgm"]["name"])
    stills = set()
    for event in props.get("events", []):
        for key in ("freezeSrc", "artSrc"):
            name = event.get(key)
            if name:
                stills.add(Path(str(name)).name)
    for name in sorted(wanted):
        source = SHARED_PUBLIC / name
        if source.exists():
            _link_or_copy(source, staging / name)
        else:
            job.emit("warning", "render", f"Thiếu file audio khi dựng staging: {name}")
    for name in sorted(stills):
        source = job.dir / name
        if source.exists():
            _link_or_copy(source, staging / name)

    keep = wanted | stills | {Path(props["videoSrc"]).name}
    for stale in staging.iterdir():
        if stale.is_file() and stale.name not in keep:
            stale.unlink()
    return staging


def parse_progress_line(line: str) -> int | None:
    """Extract a percent-complete from one line of Remotion's stdout.

    Matches `"Rendered n/m ..."`. Returns `None` for any line that does not
    carry that shape (including a malformed one), so a caller can treat
    `None` as "no progress on this line" without a `try/except`. Exported at
    module level so the cloud render path (`lib/cloud_render/remote.py`)
    reuses this exact parser instead of copying the `"Rendered "` string
    match a second time.
    """
    if "Rendered " not in line or "/" not in line:
        return None
    head = line.split("Rendered ", 1)[1].split(",", 1)[0]
    done, _, total = head.partition("/")
    try:
        return int(int(done.strip()) * 100 / int(total.strip()))
    except (ValueError, ZeroDivisionError):
        return None


def _run_remotion(job, command: list[str], log_path: Path) -> tuple[int, str]:
    last_percent = -1
    # line-buffered: a block-buffered log lags minutes behind the render and
    # reads as a hung process when you tail it during a long job
    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        process = subprocess.Popen(
            command, cwd=str(COMPOSER_DIR), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            shell=os.name == "nt",   # npx on Windows is a .cmd shim
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            percent = parse_progress_line(line)
            if percent is not None and percent != last_percent and percent % 5 == 0:
                last_percent = percent
                job.emit("progress", "render", f"Render {percent}%", percent=percent)
        code = process.wait()
    return code, log_path.read_text(encoding="utf-8", errors="replace")


def build_remotion_command(*, entry: str, composition_id: str, out_path: str | Path,
                           props_path: str | Path, public_dir: str | Path, workers: int,
                           crf: int, jpeg_quality: int, scale: float = 1.0) -> list[str]:
    """The one place the Remotion CLI flag list is built.

    Used by **both** the local render path (`run()` below) and the cloud
    render path (`lib/cloud_render/remote.py`) with only paths/concurrency
    substituted -- a second hardcoded flag list is exactly how the cloud
    deliverable would drift visibly from the local one (see
    `--crf`/`--jpeg-quality` rationale in `run()`).
    """
    command = [
        "npx", "remotion", "render", entry, composition_id, str(out_path),
        f"--props={props_path}", f"--public-dir={public_dir}",
        f"--concurrency={workers}", f"--crf={crf}",
        f"--jpeg-quality={jpeg_quality}", "--log=info",
    ]
    if scale < 1.0:
        command.append(f"--scale={scale}")
    return command


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    version = int(state["current_version"])
    props_path = job.props_path(version)
    if not props_path.exists():
        raise RenderError(f"Chưa có props v{version} — chạy stage resolve trước.")
    if not (COMPOSER_DIR / "node_modules").exists():
        raise RenderError(
            "remotion-composer/node_modules chưa cài. Chạy: cd remotion-composer && npm install"
        )

    props = json.loads(props_path.read_text(encoding="utf-8"))
    staging = stage_assets(job, props)

    scale = float(options.get("render_scale", 1.0))
    # Remotion's default leaves the deliverable around 4.5 Mbps at 1080x1920,
    # which visibly softens hair and skin texture on this kind of footage.
    crf = int(options.get("render_crf", 17))
    # Remotion screenshots each composited frame as JPEG before encoding, at
    # quality 80 by default. Measured on a 1:1 face crop, that one step cost
    # more sharpness than the whole grade chain put back: 2.1 -> 1.44. It is
    # a throwaway intermediate, so there is no reason to compress it at all.
    jpeg_quality = int(options.get("render_jpeg_quality", 100))
    out_path = job.final_path if scale >= 1.0 else job.dir / f"preview_{int(scale * 100)}.mp4"
    concurrency = _concurrency(options)

    def build_command(workers: int) -> list[str]:
        return build_remotion_command(
            entry="src/index.tsx", composition_id=COMPOSITION_ID, out_path=out_path,
            props_path=props_path, public_dir=staging, workers=workers,
            crf=crf, jpeg_quality=jpeg_quality, scale=scale)

    job.emit("log", "render",
             f"Render {COMPOSITION_ID} v{version} @ scale {scale}, concurrency {concurrency}, "
             f"staging {len(list(staging.iterdir()))} file")

    code, output = _run_remotion(job, build_command(concurrency), job.log_path("render"))

    if code != 0 and FRAME_SEEK_ERROR in output and concurrency > 2:
        retry_workers = max(2, concurrency // 2)
        job.emit("warning", "render",
                 f"Compositor trượt frame ở concurrency {concurrency} — thử lại với {retry_workers}")
        code, output = _run_remotion(job, build_command(retry_workers), job.log_path("render"))

    if code != 0:
        raise RenderError(f"Remotion render lỗi (exit {code}). 800 ký tự cuối:\n{output[-800:]}")
    if not out_path.exists():
        raise RenderError(f"Render xong nhưng không thấy file {out_path}")

    size_mb = round(out_path.stat().st_size / 1e6, 2)
    job.emit("log", "render", f"Xong: {out_path.name} ({size_mb} MB)")
    return {"output": job.rel(out_path), "output_path": str(out_path),
            "size_mb": size_mb, "scale": scale, "version": version}
