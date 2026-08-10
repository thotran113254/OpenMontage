"""Objective description of what is wrong with a voice recording.

This exists because the model cannot hear (see `audio_hearing_check`): asked to
rank processed versions under neutral labels it scored 1/6 on defects as loud as
+18 dB. What it plausibly still knows is audio engineering — which filter fixes
which problem. So the split is: ffmpeg measures, the model prescribes, ffmpeg
checks the prescription worked.

Every number here is a difference, not an absolute, so it survives a recording
being quiet or loud. Speech gaps come from the Whisper word spine rather than a
silence detector, because on noisy footage a detector finds no silence at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# ffmpeg's bandpass defaults to width_type=q, so a bare `w=2400` is a Q of
# 2400 — a razor-thin sliver, not a 2400 Hz band. Every width here is in Hz
# and says so.
VOICE_BAND = "bandpass=f=1650:width_type=h:w=2700"   # ~300-3000 Hz
# A single lowpass at 150 Hz has a skirt gentle enough that most of what it
# passes is leakage from the much louder voice band: measured through one, a
# highpass at 90 Hz appeared to remove 0.1 dB of rumble. Cascading gets the
# slope steep enough to measure the band itself.
# 80 Hz, not 150: below 150 sits the male vocal fundamental (~100-130 Hz here),
# so a "rumble" reading taken there mostly measures the speaker. Under 80 Hz the
# metric responds the way it should — a highpass at 90 moves it 7.4 dB, at 120
# it moves 11.1 dB, where the 150 Hz version budged 1.9 dB.
LOW_BAND = ",".join(["lowpass=f=80:p=2"] * 3)
HIGH_BAND = ",".join(["highpass=f=2000:p=2"] * 2)
GAP_PAD = 0.06                            # keep clear of word onsets/tails


def _mean_volume(source: Path, filters: str, start: float | None = None,
                 duration: float | None = None) -> float | None:
    command = ["ffmpeg", "-hide_banner"]
    if start is not None:
        command += ["-ss", f"{start:.3f}"]
    if duration is not None:
        command += ["-t", f"{duration:.3f}"]
    command += ["-i", str(source), "-vn", "-af", f"{filters},volumedetect", "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    for line in (result.stdout + result.stderr).splitlines():
        if "mean_volume:" in line:
            try:
                return round(float(line.split("mean_volume:")[1].split("dB")[0].strip()), 1)
            except ValueError:
                return None
    return None


def find_gaps(words: list[dict[str, Any]], window: tuple[float, float],
              min_length: float = 0.25, limit: int = 4) -> list[tuple[float, float]]:
    """Silences between words — where noise is audible and speech is not."""
    start, end = window
    inside = [w for w in words if start <= float(w["start"]) <= end]
    gaps: list[tuple[float, float]] = []
    for previous, current in zip(inside, inside[1:]):
        gap_start = float(previous["end"]) + GAP_PAD
        gap_end = float(current["start"]) - GAP_PAD
        if gap_end - gap_start >= min_length:
            gaps.append((gap_start, gap_end))
    gaps.sort(key=lambda g: g[1] - g[0], reverse=True)
    return gaps[:limit]


def spectral_edge(source: Path, window: tuple[float, float]) -> int | None:
    """Highest band still within 35 dB of the voice band.

    Worth knowing before prescribing a presence lift or a de-esser: on this
    footage the spectrum rolls off smoothly (-33.9 dB at 1 kHz to -69.8 dB at
    10 kHz), so a shelf at 10 kHz has almost nothing to lift.
    """
    start, length = window[0], window[1] - window[0]
    reference = _mean_volume(source, VOICE_BAND, start, length)
    if reference is None:
        return None
    edge = None
    for frequency in (3000, 4000, 5000, 6000, 8000, 10000, 12000):
        level = _mean_volume(source, f"bandpass=f={frequency}:width_type=h:w=800", start, length)
        if level is not None and level - reference > -35:
            edge = frequency
    return edge


def word_tail_ratio(source: Path, words: list[dict[str, Any]],
                    window: tuple[float, float]) -> float | None:
    """Energy in the last 80 ms of a word, relative to its middle.

    This is the guard that makes a measurement-driven loop safe. Every cleanup
    that scored best on noise so far was also the one a listener rejected for
    "mất đuôi âm" — a gate or an aggressive denoiser buys quiet gaps by eating
    the ends of words. That damage shows up here as a falling ratio, so it can
    be optimised against instead of only being noticed after someone listens.
    """
    usable = [previous for previous, current in zip(words, words[1:])
              if float(current["start"]) - float(previous["end"]) >= 0.25
              and window[0] <= float(previous["end"]) <= window[1]]
    ratios = []
    for word in usable[:8]:
        start, end = float(word["start"]), float(word["end"])
        if end - start < 0.18:
            continue
        middle = _mean_volume(source, VOICE_BAND, start + (end - start) / 2 - 0.04, 0.08)
        tail = _mean_volume(source, VOICE_BAND, max(start, end - 0.08), 0.08)
        if middle is not None and tail is not None:
            ratios.append(tail - middle)
    return round(sum(ratios) / len(ratios), 1) if ratios else None


def measure(source: Path, words: list[dict[str, Any]],
            window: tuple[float, float]) -> dict[str, Any]:
    """The full picture, as numbers a prescription can be checked against."""
    start, length = window[0], window[1] - window[0]
    voice = _mean_volume(source, VOICE_BAND, start, length)
    gaps = find_gaps(words, window)

    def relative(filters: str) -> float | None:
        level = _mean_volume(source, filters, start, length)
        return None if level is None or voice is None else round(level - voice, 1)

    # Noise floor: measured inside real speech gaps, relative to the voice.
    floor_low, floor_high = None, None
    if gaps and voice is not None:
        lows, highs = [], []
        for gap_start, gap_end in gaps:
            span = gap_end - gap_start
            low = _mean_volume(source, LOW_BAND, gap_start, span)
            high = _mean_volume(source, HIGH_BAND, gap_start, span)
            if low is not None:
                lows.append(low)
            if high is not None:
                highs.append(high)
        if lows:
            floor_low = round(sum(lows) / len(lows) - voice, 1)
        if highs:
            floor_high = round(sum(highs) / len(highs) - voice, 1)

    # Reverb: how much is still ringing just after a word ends, in the band
    # where the noise floor is low enough not to mask the tail.
    tail = None
    if words and voice is not None:
        decays = []
        # A word followed immediately by another word gives a "tail" that is
        # really the next onset. Only words with real silence after them count.
        followed_by_silence = []
        for previous, current in zip(words, words[1:]):
            if float(current["start"]) - float(previous["end"]) >= 0.25:
                followed_by_silence.append(previous)
        for word in followed_by_silence:
            word_end = float(word["end"])
            if not (window[0] <= word_end <= window[1] - 0.3):
                continue
            during = _mean_volume(source, "bandpass=f=1250:width_type=h:w=1500", word_end - 0.12, 0.1)
            after = _mean_volume(source, "bandpass=f=1250:width_type=h:w=1500", word_end + 0.06, 0.1)
            if during is not None and after is not None:
                decays.append(after - during)
            if len(decays) >= 6:
                break
        if decays:
            tail = round(sum(decays) / len(decays), 1)

    return {
        "cua_so_giay": [round(window[0], 2), round(window[1], 2)],
        "muc_giong_db": voice,
        "u_am_duoi_80hz_db": relative(LOW_BAND),
        "boc_200_500_db": relative("bandpass=f=350:width_type=h:w=300"),
        "nen_nhieu_tram_db": floor_low,
        "nen_nhieu_cao_db": floor_high,
        "duoi_vang_db": tail,
        "dinh_tan_so_hz": spectral_edge(source, window),
        "so_khoang_lang": len(gaps),
        "dinh_true_peak_db": _mean_volume(source, "anull", start, length),
        # Guard against buying quiet gaps with chopped word endings.
        "duoi_tu_con_lai_db": word_tail_ratio(source, words, window),
    }


def compare(before: dict[str, Any], after: dict[str, Any],
            keys: tuple[str, ...] = ("u_am_duoi_80hz_db", "boc_200_500_db",
                                     "nen_nhieu_tram_db", "nen_nhieu_cao_db",
                                     "duoi_vang_db",
                                     "duoi_tu_con_lai_db")) -> dict[str, float | None]:
    """Change per measurement. Negative = the problem got quieter.

    `duoi_tu_con_lai_db` is the exception and must stay in this list: there,
    falling means word endings are being eaten, which is the damage every
    noise-optimal cleanup has done so far.
    """
    delta: dict[str, float | None] = {}
    for key in keys:
        a, b = before.get(key), after.get(key)
        delta[key] = None if a is None or b is None else round(b - a, 1)
    return delta
