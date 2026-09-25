"""Look and sound RECIPES: the ffmpeg filter chains, and the numbers behind them.

Image corrections are opt-in: an empty grade only resizes the footage. Values
tuned on one recording must not silently become a look for every source.

Which pieces get cut lives in `resolve_spans`, and running ffmpeg over them lives
in `resolve_cut`. Neither knows how the numbers here were chosen, and dependency
runs one way: they import from here, never the reverse.

Hard-won constraints — changing any of these has broken sync before:

* `aselect` SILENTLY passes every audio frame on this ffmpeg build (verified:
  a bare aselect returned the full duration), which desyncs audio from the
  select-cut video. Audio is therefore cut with atrim-per-span + concat.
* Video and audio must be cut at IDENTICAL boundaries, then asserted to agree
  within 0.35s — otherwise every downstream event time drifts.
* `loudnorm`/compressor/limiter must run ONCE on the joined timeline. Per span,
  each span gets its own gain and the volume steps at every edit point.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# 20 ms in/out on every span. Inaudible as a fade, enough to kill the click a
# mid-waveform cut leaves behind.
SEAM_FADE = 0.02
MAX_SATURATION = 1.03   # above this, skin reads orange before anything else pops
# Phone footage arrives soft (this source: 2.0 Mbps at 720x1280) and gets
# upscaled 1.5x, so detail has to be defended rather than assumed.
SHARPEN_RADIUS = 3      # 5x5 unsharp spreads into a soft halo; 3x3 stays crisp
# 720-wide source into a 1012-wide A-roll — the setup every sharpening number
# here was measured on.
MEASURED_UPSCALE = 1012 / 720

# Voice cleanup presets. Measured on this footage: the worst signal-to-noise is
# NOT hiss but 20-120 Hz room rumble (6.4 dB SNR there vs 15 dB in the vocal
# band), which loudnorm then amplifies into the "rè" people hear. So the chain
# leads with a high-pass, not with a denoiser.
AUDIO_PRESETS: dict[str, str] = {
    "off": "",
    "voice_natural": (
        "highpass=f=75,"                                    # cut low rumble without eating vocal warmth
        "afftdn=nr=10:nf=-35:tn=1,"                         # gentle denoise: preserves word endings & natural breath
        "equalizer=f=220:t=q:w=1.2:g=1.0,"                  # warmth & vocal body (150-250Hz)
        "equalizer=f=3200:t=q:w=1.5:g=1.2,"                 # presence / clarity
        "equalizer=f=6000:t=q:w=1.5:g=-1.5,"                # soften harsh sibilance
        "deesser=i=0.3"                                     # clean de-esser
    ),
    "studio_warm": (
        "highpass=f=80,"
        "afftdn=nr=12:nf=-34:tn=1,"
        "equalizer=f=200:t=q:w=1.1:g=1.5,"
        "equalizer=f=2800:t=q:w=1.4:g=1.0,"
        "equalizer=f=6500:t=q:w=1.5:g=-2.0,"
        "deesser=i=0.35"
    ),
    "voice": (
        "highpass=f=100,"                                   # room rumble
        "afftdn=nr=18:nf=-32:tn=1,"                         # residual steady noise
        "equalizer=f=250:t=q:w=1.2:g=-2.5,"                 # boxiness
        "equalizer=f=3500:t=q:w=1.5:g=2,"                   # presence/intelligibility
        "deesser=i=0.35"                                    # tame the sibilance that adds
    ),
    "voice_strong": (
        "highpass=f=110,"
        "afftdn=nr=26:nf=-30:tn=1,"
        "equalizer=f=250:t=q:w=1.2:g=-3.5,"
        "equalizer=f=3500:t=q:w=1.5:g=2.5,"
        "deesser=i=0.45"
    ),
    # Emulates what a shotgun mic actually gives you: quiet between words
    # (directionality) plus the presence lift of a close, on-axis mic.
    #
    # The level control is a smooth `compand` expander, NOT a gate. Measured
    # room decay here is RT60 ~0.35-0.4s, and a fast gate does kill the tail —
    # it also chops the ends off words. A listening pass scored the fast gate
    # 4/10 for naturalness ("mất đuôi âm") against 8/10 for this curve, while
    # the two were within 1.5 dB of each other on tail suppression.
    "shotgun": (
        "highpass=f=90,"
        "afftdn=nr=20:nf=-32:tn=1,"
        "compand=attacks=0.003:decays=0.08:"
        "points=-80/-90|-45/-60|-30/-34|-20/-20|0/0:soft-knee=6,"
        "equalizer=f=400:t=q:w=1.1:g=-2,"
        "equalizer=f=5000:t=q:w=1.2:g=3,"
        "equalizer=f=10000:t=q:w=1:g=1.5,"
        "deesser=i=0.4"
    ),
    # Drier still, for genuinely reverberant rooms: a fast gate plus harder
    # denoise. Scored 9/10 for dryness but only 6/10 for naturalness — reach
    # for it when the room is the bigger problem than the artefacts.
    "shotgun_dry": (
        "highpass=f=90,"
        "afftdn=nr=26:nf=-30:tn=1,"
        "agate=threshold=0.04:ratio=3.5:range=0.08:attack=3:release=60:knee=3,"
        "equalizer=f=400:t=q:w=1.1:g=-2,"
        "equalizer=f=5000:t=q:w=1.2:g=3,"
        "equalizer=f=10000:t=q:w=1:g=1.5,"
        "deesser=i=0.4"
    ),
}

# Chasing signal-to-noise alone picks the wrong preset. Measured SNR ranked
# `shotgun_tight` best (16.7 dB vs 14.5); a listening pass ranked it worst for
# naturalness. Noise numbers say what was removed, not what survived.


def build_audio_master_chain(preset: str = "shotgun") -> str:
    """Cleanup → dynamics → loudness, run ONCE on the joined timeline.

    Order is the whole point: denoising after the compressor would mean
    amplifying the noise first and then trying to remove what the compressor
    already pulled up level with the voice.

    Nothing in here may run per-span. `loudnorm` measures over a window, so a
    per-span pass gives each span its own gain and the volume steps at every
    single edit point. Same for the compressor and limiter, which react to
    whatever peaks happen to be inside the piece they see.
    """
    cleanup = AUDIO_PRESETS.get(preset, AUDIO_PRESETS["voice"])
    parts = [cleanup] if cleanup else []
    if preset in ("voice_natural", "studio_warm"):
        parts += [
            "acompressor=threshold=-20dB:ratio=2.0:attack=15:release=180",
            "alimiter=limit=0.95",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            "aresample=48000",
        ]
    else:
        parts += [
            "acompressor=threshold=-20dB:ratio=2.5:attack=8:release=180",
            "alimiter=limit=0.95",          # catches peaks so loudnorm isn't the only guard
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            "aresample=48000",
        ]
    return ",".join(parts)


def build_audio_span_chain(tempo: float = 1.06, duration: float | None = None,
                           fade: float = SEAM_FADE) -> str:
    """The only audio processing allowed per span: seam fade + tempo.

    `atempo` belongs here, not in the master chain: it is linear and
    context-free, and video and audio must come out of each span at the same
    length or the concat drifts.

    The 20 ms fades are cheap insurance against the click you get when a cut
    lands mid-waveform — too short to hear as a fade, long enough to kill the
    discontinuity. Previously only the cold-open seam had one.
    """
    parts: list[str] = [f"afade=t=in:st=0:d={fade}"]
    if duration and duration > fade * 2:
        parts.append(f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade}")
    parts.append(f"atempo={tempo}")
    return ",".join(parts)


def build_audio_chain(preset: str = "shotgun", tempo: float = 1.06) -> str:
    """Master chain plus tempo — the single-pass form.

    Kept because the audio-preset preview compares presets on one short sample
    where there is no seam and no concat, and because it is the reference the
    per-span path is measured against: the filter string here is byte-identical
    to what the pipeline used before the split, so a LUFS comparison between the
    two paths is comparing the same filters in the same order.
    """
    return f"{build_audio_master_chain(preset)},atempo={tempo}"


class ResolveError(RuntimeError):
    pass


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError as exc:
        raise ResolveError(f"Không đọc được thời lượng: {path}") from exc


def stream_durations(path: str | Path) -> dict[str, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120,
    ).stdout
    durations: dict[str, float] = {}
    for line in out.strip().splitlines():
        parts = line.split(",")
        if len(parts) >= 2:
            try:
                durations[parts[0]] = float(parts[1])
            except ValueError:
                continue
    return durations


def speaking_moments(words: list[dict[str, Any]], count: int = 3,
                     fallback: float = 5.0) -> list[float]:
    """A handful of moments spread through the take where the speaker is
    actually talking — the middle WORDS, not the middle second. Shared by every
    per-footage measurement (sharpen, exposure, white balance) so they all
    sample the same kind of moment instead of each guessing its own.
    """
    if not words:
        return [fallback]
    n = len(words)
    picks = [n * (i + 1) // (count + 1) for i in range(count)]
    return [float(words[min(i, n - 1)]["start"]) for i in picks]


def default_sharpening(source_width: int | None, output_width: int) -> tuple[float, float]:
    """How hard to sharpen, given how far the footage has to be enlarged.

    Sharpening does not create detail; it raises contrast on the detail that is
    there. Interpolated pixels have none of their own, so upscaled footage needs
    a lot and footage that arrives at output size needs little — the same amount
    on native footage just makes halos and pushes skin texture.

    MEASURED_UPSCALE is the one real data point: a 720x1280 source enlarged to a
    1012-wide A-roll, where 1.6/0.85 beat the alternatives by 106% on a face crop
    of the rendered clip. The native-resolution end is a conservative
    extrapolation and has NOT been verified against real 1080p footage yet.
    """
    upscale = 1.0 if not source_width else max(1.0, output_width / source_width)
    t = (upscale - 1.0) / (MEASURED_UPSCALE - 1.0)
    return (round(min(2.0, 0.8 + (1.6 - 0.8) * t), 2),
            round(min(1.0, 0.5 + (0.85 - 0.5) * t), 2))


# `Cr - Cb` for a face crop on this footage measured 51-55; every background
# surface tested (ceiling, wall, mic foam, shelf, the white shirt) measured
# -3 to +9 — a wide, warm-vs-neutral margin that holds regardless of the
# person's own skin tone (dark or light skin is still warmer than a grey
# wall). 32 sits in the middle of that gap. Cheap and per-video-adaptive:
# no model, no calibration pass, same one threshold works on any footage
# because it compares each pixel's own warmth to neutral, not to a fixed hue.
_SKIN_MASK_CR_CB_THRESHOLD = 32
_SKIN_MASK_FEATHER_SIGMA = 3   # softens the mask edge so the jawline doesn't seam
_FS_DETAIL_SIGMA = 4           # separates pore-scale texture from macro colour
_FS_BLOTCH_SIGMA_MAX = 22      # blemish_reduce=1.0; verified no bleed up to 28


def _blemish_reduce_chain(blemish: float) -> str:
    """Frequency-separation blotch smoothing, masked to skin-toned pixels only.

    `smartblur` cannot do this job: a defined blemish or scar is a local
    edge, and smartblur is explicitly "blur without impacting the outlines"
    (its own ffmpeg description) — pushing its threshold far enough to touch
    a scar's edge also blurs hair, eyes, everything else with real contrast.

    Frequency separation sidesteps that: split the frame into a heavily
    blurred macro-colour layer (`fslowsmooth`) and a fine-detail layer
    (`fshigh` = original minus a MILDLY blurred copy), then recombine.
    Detail (pores, hair, sharp edges) survives untouched because it is added
    back verbatim; only the macro colour/tone — which is what a blotchy red
    mark actually is — gets smoothed. Verified on real footage: a visible
    scar's redness fades noticeably while hair/eyebrows stay pixel-sharp.

    Applying that over the WHOLE frame is not safe though — measured on this
    footage: recombining two DIFFERENT blur radii introduces a soft
    vignette-like haze across the background (SSIM dropped as low as 0.88 on
    a plain ceiling crop that should not have changed at all), because real
    rooms have large-scale lighting gradients that two different Gaussian
    radii smooth differently. A cheap skin-tone mask (see
    `_SKIN_MASK_CR_CB_THRESHOLD`) gates the correction to skin-toned pixels
    only, via `maskedmerge`; background then comes from the untouched
    original (verified SSIM 0.997 against raw on the same ceiling crop).
    """
    blotch_sigma = round(_FS_DETAIL_SIGMA + blemish * (_FS_BLOTCH_SIGMA_MAX - _FS_DETAIL_SIGMA), 1)
    return (
        "split=3[fsbase][fsmaskin][fsin]"
        ";[fsin]split=2[fsa][fsb]"
        f";[fsa]gblur=sigma={_FS_DETAIL_SIGMA},split=2[fslow1][fslow2]"
        ";[fsb][fslow1]blend=all_mode=grainextract[fshigh]"
        f";[fslow2]gblur=sigma={blotch_sigma}[fslowsmooth]"
        ";[fslowsmooth][fshigh]blend=all_mode=grainmerge[fsout]"
        # The mask is cr - cb > threshold. `geq` evaluates that as an expression
        # per pixel: this whole chain cost ~178 CPU-seconds for 3s of
        # 1440x2560@60 with it, ~90 without. Moving cr and cb into plane 0 of
        # two copies lets `lut2` read the answer from a precomputed 256x256
        # table; the output is bit-identical (same framemd5).
        ";[fsmaskin]format=yuv444p,split=2[fsm1][fsm2]"
        ";[fsm1]shuffleplanes=map0=2[fscr];[fsm2]shuffleplanes=map0=1[fscb]"
        f";[fscr][fscb]lut2=c0='if(gt(x-y\\,{_SKIN_MASK_CR_CB_THRESHOLD})\\,255\\,0)':c1=128:c2=128,"
        f"gblur=sigma={_SKIN_MASK_FEATHER_SIGMA}[fsmask]"
        ";[fsbase][fsout][fsmask]maskedmerge"
    )

def build_selectivecolor_chain(grade: dict[str, Any]) -> str | None:
    """Targeted per-color HSL tuning via FFmpeg selectivecolor.

    Controls color ranges (reds, yellows, magentas, greens, cyans, blues, whites, neutrals, blacks)
    with independent brightness (via K channel) and saturation/hue adjustments.
    Supports named presets ('da-trang-hong', 'khu-am-vang', 'cinema-punchy'),
    a nested 'hsl' dict, and flat keys (e.g. 'hsl_yellow_sat', 'hsl_yellow_bright').
    """
    color_map = {
        "reds": ("red", "reds"),
        "yellows": ("yellow", "yellows"),
        "greens": ("green", "greens"),
        "cyans": ("cyan", "cyans"),
        "blues": ("blue", "blues"),
        "magentas": ("magenta", "magentas", "purples", "purple"),
        "whites": ("white", "whites"),
        "neutrals": ("neutral", "neutrals"),
        "blacks": ("black", "blacks"),
    }

    presets = {
        "da-trang-hong": {
            "yellows": {"saturation": -0.12, "brightness": 0.12, "magenta": 0.04},
            "reds": {"saturation": 0.15, "brightness": 0.0, "cyan": -0.08},
            "neutrals": {"brightness": 0.04}
        },
        "khu-am-vang": {
            "yellows": {"saturation": -0.22, "brightness": 0.10, "yellow": -0.08},
            "neutrals": {"brightness": 0.04, "yellow": -0.05},
            "reds": {"saturation": 0.10, "brightness": 0.0}
        },
        "cinema-punchy": {
            "reds": {"saturation": 0.20, "brightness": 0.0},
            "yellows": {"saturation": -0.06, "brightness": 0.08},
            "magentas": {"saturation": 0.25, "brightness": 0.0},
            "blues": {"saturation": 0.15, "brightness": -0.05}
        }
    }

    hsl_input: dict[str, dict[str, float]] = {}
    preset_name = grade.get("color_preset") or grade.get("hsl_preset")
    if preset_name and preset_name in presets:
        for k, v in presets[preset_name].items():
            hsl_input[k] = dict(v)

    if isinstance(grade.get("hsl"), dict):
        for k, v in grade["hsl"].items():
            if isinstance(v, dict):
                hsl_input.setdefault(k, {}).update(v)

    for target, aliases in color_map.items():
        for prefix in ("hsl_", ""):
            for alias in aliases:
                sat_key = f"{prefix}{alias}_sat"
                bright_key = f"{prefix}{alias}_bright"
                if sat_key in grade or bright_key in grade:
                    cur = hsl_input.setdefault(target, {})
                    if sat_key in grade:
                        try:
                            cur["saturation"] = float(grade[sat_key])
                        except (ValueError, TypeError):
                            pass
                    if bright_key in grade:
                        try:
                            cur["brightness"] = float(grade[bright_key])
                        except (ValueError, TypeError):
                            pass

    if not hsl_input:
        return None

    def _clamp(val: float, low: float = -1.0, high: float = 1.0) -> float:
        return round(max(low, min(high, float(val))), 3)

    parts = []
    for target in ("reds", "yellows", "greens", "cyans", "blues", "magentas", "whites", "neutrals", "blacks"):
        data = None
        for alias in color_map[target]:
            if alias in hsl_input:
                data = hsl_input[alias]
                break
        if not data or not isinstance(data, dict):
            continue

        c = float(data.get("c", data.get("cyan", 0.0)))
        m = float(data.get("m", data.get("magenta", 0.0)))
        y = float(data.get("y", data.get("yellow", 0.0)))
        k = float(data.get("k", data.get("black", 0.0)))

        sat = float(data.get("saturation", data.get("sat", 0.0)))
        bright = float(data.get("brightness", data.get("bright", 0.0)))

        if bright:
            k -= bright

        if sat:
            if target == "reds":
                c -= sat * 0.8
                m += sat * 0.2
                y += sat * 0.2
            elif target == "yellows":
                y += sat * 0.9
                c -= sat * 0.1
            elif target == "magentas":
                m += sat * 0.9
                c -= sat * 0.1
                y -= sat * 0.1
            elif target == "greens":
                m -= sat * 0.8
                y += sat * 0.3
                c += sat * 0.3
            elif target == "cyans":
                c += sat * 0.9
                m -= sat * 0.1
            elif target == "blues":
                y -= sat * 0.8
                c += sat * 0.3
                m += sat * 0.3

        c = _clamp(c)
        m = _clamp(m)
        y = _clamp(y)
        k = _clamp(k)

        if (c, m, y, k) != (0.0, 0.0, 0.0, 0.0):
            parts.append(f"{target}='{c} {m} {y} {k}'")

    if not parts:
        return None
    return "selectivecolor=" + ":".join(parts) + ":correction_method=relative"


def build_grade_chain(grade: dict[str, Any], width: int, height: int,
                      source_width: int | None = None) -> str:
    """Resize, then apply only requested corrections; preserve source texture.

    Smoothing operates before resizing. No temporal denoising is coupled to
    skin or blemish controls: it can smear moving faces even at skin_smooth=0.
    source_width remains available to callers that also use auto calibration.
    """
    skin = float(grade.get("skin_smooth", 0) or 0)
    blemish = float(grade.get("blemish_reduce", 0) or 0)
    warmth = float(grade.get("warmth", 0) or 0)
    tone = float(grade.get("tone_curve", 0) or 0)
    vibrance = float(grade.get("vibrance", 0) or 0)
    vignette = min(1.4, float(grade.get("vignette", 0) or 0))
    sharpen = min(2.0, float(grade.get("sharpen", 0) or 0))
    clarity = min(1.0, float(grade.get("clarity", 0) or 0))

    parts = []
    if skin > 0:
        # Natural gentle skin softening: scaled so low values preserve pore texture
        clamped_skin = min(0.4, skin)
        radius = round(1.2 + clamped_skin * 1.8, 1)
        luma_strength = round(clamped_skin * 0.65, 2)
        chroma_strength = round(clamped_skin * 0.35, 2)
        parts.append(
            f"smartblur=luma_radius={radius}:luma_strength={luma_strength}:"
            f"luma_threshold=-12:chroma_radius={radius}:"
            f"chroma_strength={chroma_strength}:chroma_threshold=-20"
        )
    if blemish > 0:
        parts.append(_blemish_reduce_chain(blemish))
    parts.append(f"scale={width}:{height}:flags=lanczos+accurate_rnd+full_chroma_int")

    if tone > 0:
        shadow = round(0.25 - 0.01 * tone, 4)
        midtone = round(0.5 + 0.03 * tone, 4)
        upper = round(0.78 + 0.02 * tone, 4)
        ceiling = round(1 - 0.04 * tone, 4)
        parts.append(f"curves=all='0/0 0.25/{shadow} 0.5/{midtone} 0.78/{upper} 1/{ceiling}'")

    brightness = float(grade.get("brightness", 0))
    contrast = float(grade.get("contrast", 1))
    saturation = min(MAX_SATURATION, float(grade.get("saturation", 1)))
    gamma = float(grade.get("gamma", 1))
    if (brightness, contrast, saturation, gamma) != (0, 1, 1, 1):
        parts.append(
            f"eq=brightness={brightness}:contrast={contrast}:"
            f"saturation={round(saturation, 3)}:gamma={gamma}"
        )
    if vibrance > 0:
        parts.append(f"vibrance=intensity={round(vibrance, 3)}")
    if warmth:
        red_mid = round(warmth / 100 * 0.2, 3)
        blue_mid = round(-red_mid * 0.5, 3)
        parts.append(f"colorbalance=rm={red_mid}:bm={blue_mid}")
    selective = build_selectivecolor_chain(grade)
    if selective:
        parts.append(selective)

    lut_ref = grade.get("lut") or grade.get("lut_path")
    if lut_ref:
        lut_path = Path(lut_ref)
        if not lut_path.exists():
            # Check under repo root luts/ directory
            repo_luts = Path(__file__).resolve().parent.parent.parent / "luts"
            candidate = repo_luts / (lut_ref if str(lut_ref).endswith(".cube") else f"{lut_ref}.cube")
            if candidate.exists():
                lut_path = candidate
        if lut_path.exists():
            safe_lut = str(lut_path.resolve()).replace("\\", "/").replace(":", "\\:")
            parts.append(f"lut3d=file='{safe_lut}':interp=tetrahedral")
    if sharpen > 0:
        parts.append(
            f"unsharp={SHARPEN_RADIUS}:{SHARPEN_RADIUS}:{sharpen}:"
            f"{SHARPEN_RADIUS}:{SHARPEN_RADIUS}:0.0"
        )
    if clarity > 0:
        parts.append(f"cas=strength={round(clarity, 3)}")
    if vignette > 0:
        parts.append(f"vignette=angle=PI/{round(6 / max(0.05, vignette), 2)}:mode=forward")

    return ",".join(parts)



