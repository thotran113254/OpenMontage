# -*- coding: utf-8 -*-
"""Stage 2a — SAFE JUMP-CUT EDL.

Gemini (content editor) marks spans to remove (filler, false starts, repeated
words, long dead air). Then LOCAL logic snaps every cut boundary to a word
boundary using Whisper word timestamps, so a cut NEVER lands inside a word —
no lost words, no broken meaning. Produces a review-ready plan; does NOT apply.
"""
import os, time, json

def load_env(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return out

REPO = r"D:\CODE WITH AI\VIDEO-EDITOR-AI-AGENT"
SP = r"C:\Users\PC\AppData\Local\Temp\claude\D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT\2e50444e-ceec-4f33-83b7-e7ab1620d735\scratchpad"
env = load_env(os.path.join(REPO, ".env"))
from google import genai
from google.genai import types
client = genai.Client(api_key=env.get("GEMINI_API_KEY"))

# --- Whisper word timestamps = the precise timing ruler -----------------------
wt = json.load(open(os.path.join(SP, "user_transcript.json"), encoding="utf-8"))["word_timestamps"]
words = [{"i": i, "w": w["word"].strip(), "s": round(w["start"], 2), "e": round(w["end"], 2)}
         for i, w in enumerate(wt)]
DUR = words[-1]["e"]

# local silence gaps (>0.30s between consecutive words)
gaps = []
for a, b in zip(words, words[1:]):
    g = round(b["s"] - a["e"], 2)
    if g >= 0.30:
        gaps.append({"after_word": a["w"], "from": a["e"], "to": b["s"], "gap": g})

word_listing = "\n".join(f'{w["i"]:3d} [{w["s"]:6.2f}-{w["e"]:6.2f}] {w["w"]}' for w in words)
gap_listing = "\n".join(f'gap {g["gap"]:.2f}s @ {g["from"]:.2f}-{g["to"]:.2f} (sau "{g["after_word"]}")' for g in gaps)

PROMPT = f"""Bạn là biên tập viên dựng phim talking-head. Đây là transcript với timestamp TỪNG CHỮ (giây) của video thô 93s (một bạn nói về 3 sai lầm khi làm chatbot AI).

WORD-LEVEL:
{word_listing}

KHOẢNG LẶNG phát hiện được:
{gap_listing}

NHIỆM VỤ: chọn các ĐOẠN CẦN BỎ để video dồn nhịp, chuyên nghiệp, NHƯNG GIỮ NGUYÊN Ý. Ưu tiên bỏ:
- khoảng lặng/ngập ngừng dài
- từ đệm vô nghĩa lặp ("à", "ừ", "thì thì", "cái cái", "nó nó")
- câu nói vấp/lặp lại nguyên cụm (giữ bản rõ nhất)
- đoạn lan man trùng ý

TUYỆT ĐỐI KHÔNG bỏ từ mang nghĩa cần cho câu. Thà bỏ ít mà an toàn.

Trả về DUY NHẤT JSON:
{{"remove": [{{"start": 0.00, "end": 0.00, "reason": "vì sao bỏ", "text": "chữ bị bỏ"}}]}}
start/end lấy theo timestamp ở trên. Chỉ JSON."""

CAND = ["models/gemini-3.5-flash", "models/gemini-3-flash-preview", "models/gemini-2.5-flash"]

def parse(t):
    try: return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        return json.loads(t[a:b+1])

plan = None; used = None
for m in CAND:
    for _ in range(2):
        try:
            r = client.models.generate_content(model=m, contents=[PROMPT],
                config=types.GenerateContentConfig(temperature=0.2, response_mime_type="application/json", max_output_tokens=8192))
            plan = parse(r.text); used = m; break
        except Exception as ex:
            print("fail", m, repr(ex)[:90], flush=True); time.sleep(5)
    if plan: break

# --- SAFE SNAP: never cut inside a word --------------------------------------
def snap_left(a):
    for w in words:
        if w["s"] <= a <= w["e"]:
            return w["e"]      # a is inside a word -> keep that word
    return a
def snap_right(b):
    for w in words:
        if w["s"] <= b <= w["e"]:
            return w["s"]      # keep the word
    return b

removes = []
for r in plan.get("remove", []):
    a, b = snap_left(float(r["start"])), snap_right(float(r["end"]))
    if b - a > 0.12:
        removes.append({"start": round(a, 2), "end": round(b, 2),
                        "dur": round(b - a, 2), "reason": r.get("reason", ""), "text": r.get("text", "")})
removes.sort(key=lambda x: x["start"])
# merge overlaps
merged = []
for r in removes:
    if merged and r["start"] <= merged[-1]["end"] + 0.05:
        merged[-1]["end"] = max(merged[-1]["end"], r["end"])
        merged[-1]["dur"] = round(merged[-1]["end"] - merged[-1]["start"], 2)
    else:
        merged.append(r)

total_removed = round(sum(r["dur"] for r in merged), 2)
kept = round(DUR - total_removed, 2)
out = {"source_duration": DUR, "removed_total": total_removed, "new_duration": kept,
       "num_cuts": len(merged), "removes": merged}
json.dump(out, open(os.path.join(SP, "cut_plan.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("MODEL", used)
print(f"Gốc {DUR:.1f}s -> mới {kept:.1f}s  (bỏ {total_removed:.1f}s / {len(merged)} cắt)")
for r in merged:
    print(f'  BỎ [{r["start"]:6.2f}-{r["end"]:6.2f}] {r["dur"]:.2f}s | {r["reason"]} | "{r["text"]}"')
