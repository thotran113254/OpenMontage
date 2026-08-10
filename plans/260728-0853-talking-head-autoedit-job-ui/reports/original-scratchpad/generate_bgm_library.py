# -*- coding: utf-8 -*-
"""Synthesize 5 loopable BGM placeholder tracks for the MONA-style editor.
Voice-first mixing: sparse midrange, no melody hooks fighting speech. Each is a
64-ish second seamless loop (whole bars), written to remotion-composer/public/.
These are usable placeholders — real tracks come from Suno/Udio via the prompt
doc; same filenames so swapping in the real ones is a file replace.
"""
import numpy as np, subprocess, os, wave

SR = 44100
OUT = r"D:\CODE WITH AI\VIDEO-EDITOR-AI-AGENT\remotion-composer\public"

NOTE = {n: 440.0 * 2 ** ((i - 9) / 12) for i, n in enumerate(
    ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
def freq(name, octave):
    return NOTE[name] * 2 ** (octave - 4)

def env(n, a, d, s_level, r, total):
    """simple ADSR over `total` samples; segments clamp to fit short notes"""
    e = np.zeros(total)
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    a = min(a, total)
    d = min(d, total - a)
    r = min(r, total - a - d)
    sus = max(0, total - a - d - r)
    if a: e[:a] = np.linspace(0, 1, a)
    if d: e[a:a + d] = np.linspace(1, s_level, d)
    if sus: e[a + d:a + d + sus] = s_level
    if r: e[a + d + sus:] = np.linspace(e[a + d + sus - 1] if a + d + sus > 0 else s_level, 0, r)
    return e

def onepole_lp(x, cutoff):
    """cheap one-pole lowpass"""
    dt = 1.0 / SR
    rc = 1.0 / (2 * np.pi * cutoff)
    alpha = dt / (rc + dt)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # vectorize via lfilter-style recursion
        acc += alpha * (x[i] - acc)
        y[i] = acc
    return y

# vectorized replacement for the loop above (same math, ~100x faster)
def lp(x, cutoff):
    dt = 1.0 / SR
    rc = 1.0 / (2 * np.pi * cutoff)
    a = dt / (rc + dt)
    from scipy.signal import lfilter
    return lfilter([a], [1, -(1 - a)], x)
try:
    import scipy.signal  # noqa
except ImportError:
    lp = lambda x, c: onepole_lp(x, c)  # noqa

def tone(f, dur, kind="sine", detune=0.0):
    t = np.arange(int(dur * SR)) / SR
    x = np.sin(2 * np.pi * f * t)
    if detune:
        x = 0.6 * x + 0.4 * np.sin(2 * np.pi * f * (1 + detune) * t)
    if kind == "tri":
        x = 2 / np.pi * np.arcsin(np.sin(2 * np.pi * f * t))
    if kind == "saw":
        x = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 7)) * (2 / np.pi)
    return x

def kick(dur=0.14):
    t = np.arange(int(dur * SR)) / SR
    f = 110 * np.exp(-t * 26) + 42
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 22)

def hat(dur=0.04, hp=6500):
    n = np.random.RandomState(7).randn(int(dur * SR))
    n = n - lp(n, hp)
    return n * np.exp(-np.arange(len(n)) / SR * 90) * 0.8

def snare(dur=0.12):
    n = np.random.RandomState(3).randn(int(dur * SR))
    body = np.sin(2 * np.pi * 190 * np.arange(len(n)) / SR)
    return (0.5 * (n - lp(n, 1800)) + 0.5 * body) * np.exp(-np.arange(len(n)) / SR * 30)

def place(buf, x, at_s, gain=1.0):
    i = int(at_s * SR)
    j = min(len(buf), i + len(x))
    if i < len(buf):
        buf[i:j] += x[: j - i] * gain

CHORDS = {  # chord name -> semitone intervals over root
    "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "7": [0, 4, 7, 10],
    "maj": [0, 4, 7], "m": [0, 3, 7], "sus2": [0, 2, 7],
}
def chord_freqs(root, octv, kind):
    base = freq(root, octv)
    return [base * 2 ** (s / 12) for s in CHORDS[kind]]

def render_track(cfg):
    bpm = cfg["bpm"]; beat = 60.0 / bpm
    bars = cfg.get("bars", 16)
    total_s = bars * 4 * beat
    n = int(total_s * SR)
    mix = np.zeros(n)

    # ---- pad layer: one chord per bar --------------------------------------
    prog = cfg["prog"]
    for b in range(bars):
        root, octv, kind = prog[b % len(prog)]
        dur = 4 * beat
        for f in chord_freqs(root, octv, kind):
            x = tone(f, dur, kind="saw" if cfg.get("pad") == "saw" else "sine", detune=0.004)
            x *= env(0, 0.4, 0.3, 0.55, 0.6, len(x))
            place(mix, lp(x, cfg.get("pad_lp", 900)), b * dur, cfg.get("pad_gain", 0.16) / 4)

    # ---- bass: root notes, pattern of eighths ------------------------------
    for b in range(bars):
        root, octv, kind = prog[b % len(prog)]
        f = freq(root, octv - 1)
        for step in cfg.get("bass_steps", [0, 3, 6]):
            x = tone(f, beat * 0.45, kind="sine")
            x *= env(0.004, 0.1, 0.5, 0.2, len(x) / SR and 0.15, len(x))
            place(mix, x, b * 4 * beat + step * beat / 2, cfg.get("bass_gain", 0.22))

    # ---- drums -------------------------------------------------------------
    if cfg.get("drums", True):
        k, h, s = kick(), hat(), snare()
        for b in range(bars):
            t0 = b * 4 * beat
            for kb in cfg.get("kicks", [0, 2]):
                place(mix, k, t0 + kb * beat, cfg.get("kick_gain", 0.5))
            for hb in cfg.get("hats", [0.5, 1.5, 2.5, 3.5]):
                place(mix, h, t0 + hb * beat, 0.25)
            for sb in cfg.get("snares", []):
                place(mix, s, t0 + sb * beat, 0.3)

    # ---- pluck motif: soft pentatonic accents (kept sparse + low-passed) ---
    if cfg.get("pluck"):
        rs = np.random.RandomState(cfg.get("seed", 1))
        scale = cfg["pluck_scale"]
        for b in range(bars):
            if rs.rand() < cfg.get("pluck_density", 0.7):
                nname, octv = scale[rs.randint(len(scale))]
                x = tone(freq(nname, octv), beat * 0.9, kind="tri")
                x *= np.exp(-np.arange(len(x)) / SR * 7)
                place(mix, lp(x, 2400), b * 4 * beat + rs.choice([0, 1.5, 2, 3]) * beat, 0.12)

    # normalize to -14 dBFS-ish peak, gentle fade edges for clean looping
    mix = mix / (np.max(np.abs(mix)) + 1e-9) * 0.55
    fade = int(0.03 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)

    wav = os.path.join(OUT, cfg["name"] + ".wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())
    mp3 = os.path.join(OUT, cfg["name"] + ".mp3")
    subprocess.run(["ffmpeg", "-y", "-i", wav, "-b:a", "160k", mp3, "-loglevel", "error"], check=True)
    os.remove(wav)
    print(f'{cfg["name"]}.mp3  {total_s:.1f}s  bpm={bpm}')

TRACKS = [
    # 1) default explainer: bright marimba-pop bounce
    dict(name="bgm_upbeat_bounce", bpm=104, prog=[("C", 4, "maj7"), ("A", 3, "m7"), ("F", 3, "maj7"), ("G", 3, "7")],
         pad_lp=1100, pad_gain=0.15, bass_steps=[0, 3, 4, 7], kicks=[0, 1, 2, 3], hats=[0.5, 1.5, 2.5, 3.5],
         snares=[1, 3], pluck=True, pluck_scale=[("C", 5), ("D", 5), ("E", 5), ("G", 5), ("A", 5)], seed=11),
    # 2) lo-fi chill: slower, darker pad, lazy hats
    dict(name="bgm_lofi_chill", bpm=82, prog=[("A", 3, "m7"), ("F", 3, "maj7"), ("C", 4, "maj7"), ("E", 3, "m7")],
         pad="saw", pad_lp=700, pad_gain=0.18, bass_steps=[0, 5], kicks=[0, 2.5], hats=[1, 3],
         snares=[1, 3], pluck=True, pluck_density=0.45, pluck_scale=[("A", 4), ("C", 5), ("D", 5), ("E", 5), ("G", 5)], seed=5),
    # 3) energetic drive: four-on-floor, brighter
    dict(name="bgm_energy_drive", bpm=118, prog=[("F", 3, "maj7"), ("G", 3, "7"), ("E", 3, "m7"), ("A", 3, "m7")],
         pad_lp=1400, pad_gain=0.13, bass_steps=[0, 2, 4, 6], kicks=[0, 1, 2, 3], hats=[0.5, 1, 1.5, 2.5, 3, 3.5],
         snares=[1, 3], pluck=True, pluck_density=0.85, pluck_scale=[("C", 5), ("E", 5), ("F", 5), ("G", 5), ("A", 5)], seed=21),
    # 4) minimal tech pulse: sparse, sus chords, no snare — AI/tech mood
    dict(name="bgm_tech_pulse", bpm=100, prog=[("D", 3, "sus2"), ("A", 3, "sus2"), ("B", 3, "m"), ("G", 3, "maj")],
         pad_lp=800, pad_gain=0.2, bass_steps=[0, 6], kicks=[0, 2], hats=[0.5, 2.5], snares=[],
         pluck=True, pluck_density=0.3, pluck_scale=[("D", 5), ("E", 5), ("F#", 5), ("A", 5)], seed=8),
    # 5) warm acoustic-ish: soft triads, gentle pulse, story mood
    dict(name="bgm_warm_story", bpm=92, prog=[("G", 3, "maj"), ("E", 3, "m"), ("C", 4, "maj"), ("D", 3, "maj")],
         pad_lp=950, pad_gain=0.17, bass_steps=[0, 4], kicks=[0, 2], hats=[1, 3], snares=[3],
         pluck=True, pluck_density=0.6, pluck_scale=[("G", 4), ("B", 4), ("D", 5), ("G", 5)], seed=14),
    # 6) explainer #2: neutral clean, less playful than bounce — safe default
    dict(name="bgm_clean_explainer", bpm=100, prog=[("D", 4, "maj7"), ("B", 3, "m7"), ("G", 3, "maj7"), ("A", 3, "7")],
         pad_lp=1000, pad_gain=0.16, bass_steps=[0, 4], kicks=[0, 2], hats=[0.5, 1.5, 2.5, 3.5],
         snares=[1, 3], pluck=True, pluck_density=0.5, pluck_scale=[("D", 5), ("F#", 5), ("A", 5), ("B", 5)], seed=31),
    # 7) explainer #3: playful staccato pop — listicle "tips" mood
    dict(name="bgm_playful_pop", bpm=108, prog=[("E", 3, "maj"), ("C#", 3, "m7"), ("A", 3, "maj7"), ("B", 3, "7")],
         pad_lp=1200, pad_gain=0.13, bass_steps=[0, 2, 4, 6], kicks=[0, 1.5, 2, 3.5], hats=[0.5, 1, 2.5, 3],
         snares=[1, 3], pluck=True, pluck_density=0.9, pluck_scale=[("E", 5), ("G#", 5), ("B", 5), ("C#", 6)], seed=42),
    # 8) tech #2: data-groove — steadier bassline, tighter hats
    dict(name="bgm_data_groove", bpm=102, prog=[("F#", 3, "m7"), ("D", 3, "maj7"), ("A", 3, "maj7"), ("E", 3, "7")],
         pad="saw", pad_lp=850, pad_gain=0.15, bass_steps=[0, 2, 3, 6], kicks=[0, 2], hats=[0.5, 1, 1.5, 2.5, 3, 3.5],
         snares=[2], pluck=True, pluck_density=0.35, pluck_scale=[("F#", 5), ("A", 5), ("C#", 6)], seed=27),
    # 9) tech #3: futuristic calm — slow airy sus pads, barely-there beat
    dict(name="bgm_future_calm", bpm=88, prog=[("C", 4, "sus2"), ("G", 3, "sus2"), ("A", 3, "m7"), ("F", 3, "maj7")],
         pad_lp=750, pad_gain=0.22, bass_steps=[0], kicks=[0], hats=[2], snares=[],
         pluck=True, pluck_density=0.25, pluck_scale=[("C", 5), ("D", 5), ("G", 5)], seed=9),
    # 10) energy #2: urgent percussive — dense drums, minor tension
    dict(name="bgm_urgent_percussive", bpm=122, prog=[("D", 3, "m"), ("A#", 2, "maj"), ("F", 3, "maj"), ("C", 3, "maj")],
         pad_lp=900, pad_gain=0.12, bass_steps=[0, 1, 4, 5], kicks=[0, 1, 2, 3], hats=[0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75],
         snares=[1, 2.5, 3], pluck=False, seed=3),
    # 11) chill #2: darker night lo-fi — for reflective talking
    dict(name="bgm_lofi_night", bpm=78, prog=[("E", 3, "m7"), ("C", 4, "maj7"), ("A", 3, "m7"), ("B", 3, "m7")],
         pad="saw", pad_lp=620, pad_gain=0.2, bass_steps=[0, 6], kicks=[0, 2.75], hats=[1.5, 3.5],
         snares=[1, 3], pluck=True, pluck_density=0.35, pluck_scale=[("E", 4), ("G", 4), ("B", 4), ("D", 5)], seed=17),
    # 12) warm #2: hopeful lift — brighter, rising feel for inspirational closers
    dict(name="bgm_hopeful_lift", bpm=95, prog=[("C", 4, "maj"), ("G", 3, "maj"), ("A", 3, "m"), ("F", 3, "maj")],
         pad_lp=1050, pad_gain=0.18, bass_steps=[0, 4, 6], kicks=[0, 2], hats=[0.5, 1.5, 2.5, 3.5],
         snares=[3], pluck=True, pluck_density=0.65, pluck_scale=[("C", 5), ("E", 5), ("G", 5), ("C", 6)], seed=23),
]

for cfg in TRACKS:
    render_track(cfg)
print("DONE ->", OUT)
