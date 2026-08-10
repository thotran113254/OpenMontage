# -*- coding: utf-8 -*-
"""Gemini as MOTION-GRAPHICS DIRECTOR.

Reads the (clean, timestamped) transcript and produces a full animation
timeline that drives the MONA Remotion component: which keywords pop where,
their color/rotation/animation/timing (face-safe), and where explainer cards
trigger. This is the "prompt Gemini to design effective frames" step.
"""
import os, time, json

def load_env(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #")[0].split("\t#")[0].strip().strip('"').strip("'")
        out[k.strip()] = v
    return out

REPO = r"D:\CODE WITH AI\VIDEO-EDITOR-AI-AGENT"
SP = r"C:\Users\PC\AppData\Local\Temp\claude\D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT\2e50444e-ceec-4f33-83b7-e7ab1620d735\scratchpad"
env = load_env(os.path.join(REPO, ".env"))
from google import genai
from google.genai import types
client = genai.Client(api_key=env.get("GEMINI_API_KEY"))

lines = json.load(open(os.path.join(SP, "gemini_lines.json"), encoding="utf-8"))
transcript = "\n".join(f'[{l["start"]:.2f}-{l["end"]:.2f}] {l["text"]}' for l in lines)

CANDIDATES = ["models/gemini-3.5-flash", "models/gemini-3-flash-preview",
              "models/gemini-2.5-flash", "models/gemini-flash-latest"]

PROMPT = f"""Bạn là MOTION-GRAPHICS DIRECTOR cho video talking-head dọc 9:16 (1080x1920), phong cách MONA. Người nói ngồi giữa khung, MẶT ở vùng trung tâm.

Đây là transcript (giây, đã chuẩn chính tả), mỗi dòng là 1 câu caption đã có sẵn:
{transcript}

NHIỆM VỤ: thiết kế TIMELINE ĐỒ HOẠ hiệu quả, sync theo lời, trả JSON gồm 2 mảng: "keywords" và "cards".

=== LUẬT ĐẶT KEYWORD (chữ bay) — để KHUNG FRAME HIỆU QUẢ ===
1. Mỗi keyword = 1-3 CHỮ IN HOA, là từ NHẤN MẠNH/quan trọng nhất trong câu (danh từ, con số, từ khoá "đắt"). KHÔNG lấy nguyên câu.
2. TRÁNH MẶT: mặt ở giữa (x 34-72%, y 22-66%). CHỈ đặt keyword ở "safe zone":
   - dải trên: y 8-20%
   - lề trái: x 6-28%, y 30-64%
   - lề phải: x 72-92%, y 26-60%
   - dải dưới (trên caption): y 60-70%
3. TỐI ĐA 3 keyword hiện cùng lúc. Chúng TÍCH LUỸ rồi XOÁ HẾT khi sang ý mới (đừng để quá 3).
4. Thời lượng mỗi keyword: 1.2-1.8s. inSeconds = đúng lúc phát âm từ đó. Lệch nhau >=0.3s để pop lần lượt.
5. Màu xoay vòng trong: ["#FFFFFF","#FF8A00","#FF2E93","#00E5FF","#8F00FF"]. 2 keyword cạnh nhau KHÁC màu.
6. rotation: số nguyên từ -12 đến 12 (đa số 0, vài cái nghiêng nhẹ cho sinh động).
7. anim: chọn 1 trong "pop" | "drop" | "slide" | "whip" (đa số "pop"; xen kẽ để đỡ đơn điệu).
8. fontSize: 70-92 (từ càng quan trọng càng to).
9. KHÔNG đặt keyword trong lúc đang có card (xem cards) — vì card che toàn màn hình.

=== LUẬT CARD (giải thích full-screen nền trắng) ===
- Nội dung này có cấu trúc: HOOK "3 sai lầm" → Sai lầm 1 → Sai lầm 2 → Sai lầm 3 → Giải pháp.
- Tạo 4 card: mỗi sai lầm 1 card + 1 card giải pháp. Đặt inSeconds/outSeconds vào ĐÚNG đoạn nói về ý đó (dài 3.5-5s, đừng chồng lên nhau).
- Mỗi card: kicker (VD "SAI LẦM ① — NÓI DỄ HIỂU"), title (2-4 chữ, súc tích), badge ("1".."4"), bullets (1-2 gạch đầu dòng RẤT NGẮN, mỗi cái <=7 chữ).

Trả về DUY NHẤT JSON:
{{
  "keywords": [{{"text":"...","color":"#...","xPct":0,"yPct":0,"rotation":0,"inSeconds":0.0,"outSeconds":0.0,"fontSize":80,"anim":"pop"}}],
  "cards": [{{"inSeconds":0.0,"outSeconds":0.0,"kicker":"...","title":"...","badge":"1","bullets":["...","..."]}}]
}}
Chỉ JSON. Bám sát mốc thời gian transcript. Ưu tiên ÍT mà ĐÚNG hơn là nhiều mà rối."""

def parse_json(txt):
    try:
        return json.loads(txt)
    except Exception:
        a, b = txt.find("{"), txt.rfind("}")
        if a >= 0 and b > a:
            return json.loads(txt[a:b + 1])
        raise

resp = None; used = None; plan = None
for m in CANDIDATES:
    for attempt in range(2):
        try:
            resp = client.models.generate_content(
                model=m, contents=[PROMPT],
                config=types.GenerateContentConfig(
                    temperature=0.35, response_mime_type="application/json",
                    max_output_tokens=16384))
            open(os.path.join(SP, "director_raw.txt"), "w", encoding="utf-8").write(resp.text or "")
            plan = parse_json(resp.text)
            used = m; break
        except Exception as ex:
            print(f"try {m} #{attempt+1} fail: {repr(ex)[:120]}", flush=True); resp = None; time.sleep(5)
    if plan: break
if not plan: raise SystemExit("all failed")
# assemble full props: reuse existing lines as caption lines
caption_lines = [{"text": l["text"], "startMs": int(l["start"]*1000), "endMs": int(l["end"]*1000)} for l in lines]
# light accents on caption
for cl in caption_lines:
    for kw in ["chatbot AI", "sai lầm", "test", "quy trình"]:
        if kw in cl["text"]:
            cl["highlight"] = kw
            break
props = {
    "videoSrc": "mona_full_src.mp4",
    "lines": caption_lines,
    "keywords": plan.get("keywords", []),
    "cards": plan.get("cards", []),
    "brandPill": "3 SAI LẦM • CHATBOT AI",
}
json.dump(props, open(os.path.join(REPO, "remotion-composer", "public", "mona_full_props.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("MODEL", used)
print("KEYWORDS", len(plan.get("keywords", [])), "CARDS", len(plan.get("cards", [])))
print(json.dumps(plan, ensure_ascii=False, indent=2)[:3000])
