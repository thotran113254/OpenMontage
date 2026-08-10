"""Decide "shotgun" vs "shotgun_dry" by measuring the room, not by asking.

`resolve_media.py`'s own docstring already recorded the failure mode for going
further than this: the audio preset with the best measured SNR scored WORST
for perceived naturalness in a blind listening pass. Room liveness — how long
residual energy lingers after a word ends — is the one axis that same blind
test agrees with a direct measurement on ("chỉ nên dùng shotgun_dry cho phòng
vang nặng"), which is why this module measures only that axis and leaves
voice / voice_strong / off to a human.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np

MIN_GAP_SECONDS = 0.25
MAX_GAPS_SAMPLED = 6
WINDOW_MS = 15.0
SAMPLE_RATE = 24000
# Conservative on purpose: shotgun_dry scored 6/10 naturalness against
# shotgun's 8/10 on a normal room (see resolve_media.AUDIO_PRESETS), so
# wrongly picking "dry" on a fine room costs more than missing a genuinely
# live one. NOT yet verified against a real reverberant-room recording — same
# caveat as resolve_media.default_sharpening's unverified native-res end.
DECAY_MS_DRY_THRESHOLD = 180.0


class AudioCalibrationError(RuntimeError):
    pass


def find_gaps(words: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Silences between words long enough to hold a measurable decay tail."""
    gaps: list[tuple[float, float]] = []
    for a, b in zip(words, words[1:]):
        start, end = float(a["end"]), float(b["start"])
        if end - start >= MIN_GAP_SECONDS:
            gaps.append((start, end))
    return gaps


def rms_envelope(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE,
                 window_ms: float = WINDOW_MS) -> np.ndarray:
    window = max(1, int(sample_rate * window_ms / 1000))
    n = len(pcm) // window
    if n == 0:
        return np.array([])
    trimmed = pcm[: n * window].reshape(n, window)
    return np.sqrt((trimmed ** 2).mean(axis=1) + 1e-9)


def decay_ms_of(envelope: np.ndarray, window_ms: float = WINDOW_MS) -> float | None:
    """Time from the gap's loudest window down to near its own quiet floor.

    The floor is the gap's OWN 20th percentile, not a fixed level, because
    what matters is the tail relative to this room's noise floor, not an
    absolute number that a phone's AGC or mic gain would shift wholesale.
    """
    if len(envelope) < 3:
        return None
    peak = envelope[0]
    floor = float(np.percentile(envelope, 20))
    if peak <= floor * 1.05:
        return 0.0
    target = floor + (peak - floor) * 0.15   # ~-16 dB toward the floor
    for i, value in enumerate(envelope):
        if value <= target:
            return i * window_ms
    return len(envelope) * window_ms


def _extract_pcm(source: Path, start: float, end: float) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-loglevel", "error", "pipe:1"],
        capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise AudioCalibrationError(f"ffmpeg lỗi: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64)


def measure_room(source: Path, words: list[dict[str, Any]]) -> dict[str, Any]:
    """Average decay tail across a few real silences → "shotgun" or "shotgun_dry"."""
    gaps = find_gaps(words)[:MAX_GAPS_SAMPLED]
    if not gaps:
        return {"decay_ms": None, "samples": 0, "preset": "shotgun",
                "ly_do": "không đủ khoảng lặng để đo — giữ mặc định shotgun"}

    decays: list[float] = []
    for start, end in gaps:
        try:
            pcm = _extract_pcm(source, start, end)
        except AudioCalibrationError:
            continue
        decay = decay_ms_of(rms_envelope(pcm))
        if decay is not None:
            decays.append(decay)

    if not decays:
        return {"decay_ms": None, "samples": 0, "preset": "shotgun",
                "ly_do": "không đo được đuôi âm ở khoảng lặng nào — giữ mặc định shotgun"}

    avg_decay = sum(decays) / len(decays)
    preset = "shotgun_dry" if avg_decay >= DECAY_MS_DRY_THRESHOLD else "shotgun"
    return {
        "decay_ms": round(avg_decay, 1), "samples": len(decays), "preset": preset,
        "ly_do": (f"đuôi âm trung bình {avg_decay:.0f}ms trên {len(decays)} khoảng lặng — "
                  + ("phòng vang, chọn shotgun_dry" if preset == "shotgun_dry"
                     else "phòng bình thường, giữ shotgun")),
    }
