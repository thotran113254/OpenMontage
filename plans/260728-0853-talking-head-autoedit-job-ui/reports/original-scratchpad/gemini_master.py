# -*- coding: utf-8 -*-
"""ONE-PASS MASTER DIRECTOR.

Gemini watches the raw video (audio + picture) and returns the WHOLE edit
timeline in a single JSON: captions, floating keywords, and explainer cards
with per-bullet timing. Because every timestamp comes from one model watching
one file, there is no cross-tool drift (no separate Whisper step).
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

RAW = r"C:\Users\PC\Downloads\1783586132708_568061814121655968_7874081316310818820.mp4"
f = client.files.upload(file=RAW)
for _ in range(60):
    if client.files.get(name=f.name).state.name == "ACTIVE":
        break
    time.sleep(2)
print("VIDEO ACTIVE", flush=True)

PROMPT = """Bạn là ĐẠO DIỄN DỰNG VIDEO. XEM và NGHE toàn bộ video dọc 9:16 này (một bạn nam Việt nói về "3 sai lầm khi làm chatbot AI bán hàng"). Bạn nghe được lời + thấy được hình, hãy tự lấy MỐC THỜI GIAN từ chính điều bạn nghe.

Trả về DUY NHẤT một JSON gồm 3 mảng: lines, keywords, cards. Toàn bộ mốc thời gian tính bằng GIÂY tính từ đầu video.

=== 1) lines (PHỤ ĐỀ chạy dưới) ===
- Bám sát lời nói, SỬA chính tả chuẩn tiếng Việt (vd "chatbot", "test", "sao nhãng").
- Mỗi dòng 1 CỤM hoàn chỉnh 4-9 chữ, KHÔNG cắt giữa cụm.
- start/end đúng lúc nói. Bao phủ TRỌN video.
- Có thể thêm "highlight" = 1 cụm ngắn trong dòng để tô xanh (từ khoá).
  {"text":"...","start":0.0,"end":0.0,"highlight":"..."}

=== 2) cards (thẻ giải thích nền trắng — DÙNG CHO PHẦN THÂN) ===
- Nội dung có 5 phần: HOOK "3 sai lầm" → Sai lầm 1 → Sai lầm 2 → Sai lầm 3 → Giải pháp.
- Tạo 4 card (mỗi sai lầm + giải pháp). MỖI CARD KÉO DÀI GẦN TRỌN đoạn nói về ý đó (>=5s, có thể 8-14s) — trong lúc card hiện, khung mặt thu nhỏ nằm ở PiP góc dưới-phải, các ý (bullet) LẦN LƯỢT hiện lên trên nền card ĐÚNG LÚC anh ấy nói tới, cho người xem đủ thời gian đọc. KHÔNG cần trả về full mặt giữa các ý.
- Mỗi card: kicker (vd "SAI LẦM ① — QUÁ TẢI"), title (2-4 chữ súc tích), badge ("1".."4"), bullets (1-3 ý RẤT NGẮN <=7 chữ), bulletTimes = mảng GIÂY (mốc tuyệt đối) khi mỗi ý xuất hiện.
  {"inSeconds":0.0,"outSeconds":0.0,"kicker":"...","title":"...","badge":"1","bullets":["...","..."],"bulletTimes":[0.0,0.0]}

=== 3) keywords (CHỮ BAY — chỉ dùng cho HOOK và các khoảng KHÔNG có card) ===
- 1-3 CHỮ IN HOA, là từ nhấn mạnh. TỐI ĐA 3 hiện cùng lúc, mỗi từ 1.2-1.8s.
- TRÁNH MẶT (mặt ở giữa x34-72%, y22-66%). Chỉ đặt ở: dải trên y8-20, lề trái x6-28, lề phải x72-92, dải dưới y60-70.
- KHÔNG đặt keyword trong lúc đang có card.
- màu xoay trong ["#FFFFFF","#FF8A00","#FF2E93","#00E5FF","#8F00FF"]; 2 cái cạnh nhau khác màu.
- rotation -12..12; anim "pop"|"drop"|"slide"|"whip"; fontSize 70-92.
  {"text":"...","color":"#...","xPct":0,"yPct":0,"rotation":0,"inSeconds":0.0,"outSeconds":0.0,"fontSize":80,"anim":"pop"}

Ưu tiên: THÂN video dùng card (mặt ở PiP, ý hiện dần trên nền); keyword chỉ điểm xuyết ở hook và chỗ trống. Chỉ trả JSON, không giải thích."""

CAND = ["models/gemini-3.5-flash", "models/gemini-3-flash-preview", "models/gemini-2.5-flash"]

def parse(t):
    try: return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}"); return json.loads(t[a:b+1])

plan = None; used = None
for m in CAND:
    for _ in range(2):
        try:
            r = client.models.generate_content(model=m, contents=[f, PROMPT],
                config=types.GenerateContentConfig(temperature=0.3, response_mime_type="application/json", max_output_tokens=32768))
            open(os.path.join(SP, "master_raw.txt"), "w", encoding="utf-8").write(r.text or "")
            plan = parse(r.text); used = m; break
        except Exception as ex:
            print("fail", m, repr(ex)[:110], flush=True); time.sleep(6)
    if plan: break
if not plan: raise SystemExit("all failed")

lines = [{"text": l["text"], "startMs": int(l["start"]*1000), "endMs": int(l["end"]*1000),
          **({"highlight": l["highlight"]} if l.get("highlight") else {})} for l in plan.get("lines", [])]
props = {
    "videoSrc": "mona_full_graded.mp4",
    "lines": lines,
    "keywords": plan.get("keywords", []),
    "cards": plan.get("cards", []),
    "brandPill": "3 SAI LẦM • CHATBOT AI",
}
json.dump(props, open(os.path.join(REPO, "remotion-composer", "public", "mona_master_props.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("MODEL", used, "| lines", len(lines), "| keywords", len(plan.get("keywords", [])), "| cards", len(plan.get("cards", [])))
for c in plan.get("cards", []):
    print(f'  CARD [{c["inSeconds"]:.1f}-{c["outSeconds"]:.1f}] {c.get("title")} times={c.get("bulletTimes")}')
