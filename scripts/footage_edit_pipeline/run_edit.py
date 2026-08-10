"""Reusable footage-edit pipeline: cut -> crossfade transitions -> zoom -> SFX ->
[optional] subtitle burn -> [optional] background music.

Usage:
    python -m scripts.footage_edit_pipeline.run_edit path/to/config.json

Config schema: see scripts/footage_edit_pipeline/example_config.json
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .video_ops import extract_segments, crossfade_concat, apply_zoom
from .audio_ops import overlay_sfx_at_transitions, burn_subtitles, mix_background_music

REPO_ROOT = Path(__file__).resolve().parents[2]


def run(config: dict) -> Path:
    source = Path(config["source"])
    output = Path(config["output"])
    work_dir = output.parent / ".pipeline_tmp"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    transition_cfg = config.get("transition", {"duration": 0.3})
    zoom_cfg = config.get("zoom", {"enabled": True, "period": 4, "intensity": 0.08})
    sfx_cfg = config.get("sfx", {"enabled": False})
    sub_cfg = config.get("subtitles", {"enabled": False})
    music_cfg = config.get("music", {"enabled": False})

    print("[1/5] Extracting segments...")
    clips = extract_segments(source, config["segments"], work_dir)

    print("[2/5] Crossfade concat...")
    concat_out = work_dir / "concat.mp4"
    transition_centers = crossfade_concat(clips, concat_out, transition_cfg.get("duration", 0.3))

    current = concat_out
    if zoom_cfg.get("enabled", True):
        print("[3/5] Applying zoom...")
        zoom_out = work_dir / "zoom.mp4"
        apply_zoom(
            current, zoom_out,
            period=zoom_cfg.get("period", 4),
            intensity=zoom_cfg.get("intensity", 0.08),
            width=zoom_cfg.get("width", 720),
            height=zoom_cfg.get("height", 1280),
            fps=zoom_cfg.get("fps", 30),
            duty=zoom_cfg.get("duty", 0.5),
        )
        current = zoom_out

    if sfx_cfg.get("enabled", False):
        print("[4/5] Overlaying transition SFX...")
        sfx_out = work_dir / "sfx.mp4"
        overlay_sfx_at_transitions(
            current, sfx_out, Path(sfx_cfg["file"]),
            transition_centers, volume_db=sfx_cfg.get("volume_db", -14.0),
        )
        current = sfx_out

    if sub_cfg.get("enabled", False):
        print("[5/5] Burning subtitles...")
        sub_out = work_dir / "subtitled.mp4"
        burn_subtitles(
            current, sub_out, Path(sub_cfg["srt_path"]), Path(sub_cfg["font_path"]),
            font_size=sub_cfg.get("font_size", 42),
            primary_color=sub_cfg.get("primary_color", "&H0000FFFF"),
        )
        current = sub_out

    if music_cfg.get("enabled", False):
        print("[music] Mixing background track...")
        music_out = work_dir / "music.mp4"
        mix_background_music(
            current, music_out, Path(music_cfg["file"]),
            volume_db=music_cfg.get("volume_db", -22.0),
        )
        current = music_out

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(current, output)
    print(f"Done: {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description="Run the local footage-edit pipeline from a JSON config.")
    parser.add_argument("config", help="Path to a pipeline JSON config file.")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    run(config)


if __name__ == "__main__":
    main()
