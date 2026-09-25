"""Stage 5 — resolve the word-anchored spec into cut media + renderer props.

Order matters: cut first (that defines the new clock), then map every event
onto the post-cut timeline, then prepend the cold-open teaser and shift the
whole programme past it. Doing the teaser before the mapping would make every
event time wrong by the teaser length.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.talking_head_edit import spine_build
from lib.talking_head_edit.audio_calibrate import AudioCalibrationError, measure_room
from lib.talking_head_edit.job_store import (
    DEFAULT_OPTIONS, SHARED_PUBLIC, job_input_paths, option_enabled,
)
from lib.talking_head_edit.resolve_broll import (
    resolve_broll, speaker_aware_enabled, speaker_segments,
)
from lib.talking_head_edit.resolve_events import TimeMapper, apply_guards, resolve_events
from lib.talking_head_edit.sharpen_calibrate import (
    SharpenCalibrationError, calibrate,
)
from lib.talking_head_edit.grade_calibrate import GradeCalibrationError
from lib.talking_head_edit import grade_calibrate
from lib.talking_head_edit.resolve_media import (
    ResolveError, build_grade_chain, probe_duration, speaking_moments,
)
from lib.talking_head_edit.resolve_cut import (
    PREVIEW_PROXY_CRF, PREVIEW_PROXY_PRESET,
    cut_and_grade_multi, prepend_teaser,
)
from lib.talking_head_edit.resolve_spans import plan_spans

ENDCARD_SECONDS = 3.6

# Framing presets for the fullscreen A-roll. Inset footage on a backdrop makes
# an unflattering room read as a deliberate look instead of as the room. "dark"
# is the default because a blurred backdrop of a bright room just turns grey,
# and it costs a second video decode on every rendered frame.
FRAME_PRESETS: dict[str, dict[str, Any] | None] = {
    "none": None,
    "dark": {"inset": 60, "radius": 56, "border": 5, "borderColor": "#ffffff",
             "background": "dark"},
    "light": {"inset": 60, "radius": 56, "border": 0, "background": "light"},
    "blur": {"inset": 60, "radius": 56, "border": 0, "background": "blur", "blurPx": 40},
}

def aroll_pixel_size(width: int, height: int, frame_preset: str) -> tuple[int, int]:
    """Encode the graded footage at the size the A-roll is actually displayed at.

    A frame preset insets the A-roll, so a 1080-wide intermediate was being
    resampled down to ~1013 by the browser on every frame — an upscale followed
    by a downscale, with a lossy encode in between. Measured on a face crop,
    skipping that round trip is worth more than the whole sharpening chain:
    2.79 -> 3.96.

    The A-roll fills its box with objectFit:cover, so matching the *cover*
    width (not the box width) is what makes the browser's resample a no-op.
    """
    style = FRAME_PRESETS.get(frame_preset)
    inset = int((style or {}).get("inset", 0))
    if inset <= 0:
        return width, height
    box_height = height - inset * 2
    cover_width = round(box_height * width / height)
    # h264 in yuv420p needs even dimensions
    return cover_width - cover_width % 2, box_height - box_height % 2


def _auto_sharpen(job, options: dict[str, Any], grade: dict[str, Any], source: Path,
                  words: list[dict[str, Any]], width: int, height: int,
                  source_width: int | None, src_id: str = "s0") -> dict[str, Any]:
    """Measure this footage's softness and set the sharpening to suit it.

    Runs PER SOURCE: two phones, or the same phone in two rooms, arrive with
    different softness, and one number measured on the first file would be wrong
    for the rest. The measurement is cached by the source's own hash, so adding a
    take does not re-measure the ones already done.

    Skipped only when a HUMAN put `sharpen` in `grade_overrides`. The director's
    spec does not count, even though it proposes a number: a model judging
    stills was measured picking 1.2 where the deliverable needed 1.6, and later
    called a 2.2x difference invisible. Sharpness is not something it can see,
    so its guess must not silently win over a measurement.

    On measurement failure, retain the requested grade without adding an
    unmeasured sharpening estimate.
    """
    human_set = "sharpen" in (options.get("grade_overrides") or {})
    if not option_enabled(options, "auto_sharpen", default=False) or human_set:
        return grade

    moments = speaking_moments(words)
    # Drop the director's own guesses so they cannot skew the reference frame.
    measurable = {k: v for k, v in grade.items() if k not in ("sharpen", "clarity")}
    try:
        result = calibrate(source, moments, measurable, width, height, source_width,
                           job.dir / "sharpen_calibrate" / src_id)
    except (SharpenCalibrationError, OSError, ValueError) as exc:
        job.emit("warning", "resolve",
                 f"{source.name}: không dò được độ nét ({str(exc)[:80]}) — "
                 "giữ thông số chỉnh ảnh đã yêu cầu")
        return grade

    report_path = job.dir / (f"sharpen_report_{src_id}.json" if src_id != "s0"
                             else "sharpen_report.json")
    report_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    job.emit("log", "resolve",
             f"Độ nét {source.name}: đo được {result['detail_goc']} → chọn sharpen "
             f"{result['sharpen']} (đạt {result['detail']}, viền {result['overshoot']}) "
             f"— {result['ly_do']}")
    return {**measurable, "sharpen": result["sharpen"], "clarity": result["clarity"]}


def _auto_grade_exposure(job, options: dict[str, Any], grade: dict[str, Any], source: Path,
                         words: list[dict[str, Any]], src_id: str = "s0") -> dict[str, Any]:
    """Measure this footage's exposure and white balance instead of guessing.

    Same rule as `_auto_sharpen`: a human value in grade_overrides wins per
    key; the director's own guess does not, since it was never measured
    against the actual pixels either. Skipped entirely once a human has
    already covered every key this step would touch.
    """
    if not option_enabled(options, "auto_grade", default=False):
        return grade
    overrides = options.get("grade_overrides") or {}
    human_keys = {"brightness", "gamma", "warmth"} & overrides.keys()
    if human_keys == {"brightness", "gamma", "warmth"}:
        return grade

    moments = speaking_moments(words)
    try:
        result = grade_calibrate.calibrate(source, moments, job.dir / "grade_calibrate" / src_id)
    except (GradeCalibrationError, OSError, ValueError) as exc:
        job.emit("warning", "resolve",
                 f"{source.name}: không đo được phơi sáng/màu ({str(exc)[:80]}) — "
                 "giữ đề xuất của director")
        return grade

    report_path = job.dir / (f"grade_calibrate_report_{src_id}.json" if src_id != "s0"
                             else "grade_calibrate_report.json")
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    job.emit("log", "resolve", f"Phơi sáng/màu {source.name}: {result['ly_do']}")

    out = dict(grade)
    if "brightness" not in human_keys:
        out["brightness"] = result["brightness"]
    if "gamma" not in human_keys:
        out["gamma"] = result["gamma"]
    if "warmth" not in human_keys:
        out["warmth"] = result["warmth"]
    return out


def grade_chains_for(job, options: dict[str, Any], spec_grade: dict[str, Any],
                     words: list[dict[str, Any]], sources: list[dict[str, Any]],
                     out_size: tuple[int, int]) -> tuple[dict[str, str], dict[str, Any]]:
    """(grade chain per source id, the grade dicts used) for the report.

    `grade_overrides` may be flat (applies to every source, the old shape) or
    keyed by source id with `__all__` as the shared layer:

        {"__all__": {"warmth": 4}, "s1": {"brightness": 0.03}}

    Both are accepted because a single-source job has no reason to learn the
    keyed form.
    """
    width, height = out_size
    overrides = options.get("grade_overrides") or {}
    keyed = any(str(key).startswith("s") and isinstance(value, dict)
                for key, value in overrides.items()) or "__all__" in overrides
    shared = overrides.get("__all__", {}) if keyed else overrides

    chains: dict[str, str] = {}
    used: dict[str, Any] = {}
    words_by_src: dict[str, list[dict[str, Any]]] = {}
    for word in words:
        words_by_src.setdefault(str(word.get("src") or "s0"), []).append(word)

    for source in sources:
        src_id = str(source["id"])
        per_source = overrides.get(src_id, {}) if keyed else {}
        grade = {**spec_grade, **shared, **per_source}
        source_options = {**options, "grade_overrides": {**shared, **per_source}}
        grade = _auto_grade_exposure(job, source_options, grade, Path(source["path"]),
                                     words_by_src.get(src_id, words), src_id)
        grade = _auto_sharpen(job, source_options, grade, Path(source["path"]),
                             words_by_src.get(src_id, words), width, height,
                             source.get("width"), src_id)
        chains[src_id] = build_grade_chain(grade, width, height,
                                          source_width=source.get("width"))
        used[src_id] = grade
    return chains, used


BGM_VOLUME_RANGE = (0.08, 0.22)   # library is normalised to -17 LUFS; bed under speech
BGM_VOLUME_DEFAULT = 0.16


def _cold_open_events(cold: dict[str, Any], offset: float) -> list[dict[str, Any]]:
    """Teaser overlay + the mechanical seam glue (punch, riser, flash, whoosh)."""
    events: list[dict[str, Any]] = []
    caption = cold.get("caption")
    if caption:
        teaser_caption = {"type": "caption", "at": 0.05, "end": max(0.6, offset - 0.1),
                          "text": caption,
                          "highlightColor": cold.get("highlightColor", "#FF4D4D")}
        if cold.get("highlight"):
            teaser_caption["highlight"] = cold["highlight"]
        events.append(teaser_caption)

    keyword = cold.get("keyword")
    if isinstance(keyword, dict) and keyword.get("text"):
        events.append({
            "type": "keyword", "at": 0.15, "end": max(0.8, offset - 0.05),
            "text": keyword["text"], "color": keyword.get("color", "#FF2E93"),
            "xPct": keyword.get("xPct", 18), "yPct": keyword.get("yPct", 12),
            "rotation": keyword.get("rotation", -4),
            "fontSize": keyword.get("fontSize", 84), "anim": keyword.get("anim", "whip"),
        })

    events.append({"type": "punchIn", "at": 0.05, "scale": 1.08,
                   "holdSeconds": min(1.2, offset)})
    # One seam hit only — riser+whoosh 0.5s apart is what listeners call
    # "SFX dồn". Whoosh alone reads as the cut into the main take.
    events.append({"type": "flash", "at": offset, "durSeconds": 0.15})
    # Whoosh on the teaser tail, not on the first phoneme of the main take —
    # firing at `offset` was masking the first syllable after the join.
    events.append({"type": "sfx", "at": max(0.05, offset - 0.12),
                   "name": "sfx_whoosh.mp3", "volume": 0.14})
    return events


def spine_for_director(job) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The words the director actually saw, and the full spine.

    Recomputed from the spine plus the stored selection rather than persisted by
    `direct`, so there is exactly one definition of "what the director saw" and
    no chance of the two drifting. `_orig_index` on each word is what makes the
    cut land in the right second of the right file.
    """
    spine = json.loads(job.spine_path.read_text(encoding="utf-8"))
    spine = spine_build.upgrade_v2(spine)
    ranges = (job.load().get("selection") or {}).get("kept_word_ranges")
    return spine_build.filter_words(spine.get("word_timestamps") or [], ranges), spine


def source_index(job, spine: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """src id → source spec, with paths filled in from the job when the spine
    predates multi-source."""
    state = job.load()
    probe = state.get("probe") or {}
    sources = {str(s["id"]): dict(s) for s in (spine.get("sources") or [])}
    if not sources:
        sources = {"s0": {"id": "s0", "path": "", "role": "aroll", "order": 0}}

    for row in state.get("sources") or []:
        stored = sources.get(str(row.get("id")))
        if stored is not None:
            stored.update({k: v for k, v in row.items() if v is not None})

    # A job resumed from a spine that predates multi-source has source specs with
    # no path (upgrade_v2 cannot invent one). Fill them from the job's own inputs
    # by order — without a real path resolve cannot cut anything.
    paths = job_input_paths(state)
    for source in sources.values():
        if not source.get("path"):
            index = int(source.get("order", 0))
            if index < len(paths):
                source["path"] = str(paths[index])
        source.setdefault("width", probe.get("width"))
        if not source.get("duration"):
            source["duration"] = probe.get("duration_seconds") or 0.0
    return sources


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    state = job.load()
    version = int(state["current_version"])
    spec = json.loads(job.spec_path(version).read_text(encoding="utf-8"))
    words, spine = spine_for_director(job)
    sources = source_index(job, spine)

    tempo = float(options.get("tempo", 1.06))
    fps = int(options.get("fps", 30))
    width, height = int(options.get("width", 1080)), int(options.get("height", 1920))
    out_size = aroll_pixel_size(width, height, str(options.get("frame_preset", DEFAULT_OPTIONS["frame_preset"])))

    # --- cut + grade: this defines the new clock ---------------------------
    # Spans first: which piece of which file plays when. Video is encoded exactly
    # once, here; everything after this copies the video stream.
    spans, removes_report = plan_spans(
        words, spec.get("cut_remove") or [],
        {src_id: Path(source["path"]) for src_id, source in sources.items()
         if source.get("path")},
        # Source lengths let the last run keep its tail: the words end before the
        # file does, and cutting at the last word drops the room tone the old
        # single-pass path kept.
        {src_id: float(source.get("duration") or 0.0)
         for src_id, source in sources.items()},
    )
    used_sources = [sources[src_id] for src_id in
                   dict.fromkeys(span.src_id for span in spans)]
    # `grade_overrides` lets a human correct the look without rewriting the
    # director's spec — the spec stays the AI's proposal, this is the trim.
    grade_chains, grades_used = grade_chains_for(
        job, options, spec.get("grade") or {}, words, used_sources, out_size)

    # One decision for the whole job — `cut_and_grade_multi` takes a single
    # audio_preset, not one per source — so this measures the primary (first
    # used) source's room only, not every source.
    audio_preset = str(options.get("audio_preset", "shotgun"))
    if option_enabled(options, "auto_audio_preset") and used_sources:
        primary_path = Path(used_sources[0]["path"])
        try:
            room = measure_room(primary_path, words)
            audio_preset = room["preset"]
            (job.dir / "audio_room_report.json").write_text(
                json.dumps(room, indent=2, ensure_ascii=False), encoding="utf-8")
            job.emit("log", "resolve", f"Chọn xử lý tiếng: {room['ly_do']}")
        except (AudioCalibrationError, OSError, ValueError) as exc:
            job.emit("warning", "resolve",
                     f"Không đo được độ vang phòng ({str(exc)[:80]}) — "
                     f"giữ audio_preset đã set ({audio_preset})")

    source_seconds = round(sum(
        float(sources[src_id].get("duration") or 0.0) for src_id in
        dict.fromkeys(span.src_id for span in spans)), 3)
    job.emit("log", "resolve",
             f"Cắt {len(removes_report)} đoạn, giữ {len(spans)} span trên "
             f"{len(used_sources)} nguồn — đang encode…")
    # Step 1 of splitting "decide the cut" from "encode the cut": `resolve`
    # now always encodes at DRAFT quality (same tier as make_preview_proxy —
    # deliberately reusing it, not a second set of magic numbers), because
    # `spans`/`grades_used`/`cold_open_offset` below are what gets persisted
    # to `resolve_report` and re-executed. `intermediate_preset`/`crf` are NOT
    # read here anymore; they still mean "the deliverable's encode" and will
    # apply once `render` re-runs this same decided plan at that quality
    # (not yet wired — until then `final.mp4` is produced from this draft).
    preset = PREVIEW_PROXY_PRESET
    crf = PREVIEW_PROXY_CRF
    new_duration, seams = cut_and_grade_multi(
        spans, job.src_path, grade_chains, tempo, fps,
        preset=preset, crf=crf,
        audio_preset=audio_preset,
        work_dir=job.dir,
        probes={src_id: source for src_id, source in sources.items()},
        target_size=out_size,
        on_log=lambda message: job.emit("log", "resolve", message),
    )
    job.emit("log", "resolve",
             f"{source_seconds:.1f}s → {new_duration:.1f}s (tempo {tempo}x)")

    # --- map events onto the post-cut timeline -----------------------------
    mapper = TimeMapper(words, spans, new_duration, tempo)
    events, skipped = resolve_events(spec, mapper)
    events, guard_report = apply_guards(events)

    # --- cold-open teaser: prepend, then shift the whole programme ---------
    cold = spec.get("cold_open")
    offset = 0.0
    if isinstance(cold, dict) and "w0" in cold and "w1" in cold and option_enabled(options, "cold_open"):
        start = mapper.at(int(cold["w0"]))
        last_word = max(0, min(len(words) - 1, int(cold["w1"])))
        end_of_line = mapper.at(last_word, use_end=True)
        next_word_at = mapper.at(last_word + 1) if last_word + 1 < len(words) else new_duration
        # Keep a breath after the hook sentence — but never swallow the next word.
        trailing = max(0.0, next_word_at - end_of_line - 0.02)
        end = min(new_duration, end_of_line + min(0.22, max(0.06, trailing * 0.65)))

        if end - start >= 1.0:
            total = prepend_teaser(job.src_path, start, end, fps, preset=preset, crf=crf)
            offset = round(total - new_duration, 3)
            new_duration = total
            for event in events:
                event["at"] = round(event["at"] + offset, 3)
                if "end" in event:
                    event["end"] = round(event["end"] + offset, 3)
                if event.get("bulletTimes"):
                    event["bulletTimes"] = [round(t + offset, 3) for t in event["bulletTimes"]]
            events = _cold_open_events(cold, offset) + events
            # Cold-open injects its own SFX after apply_guards — thin again so
            # riser+whoosh (0.5s apart) cannot reappear at the teaser join.
            from lib.talking_head_edit.resolve_events import _thin_sfx
            events, cold_sfx_drop = _thin_sfx(events)
            if cold_sfx_drop:
                job.emit("log", "resolve",
                         f"Bỏ {len(cold_sfx_drop)} SFX dồn sau cold-open")
            # Every seam moved by the teaser length, and the teaser itself added
            # one at its own end — `verify` inspects this list, so a stale one
            # would send it looking at the wrong places.
            seams = [round(seam + offset, 3) for seam in seams] + [offset]
            seams.sort()
            job.emit("log", "resolve", f"Cold-open {offset:.2f}s đã ghép vào đầu video")
        else:
            job.emit("warning", "resolve",
                     "Cold-open bị bỏ: đoạn được chọn ngắn hơn 1s sau khi cắt")

    # --- browser-preview proxy ----------------------------------------------
    # `src.mp4` is ALREADY draft-quality (see the encode above), so a separate
    # re-encode into `preview_src.mp4` right now would just spend seconds
    # producing a second copy of a file that is already light — `videoSrc`
    # itself is fine for the Player until `render` (step 2, not yet wired)
    # brings the intermediate back up to deliverable quality. `previewVideoSrc`
    # goes back to being a real, populated field once that lands; for now it
    # is None and `MonaTimeline` falls back to `videoSrc`, which is the
    # correct file to fall back to at this stage.
    preview_video_name: str | None = None

    # --- endcard + props ---------------------------------------------------
    endcard = spec.get("endcard") or {}
    total_seconds = new_duration
    if endcard.get("title"):
        freeze_name = "endcard_freeze.jpg"
        freeze_ok = False
        try:
            from lib.talking_head_edit.stills import extract_still
            freeze_ok = extract_still(
                job.src_path, job.dir / freeze_name, max(0.0, new_duration - 0.08))
        except OSError:
            freeze_ok = False
        event: dict[str, Any] = {
            "type": "endcard",
            "at": round(new_duration, 3),
            "end": round(new_duration + ENDCARD_SECONDS, 3),
            "title": endcard["title"],
            "subtitle": endcard.get("subtitle", ""),
        }
        if endcard.get("kicker"):
            event["kicker"] = endcard["kicker"]
        if endcard.get("accent"):
            event["accent"] = endcard["accent"]
        if freeze_ok:
            event["freezeSrc"] = freeze_name
        art = job.dir / "endcard_art.png"
        if art.exists():
            event["artSrc"] = art.name
        events.append(event)
        total_seconds = new_duration + ENDCARD_SECONDS

    frame_preset = str(options.get("frame_preset", DEFAULT_OPTIONS["frame_preset"]))
    if frame_preset not in FRAME_PRESETS:
        job.emit("warning", "resolve",
                 f"Kiểu khung '{frame_preset}' không có — dùng full khung")
    frame_style = FRAME_PRESETS.get(frame_preset)

    props: dict[str, Any] = {
        # bare name: the render stage stages this file into a per-job public
        # dir, and the web server serves it under the job's media route
        "videoSrc": job.src_path.name,
        # Player-only, never staged for a render (see make_preview_proxy).
        # `MonaTimeline` picks this over `videoSrc` outside Remotion's own
        # rendering environment, falling back to `videoSrc` when absent.
        "previewVideoSrc": preview_video_name,
        "events": sorted(events, key=lambda e: e["at"]),
        "durationSeconds": round(total_seconds, 3),
        "brandPill": options.get("brand_pill", ""),
    }
    if frame_style:
        props["frame"] = {**frame_style, **(options.get("frame_overrides") or {})}

    # --- b-roll overlays + speaker framing --------------------------------
    assembly = state.get("assembly_resolved") or {}
    broll_report: dict[str, Any] = {"clips": [], "warnings": []}
    broll_events = [e for e in events if e.get("type") == "broll"]
    if broll_events and assembly.get("broll_overlay", True):
        clips, broll_warnings = resolve_broll(
            broll_events, spine.get("overlay_pool") or [],
            job.render_public_dir, out_size[0], out_size[1], fps,
            preset=preset, crf=crf,
            on_warning=lambda message: job.emit("warning", "resolve", message))
        broll_report = {"clips": clips, "warnings": broll_warnings}
        if clips:
            props["broll"] = clips
            job.emit("log", "resolve",
                     f"{len(clips)} lớp b-roll: "
                     + ", ".join(f"{c['source_id']}@{c['start']:.1f}s ({c['mode']})"
                                 for c in clips))
    elif broll_events:
        job.emit("warning", "resolve",
                 f"Bỏ {len(broll_events)} b-roll: assembly.broll_overlay đang tắt")
    # The renderer reads `props.broll`, not the event list — drop the events so
    # nothing downstream tries to interpret them a second way.
    events = [e for e in events if e.get("type") != "broll"]
    props["events"] = sorted(events, key=lambda e: e["at"])

    speaker_on, speaker_reason = speaker_aware_enabled(
        assembly.get("speaker_aware", "auto"), words)
    if speaker_on:
        segments = speaker_segments(words, mapper.at)
        if offset:
            segments = [{**s, "start": round(s["start"] + offset, 3),
                         "end": round(s["end"] + offset, 3)} for s in segments]
        props["speakerSegments"] = segments
        job.emit("log", "resolve",
                 f"speaker_aware: {len(segments)} lượt nói — {speaker_reason}")
    elif assembly.get("speaker_aware") is not False:
        job.emit("log", "resolve", f"speaker_aware tắt: {speaker_reason}")

    bgm = spec.get("bgm")
    if isinstance(bgm, dict) and bgm.get("name") and option_enabled(options, "bgm"):
        bgm_path = SHARED_PUBLIC / bgm["name"]
        if bgm_path.exists():
            volume = float(bgm.get("volume", BGM_VOLUME_DEFAULT))
            props["bgm"] = {
                "name": bgm["name"],
                "volume": max(BGM_VOLUME_RANGE[0], min(BGM_VOLUME_RANGE[1], volume)),
                # the renderer tiles Audio copies (loop + volume callback renders
                # silent in Remotion), so it needs the real file length
                "durationSeconds": round(probe_duration(bgm_path), 3),
            }
        else:
            job.emit("warning", "resolve", f"Không tìm thấy file nhạc {bgm['name']} — bỏ nhạc nền")

    job.props_path(version).write_text(
        json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    report = {
        "version": version,
        "removes": len(removes_report),
        "removed_spans": removes_report,
        "source_seconds": source_seconds,
        "timeline_seconds": round(new_duration, 3),
        "total_seconds": round(total_seconds, 3),
        "cold_open_offset": offset,
        "spans": [{"src": span.src_id, "start": span.start, "end": span.end}
                  for span in spans],
        # Output-timeline seconds where one span hands over to the next. `verify`
        # needs this to know where to look for pops and level jumps: without it a
        # report can say "duplicate frames somewhere" and nothing more.
        "seams": seams,
        "audio_preset": audio_preset,
        "sources_used": [source["id"] for source in used_sources],
        "grades": grades_used,
        "broll": broll_report,
        "speaker_aware": {"enabled": speaker_on, "reason": speaker_reason,
                          "segments": len(props.get("speakerSegments") or [])},
        "event_count": len(events),
        "skipped_events": skipped,
        "guards": guard_report,
        "props_path": job.rel(job.props_path(version)),
    }
    (job.dir / f"resolve_report_v{version}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    for item in skipped:
        job.emit("warning", "resolve", f"Bỏ event: {item}")
    job.emit("log", "resolve",
             f"{len(events)} event | guard: dời {len(guard_report['keywords_moved'])} keyword, "
             f"bỏ {len(guard_report['keywords_dropped'])}, gộp {guard_report['captions_merged']} caption")
    return report


__all__ = ["run", "ResolveError"]
