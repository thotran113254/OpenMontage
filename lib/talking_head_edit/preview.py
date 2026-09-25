"""Fast look previews — tune the picture without rendering a video.

Two levels, because they answer different questions:

* `grade_still` (~1s) — one frame of the RAW footage through the grade chain.
  This is the loop for colour and skin: change a number, look, change again.
* `composition_still` (~25s) — one frame through Remotion, so overlays, the
  frame/backdrop, cards and captions are all real.

A full render is ~5 minutes; neither of these is. Nothing here writes to the
job's real artifacts — previews land in `preview/` inside the job directory.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from lib.talking_head_edit.cpu_budget import low_priority_prefix
from lib.talking_head_edit.cache import hash_inputs
from lib.talking_head_edit.job_store import DEFAULT_OPTIONS, REPO_ROOT, Job, option_enabled
from lib.talking_head_edit.resolve_media import (
    AUDIO_PRESETS, build_audio_chain, build_grade_chain, probe_duration,
)

def _stage_assets(job, props: dict[str, Any]) -> Path:
    """Imported late on purpose: importing it at module level closes a cycle.

    preview -> stages.render -> stages/__init__ -> calibrate -> preview. The CLI
    happened to import in an order that hid it; anything importing preview first
    hit an ImportError.
    """
    from lib.talking_head_edit.stages.render import stage_assets
    return stage_assets(job, props)

COMPOSER_DIR = REPO_ROOT / "remotion-composer"


class PreviewError(RuntimeError):
    pass


def _measured_sharpen(job: Job) -> dict[str, float] | None:
    """The sharpening `auto_sharpen` measured on this footage, if it has run."""
    report = job.dir / "sharpen_report.json"
    if not report.exists():
        return None
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if "sharpen" not in data:
        return None
    return {"sharpen": float(data["sharpen"]), "clarity": float(data.get("clarity", 0.85))}


def render_grade_context(job: Job, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Match resolve's display size and opt-in measured sharpening.

    Without a measurement or explicit grade, the image remains unsharpened.
    """
    # Late import for the same cycle `_stage_assets` dodges: stages/__init__
    # pulls in calibrate, which imports this module. calibrate.py does the same.
    from lib.talking_head_edit.stages.resolve import aroll_pixel_size

    state = job.load()
    opts = options if options is not None else state.get("options", {})
    width, height = aroll_pixel_size(
        int(opts.get("width", 1080)), int(opts.get("height", 1920)),
        str(opts.get("frame_preset", DEFAULT_OPTIONS["frame_preset"])),
    )

    # Precedence copied from stages/resolve.py: a human's explicit `sharpen`
    # beats the measurement, and the director's own guess never wins — a model
    # was measured picking 1.2 where the deliverable needed 1.6.
    human_set = "sharpen" in (opts.get("grade_overrides") or {})
    measured = None if human_set else (
        _measured_sharpen(job) if option_enabled(opts, "auto_sharpen", default=False) else None
    )
    if human_set:
        sharpen_source = "human"
    elif measured:
        sharpen_source = "measured"
    else:
        sharpen_source = "grade"

    return {
        "width": width,
        "height": height,
        "source_width": (state.get("probe") or {}).get("width") or None,
        "measured_sharpen": measured,
        "sharpen_source": sharpen_source,
        "frame_preset": str(opts.get("frame_preset", DEFAULT_OPTIONS["frame_preset"])),
    }


def _with_measured_sharpen(grade: dict[str, Any], measured: dict[str, float] | None) -> dict[str, Any]:
    """Overlay the measured sharpening, the way resolve does before encoding."""
    if not measured:
        return grade
    return {**grade, "sharpen": measured["sharpen"], "clarity": measured["clarity"]}


def _run(command: list[str], timeout: int = 300,
         cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Previews run inside the API server, not the queue, so they lower their own priority."""
    return subprocess.run([*low_priority_prefix(), *command], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          cwd=str(cwd) if cwd else None,
                          # npx on Windows is a .cmd shim and needs a shell
                          shell=os.name == "nt" and command[0] == "npx")


def grade_still(source: Path, at_seconds: float, grade: dict[str, Any], out_path: Path,
                width: int = 1080, height: int = 1920,
                source_width: int | None = None) -> Path:
    """One graded frame straight from the source footage.

    Good for colour and skin. NOT for judging sharpness — a still skips the two
    x264 passes that decide how much of a sharpen actually reaches the viewer,
    and it flattered a level that measured only +14% on the real deliverable.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chain = build_grade_chain(grade, width, height, source_width=source_width)
    result = _run(["ffmpeg", "-y", "-ss", f"{at_seconds:.3f}", "-i", str(source),
                   "-frames:v", "1", "-vf", chain, str(out_path), "-loglevel", "error"])
    if result.returncode != 0 or not out_path.exists():
        raise PreviewError(f"ffmpeg preview lỗi: {result.stderr.strip()[:300]}")
    return out_path


def measure_face(image: Path) -> dict[str, float]:
    """Same reading, but only over the middle of the frame where the face is.

    Whole-frame numbers get dragged around by the ceiling, the shirt and (once
    a frame preset is on) the dark backdrop. Skin is what people judge, so the
    centre crop is the number worth chasing.
    """
    return measure_cast(image, crop="crop=iw*0.45:ih*0.3:iw*0.28:ih*0.27")


def measure_cast(image: Path, crop: str | None = None) -> dict[str, float]:
    """Mean channel levels — turns "trông vàng quá" into a number you can chase.

    `red_minus_blue` is the useful one: the higher it climbs above the source's
    own value, the more the grade is pushing the picture warm.
    """
    chain = f"{crop}," if crop else ""
    result = _run(["ffmpeg", "-i", str(image), "-vf",
                   f"{chain}signalstats,metadata=print:file=-", "-f", "null", "-"])
    output = result.stdout + result.stderr
    stats: dict[str, float] = {}
    for line in output.splitlines():
        for key, name in (("YAVG", "luma"), ("UAVG", "chroma_u"), ("VAVG", "chroma_v")):
            if f"signalstats.{key}" in line:
                try:
                    stats[name] = float(line.split("=")[-1].strip())
                except ValueError:
                    pass
    # U is blue-difference, V is red-difference, both neutral at 128. Warm means
    # V above neutral AND U below it, so the gap between them is the bias:
    # higher = more orange, lower = more blue.
    if "chroma_u" in stats and "chroma_v" in stats:
        stats["warm_bias"] = round(stats["chroma_v"] - stats["chroma_u"], 2)
    return stats


def contact_sheet(images: list[tuple[str, Path]], out_path: Path, panel_width: int = 420) -> Path:
    """Stack labelled variants side by side with clean badges so they can be judged together."""
    if not images:
        raise PreviewError("Không có ảnh nào để ghép")
    inputs: list[str] = []
    for _, path in images:
        inputs += ["-i", str(path)]
    labels = {
        "raw": "GOC (RAW)",
        "hien_tai": "HIEN TAI",
        "thu_nghiem": "THU NGHIEM (HSL/LUT)",
    }
    scale_parts = []
    for i, (name, _) in enumerate(images):
        badge = labels.get(name, name.upper())
        scale_parts.append(
            f"[{i}]scale={panel_width}:-1,"
            f"drawtext=text='{badge}':fontsize=26:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=8:x=(w-text_w)/2:y=20[p{i}];"
        )
    stack_inputs = "".join(f"[p{i}]" for i in range(len(images)))
    filter_complex = f"{''.join(scale_parts)}{stack_inputs}hstack=inputs={len(images)}"
    result = _run(["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
                   str(out_path), "-loglevel", "error"])
    if result.returncode != 0:
        raise PreviewError(f"Ghép ảnh lỗi: {result.stderr.strip()[:200]}")
    return out_path


MAX_CACHED_GRADE_FRAMES = 60   # ~2.8 MB each; trimmed oldest-first past this


def _variant_frame(source: Path, at_seconds: float, grade: dict[str, Any], out_dir: Path,
                   label: str, index: int, ctx: dict[str, Any],
                   input_sha: str) -> tuple[Path, dict[str, Any], bool]:
    """One graded frame plus its measurements, reused when nothing changed.

    Keyed per variant rather than per report because that is the actual loop:
    typing a new number changes `thu_nghiem` while `raw` and `hien_tai` stay
    identical, so two of the three frames should cost nothing the second time.
    """
    key = hash_inputs({
        "sha": input_sha,
        "at": round(at_seconds, 3),
        "grade": grade,
        "size": [ctx["width"], ctx["height"]],
        "source_width": ctx["source_width"],
        "filter": build_grade_chain(grade, ctx["width"], ctx["height"],
                                    source_width=ctx["source_width"]),
    })[:10]
    image = out_dir / f"grade_{index:02d}_{label}_{key}.png"
    stats_path = image.with_suffix(".stats.json")

    if image.exists() and stats_path.exists():
        try:
            return image, json.loads(stats_path.read_text(encoding="utf-8")), True
        except (OSError, json.JSONDecodeError):
            pass   # fall through and rebuild

    grade_still(source, at_seconds, grade, image, ctx["width"], ctx["height"],
                source_width=ctx["source_width"])
    measured = {"stats": measure_cast(image), "face": measure_face(image)}
    stats_path.write_text(json.dumps(measured, ensure_ascii=False), encoding="utf-8")
    return image, measured, False


def _trim_grade_cache(out_dir: Path, keep: int | None = None) -> None:
    """Keep the newest frames and comparison sheets, drop the rest.

    Each frame is ~3 MB, so an afternoon of colour tweaking would otherwise fill
    the job folder. Frames and sheets are counted separately — the glob is
    anchored on the two-digit index precisely so `compare_*.png` cannot be swept
    away as if it were a variant frame.
    """
    limit = MAX_CACHED_GRADE_FRAMES if keep is None else keep
    for pattern in ("grade_[0-9][0-9]_*.png", "compare_*.png"):
        files = sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
        for stale in files[:-limit] if len(files) > limit else []:
            stale.unlink(missing_ok=True)
            stale.with_suffix(".stats.json").unlink(missing_ok=True)


def preview_grades(job: Job, variants: dict[str, dict[str, Any]], at_seconds: float | None = None,
                   options: dict[str, Any] | None = None) -> dict[str, Any]:
    """One graded frame per variant, at the size and sharpening resolve will use.

    See `render_grade_context`: previewing at the nominal 1080x1920 with no
    source width shows a softer frame than the render, which defeats the point
    of approving a look here.
    """
    state = job.load()
    source = Path(state["input_path"])
    opts = options if options is not None else state.get("options", {})
    if at_seconds is None:
        at_seconds = probe_duration(source) / 2

    ctx = render_grade_context(job, opts)
    input_sha = state.get("input_sha256") or ""
    preview_dir = job.dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"at_seconds": round(at_seconds, 2), "variants": [],
                               "context": ctx}
    images: list[tuple[str, Path]] = []
    cached_all = True

    # The reference frame: colour untouched, so the numbers below say how far
    # each grade pushes the picture away from the footage as shot.
    raw_grade = {}
    raw_path, raw_measured, raw_cached = _variant_frame(
        source, at_seconds, raw_grade, preview_dir, "raw", 0, ctx, input_sha)
    cached_all &= raw_cached
    images.append(("raw", raw_path))
    results["variants"].append({"name": "raw", "image": job.rel(raw_path), **raw_measured})

    for index, (name, grade) in enumerate(variants.items(), start=1):
        applied = _with_measured_sharpen(grade, ctx["measured_sharpen"])
        path, measured, was_cached = _variant_frame(
            source, at_seconds, applied, preview_dir, name, index, ctx, input_sha)
        cached_all &= was_cached
        images.append((name, path))
        results["variants"].append({"name": name, "image": job.rel(path),
                                    "grade": grade, "applied_grade": applied, **measured})

    # Named `compare_`, not `grade_`, so the cache trim can tell the two apart.
    sheet_key = hash_inputs([p.name for _, p in images])[:10]
    sheet = preview_dir / f"compare_{sheet_key}.png"
    if not sheet.exists():
        contact_sheet(images, sheet)
    results["contact_sheet"] = job.rel(sheet)
    results["cached"] = cached_all
    _trim_grade_cache(preview_dir)
    return results


def preview_audio(job: Job, at_seconds: float, duration: float = 8.0,
                  presets: list[str] | None = None) -> dict[str, Any]:
    """One short audio sample per cleanup preset, plus the numbers behind them.

    Audio has to be judged by ear — these are files to listen to, not a verdict.
    The measurements come along because "rè" is usually a signal-to-noise
    problem, and the level in a speech gap says more than an opinion does.
    """
    state = job.load()
    source = Path(state["input_path"])
    tempo = float(state.get("options", {}).get("tempo", 1.06))
    preview_dir = job.dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"at_seconds": at_seconds, "samples": []}
    cached_all = True
    for preset in (presets or list(AUDIO_PRESETS)):
        chain = build_audio_chain(preset, tempo)
        # Keyed on everything that changes the audio, so re-opening the panel at
        # the same moment replays the files instead of re-encoding five presets.
        key = hash_inputs({"chain": chain, "at": round(at_seconds, 2),
                           "dur": round(duration, 2),
                           "sha": job.load().get("input_sha256") or ""})[:10]
        out_path = preview_dir / f"audio_{preset}_{key}.m4a"
        if not out_path.exists():
            cached_all = False
            result = _run(["ffmpeg", "-y", "-ss", f"{at_seconds:.2f}", "-t", f"{duration:.2f}",
                           "-i", str(source), "-vn", "-af", chain,
                           "-c:a", "aac", "-b:a", "192k", str(out_path), "-loglevel", "error"])
            if result.returncode != 0:
                raise PreviewError(f"Xử lý audio '{preset}' lỗi: {result.stderr.strip()[:200]}")
        results["samples"].append({"preset": preset, "path": str(out_path),
                                   "rel": job.rel(out_path)})
    results["cached"] = cached_all
    return results


JUDGE_PROMPT = """Bạn là kỹ sư âm thanh. Dưới đây là các phiên bản CÙNG MỘT đoạn thoại tiếng Việt, xử lý khác nhau, gửi theo đúng thứ tự nhãn.

Chấm từng bản thang 1-10:
- do_sach: còn tiếng ù/nhiễu nền không
- do_ro: giọng rõ chữ, có hiện diện không
- tu_nhien: có bị méo/ướt/nghẹt/máy móc do xử lý không
- giong_shotgun: giống thu bằng micro shotgun chuyên nghiệp tới đâu

Nói rõ bản nào nên dùng, bản nào xử lý quá tay và biểu hiện cụ thể.
CHỈ trả JSON: {"danh_gia":[{"ban":"<nhan>","do_sach":n,"do_ro":n,"tu_nhien":n,"giong_shotgun":n,"nhan_xet":"..."}],"nen_dung":"<nhan>","ly_do":"...","canh_bao":"..."}"""


def judge_audio(job: Job, at_seconds: float = 20.0, duration: float = 6.0,
                presets: list[str] | None = None, model: str | None = None) -> dict[str, Any]:
    """Render one sample per preset and have a model listen to them.

    The measurements in `preview_audio` say how much noise went away; this says
    whether the voice survived. Both are needed — they disagreed here, and the
    ear was right: the best signal-to-noise scored worst for naturalness.
    """
    from lib.talking_head_edit.director_client import chat_with_audio

    samples = preview_audio(job, at_seconds=at_seconds, duration=duration, presets=presets)
    compact: list[tuple[str, Path]] = []
    for entry in samples["samples"]:
        source = Path(entry["path"])
        mp3 = source.with_name(f"judge_{entry['preset']}.mp3")
        # small mono files keep the request light; artefacts stay audible
        _run(["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-ar", "24000",
              "-b:a", "40k", str(mp3), "-loglevel", "error"])
        compact.append((entry["preset"], mp3))

    verdict, usage = chat_with_audio(JUDGE_PROMPT, compact, model=model)
    return {"at_seconds": at_seconds, "presets": [p for p, _ in compact],
            "verdict": verdict, "usage": usage}


def preview_clip(job: Job, start_seconds: float = 0.0, duration: float = 5.0,
                 scale: float = 0.5, fps: int = 30, crf: int | None = None) -> dict[str, Any]:
    """A short, half-size render — motion, audio and look, in about a minute.

    This is the approval step. A full 1080x1920 render is ~5 minutes, which is
    far too slow to iterate on a look, and far too slow to discover that the
    music or a card is wrong. Approve the clip first, render the full thing
    only when it's actually wanted.
    """
    state = job.load()
    version = int(state["current_version"])
    props_path = job.props_path(version)
    if not props_path.exists():
        raise PreviewError(f"Chưa có props v{version} — chạy resolve trước.")

    props = json.loads(props_path.read_text(encoding="utf-8"))
    total = float(props.get("durationSeconds") or 0)
    start = max(0.0, min(start_seconds, max(0.0, total - 1)))
    first_frame = int(start * fps)
    last_frame = min(int((start + duration) * fps), max(0, int(total * fps) - 1))

    preview_dir = job.dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    out_path = preview_dir / f"clip_v{version}_{int(start)}s.mp4"

    # Re-stage first. The staged src.mp4 is a hardlink, and every resolve run
    # writes a brand new file — so without this a preview happily renders the
    # PREVIOUS cut and grade while reporting the current version.
    _stage_assets(job, props)

    # Same encoder settings as the real render, otherwise the preview lies
    # about sharpness — the deliverable's CRF is a big part of how crisp it looks.
    options = state.get("options", {})
    crf = crf if crf is not None else int(options.get("render_crf", 17))
    jpeg_quality = int(options.get("render_jpeg_quality", 100))
    from lib.talking_head_edit.stages.render import _configured_max_concurrency
    preview_concurrency = max(1, min(4, _configured_max_concurrency() // 2))
    result = _run([
        "npx", "remotion", "render", "src/index.tsx", "MonaTimeline", str(out_path),
        f"--props={props_path}", f"--public-dir={job.render_public_dir}",
        f"--frames={first_frame}-{last_frame}", f"--scale={scale}",
        f"--crf={crf}", f"--jpeg-quality={jpeg_quality}", f"--concurrency={preview_concurrency}",
    ], timeout=900, cwd=COMPOSER_DIR)
    if result.returncode != 0 or not out_path.exists():
        raise PreviewError("Remotion preview lỗi:\n" + (result.stdout + result.stderr)[-500:])

    return {
        "path": str(out_path),
        "rel": job.rel(out_path),
        "version": version,
        "start_seconds": round(start, 2),
        "duration_seconds": round((last_frame - first_frame + 1) / fps, 2),
        "scale": scale,
        "crf": crf,
    }


def composition_still(job: Job, frame_number: int, out_path: Path | None = None,
                      props_override: dict[str, Any] | None = None) -> Path:
    """One frame through the real renderer: overlays, frame, cards and all."""
    state = job.load()
    version = int(state["current_version"])
    props_path = job.props_path(version)
    if not props_path.exists():
        raise PreviewError(f"Chưa có props v{version} — chạy resolve trước.")

    preview_dir = job.dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    if props_override:
        props = json.loads(props_path.read_text(encoding="utf-8"))
        props.update(props_override)
        props_path = preview_dir / "props_preview.json"
        props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    out_path = out_path or preview_dir / f"still_{frame_number:05d}.png"
    _stage_assets(job, json.loads(props_path.read_text(encoding="utf-8")))
    result = _run([
        "npx", "remotion", "still", "src/index.tsx", "MonaTimeline", str(out_path),
        f"--props={props_path}", f"--public-dir={job.render_public_dir}",
        f"--frame={frame_number}",
    ], timeout=600, cwd=COMPOSER_DIR)
    if result.returncode != 0 or not out_path.exists():
        raise PreviewError(
            "Remotion still lỗi:\n" + (result.stdout + result.stderr)[-500:]
        )
    return out_path
