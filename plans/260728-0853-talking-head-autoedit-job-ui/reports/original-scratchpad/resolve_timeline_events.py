# -*- coding: utf-8 -*-
"""Resolve the free-timeline spec (word-anchored events) into MonaTimeline
props. Same guarantees as the wordspine pipeline: one clock, cut removes video
+audio at identical boundaries with safety padding, every event time is looked
up from the spine and remapped onto the post-cut timeline.
"""
import os, json, subprocess

REPO = r"D:\CODE WITH AI\VIDEO-EDITOR-AI-AGENT"
SP = os.path.dirname(os.path.abspath(__file__))
RAW = r"C:\Users\PC\Downloads\1783586132708_568061814121655968_7874081316310818820.mp4"
PUB = os.path.join(REPO, "remotion-composer", "public")

SFX_OK = {"sfx_pop.mp3", "sfx_pop_high.mp3", "sfx_pop_low.mp3", "sfx_whoosh.mp3",
          "sfx_riser.mp3", "sfx_ding.mp3", "sfx_tick.mp3"}

spec = json.load(open(os.path.join(SP, "timeline_spec.json"), encoding="utf-8"))
words = json.load(open(os.path.join(SP, "user_transcript.json"), encoding="utf-8"))["word_timestamps"]
NW = len(words)
ws = [w["start"] for w in words]
we = [w["end"] for w in words]

def probe_dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip())
DUR = probe_dur(RAW)

# ---- grade chain (identical mapping to the wordspine pipeline) ---------------
g = spec.get("grade", {})
ss = g.get("skin_smooth", 0); br = g.get("blemish_reduce", 0); w = g.get("warmth", 0)
lr = round(2 + ss * 2.5, 1); ls = round(ss, 2); lt = max(-30, int(-(15 + br * 30)))
cs = round(ss * 0.5, 2); dn_t = round(4 + br * 6, 1)
rm = round(w / 100 * 0.4, 3); bm = round(-w / 100 * 0.4, 3)
grade = (f"scale=1080:1920:flags=lanczos,hqdn3d=1.2:1.2:{dn_t}:{dn_t},"
         f"smartblur=luma_radius={lr}:luma_strength={ls}:luma_threshold={lt}:chroma_radius={lr}:chroma_strength={cs}:chroma_threshold=-20,"
         f"eq=brightness={g.get('brightness',0)}:contrast={g.get('contrast',1)}:saturation={g.get('saturation',1)}:gamma={g.get('gamma',1)},"
         f"colorbalance=rm={rm}:bm={bm},unsharp=5:5:{g.get('sharpen',0)}:5:5:0.0")

# ---- cut with safety padding (never clip word onsets) ------------------------
PAD = 0.08
MIN_CUT = 0.12
def clampi(i): return max(0, min(NW - 1, int(i)))
removes = []
for pair in spec.get("cut_remove", []):
    if not pair or len(pair) < 2:
        continue
    a, b = clampi(pair[0]), clampi(pair[1])
    if b < a: a, b = b, a
    rs = min(we[a - 1] + PAD, ws[a]) if a > 0 else ws[a]
    re_ = max(ws[b + 1] - PAD, we[b]) if b + 1 < NW else we[b]
    if re_ - rs >= MIN_CUT:
        removes.append((round(rs, 3), round(re_, 3)))
removes.sort()
merged = []
for s, e in removes:
    if merged and s <= merged[-1][1] + 0.02:
        merged[-1] = (merged[-1][0], max(merged[-1][1], e))
    else:
        merged.append((s, e))
removes = merged

kept, cur = [], 0.0
for s, e in removes:
    if s > cur: kept.append((cur, s))
    cur = max(cur, e)
if cur < DUR: kept.append((cur, DUR))
if not kept: kept = [(0.0, DUR)]

TEMPO = 1.06  # imperceptible speed-up — shorter video, same pitch (atempo)
keepexpr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in kept)
# Audio cutting uses atrim-per-span + concat — aselect silently passes ALL
# frames on this ffmpeg build (verified: bare aselect returned full duration),
# which desynced audio from the select-cut video. atrim/concat is exact.
fc = (
    f"[0:v]{grade},select='{keepexpr}',setpts=N/FRAME_RATE/TB,setpts=PTS/{TEMPO}[vout];"
    + "".join(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}];"
              for i, (a, b) in enumerate(kept))
    + "".join(f"[a{i}]" for i in range(len(kept)))
    + f"concat=n={len(kept)}:v=0:a=1[acat];"
    f"[acat]acompressor=threshold=-18dB:ratio=3:attack=10:release=200,"
    f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,atempo={TEMPO}[aout]"
)
out_video = os.path.join(PUB, "mona_timeline_src.mp4")
subprocess.run(["ffmpeg", "-y", "-i", RAW, "-filter_complex", fc,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-r", "30", out_video, "-loglevel", "error"], check=True)
NEW = probe_dur(out_video)
# hard guarantee: video and audio must agree, else everything downstream drifts
def stream_durs(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
                          "-of", "csv=p=0", p], capture_output=True, text=True).stdout
    d = {}
    for line in out.strip().splitlines():
        typ, dur = line.split(",")[:2]
        d[typ] = float(dur)
    return d
sd = stream_durs(out_video)
assert abs(sd.get("video", 0) - sd.get("audio", 0)) < 0.35, f"A/V mismatch: {sd}"

def removed_before(x):
    tot = 0.0
    for s, e in removes:
        if e <= x: tot += e - s
        elif s < x < e: tot += x - s
    return tot

def T(word_idx, use_end=False):
    i = clampi(word_idx)
    raw = we[i] if use_end else ws[i]
    for s, e in removes:
        if s < raw < e:
            raw = s
            break
    return round(max(0.0, min(NEW, (raw - removed_before(raw)) / TEMPO)), 3)

# ---- resolve events ----------------------------------------------------------
events = []
skipped = []
for e in spec.get("events", []):
    t = e.get("type")
    try:
        if t == "caption":
            w0, w1 = e["w0"], e["w1"]
            if w1 < w0: w0, w1 = w1, w0
            a, b = T(w0), T(w1, use_end=True)
            if b <= a: b = min(NEW, a + 0.4)
            ev = {"type": "caption", "at": a, "end": b, "text": e["text"]}
            if e.get("highlight"): ev["highlight"] = e["highlight"]
            if e.get("highlightColor"): ev["highlightColor"] = e["highlightColor"]
            events.append(ev)
        elif t == "keyword":
            at = T(e.get("atWord", e.get("w0", 0)))
            dur = float(e.get("durSec", 1.5))
            events.append({"type": "keyword", "at": at, "end": round(min(NEW, at + dur), 3),
                           "text": e["text"], "color": e.get("color", "#FFFFFF"),
                           "xPct": e.get("xPct", 20), "yPct": e.get("yPct", 15),
                           "rotation": e.get("rotation", 0), "fontSize": e.get("fontSize", 80),
                           "anim": e.get("anim", "pop")})
        elif t == "card":
            a, b = T(e["w0"]), T(e["w1"], use_end=True)
            if b - a < 1.0: b = min(NEW, a + 1.0)
            ev = {"type": "card", "at": a, "end": b, "kicker": e.get("kicker", ""),
                  "title": e.get("title", ""), "badge": str(e.get("badge", "")),
                  "bullets": e.get("bullets", [])}
            if e.get("bulletWords"):
                ev["bulletTimes"] = [T(k) for k in e["bulletWords"]]
            events.append(ev)
        elif t == "punch_in":
            events.append({"type": "punchIn", "at": T(e["atWord"]),
                           "scale": float(e.get("scale", 1.08)),
                           "holdSeconds": float(e.get("holdSec", 1.1))})
        elif t == "sfx":
            name = e.get("name", "")
            if name not in SFX_OK:
                skipped.append(f"sfx:{name}")
                continue
            at = max(0.0, T(e["atWord"]) + float(e.get("offsetSec", 0)))
            events.append({"type": "sfx", "at": round(at, 3), "name": name,
                           "volume": float(e.get("volume", 0.5))})
        elif t == "shake":
            events.append({"type": "shake", "at": T(e["atWord"]),
                           "durSeconds": float(e.get("durSec", 0.4)),
                           "intensity": float(e.get("intensity", 10))})
        elif t == "flash":
            events.append({"type": "flash", "at": T(e["atWord"]),
                           "durSeconds": float(e.get("durSec", 0.15))})
        else:
            skipped.append(f"unknown:{t}")
    except Exception as ex:
        skipped.append(f"{t}:{repr(ex)[:60]}")

# ---- auto-guards (mechanical fixes, keep Gemini's creative intent) -----------
# 1) A keyword whose span falls inside a card is invisible (renderer fades the
#    keyword layer while a card owns the screen). Shift it to just BEFORE the
#    card so it pops as a teaser; if there's no room, drop it and its paired sfx.
card_spans = [(e["at"], e["end"]) for e in events if e["type"] == "card"]
def in_card(t):
    return next(((a, b) for a, b in card_spans if a <= t < b), None)

moved, dropped = [], []
kept_events = []
for e in events:
    if e["type"] != "keyword":
        kept_events.append(e)
        continue
    span = in_card(e["at"])
    if not span:
        kept_events.append(e)
        continue
    dur = e["end"] - e["at"]
    new_at = span[0] - dur - 0.3        # finish right before the card wipes in
    if new_at >= 0.2 and not in_card(new_at):
        old = e["at"]
        e = {**e, "at": round(new_at, 3), "end": round(new_at + dur, 3)}
        kept_events.append(e)
        moved.append((old, e["at"], e["text"]))
    else:
        dropped.append((e["at"], e["text"]))

# post-pass on sfx glued to moved/dropped keywords (order-independent):
# dropped keyword -> its pop is an orphan, remove; moved keyword -> move pop too.
drop_ts = [t for t, _ in dropped]
move_map = [(old, new) for old, new, _ in moved]
fixed = []
for s in kept_events:
    if s["type"] == "sfx":
        if any(abs(s["at"] - t) < 0.25 for t in drop_ts):
            continue
        m = next(((o, n) for o, n in move_map if abs(s["at"] - o) < 0.25), None)
        if m:
            s = {**s, "at": round(m[1] + (s["at"] - m[0]), 3)}
    fixed.append(s)
events = fixed

# a MOVED keyword that now overlaps another keyword in time just clutters the
# hook — drop it (and its glued pop) rather than stack two flying words.
kws_now = sorted([e for e in events if e["type"] == "keyword"], key=lambda x: x["at"])
moved_ats = {n for _, n in move_map}
clutter_ts = []
for k in kws_now:
    if k["at"] in moved_ats and any(
        o is not k and not (k["end"] <= o["at"] or k["at"] >= o["end"]) for o in kws_now
    ):
        clutter_ts.append(k["at"])
if clutter_ts:
    events = [e for e in events
              if not (e["type"] == "keyword" and e["at"] in clutter_ts)
              and not (e["type"] == "sfx" and any(abs(e["at"] - t) < 0.25 for t in clutter_ts))]
    dropped.extend([(t, "clutter-after-move") for t in clutter_ts])

# 2) merge captions shorter than 0.5s into the next one (pill would just blink)
caps = sorted([e for e in events if e["type"] == "caption"], key=lambda c: c["at"])
others = [e for e in events if e["type"] != "caption"]
merged_caps, i = [], 0
n_merged = 0
while i < len(caps):
    c = caps[i]
    if c["end"] - c["at"] < 0.5 and i + 1 < len(caps):
        nxt = caps[i + 1]
        joined = {"type": "caption", "at": c["at"], "end": nxt["end"],
                  "text": (c["text"].rstrip() + " " + nxt["text"].lstrip()).strip()}
        hl = nxt.get("highlight") or c.get("highlight")
        if hl and hl in joined["text"]:
            joined["highlight"] = hl
        merged_caps.append(joined)
        n_merged += 1
        i += 2
    else:
        merged_caps.append(c)
        i += 1
events = others + merged_caps

# keyword width guard: shrink fontSize so long uppercase text never clips the
# frame edge (KeywordView anchors left/right/center by xPct; ~0.62em/char).
def fit_keyword_font(ev):
    n = max(1, len(ev.get("text", "")))
    x = ev.get("xPct", 20)
    if x <= 35:
        avail = 1080 * (1 - max(4, x) / 100) - 40
    elif x >= 65:
        avail = 1080 * (1 - max(4, 100 - x) / 100) - 40
    else:
        avail = 1080 * min(x, 100 - x) / 100 * 2 - 40
    fs_max = int(avail / (0.62 * n))
    ev["fontSize"] = max(44, min(int(ev.get("fontSize", 80)), fs_max))
    return ev

events = [fit_keyword_font(e) if e.get("type") == "keyword" else e for e in events]

# ---- COLD-OPEN: Gemini-picked "money quote" replayed as a 1.5-2.5s opener ---
# The span is cut from the FINAL (graded+cut+tempo) timeline and prepended, so
# the video opens on the most gripping line, then plays through normally.
cold = spec.get("cold_open")
offset = 0.0
teaser_events = []
if isinstance(cold, dict) and "w0" in cold and "w1" in cold:
    a = T(cold["w0"])
    w1i = clampi(cold["w1"])
    b_end = T(w1i, use_end=True)
    # pad only into real silence: when the next word starts back-to-back the
    # old fixed +0.1s pad dragged its first phoneme into the teaser ("...đâu B–")
    nxt = T(w1i + 1) if w1i + 1 < NW else NEW
    b = min(NEW, b_end + max(0.02, min(0.1, nxt - b_end - 0.02)))
    if b - a >= 1.0:
        teaser = os.path.join(PUB, "_teaser_tmp.mp4")
        base = os.path.join(PUB, "_base_tmp.mp4")
        os.replace(out_video, base)
        # 80ms audio fade-out masks any residual consonant at the seam
        subprocess.run(["ffmpeg", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", base,
                        "-af", f"afade=t=out:st={max(0.0, (b - a) - 0.08):.3f}:d=0.08",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-r", "30", teaser, "-loglevel", "error"], check=True)
        subprocess.run(["ffmpeg", "-y", "-i", teaser, "-i", base, "-filter_complex",
                        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
                        "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-r", "30", out_video, "-loglevel", "error"], check=True)
        os.remove(teaser); os.remove(base)
        total_now = probe_dur(out_video)
        offset = round(total_now - NEW, 3)
        NEW = total_now
        for e in events:  # shift the whole program past the teaser
            e["at"] = round(e["at"] + offset, 3)
            if "end" in e: e["end"] = round(e["end"] + offset, 3)
            if e.get("bulletTimes"):
                e["bulletTimes"] = [round(t + offset, 3) for t in e["bulletTimes"]]
        cap = cold.get("caption")
        if cap:
            tev = {"type": "caption", "at": 0.05, "end": max(0.6, offset - 0.1), "text": cap,
                   "highlightColor": cold.get("highlightColor", "#FF4D4D")}
            if cold.get("highlight"): tev["highlight"] = cold["highlight"]
            teaser_events.append(tev)
        kw = cold.get("keyword")
        if isinstance(kw, dict) and kw.get("text"):
            teaser_events.append(fit_keyword_font(
                {"type": "keyword", "at": 0.15, "end": max(0.8, offset - 0.05),
                 "text": kw["text"], "color": kw.get("color", "#FF2E93"),
                 "xPct": kw.get("xPct", 18), "yPct": kw.get("yPct", 12),
                 "rotation": kw.get("rotation", -4),
                 "fontSize": kw.get("fontSize", 84), "anim": kw.get("anim", "whip")}))
        # mechanical glue at the teaser->intro seam (declared to Gemini, so no doubles)
        teaser_events.append({"type": "punchIn", "at": 0.05, "scale": 1.08, "holdSeconds": min(1.2, offset)})
        teaser_events.append({"type": "sfx", "at": max(0.0, offset - 0.5), "name": "sfx_riser.mp3", "volume": 0.5})
        teaser_events.append({"type": "flash", "at": offset, "durSeconds": 0.15})
        teaser_events.append({"type": "sfx", "at": offset, "name": "sfx_whoosh.mp3", "volume": 0.5})
events = teaser_events + events

# ---- ENDCARD: closing CTA card after the footage ends ------------------------
end_spec = spec.get("endcard") or {}
if end_spec.get("title"):
    events.append({"type": "endcard", "at": round(NEW - 0.15, 3), "end": round(NEW + 2.8, 3),
                   "title": end_spec["title"], "subtitle": end_spec.get("subtitle", "")})
    TOTAL = NEW + 2.8
else:
    TOTAL = NEW

BGM_OK = {"bgm_upbeat_bounce.mp3", "bgm_clean_explainer.mp3", "bgm_playful_pop.mp3",
          "bgm_tech_pulse.mp3", "bgm_data_groove.mp3", "bgm_future_calm.mp3",
          "bgm_energy_drive.mp3", "bgm_urgent_percussive.mp3",
          "bgm_lofi_chill.mp3", "bgm_lofi_night.mp3",
          "bgm_warm_story.mp3", "bgm_hopeful_lift.mp3"}
props = {"videoSrc": "mona_timeline_src.mp4", "events": events,
         "durationSeconds": round(TOTAL, 3), "brandPill": "3 SAI LẦM • CHATBOT AI"}
bgm = spec.get("bgm")
if isinstance(bgm, dict) and bgm.get("name") in BGM_OK:
    props["bgm"] = {"name": bgm["name"],
                    # library normalized to -17 LUFS; 0.22 puts music ~15 dB
                    # under the -14 LUFS speech bed (pauses ride up 6 dB in-renderer)
                    "volume": max(0.15, min(0.35, float(bgm.get("volume", 0.22)))),
                    # renderer tiles Audio copies (loop+volume-callback is
                    # silent in Remotion) — it needs the real file length
                    "durationSeconds": round(probe_dur(os.path.join(PUB, bgm["name"])), 3)}
json.dump(props, open(os.path.join(PUB, "mona_timeline_props.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

from collections import Counter
print(f"CUT {len(removes)} removes | {DUR:.1f}s -> {NEW:.1f}s")
print("guard: keyword moved:", [(round(a,1), round(b,1), t) for a, b, t in moved],
      "| dropped:", dropped, f"| captions merged: {n_merged}")
print("event mix:", dict(Counter(e["type"] for e in events)))
if skipped: print("skipped:", skipped)
for e in events:
    if e["type"] == "card":
        print(f'  CARD [{e["at"]:.2f}-{e["end"]:.2f}] {e["title"]} bt={e.get("bulletTimes")}')
caps = [e for e in events if e["type"] == "caption"]
print("first captions:", [(c["at"], c["text"]) for c in caps[:3]])
print(f"cold_open offset={offset}s | TOTAL {TOTAL:.1f}s (incl endcard)")
print(f"FRAMES 0-{int(TOTAL*30)-1}")
