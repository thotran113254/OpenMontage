"""Stage — find the right look and sound on a sample, before rendering 93 seconds of it.

OFF BY DEFAULT. A blind test says this stage was not doing what it looks like it
was doing, and the evidence is in `audio_hearing_check`:

* Told which defect was in which sample (the labels were the defect names), the
  model scored 6/6 at every strength — including one too faint to hear.
* With the same audio under neutral labels m1..m8, it scored 1/6 with an 18 dB
  hum injected, and rated two byte-identical files 1.8-3.2 points apart.
* Rerunning the real preset comparison with neutral, rotated labels changed the
  winner between rounds: shotgun / voice / shotgun on one model, voice / shotgun
  / shotgun on the other. With the real names it picked shotgun 6 times out of 6.

The audio IS reaching the model — asked to transcribe, both models returned the
Vietnamese sentence almost exactly. It hears WHAT IS SAID; it does not reliably
hear HOW IT SOUNDS.

The colour half is weaker evidence — those candidates differ subtly — but it did
not survive blinding either, and one model picked the first label in 3 of 4
rounds, which is position bias rather than judgement.

The settings this pipeline ships are not affected: they were arrived at by
measurement plus the operator's own eyes and ears, not by this stage. What is
withdrawn is the automatic per-video re-selection.

Re-enable with `calibrate_grade` / `calibrate_audio` once a model passes the
blind check — not before.

What it does when enabled: a few seconds of the actual footage go through each
candidate, a model ranks them, and the winner is written into the job's options
so `resolve` applies it to the whole video.

The original argument for it was that measurements pick the wrong option: the
highest signal-to-noise audio was rated worst for naturalness, the most
saturated grade read as "cháy vàng". That argument still holds against blind
faith in measurements — but the replacement has to be an ear that works, and
this one does not.

Sharpness was never asked of this stage: judging stills, the model picked 1.2
where the rendered deliverable needed 1.6, then called a measured 2.2x
difference invisible. Sharpening comes from `resolve_media.default_sharpening`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from lib.talking_head_edit.calibrate_candidates import (
    AUDIO_CANDIDATES, AUDIO_PROMPT, GRADE_CANDIDATES, GRADE_PROMPT, pick_winner,
)
from lib.talking_head_edit.director_client import chat_with_audio, chat_with_images
from lib.talking_head_edit.job_store import DEFAULT_OPTIONS
from lib.talking_head_edit.preview import grade_still, preview_audio
from lib.talking_head_edit.resolve_media import probe_duration
from lib.talking_head_edit.stages.resolve import aroll_pixel_size

# Sharpness is NOT among these on purpose — a model judging stills rated it
# wrong twice. It is set from the upscale factor instead.
GRADE_KEYS = ("do_sang", "mau_da", "ket_cau_da", "tu_nhien")
AUDIO_KEYS = ("do_sach", "do_vang", "do_ro", "tu_nhien")
SAMPLE_SECONDS = 6.0


def _sample_moment(job, words: list[dict[str, Any]]) -> float:
    """A moment with speech in it — the middle word, not the middle second."""
    if words:
        return float(words[len(words) // 2]["start"])
    return probe_duration(Path(job.load()["input_path"])) / 2


def calibrate_grade(job, spec_grade: dict[str, Any], at_seconds: float,
                    options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    source = Path(state["input_path"])
    out_dir = job.dir / "calibrate"
    # Same pixel size and same sharpening the deliverable's A-roll gets, so the
    # colour and skin being judged are the ones that ship.
    width, height = aroll_pixel_size(int(options.get("width", 1080)),
                                     int(options.get("height", 1920)),
                                     str(options.get("frame_preset", DEFAULT_OPTIONS["frame_preset"])))
    source_width = state.get("probe", {}).get("width")

    images: list[tuple[str, Path]] = [(
        "goc",
        grade_still(source, at_seconds,
                    {"tone_curve": 0, "vibrance": 0, "vignette": 0,
                     "skin_smooth": 0, "blemish_reduce": 0, "sharpen": 0, "clarity": 0},
                    out_dir / "grade_goc.jpg", width, height, source_width),
    )]
    for name, overrides in GRADE_CANDIDATES.items():
        images.append((name, grade_still(source, at_seconds, {**spec_grade, **overrides},
                                         out_dir / f"grade_{name}.jpg", width, height,
                                         source_width)))

    verdict, usage = chat_with_images(GRADE_PROMPT, images, model=options.get("model"))
    winner, how = pick_winner(verdict, list(GRADE_CANDIDATES), GRADE_KEYS)
    return {"verdict": verdict, "winner": winner, "chosen_by": how, "usage": usage,
            "images": [job.rel(p) for _, p in images]}


def calibrate_audio(job, at_seconds: float, options: dict[str, Any]) -> dict[str, Any]:
    samples = preview_audio(job, at_seconds=at_seconds, duration=SAMPLE_SECONDS,
                            presets=AUDIO_CANDIDATES)
    compact: list[tuple[str, Path]] = []
    for entry in samples["samples"]:
        source = Path(entry["path"])
        mp3 = job.dir / "calibrate" / f"audio_{entry['preset']}.mp3"
        mp3.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-ar", "24000",
                        "-b:a", "40k", str(mp3), "-loglevel", "error"],
                       capture_output=True, check=False)
        compact.append((entry["preset"], mp3))

    verdict, usage = chat_with_audio(AUDIO_PROMPT, compact, model=options.get("model"))
    winner, how = pick_winner(verdict, AUDIO_CANDIDATES, AUDIO_KEYS)
    return {"verdict": verdict, "winner": winner, "chosen_by": how, "usage": usage,
            "samples": [job.rel(p) for _, p in compact]}


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    version = int(state.get("current_version", 0))
    spec_grade: dict[str, Any] = {}
    if version and job.spec_path(version).exists():
        spec_grade = json.loads(job.spec_path(version).read_text(encoding="utf-8")).get("grade", {})

    words = []
    if job.spine_path.exists():
        words = json.loads(job.spine_path.read_text(encoding="utf-8"))["word_timestamps"]
    at_seconds = float(options.get("calibrate_at") or _sample_moment(job, words))

    job.emit("log", "calibrate",
             f"Dò thông số trên mẫu tại {at_seconds:.1f}s: "
             f"{len(GRADE_CANDIDATES)} bản màu + {len(AUDIO_CANDIDATES)} bản tiếng")

    report: dict[str, Any] = {"at_seconds": round(at_seconds, 2)}
    applied: dict[str, Any] = {}

    if options.get("calibrate_grade", False):
        grade = calibrate_grade(job, spec_grade, at_seconds, options)
        report["grade"] = grade
        if grade["winner"]:
            applied["grade_overrides"] = {
                **(options.get("grade_overrides") or {}),
                **GRADE_CANDIDATES[grade["winner"]],
            }
            job.emit("log", "calibrate",
                     f"Màu: chọn '{grade['winner']}' ({grade['chosen_by']}) — "
                     f"{str(grade['verdict'].get('ly_do', ''))[:90]}")
        else:
            job.emit("warning", "calibrate", "Không chọn được bản màu — giữ nguyên đề xuất")

    if options.get("calibrate_audio", False):
        audio = calibrate_audio(job, at_seconds, options)
        report["audio"] = audio
        problems = audio["verdict"].get("chan_doan")
        if problems:
            job.emit("log", "calibrate",
                     f"Bản thu gốc nghe ra: {', '.join(str(p) for p in problems)}")
        if audio["winner"]:
            applied["audio_preset"] = audio["winner"]
            job.emit("log", "calibrate",
                     f"Tiếng: chọn '{audio['winner']}' ({audio['chosen_by']}) — "
                     f"{str(audio['verdict'].get('ly_do', ''))[:90]}")
        else:
            job.emit("warning", "calibrate", "Không chọn được bản tiếng — giữ mặc định")

    # write the winners back so resolve (and every later run) uses them
    state = job.load()
    state["options"] = {**state.get("options", {}), **applied}
    state["calibration"] = {k: {"winner": v.get("winner"), "chosen_by": v.get("chosen_by")}
                            for k, v in report.items() if isinstance(v, dict)}
    job.save(state)
    options.update(applied)

    (job.dir / "calibrate_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["applied"] = applied
    return report
