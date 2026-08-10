"""Stage 1 — probe every source and classify what it is for.

Cheap, deterministic, and the first place a bad input is caught: no speech
anywhere means there is no word spine, and without a spine every later stage is
guessing. Fail here rather than three minutes into a render.

With several sources the check softens in exactly one way: an individual silent
file is fine (that is b-roll), but a job where *nothing* speaks is still fatal.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from lib.talking_head_edit import assembly_config
from lib.talking_head_edit import sources as sources_mod
from lib.talking_head_edit.job_store import job_input_paths


class ProbeError(RuntimeError):
    pass


def ffprobe_json(path: str | Path) -> dict[str, Any]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise ProbeError(f"ffprobe thất bại: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout or "{}")


def _fps(stream: dict[str, Any]) -> float:
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    try:
        num, _, den = raw.partition("/")
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    paths = job_input_paths(state)
    if not paths:
        raise ProbeError("Job không có nguồn nào.")
    for path in paths:
        if not path.exists():
            raise ProbeError(f"Không tìm thấy file nguồn: {path}")

    assembly = assembly_config.from_job_state(state, state.get("project"))
    # A project (or a human in the UI) that already classified these sources has
    # the authoritative answer for role/take_group; re-detecting would overwrite
    # a decision with a guess.
    stored = {str(s.get("path") or ""): s for s in (state.get("sources") or [])}

    try:
        specs = sources_mod.scan(
            paths, take_detect=str(assembly.get("take_detect", "suggest")))
    except sources_mod.SourceError as exc:
        raise ProbeError(str(exc)) from exc

    rows: list[dict[str, Any]] = []
    for spec in specs:
        row = spec.as_dict()
        previous = stored.get(row["path"])
        if previous:
            for key in ("role", "take_group", "label", "order"):
                if previous.get(key) is not None:
                    row[key] = previous[key]
            row["speech"] = row["role"] == "aroll" and row["speech"]
        rows.append(row)
        for warning in row.get("warnings") or []:
            job.emit("warning", "probe", f"{Path(row['path']).name}: {warning}")

    speaking = sources_mod.aroll(rows)
    if not speaking:
        raise ProbeError(
            "Không nguồn nào có lời nói. Pipeline này neo mọi thứ theo lời nói "
            "(word spine) nên bắt buộc phải có ít nhất một nguồn có tiếng."
        )

    suggestions = (sources_mod.suggest_take_groups(specs)
                   if assembly.get("take_detect") != "off" else [])
    for suggestion in suggestions:
        job.emit("log", "probe",
                 f"Gợi ý take trùng: {', '.join(suggestion['sources'])} "
                 f"(tin cậy {suggestion['confidence']:.0%}) — {suggestion['reason']}")

    primary = speaking[0]
    probe = {
        # The top-level numbers describe the FIRST speaking source. Grade,
        # sharpening and frame maths all reason about "the footage", and for the
        # common single-source job that is what this means. Per-source numbers
        # live in `sources` below, and phase-03 resolve reads those.
        "duration_seconds": primary["duration"],
        "width": primary["width"],
        "height": primary["height"],
        "fps": primary["fps"],
        "sha256": primary["sha256"],
        "source_count": len(rows),
        "aroll_count": len(speaking),
        "broll_count": len(rows) - len(speaking),
        "total_seconds": round(sum(float(r["duration"]) for r in speaking), 3),
    }

    (job.dir / "probe.json").write_text(
        json.dumps({**probe, "sources": rows, "take_suggestions": suggestions},
                   indent=2, ensure_ascii=False), encoding="utf-8"
    )
    job.update(input_sha256=probe["sha256"], probe=probe, sources=rows,
               take_suggestions=suggestions, assembly_resolved=assembly)

    summary = ", ".join(
        f"{Path(r['path']).name} {r['duration']:.0f}s [{r['role']}]" for r in rows)
    job.emit("log", "probe",
             f"{len(rows)} nguồn ({len(speaking)} aroll, "
             f"{len(rows) - len(speaking)} broll): {summary}")
    return {**probe, "sources": rows, "take_suggestions": suggestions}
