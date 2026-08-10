"""Builds a frame-synced narration audio track from `edit_decisions.cuts[]`.

Root-cause fix for progressive audio/video desync in footage-led (hybrid)
productions: the narration track must be assembled from the EXACT SAME
per-cut source sub-ranges the video track uses, not from an independent
global ripple-delete of detected pause spans. Two independently-computed
ripple-edits of the same source (one for video cut points, one for audio
pause spans) will diverge cut by cut, because the video only actually jumps
at a subset of points (overlay cutaways + a few masked mid-shot splices)
while a naive audio pause-removal skips every detected pause uniformly.

This tool takes the narration source audio (or the source video — ffmpeg
extracts the audio stream directly) plus the `cuts[]` array itself and
concatenates, in output order, the EXACT source sub-range each cut actually
displays: `source_in_seconds` for primary cuts, `backgroundVideoStart` for
overlay cuts (the background video keeps playing under overlay content, so
the correct narration for that window is the speech at that same source
position). Segments are concatenated with zero overlap so the resulting
track's total duration and every cut boundary's absolute output timestamp
match `edit_decisions`'s cuts[] exactly, by construction.

A true overlapping crossfade (ffmpeg `acrossfade`) was deliberately NOT used
here: at a real ripple-delete splice the source content immediately before/
after the cut boundary is the removed pause/filler itself, so padding a
blend region from that adjoining source content would leak the very audio
the cut exists to remove, and would also change the track's total duration
away from an exact match with the video (the top-priority fix this tool
exists for). Instead, each segment gets a short symmetric declick
`afade` (in at its head, out at its tail) entirely within its own already-
correct duration -- this removes the amplitude-discontinuity click/pop of a
hard splice without moving a single boundary or adding/leaking any audio.
"""

from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ResumeSupport,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

DEFAULT_CROSSFADE_SECONDS = 0.1


def _cut_source_start(cut: dict[str, Any]) -> float:
    """Resolve the source-time position a cut actually displays.

    Primary cuts declare `source_in_seconds`; overlay cuts (b-roll cutaways)
    declare `backgroundVideoStart` for the video that keeps playing, muted
    and dimmed, behind the overlay content. Falls back to 0.0 only when
    neither is present (e.g. a non-video overlay with no background video).
    """
    if cut.get("source_in_seconds") is not None:
        return float(cut["source_in_seconds"])
    if cut.get("backgroundVideoStart") is not None:
        return float(cut["backgroundVideoStart"])
    return 0.0


def _is_video_backed(cut: dict[str, Any]) -> bool:
    """A cut needs a narration segment only if it maps to real source audio:
    a primary video cut, or an overlay with a backgroundVideo playing under it.
    Non-video overlays (e.g. a graphic with no anchor footage) have no
    corresponding source position and must not be given a synthetic one.
    """
    return cut.get("source_in_seconds") is not None or cut.get("backgroundVideoStart") is not None


class NarrationRippleCutter(BaseTool):
    name = "narration_ripple_cutter"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "audio_processing"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = "FFmpeg is required (ffmpeg + ffprobe on PATH)."
    agent_skills = ["ffmpeg"]

    capabilities = ["ripple_cut_narration", "cut_aligned_audio_concat"]
    best_for = [
        "building a narration track for a footage-led edit whose video track "
        "uses non-linear (ripple-delete / pause-tightened) cuts",
    ]
    not_good_for = [
        "adding SFX/music (use audio_mixer for that, on top of this tool's output)",
    ]

    input_schema = {
        "type": "object",
        "required": ["source_audio", "cuts", "output_path"],
        "properties": {
            "source_audio": {
                "type": "string",
                "description": "Path to the original continuous source audio or video "
                "(ffmpeg extracts the audio stream directly).",
            },
            "cuts": {
                "type": "array",
                "description": "The edit_decisions.cuts[] array (or a compatible subset) "
                "in OUTPUT order. Each cut needs in_seconds/out_seconds plus either "
                "source_in_seconds (primary) or backgroundVideoStart (overlay).",
                "items": {"type": "object"},
            },
            "crossfade_seconds": {
                "type": "number", "default": DEFAULT_CROSSFADE_SECONDS,
                "description": "Declick fade-in/fade-out duration applied at each cut's own "
                "edges (within its exact nominal duration -- see module docstring for why "
                "this is used instead of an overlapping acrossfade).",
            },
            "output_path": {"type": "string"},
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string"},
            "segments_used": {"type": "integer"},
            "duration_seconds": {"type": "number"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500)
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    resume_support = ResumeSupport.FROM_START
    idempotency_key_fields = ["source_audio", "cuts", "crossfade_seconds"]
    side_effects = ["writes a ripple-cut narration wav to output_path"]
    fallback_tools = []
    user_visible_verification = [
        "For a handful of output timestamps, confirm the spoken word matches the "
        "speaker's mouth movement in the video at that same timestamp",
        "Compare total output duration against edit_decisions' final cut-list duration",
    ]

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        source_audio = Path(inputs["source_audio"])
        if not source_audio.exists():
            return ToolResult(success=False, error=f"source_audio not found: {source_audio}")

        cuts = [c for c in inputs["cuts"] if _is_video_backed(c)]
        if not cuts:
            return ToolResult(success=False, error="No video-backed cuts to build narration from")

        cuts = sorted(cuts, key=lambda c: float(c["in_seconds"]))
        crossfade = float(inputs.get("crossfade_seconds", DEFAULT_CROSSFADE_SECONDS))
        output_path = Path(inputs["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        start = time.time()
        with TemporaryDirectory(prefix="narration_ripple_") as tmp:
            tmp_dir = Path(tmp)
            segments = self._extract_declicked_segments(source_audio, cuts, crossfade, tmp_dir)
            self._concat_exact(segments, tmp_dir, output_path)

        duration = self._probe_duration(output_path)
        return ToolResult(
            success=True,
            data={
                "output": str(output_path),
                "segments_used": len(segments),
                "duration_seconds": duration,
            },
            artifacts=[str(output_path)],
            duration_seconds=round(time.time() - start, 2),
        )

    def _extract_declicked_segments(
        self,
        source_audio: Path,
        cuts: list[dict[str, Any]],
        fade_seconds: float,
        tmp_dir: Path,
    ) -> list[Path]:
        """Extract each cut's EXACT nominal source range (no padding, no
        overlap) and apply a short declick fade at its own head/tail.

        Interior boundaries get both a fade-out (previous segment's tail)
        and a fade-in (next segment's head) so the splice ramps through
        near-silence instead of jumping between two different waveform
        phases/amplitudes. The very first head and very last tail are left
        unfaded so the whole track doesn't needlessly fade in/out.
        """
        segments = []
        n = len(cuts)
        for i, cut in enumerate(cuts):
            src_start = _cut_source_start(cut)
            duration = float(cut["out_seconds"]) - float(cut["in_seconds"])
            # Clamp so the declick fade can never exceed a very short cut's
            # own duration (would otherwise silence the whole segment).
            fade = min(fade_seconds, duration * 0.3)

            raw_path = tmp_dir / f"raw_{i:03d}.wav"
            self.run_command([
                "ffmpeg", "-y",
                "-ss", f"{src_start:.6f}",
                "-i", str(source_audio),
                "-t", f"{duration:.6f}",
                "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
                str(raw_path), "-loglevel", "error",
            ])

            audio_filters = []
            if fade > 0.0 and i > 0:
                audio_filters.append(f"afade=t=in:st=0:d={fade:.4f}")
            if fade > 0.0 and i < n - 1:
                fade_start = max(0.0, duration - fade)
                audio_filters.append(f"afade=t=out:st={fade_start:.4f}:d={fade:.4f}")

            out_path = tmp_dir / f"seg_{i:03d}.wav"
            if audio_filters:
                self.run_command([
                    "ffmpeg", "-y", "-i", str(raw_path),
                    "-af", ",".join(audio_filters),
                    "-acodec", "pcm_s16le", "-ar", "48000",
                    str(out_path), "-loglevel", "error",
                ])
            else:
                out_path = raw_path
            segments.append(out_path)
        return segments

    def _concat_exact(
        self, segments: list[Path], tmp_dir: Path, output_path: Path
    ) -> None:
        """Concatenate segments with zero overlap via ffmpeg's concat demuxer
        -- total output duration is exactly the sum of the segments' own
        (unmodified) durations, which is what keeps this frame-synced to the
        video's cuts[] boundaries."""
        if len(segments) == 1:
            self.run_command([
                "ffmpeg", "-y", "-i", str(segments[0]),
                "-acodec", "pcm_s16le", str(output_path), "-loglevel", "error",
            ])
            return

        list_path = tmp_dir / "concat_list.txt"
        lines = [f"file '{s.resolve().as_posix()}'" for s in segments]
        list_path.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-acodec", "pcm_s16le", "-ar", "48000",
            str(output_path), "-loglevel", "error",
        ]
        self.run_command(cmd)

    @staticmethod
    def _probe_duration(path: Path) -> float:
        import json
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return round(float(json.loads(out.stdout)["format"]["duration"]), 3)
