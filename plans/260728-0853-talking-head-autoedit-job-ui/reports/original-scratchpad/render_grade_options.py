# -*- coding: utf-8 -*-
"""Render the raw clip through each beauty-grade preset + keep an ungraded
reference, then build one side-by-side comparison image. Prints the exact
ffmpeg numbers per option so the user can learn the "chỉ số"."""
import os, json, subprocess

SP = r"C:\Users\PC\AppData\Local\Temp\claude\D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT\2e50444e-ceec-4f33-83b7-e7ab1620d735\scratchpad"
CLIP = os.path.join(SP, "grade_clip.mp4")
reco = json.load(open(os.path.join(SP, "grade_reco.json"), encoding="utf-8"))

def beauty_chain(p):
    ss = p["skin_smooth"]; br = p["blemish_reduce"]; w = p["warmth"]
    lr = round(2 + ss * 2.5, 1)          # smartblur luma radius
    ls = round(ss, 2)                    # luma strength (>0 blurs flat skin)
    lt = max(-30, int(-(15 + br * 30)))  # negative threshold => keep edges, smooth skin (ffmpeg range [-30,30])
    cs = round(ss * 0.5, 2)             # chroma smooth => calms blemish redness
    ct = -20
    dn_t = round(4 + br * 6, 1)          # temporal denoise
    rm = round(w / 100 * 0.4, 3)        # warmth via midtone red/blue balance
    bm = round(-w / 100 * 0.4, 3)
    return (
        f"scale=1080:1920:flags=lanczos,"
        f"hqdn3d=1.2:1.2:{dn_t}:{dn_t},"
        f"smartblur=luma_radius={lr}:luma_strength={ls}:luma_threshold={lt}:"
        f"chroma_radius={lr}:chroma_strength={cs}:chroma_threshold={ct},"
        f"eq=brightness={p['brightness']}:contrast={p['contrast']}:saturation={p['saturation']}:gamma={p['gamma']},"
        f"colorbalance=rm={rm}:bm={bm},"
        f"unsharp=5:5:{p['sharpen']}:5:5:0.0"
    )

def run(cmd):
    subprocess.run(cmd, check=True)

# 0) ungraded reference (just upscale)
run(["ffmpeg", "-y", "-i", CLIP, "-vf", "scale=1080:1920:flags=lanczos",
     "-frames:v", "1", "-ss", "2", os.path.join(SP, "opt_original.jpg"), "-loglevel", "error"])

labels = ["ORIGINAL"]
for p in reco["presets"]:
    ch = beauty_chain(p)
    print(f'--- {p["name"].upper()} ---')
    print("  ", ch)
    out_frame = os.path.join(SP, f"opt_{p['name']}.jpg")
    run(["ffmpeg", "-y", "-ss", "2", "-i", CLIP, "-vf", ch, "-frames:v", "1",
         out_frame, "-loglevel", "error"])
    labels.append(p["name"].upper())

# montage: original | natural | balanced | clean, each labelled
frames = ["opt_original.jpg", "opt_natural.jpg", "opt_balanced.jpg", "opt_clean.jpg"]
inputs = []
for f in frames:
    inputs += ["-i", os.path.join(SP, f)]
# scale each to width 360 (keep 9:16 -> 360x640), draw label, hstack
filt = ""
for i, lab in enumerate(labels):
    filt += (f"[{i}:v]scale=360:640,"
             f"drawtext=text='{lab}':x=(w-tw)/2:y=12:fontsize=30:fontcolor=white:"
             f"box=1:boxcolor=black@0.6:boxborderw=8[v{i}];")
filt += "".join(f"[v{i}]" for i in range(4)) + "hstack=inputs=4[out]"
run(["ffmpeg", "-y", *inputs, "-filter_complex", filt, "-map", "[out]",
     os.path.join(SP, "grade_compare.png"), "-loglevel", "error"])
print("COMPARE_SAVED", os.path.join(SP, "grade_compare.png"))
