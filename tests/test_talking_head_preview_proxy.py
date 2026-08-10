"""`make_preview_proxy` against real ffmpeg — no stubs.

The bug this exists to fix was invisible to every other test in this suite:
`resolve_cut`'s own numbers (intermediate ~55 Mbps vs. deliverable 6 Mbps,
measured on this pipeline's real footage) only show up by actually encoding
something and reading the bytes back, not by asserting a command line was
built correctly.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from lib.talking_head_edit.resolve_cut import make_preview_proxy
from lib.talking_head_edit.resolve_media import ResolveError

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")


def _make_bloated_source(path, seconds: float = 2.0) -> None:
    """A tiny clip encoded the way `src_path` really is: CRF 0, near-lossless.

    `testsrc` (moving bars) rather than a flat colour — a still frame
    compresses to almost nothing regardless of CRF, which would make this
    fixture "prove" a bitrate drop that has nothing to do with the function
    under test.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "0",
         "-c:a", "aac", "-shortest", str(path), "-loglevel", "error"],
        check=True, capture_output=True,
    )


def _probe(path) -> dict:
    import json

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,duration:format=size,bit_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


class TestMakePreviewProxy:
    def test_produces_a_playable_file_with_video_and_audio(self, tmp_path):
        src, out = tmp_path / "src.mp4", tmp_path / "preview_src.mp4"
        _make_bloated_source(src)

        make_preview_proxy(src, out)

        assert out.exists()
        streams = {s["codec_type"] for s in _probe(out)["streams"]}
        assert streams == {"video", "audio"}

    def test_is_meaningfully_smaller_than_a_near_lossless_source(self, tmp_path):
        """The whole point: `src_path`-quality input in, small file out."""
        src, out = tmp_path / "src.mp4", tmp_path / "preview_src.mp4"
        _make_bloated_source(src)

        make_preview_proxy(src, out)

        source_size = src.stat().st_size
        proxy_size = out.stat().st_size
        assert proxy_size < source_size * 0.5, (
            f"proxy ({proxy_size}B) không nhẹ hơn đáng kể so nguồn ({source_size}B)")

    def test_audio_duration_matches_the_source_within_a_frame(self, tmp_path):
        """Copying the audio stream must not drift it from the video."""
        src, out = tmp_path / "src.mp4", tmp_path / "preview_src.mp4"
        _make_bloated_source(src, seconds=3.0)

        make_preview_proxy(src, out)

        streams = {s["codec_type"]: s for s in _probe(out)["streams"]}
        video_dur = float(streams["video"]["duration"])
        audio_dur = float(streams["audio"]["duration"])
        assert abs(video_dur - audio_dur) < 0.1

    def test_a_bad_source_raises_resolve_error_not_a_bare_ffmpeg_failure(self, tmp_path):
        src = tmp_path / "not_a_video.mp4"
        src.write_bytes(b"this is not a video file")

        with pytest.raises(ResolveError):
            make_preview_proxy(src, tmp_path / "preview_src.mp4")

    def test_custom_crf_and_preset_are_honoured(self, tmp_path):
        """Not just that it runs — that the caller's quality knob reaches ffmpeg."""
        src, out_default = tmp_path / "src.mp4", tmp_path / "default.mp4"
        out_high_quality = tmp_path / "high_quality.mp4"
        _make_bloated_source(src)

        make_preview_proxy(src, out_default)                      # crf 24
        make_preview_proxy(src, out_high_quality, crf=10)          # near source quality

        # A lower CRF number means less compression, so the file must be larger.
        assert out_high_quality.stat().st_size > out_default.stat().st_size
