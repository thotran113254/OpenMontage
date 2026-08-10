# -*- coding: utf-8 -*-
"""Anchor Gemini's (approximate, whole-second-rounded) timeline to the real
audio. Gemini keeps all creative decisions; timing is force-aligned to precise
word timestamps so captions/keywords/cards fire exactly on the spoken word.

Method: sequence-align Gemini's corrected caption tokens against the word-level
timestamps (accent-stripped), giving each token a real time; caption lines take
their span from their tokens; keywords/cards are shifted by the same local drift
via an anchor map (gemini_time -> real_time)."""
import json, re, unicodedata, difflib, bisect

base = r"C:\Users\PC\AppData\Local\Temp\claude\D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT\2e50444e-ceec-4f33-83b7-e7ab1620d735\scratchpad"
spec = json.load(open(base + r"\editor_spec.json", encoding="utf-8"))
words = json.load(open(base + r"\user_transcript.json", encoding="utf-8"))["word_timestamps"]

def norm(s):
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())

# ground-truth token stream from Whisper
wtok, wtime = [], []
for w in words:
    n = norm(w["word"])
    if n:
        wtok.append(n)
        wtime.append((w["start"], w["end"]))

# Gemini caption token stream, remembering which line each token belongs to
lines = spec["timeline"]["lines"]
gtok, gline = [], []
for li, l in enumerate(lines):
    for piece in l["text"].split():
        n = norm(piece)
        if n:
            gtok.append(n)
            gline.append(li)

# align gemini tokens -> whisper tokens
sm = difflib.SequenceMatcher(a=gtok, b=wtok, autojunk=False)
gtime = [None] * len(gtok)
for a0, b0, size in sm.get_matching_blocks():
    for k in range(size):
        gtime[a0 + k] = wtime[b0 + k][0]  # real start time of that word
# fill gaps by interpolation between known anchors
known = [(i, t) for i, t in enumerate(gtime) if t is not None]
for i in range(len(gtime)):
    if gtime[i] is None:
        # nearest known on both sides
        left = [k for k in known if k[0] <= i]
        right = [k for k in known if k[0] >= i]
        if left and right:
            (il, tl), (ir, tr) = left[-1], right[0]
            gtime[i] = tl if ir == il else tl + (tr - tl) * (i - il) / (ir - il)
        elif left:
            gtime[i] = left[-1][1]
        elif right:
            gtime[i] = right[0][1]
        else:
            gtime[i] = 0.0

# accurate per-line start/end from its tokens
line_real = {}
for li in range(len(lines)):
    ts = [gtime[i] for i in range(len(gtok)) if gline[i] == li]
    if ts:
        we = [wtime[b][1] for b in range(len(wtok))]  # not used; use token ends approx
        line_real[li] = (min(ts), max(ts))

# build anchor map: gemini original line.start/end -> real start/end
anchors = []  # (gemini_time, real_time)
for li, l in enumerate(lines):
    if li in line_real:
        anchors.append((l["start"], line_real[li][0]))
        anchors.append((l["end"], line_real[li][1]))
anchors.sort()
axs = [a[0] for a in anchors]
ays = [a[1] for a in anchors]

def remap_time(t):
    if not axs:
        return t
    if t <= axs[0]:
        return ays[0] + (t - axs[0])
    if t >= axs[-1]:
        return ays[-1] + (t - axs[-1])
    j = bisect.bisect_left(axs, t)
    x0, x1 = axs[j - 1], axs[j]
    y0, y1 = ays[j - 1], ays[j]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (t - x0) / (x1 - x0)

# write corrected caption lines
for li, l in enumerate(lines):
    if li in line_real:
        a, b = line_real[li]
        l["start"] = round(a, 2)
        l["end"] = round(max(a + 0.4, b + 0.15), 2)  # small tail past last word

# correct keywords + cards via anchor map
for k in spec["timeline"].get("keywords", []):
    k["inSeconds"] = round(remap_time(k["inSeconds"]), 2)
    k["outSeconds"] = round(remap_time(k["outSeconds"]), 2)
for c in spec["timeline"].get("cards", []):
    c["inSeconds"] = round(remap_time(c["inSeconds"]), 2)
    c["outSeconds"] = round(remap_time(c["outSeconds"]), 2)
    if c.get("bulletTimes"):
        c["bulletTimes"] = [round(remap_time(t), 2) for t in c["bulletTimes"]]

json.dump(spec, open(base + r"\editor_spec_aligned.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("Aligned caption lines (real audio times):")
for l in lines[:12]:
    print(f"  [{l['start']:6.2f}-{l['end']:6.2f}] {l['text']}")
print("Cards:")
for c in spec["timeline"].get("cards", []):
    print(f"  [{c['inSeconds']:6.2f}-{c['outSeconds']:6.2f}] {c.get('title')} bt={c.get('bulletTimes')}")
