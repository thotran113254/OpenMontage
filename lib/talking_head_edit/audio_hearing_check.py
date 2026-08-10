"""Does the model actually hear the audio, or is it pattern-matching the prompt?

Everything downstream assumes the calibrate stage's ears are real. That is a
testable claim, so this tests it: take one clip, inject ONE known defect into
each copy, and check whether the model's severity score for that defect rises
in the copy that has it. A model reading the prompt back at us scores the same
everywhere; a model listening scores the injected defect higher.

Two traps are built in:

* two identical copies under different labels — a model inventing differences
  will score them differently
* severity is asked for EVERY defect on EVERY sample, so a lucky guess on the
  one that was injected does not pass on its own

Run it whenever the prompt, the model or the sample encoding changes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# One ffmpeg filter per defect, each strong enough to be unmistakable to a
# listener but still in the range real footage produces.
# Gain in dB at strength 1.0. Scaling these is the point: detecting a +18 dB
# hum proves very little, because the presets being chosen between differ by a
# few dB. The strength that stops being detected is the useful number.
DEFECT_GAINS: dict[str, tuple[str, float]] = {
    "u_am_tram": ("equalizer=f=100:t=q:w=0.7:g={g}", 18),        # mains/aircon rumble
    "bi_boc": ("equalizer=f=400:t=q:w=1:g={g}", 11),             # speaking into a box
    # Harshness rather than sibilance-proper. The textbook 7 kHz band sits ~28 dB
    # below the voice on this footage, so boosting it stays inaudible; 3.8 kHz is
    # where a harshness probe is reliably heard.
    "xi_gio": ("equalizer=f=3800:t=q:w=1.2:g={g}", 14),
    "vo_tieng": ("volume={g}dB,alimiter=limit=0.95:level=disabled", 14),  # clipping
}
# These two are not gains in dB, so they scale their own way.
REVERB = "vang_phong"
HISS = "xi_nen"
ALL_DEFECTS = (*DEFECT_GAINS, REVERB, HISS)

# Labels must carry NO information. Naming a sample after the defect inside it
# hands over the answer: an early version of this check did exactly that and
# scored a perfect 6/6 at every strength, including one so faint it should have
# been inaudible — the giveaway that the labels, not the audio, were being read.
# The order below is fixed but deliberately not the order defects are generated
# in, so position leaks nothing either.
LABEL_ORDER = ("m4", "m7", "m2", "m8", "m1", "m5", "m3", "m6")


def defect_filter(defect: str, strength: float) -> tuple[str, bool]:
    """(ffmpeg filter, needs_filter_complex) for one defect at one strength."""
    if defect == HISS:
        # mixed in, not filtered out of the voice, so it needs two inputs
        return (f"anoisesrc=color=white:amplitude={round(0.035 * strength, 4)}:"
                f"sample_rate=48000[n];"
                f"[0:a][n]amix=inputs=2:duration=first:weights=1 0.85[out]", True)
    if defect == REVERB:
        return f"aecho=0.8:0.88:55:{round(0.5 * strength, 3)}", False
    template, gain = DEFECT_GAINS[defect]
    return template.format(g=round(gain * strength, 2)), False

_VOCAB = ", ".join(ALL_DEFECTS)

PROMPT_VI = f"""Bạn là kỹ sư âm thanh. Dưới đây là nhiều bản ghi CÙNG MỘT câu nói tiếng Việt, gửi theo đúng thứ tự nhãn. Mỗi bản có thể đã bị thêm lỗi kỹ thuật, hoặc không.

Với TỪNG bản, chấm mức độ nghiêm trọng 0-10 cho TỪNG lỗi sau (0 = hoàn toàn không có, 10 = rất nặng):
- u_am_tram: tiếng ù/rền trầm dưới 150 Hz
- xi_nen: tiếng xì nền đều đều ở dải cao
- vang_phong: đuôi tiếng vọng lại, như nói trong phòng trống
- bi_boc: nghe bí như nói trong thùng, dư 200-500 Hz
- xi_gio: âm /s/ /x/ /ch/ chói gắt
- vo_tieng: méo/rè do tín hiệu quá to

Chấm theo đúng thứ bạn NGHE được ở từng bản. Các bản khác nhau ở mức độ lỗi, đừng cho điểm giống hệt nhau nếu chúng thực sự khác — và cũng đừng bịa ra khác biệt nếu chúng nghe giống nhau.

CHỈ trả JSON: {{"ket_qua":[{{"ban":"<nhãn>","u_am_tram":n,"xi_nen":n,"vang_phong":n,"bi_boc":n,"xi_gio":n,"vo_tieng":n}}]}}"""

PROMPT_EN = f"""You are an audio engineer. Below are several recordings of THE SAME Vietnamese sentence, sent in label order. Each one may or may not have had a technical defect added to it.

For EACH recording, rate the severity 0-10 of EACH defect below (0 = completely absent, 10 = severe):
- u_am_tram: low-frequency hum/rumble below 150 Hz
- xi_nen: steady broadband hiss in the high frequencies
- vang_phong: audible room reverb tail, as if speaking in an empty room
- bi_boc: boxy/muffled, excess energy around 200-500 Hz
- xi_gio: harsh, piercing sibilance on /s/ /sh/ /ch/ sounds
- vo_tieng: distortion/clipping from an over-hot signal

Rate what you actually HEAR in each recording. The recordings differ in how affected they are — do not give identical scores if they genuinely differ, and do not invent differences if they sound the same.

Reply with JSON ONLY: {{"ket_qua":[{{"ban":"<label>","u_am_tram":n,"xi_nen":n,"vang_phong":n,"bi_boc":n,"xi_gio":n,"vo_tieng":n}}]}}"""


def _encode(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi: {result.stderr.strip()[-400:]}")


def build_probe_set(source: Path, out_dir: Path, at_seconds: float,
                    duration: float = 5.0,
                    strength: float = 1.0) -> list[tuple[str, Path, str | None]]:
    """One clean control (twice) plus one copy per injected defect.

    `strength` scales every defect; run the same set at 1.0 and at 0.3 to find
    where the ear stops working. Returns (label, path, injected_or_None).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"s{int(strength * 100)}"
    base = out_dir / "_base.wav"
    _encode(["ffmpeg", "-y", "-ss", f"{at_seconds:.2f}", "-t", f"{duration:.2f}",
             "-i", str(source), "-vn", "-ac", "1", "-ar", "48000",
             str(base), "-loglevel", "error"])

    def to_mp3(src_args: list[str], name: str) -> Path:
        path = out_dir / f"{tag}_{name}.mp3"
        _encode(["ffmpeg", "-y", *src_args, "-ac", "1", "-ar", "24000", "-b:a", "40k",
                 str(path), "-loglevel", "error"])
        return path

    built: list[tuple[Path, str | None]] = [(to_mp3(["-i", str(base)], "ctl"), None)]
    for defect in ALL_DEFECTS:
        chain, complex_needed = defect_filter(defect, strength)
        args = ["-i", str(base)]
        args += (["-filter_complex", chain, "-map", "[out]"] if complex_needed
                 else ["-af", chain])
        built.append((to_mp3(args, f"x_{defect}"), defect))
    built.append((to_mp3(["-i", str(base)], "ctl2"), None))   # the twin

    # Neutral labels, and the control is not first in the sent order.
    labelled = list(zip(LABEL_ORDER, built))
    labelled.sort(key=lambda item: item[0])
    return [(label, path, injected) for label, (path, injected) in labelled]


def score_hearing(rows: list[dict[str, Any]],
                  probes: list[tuple[str, Path, str | None]]) -> dict[str, Any]:
    """Turn the model's severity grid into a verdict on whether it heard anything.

    `lift` is the number that matters: severity of the injected defect in the
    sample that has it, minus the same defect's severity in the control. A model
    that is not listening scores both the same and lifts to zero.
    """
    by_label = {str(r.get("ban")): r for r in rows if r.get("ban")}
    # The two clean copies are told apart by filename, not by label — the labels
    # sent to the model deliberately carry nothing.
    control_label = next((l for l, p, i in probes if i is None and p.stem.endswith("_ctl")), "")
    twin_label = next((l for l, p, i in probes if i is None and p.stem.endswith("_ctl2")), "")
    control = by_label.get(control_label, {})

    def severity(row: dict[str, Any], defect: str) -> float | None:
        value = row.get(defect)
        return float(value) if isinstance(value, (int, float)) else None

    detections: list[dict[str, Any]] = []
    for label, _, injected in probes:
        if not injected:
            continue
        here, there = severity(by_label.get(label, {}), injected), severity(control, injected)
        detections.append({
            "loi": injected,
            "diem_khi_co": here,
            "diem_doi_chung": there,
            "lift": None if here is None or there is None else round(here - there, 2),
        })

    lifts = [d["lift"] for d in detections if d["lift"] is not None]
    heard = [d for d in detections if (d["lift"] or 0) >= 2.0]

    # Consistency: the twin is byte-identical audio under another label.
    twin = by_label.get(twin_label, {})
    gaps = [abs(a - b) for defect in ALL_DEFECTS
            if (a := severity(control, defect)) is not None
            and (b := severity(twin, defect)) is not None]

    return {
        "chi_tiet": detections,
        "so_loi_nghe_ra": len(heard),
        "tong_so_loi": len(detections),
        "lift_trung_binh": round(sum(lifts) / len(lifts), 2) if lifts else None,
        "sai_lech_hai_ban_giong_nhau": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "ket_luan": _verdict(len(heard), len(detections), gaps),
    }


def _verdict(heard: int, total: int, gaps: list[float]) -> str:
    drift = sum(gaps) / len(gaps) if gaps else 0.0
    if not total:
        return "không chấm được"
    if heard >= total * 0.75 and drift <= 1.5:
        return "NGHE ĐƯỢC — bắt đúng lỗi và không bịa khác biệt"
    if heard >= total * 0.75:
        return f"nghe được nhưng thiếu ổn định (lệch {drift:.1f} điểm giữa hai bản giống nhau)"
    if heard >= total * 0.4:
        return "nghe được một phần — chỉ bắt được lỗi rõ nhất"
    return "KHÔNG NGHE ĐƯỢC — điểm không đổi khi lỗi được thêm vào"
