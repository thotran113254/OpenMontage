# -*- coding: utf-8 -*-
"""Apply the one-session editor spec deterministically:
grade (ffmpeg beauty) + jump-cut (remove silence) in a single encode, then
remap every timeline timestamp onto the shortened timeline and write props."""
import os, json, subprocess

REPO = r"D:\CODE WITH AI\VIDEO-EDITOR-AI-AGENT"
SP = r"C:\Users\PC\AppData\Local\Temp\claude\D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT\2e50444e-ceec-4f33-83b7-e7ab1620d735\scratchpad"
RAW = r"C:\Users\PC\Downloads\1783586132708_568061814121655968_7874081316310818820.mp4"
PUB = os.path.join(REPO, "remotion-composer", "public")
spec = json.load(open(os.path.join(SP, "editor_spec.json"), encoding="utf-8"))

def probe_dur(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip()
    return float(out)

DUR = probe_dur(RAW)

# ---- grade chain -------------------------------------------------------------
g = spec.get("grade", {})
ss = g.get("skin_smooth", 0); br = g.get("blemish_reduce", 0); w = g.get("warmth", 0)
lr = round(2 + ss * 2.5, 1); ls = round(ss, 2)
lt = max(-30, int(-(15 + br * 30))); cs = round(ss * 0.5, 2)
dn_t = round(4 + br * 6, 1); rm = round(w / 100 * 0.4, 3); bm = round(-w / 100 * 0.4, 3)
grade = (f"scale=1080:1920:flags=lanczos,hqdn3d=1.2:1.2:{dn_t}:{dn_t},"
         f"smartblur=luma_radius={lr}:luma_strength={ls}:luma_threshold={lt}:chroma_radius={lr}:chroma_strength={cs}:chroma_threshold=-20,"
         f"eq=brightness={g.get('brightness',0)}:contrast={g.get('contrast',1)}:saturation={g.get('saturation',1)}:gamma={g.get('gamma',1)},"
         f"colorbalance=rm={rm}:bm={bm},unsharp=5:5:{g.get('sharpen',0)}:5:5:0.0")

# ---- cut: kept intervals = complement of removes -----------------------------
removes = sorted([(max(0.0, float(r["start"])), min(DUR, float(r["end"])))
                  for r in spec.get("cut", {}).get("remove", []) if float(r["end"]) > float(r["start"])])
kept = []
cur = 0.0
for s, e in removes:
    if s > cur:
        kept.append((cur, s))
    cur = max(cur, e)
if cur < DUR:
    kept.append((cur, DUR))
if not kept:
    kept = [(0.0, DUR)]

keepexpr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in kept)
vf = f"{grade},select='{keepexpr}',setpts=N/FRAME_RATE/TB"
af = f"aselect='{keepexpr}',asetpts=N/SR/TB"

out_video = os.path.join(PUB, "mona_edit_final_src.mp4")
subprocess.run(["ffmpeg", "-y", "-i", RAW, "-vf", vf, "-af", af,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-r", "30", out_video, "-loglevel", "error"], check=True)
new_dur = probe_dur(out_video)

# ---- remap timeline onto cut timeline ----------------------------------------
def removed_before(x):
    tot = 0.0
    for s, e in removes:
        if e <= x: tot += e - s
        elif s < x < e: tot += x - s
    return tot

def remap(t):
    for s, e in removes:
        if s < t < e:
            t = s
            break
    return round(t - removed_before(t), 3)

def clamp(t):
    # keep every timestamp inside the (post-cut) video so nothing renders on a
    # frozen tail — Gemini sometimes over-shoots a card's outSeconds.
    return max(0.0, min(t, new_dur))

tl = spec.get("timeline", {})
lines = []
for l in tl.get("lines", []):
    a, b = clamp(remap(l["start"])), clamp(remap(l["end"]))
    if b <= a:
        b = min(new_dur, a + 0.4)
    item = {"text": l["text"], "startMs": int(a * 1000), "endMs": int(b * 1000)}
    if l.get("highlight"):
        item["highlight"] = l["highlight"]
    lines.append(item)

keywords = []
for k in tl.get("keywords", []):
    ki, ko = clamp(remap(k["inSeconds"])), clamp(remap(k["outSeconds"]))
    if ko - ki < 0.4:
        ko = min(new_dur, ki + 1.4)
    kk = dict(k); kk["inSeconds"] = ki; kk["outSeconds"] = ko
    keywords.append(kk)

cards = []
for c in tl.get("cards", []):
    ci, co = clamp(remap(c["inSeconds"])), clamp(remap(c["outSeconds"]))
    if co - ci < 1.0:
        co = min(new_dur, ci + 1.0)
    cc = dict(c); cc["inSeconds"] = ci; cc["outSeconds"] = co
    if c.get("bulletTimes"):
        cc["bulletTimes"] = [clamp(remap(t)) for t in c["bulletTimes"]]
    cards.append(cc)

props = {"videoSrc": "mona_edit_final_src.mp4", "lines": lines, "keywords": keywords,
         "cards": cards, "brandPill": "3 SAI LẦM • CHATBOT AI"}
json.dump(props, open(os.path.join(PUB, "mona_edit_final_props.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

print(f"GRADE: {grade}")
print(f"CUT: {len(removes)} removes, {DUR:.1f}s -> {new_dur:.1f}s")
print(f"TIMELINE: lines {len(lines)} | keywords {len(keywords)} | cards {len(cards)}")
print(f"NEW_DURATION {new_dur:.2f}")
print(f"FRAMES 0-{int(new_dur*30)-1}")
