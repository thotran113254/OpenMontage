"""The delivery-quality cut: resolve's plan, re-encoded for the real render.

`resolve` encodes at draft quality (the make_preview_proxy tier): it runs on
every preview and revise, and the browser Player needs a light file. What it
persists as `cut_plan` in resolve_report is the whole decision — spans, grade
chains as built, tempo, frame rate, output size, audio preset, the cold-open
window — so a full-size render re-executes that plan once at
`intermediate_preset` / `intermediate_crf` (medium / 12: at crf 12 instead of
17 the deliverable measured 2.85 vs 2.65 on a face crop).

Every piece is frame-capped, so the result has exactly the draft's frames and
every event in props lands on the same frame. That is checked, not assumed: a
frame count that differs from the draft's raises instead of rendering captions
against a shifted picture.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from typing import Any

from lib.talking_head_edit.cache import hash_inputs
from lib.talking_head_edit.resolve_cut import (
    SEGMENT_SUFFIX,
    apply_master_audio,
    cut_and_grade_multi,
    prepend_teaser,
)
from lib.talking_head_edit.resolve_media import ResolveError
from lib.talking_head_edit.resolve_spans import Span

DELIVERABLE_NAME = "src_master.mp4"
_RECIPE_FILES = ("resolve_cut.py", "resolve_media.py", "resolve_spans.py")


def _code_fingerprint(source: str) -> str:
    """The code's syntax tree, without comments or docstrings.

    Hashing the raw text re-encoded a whole deliverable — ten minutes of
    ffmpeg on the shared VPS — after a docstring edit that changed nothing.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def _recipe_hash() -> str:
    here = Path(__file__).parent
    return hash_inputs([_code_fingerprint((here / name).read_text(encoding="utf-8"))
                        for name in _RECIPE_FILES])


def count_frames(path: Path) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def load_plan(job, version: int) -> dict[str, Any]:
    report_path = job.dir / f"resolve_report_v{version}.json"
    try:
        plan = json.loads(report_path.read_text(encoding="utf-8")).get("cut_plan")
    except (OSError, json.JSONDecodeError):
        plan = None
    if not plan:
        raise ResolveError(
            f"resolve_report v{version} không có cut_plan (resolve từ bản cũ) — "
            "chạy lại resolve rồi render.")
    return plan


def ensure(job, version: int, options: dict[str, Any]) -> Path:
    """The delivery-quality src for `version`, built once and reused while fresh."""
    plan = load_plan(job, version)
    preset = str(options.get("intermediate_preset") or "medium")
    crf = int(options.get("intermediate_crf", 12))
    signature = hash_inputs({"plan": plan, "encode": [preset, crf], "recipe": _recipe_hash(),
                             "draft_frames": count_frames(job.src_path)})
    out_path = job.dir / DELIVERABLE_NAME
    meta_path = out_path.with_suffix(".json")
    try:
        fresh = (out_path.exists()
                 and json.loads(meta_path.read_text(encoding="utf-8")).get("signature") == signature)
    except (OSError, json.JSONDecodeError):
        fresh = False
    if fresh:
        job.emit("log", "render", f"Dùng lại bản cắt chất lượng cao ({preset}, crf {crf})")
        return out_path

    job.emit("log", "render", f"Encode bản cắt chất lượng cao ({preset}, crf {crf}) từ cut_plan…")
    meta_path.unlink(missing_ok=True)
    tmp_path = job.dir / f"_{DELIVERABLE_NAME}"
    premaster = job.dir / f"_deliverable_premaster{SEGMENT_SUFFIX}"
    teaser = plan.get("teaser")
    fps = int(plan["fps"])
    try:
        cut_and_grade_multi(
            [Span(str(s["src"]), Path(s["path"]), float(s["start"]), float(s["end"]))
             for s in plan["spans"]],
            tmp_path, plan["grade_chains"], float(plan["tempo"]), fps,
            preset=preset, crf=crf, audio_preset=str(plan["audio_preset"]),
            work_dir=job.dir, probes=plan.get("probes") or {},
            target_size=tuple(plan["target_size"]),
            on_log=lambda message: job.emit("log", "render", message),
            keep_joined=premaster if teaser else None)
        if teaser:
            prepend_teaser(premaster, float(teaser[0]), float(teaser[1]), fps,
                           preset=preset, crf=crf)
            apply_master_audio(premaster, tmp_path, str(plan["audio_preset"]))

        draft, built = count_frames(job.src_path), count_frames(tmp_path)
        if built != draft:
            raise ResolveError(
                f"Bản chất lượng cao có {built} khung, bản nháp có {draft} — "
                "caption/card sẽ lệch hình. Chạy lại resolve rồi render.")
        tmp_path.replace(out_path)
    finally:
        premaster.unlink(missing_ok=True)
        tmp_path.unlink(missing_ok=True)

    meta_path.write_text(json.dumps({"signature": signature, "version": version,
                                     "preset": preset, "crf": crf, "frames": built}),
                         encoding="utf-8")
    return out_path
