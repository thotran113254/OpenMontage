"""Discover and classify the media a job is built from.

One job used to mean one file. Now it means N sources with roles, and this file
is where a path becomes a `SourceSpec` the rest of the pipeline can reason about.

Two classifications happen here, and they are deliberately different in kind:

* **role** (`aroll` vs `broll`) is decided automatically. It is cheap to measure
  (does this file contain speech?) and cheap to be wrong about (a b-roll clip
  misread as A-roll shows up as gibberish in the transcript, immediately).
* **take_group** (which files are different takes of the same content) is only
  *suggested*. Being wrong there deletes real content, so a human confirms —
  hence `take_detect: "suggest"` as the default.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lib.talking_head_edit.cache import file_sha256

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi")

# Below this a track is room tone, not speech. Measured on real footage: a
# talking-head clip sits around -20 dBFS mean, a silent b-roll clip around -60,
# and camera-hiss-only clips land near -50. -45 separates them with room to
# spare either way.
SPEECH_MEAN_DB = -45.0
# Sampling three short windows instead of decoding the whole file: a full
# volumedetect pass on a 10-minute clip costs seconds per source for a number
# that only has to answer "is anyone talking".
PROBE_WINDOW_SECONDS = 4.0

# Trailing take markers to strip before comparing filenames: "clip-take2",
# "clip_2", "clip (2)", "clip-final" are all the same content.
_TAKE_MARKER = re.compile(
    r"[\s_\-.(]*(?:take|lan|lần|v|ver|version|final|cut)?[\s_\-.]*\(?\d{1,2}\)?$",
    re.IGNORECASE,
)
# Durations within this fraction of each other are plausibly the same content.
TAKE_DURATION_TOLERANCE = 0.25


class SourceError(RuntimeError):
    pass


@dataclass
class SourceSpec:
    id: str
    path: str
    duration: float = 0.0
    sha256: str = ""
    role: str = "aroll"
    order: int = 0
    take_group: str = "main"
    speech: bool = True
    label: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = True
    mean_volume_db: float | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def mean_volume_db(path: str | Path, duration: float) -> float | None:
    """Loudest of three sampled windows, in dBFS. None when it cannot be read.

    The loudest window, not the average: a clip that is silent for its first ten
    seconds and then has speech is still A-roll, and averaging would bury that.
    """
    if duration <= 0:
        offsets = [0.0]
    else:
        offsets = [duration * fraction for fraction in (0.15, 0.5, 0.85)]

    readings: list[float] = []
    for offset in offsets:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats",
             "-ss", f"{max(0.0, offset):.3f}",
             "-t", f"{PROBE_WINDOW_SECONDS}", "-i", str(path),
             "-map", "0:a:0?", "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        for line in (result.stderr or "").splitlines():
            if "mean_volume:" in line:
                try:
                    readings.append(float(line.split("mean_volume:")[1].split("dB")[0]))
                except (IndexError, ValueError):
                    continue
    return max(readings) if readings else None


def probe_source(path: str | Path) -> dict[str, Any]:
    """ffprobe facts plus the speech reading, in one place."""
    from lib.talking_head_edit.stages.probe import ffprobe_json, _fps

    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0.0)

    if video is None:
        raise SourceError(f"{Path(path).name}: không có luồng video.")

    volume = mean_volume_db(path, duration) if audio is not None else None
    return {
        "duration": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": _fps(video),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "has_audio": audio is not None,
        "mean_volume_db": volume,
        "size_bytes": int(data.get("format", {}).get("size") or 0),
    }


def classify_role(probe: dict[str, Any]) -> tuple[str, bool, list[str]]:
    """(role, speech, warnings) from a probe reading.

    Any file without speech is b-roll: this pipeline anchors everything to words,
    so a silent clip has nothing to contribute to the clock — but it is perfectly
    good as an overlay.
    """
    warnings: list[str] = []
    if not probe.get("has_audio"):
        return "broll", False, ["không có luồng audio → coi là b-roll"]

    volume = probe.get("mean_volume_db")
    if volume is None:
        warnings.append("không đo được âm lượng — mặc định coi là có lời nói")
        return "aroll", True, warnings
    if volume < SPEECH_MEAN_DB:
        return "broll", False, [
            f"âm lượng {volume:.1f} dB dưới ngưỡng {SPEECH_MEAN_DB} dB → coi là b-roll"
        ]
    return "aroll", True, warnings


def content_key(spec: "SourceSpec") -> str:
    """The name a source is about, with take markers and storage prefixes gone.

    Uses `label` in preference to the filename: a source stored inside a project
    is written as `s0_intro-take1.mp4`, and the `s0_`/`s1_` prefix would make two
    takes of the same content look like two different subjects — which is exactly
    the grouping this is trying to find.
    """
    stem = (spec.label or "").strip() or Path(spec.path).stem
    previous = None
    # Strip repeatedly: "clip-take-2" needs two passes.
    while previous != stem:
        previous = stem
        stem = _TAKE_MARKER.sub("", stem)
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


def suggest_take_groups(sources: list[SourceSpec]) -> list[dict[str, Any]]:
    """Groups of files that look like takes of the same content.

    Suggestion only, with a confidence, because the failure mode is severe: two
    files wrongly grouped means `select` throws one of them away as a redundant
    take, and that content is simply gone from the video.

    Signals available before transcription: a shared filename stem once take
    markers are stripped, and similar durations. Content overlap is a stronger
    signal but needs transcripts, which do not exist yet at probe time.
    """
    speech_sources = [s for s in sources if s.speech]
    buckets: dict[str, list[SourceSpec]] = {}
    for source in speech_sources:
        buckets.setdefault(content_key(source), []).append(source)

    suggestions: list[dict[str, Any]] = []
    for key, members in buckets.items():
        if len(members) < 2 or not key:
            continue
        durations = [m.duration for m in members if m.duration > 0]
        longest = max(durations, default=0.0)
        shortest = min(durations, default=0.0)
        similar = bool(longest) and (longest - shortest) / longest <= TAKE_DURATION_TOLERANCE
        suggestions.append({
            "take_group": key[:24] or "main",
            "sources": [m.id for m in members],
            # Name match alone is a decent hint; name match plus similar length
            # is close to conclusive for footage shot in one session.
            "confidence": 0.85 if similar else 0.55,
            "reason": ("tên file giống nhau sau khi bỏ hậu tố take"
                       + (f", độ dài gần nhau ({shortest:.0f}s–{longest:.0f}s)"
                          if similar else
                          f", nhưng độ dài lệch nhiều ({shortest:.0f}s vs {longest:.0f}s)")),
        })
    return sorted(suggestions, key=lambda s: -s["confidence"])


def expand_dir(directory: str | Path) -> list[Path]:
    """Video files directly inside a folder, sorted by name.

    Name order, not modification time: people name takes `01_`, `02_` and expect
    that order, while copying files rewrites mtimes in whatever order the
    filesystem felt like.
    """
    root = Path(directory)
    if not root.is_dir():
        raise SourceError(f"Không phải thư mục: {root}")
    found = sorted(
        (p for p in root.iterdir()
         if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES),
        key=lambda p: p.name.lower(),
    )
    if not found:
        raise SourceError(
            f"Không có file video nào trong {root} "
            f"(chấp nhận: {', '.join(VIDEO_SUFFIXES)})")
    return found


def scan(paths: list[str | Path], *, compute_sha: bool = True,
         take_detect: str = "suggest") -> list[SourceSpec]:
    """Paths → probed, classified, ordered sources.

    `order` follows the order given: the caller decided the sequence (CLI arg
    order, or a project's stored order), and re-sorting here would silently
    override that.
    """
    if not paths:
        raise SourceError("Không có nguồn nào để xử lý.")

    specs: list[SourceSpec] = []
    for index, raw in enumerate(paths):
        path = Path(raw)
        if not path.exists():
            raise SourceError(f"Không tìm thấy file nguồn: {path}")
        probe = probe_source(path)
        role, speech, warnings = classify_role(probe)
        specs.append(SourceSpec(
            id=f"s{index}",
            path=str(path.resolve()),
            duration=probe["duration"],
            sha256=file_sha256(path) if compute_sha else "",
            role=role,
            order=index,
            speech=speech,
            label=path.stem,
            width=probe["width"],
            height=probe["height"],
            fps=probe["fps"],
            has_audio=bool(probe["has_audio"]),
            mean_volume_db=probe["mean_volume_db"],
            warnings=warnings,
        ))

    if take_detect == "apply":
        for suggestion in suggest_take_groups(specs):
            for source_id in suggestion["sources"]:
                for spec in specs:
                    if spec.id == source_id:
                        spec.take_group = suggestion["take_group"]
    return specs


def aroll(sources: list[dict[str, Any]] | list[SourceSpec]) -> list[dict[str, Any]]:
    """Speaking sources in play order — the ones that make the word spine."""
    rows = [s.as_dict() if isinstance(s, SourceSpec) else s for s in sources]
    return sorted((s for s in rows if s.get("role", "aroll") == "aroll"),
                  key=lambda s: int(s.get("order", 0)))


def broll(sources: list[dict[str, Any]] | list[SourceSpec]) -> list[dict[str, Any]]:
    """Overlay-only sources — never part of the clock."""
    rows = [s.as_dict() if isinstance(s, SourceSpec) else s for s in sources]
    return sorted((s for s in rows if s.get("role") == "broll"),
                  key=lambda s: int(s.get("order", 0)))
