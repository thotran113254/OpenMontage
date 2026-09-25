"""Cover still composite — no network, no ffmpeg."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from lib.talking_head_edit.visuals import _hex_rgb, composite_thumbnail


def test_hex_rgb_parses_and_falls_back():
    assert _hex_rgb("#E10600") == (225, 6, 0)
    assert _hex_rgb("nope") == (225, 6, 0)


def test_composite_writes_1080x1920_jpeg(tmp_path: Path):
    hook = tmp_path / "hook.jpg"
    Image.new("RGB", (640, 360), (180, 40, 40)).save(hook, "JPEG")
    dest = tmp_path / "thumbnail.jpg"
    composite_thumbnail(hook, dest, title="100.000 đơn một ngày",
                        subtitle="Link trong bio", accent="#E10600")
    assert dest.exists() and dest.stat().st_size > 2000
    out = Image.open(dest)
    assert out.size == (1080, 1920)
