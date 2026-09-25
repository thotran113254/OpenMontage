"""Look controls: the grade chain and the A-roll frame.

These guard the two things a viewer notices first — skin/colour and whether the
footage looks deliberately framed — and they are pure string/dict construction,
so they run without ffmpeg.
"""

from __future__ import annotations

import pytest

from lib.talking_head_edit.resolve_media import build_grade_chain, default_sharpening
from lib.talking_head_edit.stages.resolve import FRAME_PRESETS, aroll_pixel_size


def chain(**grade) -> str:
    return build_grade_chain(grade, 1080, 1920)


class TestGradeChain:
    def test_an_empty_grade_only_resizes(self):
        """Footage is preserved unless a correction is asked for."""
        assert chain() == "scale=1080:1920:flags=lanczos+accurate_rnd+full_chroma_int"

    def test_a_basic_grade_adds_no_unrequested_look(self):
        result = chain(brightness=0.02, contrast=1.05, gamma=1.0)
        assert "eq=brightness=0.02" in result
        assert "curves=" not in result and "vignette=" not in result

    def test_vibrance_is_off_until_asked_for(self):
        """It stacks on the director's saturation, which is what turned skin orange."""
        assert "vibrance=" not in chain()
        assert "vibrance=intensity=0.15" in chain(vibrance=0.15)

    def test_saturation_is_capped_where_skin_starts_to_go_orange(self):
        result = chain(saturation=1.4)
        value = float(result.split("saturation=")[1].split(":")[0])
        assert value <= 1.03, "saturation cao là da ngả cam trước khi ảnh kịp rực"

    def test_saturation_below_the_cap_passes_through(self):
        result = chain(saturation=1.01)
        assert "saturation=1.01" in result

    def test_filters_are_ordered_tone_then_colour_then_sharpen(self):
        result = chain(tone_curve=1, brightness=0.02, vibrance=0.2, sharpen=0.6,
                       clarity=0.4, vignette=0.5)
        order = [result.index(f) for f in
                 ("curves=", "eq=", "vibrance=", "unsharp=", "cas=", "vignette=")]
        assert order == sorted(order), "sai thứ tự sẽ ra màu khác hẳn"

    def test_sharpening_uses_a_tight_radius_and_adds_cas(self):
        """Wide-radius unsharp makes a soft halo; CAS lifts edges without one."""
        result = chain(sharpen=0.6, clarity=0.4)
        assert "unsharp=3:3:0.6:3:3:0.0" in result
        assert "cas=strength=0.4" in result

    def test_sharpening_is_off_by_default(self):
        """Upscaled phone footage is left as shot; `auto_sharpen` opts back in."""
        result = build_grade_chain({}, 1012, 1800, source_width=720)
        assert "unsharp=" not in result and "cas=" not in result

    def test_sharpening_scales_down_when_there_is_nothing_to_enlarge(self):
        """Native-resolution footage has real detail; the same push makes halos."""
        upscaled = default_sharpening(720, 1012)
        native = default_sharpening(1080, 1012)
        assert upscaled == (1.6, 0.85), "điểm đã đo trên clip render phải giữ nguyên"
        assert native[0] < upscaled[0] and native[1] < upscaled[1]

    def test_downscaling_is_treated_like_native(self):
        """Shrinking sharpens on its own — no extra push warranted."""
        assert default_sharpening(1440, 1012) == default_sharpening(1080, 1012)

    def test_an_unknown_source_size_does_not_over_sharpen(self):
        assert default_sharpening(None, 1012) == (0.8, 0.5)

    def test_sharpening_is_capped(self):
        """Past this, halos appear around hair and the shoulder line."""
        amount = float(chain(sharpen=9).split("unsharp=3:3:")[1].split(":")[0])
        assert amount <= 2.0

    def test_sharpening_can_be_switched_off(self):
        result = chain(sharpen=0, clarity=0)
        assert "unsharp=" not in result
        assert "cas=" not in result

    def test_smoothing_runs_before_the_upscale_and_sharpening_after(self):
        """Correcting source pixels covers proportionally more face per unit of blur."""
        result = chain(skin_smooth=0.3, sharpen=0.5)
        assert result.index("smartblur=") < result.index("scale=1080:1920")
        assert result.index("scale=1080:1920") < result.index("unsharp=")

    def test_upscale_interpolates_chroma_properly(self):
        """Source is yuv420p, so chroma arrives at half size — it softens edges."""
        assert "full_chroma_int" in chain()

    def test_each_extra_can_be_switched_off(self):
        result = chain(brightness=0.02, tone_curve=0, vibrance=0, vignette=0)
        assert "curves=" not in result
        assert "vibrance=" not in result
        assert "vignette=" not in result
        assert "eq=" in result, "phần grade cơ bản vẫn phải còn"

    def test_highlight_rolloff_keeps_whites_below_clipping(self):
        result = chain(tone_curve=1)
        curve = result.split("curves=all='")[1].split("'")[0]
        assert curve.endswith("1/0.96"), "trần sáng phải được kéo xuống dưới 1.0"

    def test_stronger_tone_pushes_midtones_further(self):
        weak = chain(tone_curve=0.5).split("curves=all='")[1].split("'")[0]
        strong = chain(tone_curve=1.0).split("curves=all='")[1].split("'")[0]
        assert weak != strong

    def test_vignette_strength_is_capped(self):
        result = chain(vignette=99)
        angle = float(result.split("vignette=angle=PI/")[1].split(":")[0])
        assert angle >= 6 / 1.4 - 0.01, "vignette quá tay sẽ thành đường hầm tối"

    def test_warmth_lifts_red_much_more_than_it_drops_blue(self):
        """Symmetric push was what burned skin yellow: blue fell 29% in the face."""
        result = chain(warmth=20)
        balance = result.split("colorbalance=")[1].split(",")[0]
        red = float(balance.split("rm=")[1].split(":")[0])
        blue = float(balance.split("bm=")[1])
        assert red > 0 > blue
        assert abs(blue) < abs(red), "kênh xanh không được bị dìm ngang mức đẩy đỏ"
        assert abs(blue) == pytest.approx(red * 0.5, abs=0.005)

    def test_half_strength_vignette_stays_gentle(self):
        """Measured against the source: full strength cost 14% of brightness."""
        angle = float(chain(vignette=0.5).split("vignette=angle=PI/")[1].split(":")[0])
        assert angle == pytest.approx(12.0, abs=0.1)

    def test_skin_smoothing_scales_with_the_requested_amount(self):
        def strength(amount: float) -> float:
            return float(chain(skin_smooth=amount)
                         .split("luma_strength=")[1].split(":")[0])

        assert strength(0.6) > strength(0.1)
        assert "smartblur=" not in chain(skin_smooth=0)

    def test_smoothing_is_dialled_back_for_running_at_source_resolution(self):
        """A radius on 720p pixels covers ~1.5x more face than the same at 1080."""
        applied = float(chain(skin_smooth=0.4).split("luma_strength=")[1].split(":")[0])
        assert 0 < applied < 0.4

    def test_blemish_reduce_does_not_touch_the_smartblur_threshold(self):
        """A defined blemish/scar is a local edge, and smartblur is built to
        protect edges (its own ffmpeg description: "blur without impacting
        the outlines") — no threshold setting removes one without also
        blurring hair/eyes. `blemish_reduce` drives frequency separation
        instead (see the tests below); the smartblur threshold stays fixed."""
        for amount in (1.0, 0.0):
            result = chain(skin_smooth=0.3, blemish_reduce=amount)
            assert result.split("luma_threshold=")[1].split(":")[0] == "-12"

    def test_blemish_reduce_off_skips_the_frequency_separation_chain(self):
        """Zero cost when the feature isn't used — matches every other
        default-off knob in this chain (vibrance, tone_curve, vignette)."""
        result = chain(blemish_reduce=0)
        assert "maskedmerge" not in result
        assert "grainextract" not in result

    def test_blemish_reduce_on_adds_a_masked_frequency_separation_step(self):
        result = chain(blemish_reduce=0.5)
        assert "grainextract" in result and "grainmerge" in result
        assert "maskedmerge" in result
        # masked to skin-toned pixels (Cr-Cb warm bias) — not the whole frame,
        # or a room's own lighting gradient shows up as a false "haze"
        # (measured: SSIM 0.88 on an untouched ceiling crop without the mask).
        assert "geq=" in result

    def test_blemish_reduce_scales_how_much_the_colour_layer_smooths(self):
        def blotch_sigma(amount: float) -> float:
            return float(chain(blemish_reduce=amount).split("fslow2]gblur=sigma=")[1].split("[")[0])

        assert blotch_sigma(1.0) > blotch_sigma(0.5) > blotch_sigma(0.01)

    def test_frequency_separation_runs_before_the_upscale(self):
        """Same reasoning as smartblur: correcting source pixels before the
        upscale covers proportionally more of the face per unit of blur."""
        result = chain(blemish_reduce=0.5)
        assert result.index("maskedmerge") < result.index("scale=1080:1920")


class TestFramePresets:
    def test_none_means_edge_to_edge(self):
        assert FRAME_PRESETS["none"] is None

    def test_dark_is_inset_with_a_visible_border(self):
        preset = FRAME_PRESETS["dark"]
        assert preset["inset"] >= 40, "inset quá nhỏ thì không ai thấy khung"
        assert preset["border"] > 0
        assert preset["background"] == "dark"

    def test_every_preset_stays_within_the_frame(self):
        for name, preset in FRAME_PRESETS.items():
            if preset is None:
                continue
            assert preset["inset"] * 2 < 1080, f"{name}: inset nuốt hết chiều ngang"
            assert preset.get("radius", 0) >= 0


class TestArollPixelSize:
    """The intermediate must be encoded at the size it is displayed at.

    Encoding at 1080 and letting the browser resample down to the inset box was
    an upscale-then-downscale with a lossy encode between; on a face crop it
    cost more sharpness than the entire sharpening chain recovered.
    """

    def test_an_inset_preset_encodes_at_the_displayed_size(self):
        assert aroll_pixel_size(1080, 1920, "dark") == (1012, 1800)

    def test_no_frame_means_no_change(self):
        assert aroll_pixel_size(1080, 1920, "none") == (1080, 1920)

    def test_an_unknown_preset_falls_back_to_full_size(self):
        assert aroll_pixel_size(1080, 1920, "khong-co-that") == (1080, 1920)

    def test_dimensions_stay_even_for_yuv420p(self):
        for name in FRAME_PRESETS:
            width, height = aroll_pixel_size(1080, 1920, name)
            assert width % 2 == 0 and height % 2 == 0, name

    def test_aspect_is_preserved_so_the_face_is_not_stretched(self):
        width, height = aroll_pixel_size(1080, 1920, "dark")
        assert width / height == pytest.approx(1080 / 1920, abs=0.002)


class TestAudioChain:
    """Voice cleanup. Order matters more than the individual filters."""

    def test_cleanup_runs_before_dynamics_and_loudness(self):
        from lib.talking_head_edit.resolve_media import build_audio_chain
        chain = build_audio_chain("voice", 1.06)
        assert chain.index("highpass=") < chain.index("acompressor=")
        assert chain.index("afftdn=") < chain.index("acompressor=")
        assert chain.index("acompressor=") < chain.index("loudnorm=")

    def test_highpass_leads_because_rumble_is_the_worst_band(self):
        """Measured: 20-120 Hz had 6.4 dB SNR vs ~15 dB in the vocal band."""
        from lib.talking_head_edit.resolve_media import build_audio_chain
        assert build_audio_chain("voice", 1.0).startswith("highpass=")

    def test_off_keeps_loudness_but_skips_cleanup(self):
        from lib.talking_head_edit.resolve_media import build_audio_chain
        chain = build_audio_chain("off", 1.0)
        assert "highpass=" not in chain
        assert "afftdn=" not in chain
        assert "loudnorm=" in chain, "vẫn phải chuẩn hoá độ to"

    def test_tempo_is_applied_last(self):
        from lib.talking_head_edit.resolve_media import build_audio_chain
        chain = build_audio_chain("voice", 1.06)
        assert chain.endswith("atempo=1.06")

    def test_unknown_preset_falls_back_to_voice(self):
        from lib.talking_head_edit.resolve_media import build_audio_chain
        assert build_audio_chain("khong-co-that", 1.0) == build_audio_chain("voice", 1.0)

    def test_strong_preset_denoises_harder_than_the_default(self):
        from lib.talking_head_edit.resolve_media import AUDIO_PRESETS
        weak = int(AUDIO_PRESETS["voice"].split("afftdn=nr=")[1].split(":")[0])
        strong = int(AUDIO_PRESETS["voice_strong"].split("afftdn=nr=")[1].split(":")[0])
        assert strong > weak

    def test_limiter_guards_peaks_before_loudness_normalisation(self):
        from lib.talking_head_edit.resolve_media import build_audio_chain
        chain = build_audio_chain("voice", 1.0)
        assert chain.index("alimiter=") < chain.index("loudnorm=")

    def test_shotgun_uses_a_smooth_expander_not_a_gate(self):
        """A fast gate killed the reverb tail and the ends of words with it."""
        from lib.talking_head_edit.resolve_media import AUDIO_PRESETS
        assert "compand=" in AUDIO_PRESETS["shotgun"]
        assert "agate=" not in AUDIO_PRESETS["shotgun"]

    def test_dry_preset_is_the_one_allowed_to_gate(self):
        from lib.talking_head_edit.resolve_media import AUDIO_PRESETS
        assert "agate=" in AUDIO_PRESETS["shotgun_dry"]

    def test_every_preset_keeps_the_presence_lift(self):
        from lib.talking_head_edit.resolve_media import AUDIO_PRESETS
        for name in ("shotgun", "shotgun_dry"):
            assert "f=5000" in AUDIO_PRESETS[name], f"{name}: mất độ hiện diện của mic cận"


class TestOptionEnabled:
    """Boolean options must survive an explicit null.

    `options.get(name, True)` returns None for `{"bgm": null}`, and None is
    falsy — so a form or API client that sends nulls instead of omitting keys
    silently turns off the cold open, the music, or the sharpening measurement,
    with nothing in any log pointing at the cause. `POST /run` merges its
    payload straight into the stored options, so that path is the likely one.
    """

    def test_absent_key_uses_the_default(self):
        from lib.talking_head_edit.job_store import option_enabled
        assert option_enabled({}, "bgm") is True
        assert option_enabled({}, "calibrate_grade", False) is False

    def test_explicit_null_is_treated_as_not_set(self):
        from lib.talking_head_edit.job_store import option_enabled
        assert option_enabled({"bgm": None}, "bgm") is True
        assert option_enabled({"auto_sharpen": None}, "auto_sharpen") is True

    def test_explicit_false_still_turns_it_off(self):
        from lib.talking_head_edit.job_store import option_enabled
        assert option_enabled({"bgm": False}, "bgm") is False
        assert option_enabled({"cold_open": 0}, "cold_open") is False


class TestNullOptionsDoNotDisableFeatures:
    """The three default-on options, through the code that actually reads them."""

    def test_prompt_keeps_the_cold_open_and_music_blocks(self):
        from lib.talking_head_edit.prompt_structure import build_structure_prompt
        words = [{"word": "xin", "start": 0.0, "end": 0.2}] * 5

        for options in ({}, {"cold_open": None, "bgm": None}):
            prompt = build_structure_prompt(words, options)
            assert '"cold_open": null (người dùng tắt' not in prompt, options
            assert "người dùng tắt nhạc nền" not in prompt, options

        off = build_structure_prompt(words, {"cold_open": False, "bgm": False})
        assert '"cold_open": null (người dùng tắt' in off
        assert "người dùng tắt nhạc nền" in off

    def test_preview_context_still_reads_the_measurement(self, tmp_path):
        import json

        from lib.talking_head_edit import preview
        from lib.talking_head_edit.job_store import JobStore

        source = tmp_path / "footage.mp4"
        source.write_bytes(b"video")
        job = JobStore(root=tmp_path / "jobs").create(source, {}, title="null opts")
        (job.dir / "sharpen_report.json").write_text(
            json.dumps({"sharpen": 1.5, "clarity": 0.85}), encoding="utf-8")

        # auto_sharpen is opt-in: null means "not set", which is off.
        job.update(options={**job.load()["options"], "auto_sharpen": True})
        assert preview.render_grade_context(job)["sharpen_source"] == "measured"

        job.update(options={**job.load()["options"], "auto_sharpen": None})
        assert preview.render_grade_context(job)["sharpen_source"] != "measured"


class TestHslAndLutAndNaturalAudio:
    def test_selectivecolor_hsl_preset_string(self):
        from lib.talking_head_edit.resolve_media import build_selectivecolor_chain
        chain_str = build_selectivecolor_chain({"color_preset": "da-trang-hong"})
        assert chain_str is not None
        assert "selectivecolor=" in chain_str
        assert "reds=" in chain_str
        assert "yellows=" in chain_str

    def test_selectivecolor_custom_hsl_string(self):
        from lib.talking_head_edit.resolve_media import build_selectivecolor_chain
        chain_str = build_selectivecolor_chain({
            "hsl": {
                "yellows": {"saturation": -0.2, "brightness": 0.15},
                "magentas": {"saturation": 0.25, "brightness": 0.0},
            }
        })
        assert chain_str is not None
        assert "yellows=" in chain_str
        assert "magentas=" in chain_str

    def test_curated_lut_included_in_grade_chain(self):
        from lib.talking_head_edit.resolve_media import build_grade_chain
        chain = build_grade_chain({"lut": "clean_bright"}, 64, 96)
        assert "lut3d=file=" in chain
        assert "clean_bright.cube" in chain

    def test_voice_natural_uses_gentle_denoise_without_compand(self):
        from lib.talking_head_edit.resolve_media import AUDIO_PRESETS, build_audio_chain
        assert "voice_natural" in AUDIO_PRESETS
        assert "afftdn=nr=10" in AUDIO_PRESETS["voice_natural"]
        assert "compand=" not in AUDIO_PRESETS["voice_natural"]
        assert "agate=" not in AUDIO_PRESETS["voice_natural"]
        chain = build_audio_chain("voice_natural", 1.0)
        assert "ratio=2.0:attack=15" in chain
