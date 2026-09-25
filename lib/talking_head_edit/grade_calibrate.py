"""Measure exposure and colour cast from this footage instead of guessing.

Sibling to `sharpen_calibrate.py`: same shape, same reason. The director LLM
fills in brightness/gamma/warmth as plausible-looking numbers without ever
seeing the actual pixels, and `calibrate.py`'s blind test already showed a
model asked to judge stills is not a reliable substitute either. Exposure and
white balance are two axes a mean-pixel measurement answers directly — no
search loop like sharpen needs, because there is no halo-style tradeoff to
balance against here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

# Same centred window sharpen_calibrate.py measures on — the speaker's face,
# not the room around it.
FACE_CROP = "crop=iw*0.55:ih*0.36:iw*0.23:ih*0.29"
# Top-left corner: usually background, not face, for a centred talking-head
# shot. A cluttered or off-centre frame can still land skin or clutter in
# here — this is a cheap proxy, not a segmentation, so its reading only drives
# a small, clamped correction (see MAX_WARMTH_CORRECTION), never a large swing.
BG_CROP = "crop=iw*0.18:ih*0.18:0:0"

TARGET_LUMA = 128.0    # neutral mid-grey target for the mean face-crop luma
LUMA_DEADBAND = 10.0   # do not correct drift smaller than this
MAX_GAMMA_CORRECTION = 0.25
MAX_BRIGHTNESS_CORRECTION = 0.06

# resolve_media.py's own skin-mask comment measured background surfaces at
# Cr-Cb -3..+9 on its reference footage; take the middle of that band as
# neutral and only correct once a reading sits clearly outside it.
BG_NEUTRAL_CR_CB = 3.0
WARM_DEADBAND = 8.0
MAX_WARMTH_CORRECTION = 6.0   # same unit as the `warmth` grade field


class GradeCalibrationError(RuntimeError):
    pass


def luma_of(rgb: np.ndarray) -> float:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return float((0.299 * r + 0.587 * g + 0.114 * b).mean())


def cr_minus_cb_of(rgb: np.ndarray) -> float:
    """Warm-vs-neutral reading: positive leans red/yellow, negative leans blue."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cb = -0.169 * r - 0.331 * g + 0.5 * b + 128
    cr = 0.5 * r - 0.419 * g - 0.081 * b + 128
    return float((cr - cb).mean())


def exposure_correction(luma: float) -> tuple[float, float]:
    """(brightness, gamma) nudge to bring `luma` back toward TARGET_LUMA.

    Gamma leads because it protects highlights already near clipping; a flat
    brightness add would lift those by the same amount as the shadows.
    """
    if luma < TARGET_LUMA - LUMA_DEADBAND:
        gap = TARGET_LUMA - luma
        # Progressive response: noticeable lift for dim indoor video without clipping highlights
        severity = min(1.0, max(0.2, (gap - LUMA_DEADBAND) / 40.0 + 0.25))
        return (round(MAX_BRIGHTNESS_CORRECTION * severity, 3),
                round(1.0 + MAX_GAMMA_CORRECTION * severity, 3))
    if luma > TARGET_LUMA + LUMA_DEADBAND:
        excess = min(1.0, (luma - TARGET_LUMA) / (255 - TARGET_LUMA))
        return (round(-MAX_BRIGHTNESS_CORRECTION * excess, 3),
                round(1.0 - MAX_GAMMA_CORRECTION * excess, 3))
    return (0.0, 1.0)


def warmth_correction(cr_minus_cb: float) -> float:
    """`warmth` grade-field nudge to bring the background back toward neutral."""
    if cr_minus_cb > BG_NEUTRAL_CR_CB + WARM_DEADBAND:
        return -round(min(MAX_WARMTH_CORRECTION, (cr_minus_cb - BG_NEUTRAL_CR_CB) * 0.5), 2)
    if cr_minus_cb < BG_NEUTRAL_CR_CB - WARM_DEADBAND:
        return round(min(MAX_WARMTH_CORRECTION, (BG_NEUTRAL_CR_CB - cr_minus_cb) * 0.5), 2)
    return 0.0


def _frame(source: Path, at_seconds: float, crop: str, out: Path) -> np.ndarray:
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{at_seconds:.3f}", "-i", str(source),
         "-frames:v", "1", "-vf", crop, str(out), "-loglevel", "error"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not out.exists():
        raise GradeCalibrationError(f"ffmpeg lỗi: {result.stderr.strip()[:300]}")
    return np.asarray(Image.open(out).convert("RGB"), dtype=np.float64)


def calibrate(source: Path, moments: list[float], work_dir: Path) -> dict[str, Any]:
    """Measured exposure/white-balance nudge, averaged over several moments."""
    work_dir.mkdir(parents=True, exist_ok=True)
    lumas: list[float] = []
    warmths: list[float] = []
    for index, at in enumerate(moments):
        face = _frame(source, at, FACE_CROP, work_dir / f"face_{index}.jpg")
        bg = _frame(source, at, BG_CROP, work_dir / f"bg_{index}.jpg")
        lumas.append(luma_of(face))
        warmths.append(cr_minus_cb_of(bg))

    luma = sum(lumas) / len(lumas)
    warm = sum(warmths) / len(warmths)
    brightness, gamma = exposure_correction(luma)
    warmth = warmth_correction(warm)

    parts = []
    if (brightness, gamma) != (0.0, 1.0):
        parts.append(f"mặt {'tối' if luma < TARGET_LUMA else 'sáng'} hơn mức trung tính "
                     f"({luma:.0f} so với {TARGET_LUMA:.0f})")
    if warmth != 0.0:
        parts.append(f"nền {'ám vàng/đỏ' if warm > BG_NEUTRAL_CR_CB else 'ám xanh'} "
                     f"hơn mức trung tính ({warm:.0f})")
    reason = "; ".join(parts) or "đã ở mức trung tính, không chỉnh"

    return {
        "luma_do_duoc": round(luma, 1), "target_luma": TARGET_LUMA,
        "cr_minus_cb_do_duoc": round(warm, 1), "target_cr_minus_cb": BG_NEUTRAL_CR_CB,
        "brightness": brightness, "gamma": gamma, "warmth": warmth,
        "so_khung_do": len(moments), "ly_do": reason,
    }
