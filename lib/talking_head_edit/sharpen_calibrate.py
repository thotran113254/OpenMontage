"""Pick the sharpening amount by measuring this footage, not by assuming.

The amount that suits a clip depends on how soft it arrives — bitrate, focus,
light, and how far it has to be enlarged. A fixed number, or one derived from
the upscale factor alone, is right for the clip it was tuned on and drifts on
every other. So: grade one real frame at several amounts, measure, and keep the
smallest amount that reaches the target.

Two numbers are measured, and the second is what makes this safe to automate:

* `detail` — mean absolute Laplacian over the face. More sharpening always
  raises it, so on its own it has no stopping point and would run away.
* `overshoot` — how far pixels are pushed beyond the brightest and darkest
  values present in their own neighbourhood before sharpening. That is exactly
  what a halo is: a rim of light and dark that was not in the picture. It rises
  with the amount too, so it is the brake.

Measured on a graded still rather than a rendered clip. That is deliberate and
narrow: stills overstate how much fine detail survives to the viewer, so they
must not be used to choose an absolute level — but both clips here go through
the same delivery path afterwards, so comparing them to each other is sound.
The absolute target below comes from a level that was checked on real renders.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from lib.talking_head_edit.resolve_media import build_grade_chain, default_sharpening

# Both anchors come from the level that was verified on real renders and
# approved by eye: 720x1280 source into a 1012-wide A-roll at sharpen 1.6 /
# clarity 0.85, measured with the functions below on the crop below. Anchors
# taken under any other crop or clarity do not transfer — an earlier pair,
# measured with clarity left on in the reference, put the ceiling BELOW the
# approved level and quietly capped this footage at 1.0.
# Both are averages over SAMPLE_FRAMES frames, which is how `calibrate` reads
# them — anchoring on one frame and comparing against a three-frame average put
# the target systematically out of reach and pinned this footage at the ceiling.
TARGET_DETAIL = 12.5        # detail at the approved level, averaged
MAX_OVERSHOOT = 1.50        # sharpen 2.0 reaches 1.49 here; rims show past it
SHARPEN_RANGE = (0.0, 2.0)
SEARCH_STEPS = 5
# One frame is not enough: face detail varies with expression and how much of
# the busy background is in shot. Calibrating on a single frame put the same
# footage at 1.6 in one moment and at the 2.0 ceiling in another.
SAMPLE_FRAMES = 3
# Middle of the frame, where the speaker is. Proportional so it survives a
# different output size; it does assume a roughly centred talking head.
FACE_CROP = "crop=iw*0.55:ih*0.36:iw*0.23:ih*0.29"


class SharpenCalibrationError(RuntimeError):
    pass


def _still(source: Path, at_seconds: float, grade: dict[str, Any], out_path: Path,
           width: int, height: int, source_width: int | None) -> Path:
    chain = build_grade_chain(grade, width, height, source_width=source_width)
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at_seconds:.3f}", "-i", str(source), "-frames:v", "1",
         "-vf", f"{chain},{FACE_CROP}", str(out_path), "-loglevel", "error"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not out_path.exists():
        raise SharpenCalibrationError(f"ffmpeg lỗi: {result.stderr.strip()[:300]}")
    return out_path


def _luma(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def detail_of(image: np.ndarray) -> float:
    """Mean absolute Laplacian — how much fine structure is present."""
    padded = np.pad(image, 1, mode="edge")
    laplacian = (4 * padded[1:-1, 1:-1]
                 - padded[:-2, 1:-1] - padded[2:, 1:-1]
                 - padded[1:-1, :-2] - padded[1:-1, 2:])
    return float(np.abs(laplacian).mean())


def overshoot_of(reference: np.ndarray, processed: np.ndarray) -> float:
    """How far sharpening pushed pixels outside their own neighbourhood's range.

    A halo is exactly this: values brighter than anything that was nearby, or
    darker. Comparing against the local extremes of the UNSHARPENED frame keeps
    genuine contrast in the picture from counting against it.
    """
    padded = np.pad(reference, 1, mode="edge")
    stack = np.stack([padded[a:a + reference.shape[0], b:b + reference.shape[1]]
                      for a in range(3) for b in range(3)])
    local_max, local_min = stack.max(axis=0), stack.min(axis=0)
    above = np.maximum(processed - local_max, 0)
    below = np.maximum(local_min - processed, 0)
    return float((above + below).mean())


def calibrate(source: Path, moments: list[float] | float, grade: dict[str, Any],
              width: int, height: int, source_width: int | None,
              work_dir: Path, target_detail: float = TARGET_DETAIL,
              max_overshoot: float = MAX_OVERSHOOT) -> dict[str, Any]:
    """Smallest sharpening that reaches the target without visible rims.

    `moments` may be one timestamp or several; several is strongly preferred,
    as one frame's worth of face detail is not representative.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    clarity = grade.get("clarity", default_sharpening(source_width, width)[1])
    at_list = [moments] if isinstance(moments, (int, float)) else list(moments)

    references = [
        _luma(_still(source, at, {**grade, "sharpen": 0, "clarity": 0},
                     work_dir / f"sharpen_ref{index}.png", width, height, source_width))
        for index, at in enumerate(at_list)
    ]
    baseline_detail = sum(detail_of(r) for r in references) / len(references)

    low, high = SHARPEN_RANGE
    tried: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    for step in range(SEARCH_STEPS):
        amount = round((low + high) / 2, 2)
        images = [
            _luma(_still(source, at, {**grade, "sharpen": amount, "clarity": clarity},
                         work_dir / f"sharpen_{step}_{index}.png",
                         width, height, source_width))
            for index, at in enumerate(at_list)
        ]
        detail = sum(detail_of(i) for i in images) / len(images)
        overshoot = sum(overshoot_of(r, i) for r, i in zip(references, images)) / len(images)
        tried.append({"sharpen": amount, "detail": round(detail, 2),
                      "overshoot": round(overshoot, 3)})

        within_limits = overshoot <= max_overshoot
        if within_limits and (best is None or amount > best["sharpen"]):
            best = tried[-1]
        # too soft and still clean -> push; at the target or ringing -> back off
        if detail < target_detail and within_limits:
            low = amount
        else:
            high = amount

    if best is None:
        raise SharpenCalibrationError(
            "Không tìm được mức làm nét nào dưới ngưỡng viền — footage quá nhiễu?")

    reason = ("đạt mức nét mục tiêu" if best["detail"] >= target_detail
              else "chạm trần viền sáng trước khi đạt mục tiêu")
    return {
        "sharpen": best["sharpen"],
        "clarity": round(clarity, 2),
        "detail": best["detail"],
        "overshoot": best["overshoot"],
        "detail_goc": round(baseline_detail, 2),
        "so_khung_do": len(at_list),
        "target_detail": target_detail,
        "ly_do": reason,
        "da_thu": tried,
    }
