# -*- coding: utf-8 -*-
"""Resolve a WORD-ANCHORED spec into a rendered-ready props file.
Every overlay time is looked up from the word spine (accurate). Cut removes
whole word ranges from BOTH video and audio at identical timestamps, then all
overlay times are recomputed on the shortened spine. One clock, always in sync.
"""
import os, json, subprocess

REPO = r"D:\CODE WITH AI\VIDEO-EDITOR-AI-AGENT"
SP = r"C:\Users\PC\AppData\Local\Temp\claude\D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT\2e50444e-ceec-4f33-83b7-e7ab1620d735\scratchpad"
RAW = r"C:\Users\PC\Downloads\1783586132708_568061814121655968_7874081316310818820.mp4"
PUB = os.path.join(REPO, "remotion-composer", "public")

spec = json.load(open(os.path.join(SP, "wordspec.json"), encoding="utf-8"))
words = json.load(open(os.path.join(SP, "user_transcript.json"), encoding="utf-8"))["word_timestamps"]
NW = len(words)
ws = [w["start"] for w in words]
we = [w["end"] for w in words]

def probe_dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip())
DUR = probe_dur(RAW)

# ---- grade chain -------------------------------------------------------------
g = spec.get("grade", {})
ss = g.get("skin_smooth", 0); br = g.get("blemish_reduce", 0); w = g.get("warmth", 0)
lr = round(2 + ss * 2.5, 1); ls = round(ss, 2); lt = max(-30, int(-(15 + br * 30)))
cs = round(ss * 0.5, 2); dn_t = round(4 + br * 6, 1)
rm = round(w / 100 * 0.4, 3); bm = round(-w / 100 * 0.4, 3)
grade = (f"scale=1080:1920:flags=lanczos,hqdn3d=1.2:1.2:{dn_t}:{dn_t},"
         f"smartblur=luma_radius={lr}:luma_strength={ls}:luma_threshold={lt}:chroma_radius={lr}:chroma_strength={cs}:chroma_threshold=-20,"
         f"eq=brightness={g.get('brightness',0)}:contrast={g.get('contrast',1)}:saturation={g.get('saturation',1)}:gamma={g.get('gamma',1)},"
         f"colorbalance=rm={rm}:bm={bm},unsharp=5:5:{g.get('sharpen',0)}:5:5:0.0")

# ---- cut: word ranges -> time intervals, with SAFETY PADDING -----------------
# Never cut flush against the neighbouring words. Remove the filler + most of
# the surrounding silence, but leave PAD of breathing room next to each kept
# word so onsets aren't clipped and the jump-cut lands inside silence (no click).
PAD = 0.08          # seconds of breathing room kept next to neighbour words
MIN_CUT = 0.12      # skip cuts shorter than this (not worth the risk)
def clampi(i): return max(0, min(NW - 1, int(i)))
removes = []
for pair in spec.get("cut_remove", []):
    if not pair or len(pair) < 2:
        continue
    a, b = clampi(pair[0]), clampi(pair[1])
    if b < a: a, b = b, a
    # remove AT LEAST the filler span [ws[a], we[b]]; extend into the silence on
    # each side but stop PAD short of the neighbour word.
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

keepexpr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in kept)
vf = f"{grade},select='{keepexpr}',setpts=N/FRAME_RATE/TB"
af = f"aselect='{keepexpr}',asetpts=N/SR/TB"
out_video = os.path.join(PUB, "mona_wordspine_src.mp4")
subprocess.run(["ffmpeg", "-y", "-i", RAW, "-vf", vf, "-af", af,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-r", "30", out_video, "-loglevel", "error"], check=True)
NEW = probe_dur(out_video)

def removed_before(x):
    tot = 0.0
    for s, e in removes:
        if e <= x: tot += e - s
        elif s < x < e: tot += x - s
    return tot

def T(word_idx, use_end=False):
    """resolve a word index -> post-cut second (accurate, from the spine)."""
    i = clampi(word_idx)
    raw = we[i] if use_end else ws[i]
    for s, e in removes:
        if s < raw < e:
            raw = s
            break
    return round(max(0.0, min(NEW, raw - removed_before(raw))), 3)

# ---- resolve overlays --------------------------------------------------------
lines = []
for c in spec.get("captions", []):
    w0, w1 = c["w0"], c["w1"]
    if w1 < w0: w0, w1 = w1, w0          # tolerate reversed indices
    a, b = T(w0), T(w1, use_end=True)
    if b <= a: b = min(NEW, a + 0.4)
    item = {"text": c["text"], "startMs": int(a * 1000), "endMs": int(b * 1000)}
    if c.get("highlight"): item["highlight"] = c["highlight"]
    lines.append(item)
lines.sort(key=lambda x: x["startMs"])   # keep captions monotonic

keywords = []
for k in spec.get("keywords", []):
    at = T(k["atWord"])
    dur = float(k.get("durSec", 1.5))
    kk = {"text": k["text"], "color": k.get("color", "#FFFFFF"), "xPct": k.get("xPct", 20),
          "yPct": k.get("yPct", 15), "rotation": k.get("rotation", 0),
          "inSeconds": at, "outSeconds": round(min(NEW, at + dur), 3),
          "fontSize": k.get("fontSize", 80), "anim": k.get("anim", "pop")}
    keywords.append(kk)

punch_ins = []
for pi in spec.get("punch_in", []):
    punch_ins.append({"atSeconds": T(pi["atWord"]),
                      "scale": float(pi.get("scale", 1.06)),
                      "holdSeconds": float(pi.get("holdSec", 1.1))})

cards = []
for c in spec.get("cards", []):
    ci, co = T(c["w0"]), T(c["w1"], use_end=True)
    if co - ci < 1.0: co = min(NEW, ci + 1.0)
    cc = {"inSeconds": ci, "outSeconds": co, "kicker": c.get("kicker", ""),
          "title": c.get("title", ""), "badge": str(c.get("badge", "")), "bullets": c.get("bullets", [])}
    if c.get("bulletWords"):
        cc["bulletTimes"] = [T(k) for k in c["bulletWords"]]
    cards.append(cc)

props = {"videoSrc": "mona_wordspine_src.mp4", "lines": lines, "keywords": keywords,
         "cards": cards, "punchIns": punch_ins, "brandPill": "3 SAI LẦM • CHATBOT AI"}
json.dump(props, open(os.path.join(PUB, "mona_wordspine_props.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

print(f"CUT {len(removes)} removes | {DUR:.1f}s -> {NEW:.1f}s")
print(f"captions {len(lines)} | keywords {len(keywords)} | cards {len(cards)} | punchIns {len(punch_ins)}")
for c in cards:
    print(f'  CARD [{c["inSeconds"]:.2f}-{c["outSeconds"]:.2f}] {c["title"]} bt={c.get("bulletTimes")}')
print("first 6 captions (accurate times):")
for l in lines[:6]:
    print(f'  [{l["startMs"]/1000:.2f}-{l["endMs"]/1000:.2f}] {l["text"]}')
print(f"FRAMES 0-{int(NEW*30)-1}")
