# -*- coding: utf-8 -*-
"""ONE-SESSION CAPABILITY-AWARE EDITOR.

Gemini is given the EXACT capability manifest of our renderer (scene types,
animation names, SFX filenames, color-grade parameter ranges, cut model) plus
the raw video (audio + picture), and — in a single high-thinking session —
returns a complete edit spec that maps 1:1 onto what the system can actually
do (no invented APIs): cut EDL + grade preset + full graphics timeline.
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

# ---- CAPABILITY MANIFEST: only these primitives exist. Do not invent. --------
MANIFEST = """BẢN KÊ NĂNG LỰC HỆ THỐNG (chỉ được dùng đúng những gì liệt kê — KHÔNG bịa thêm API/hiệu ứng):

RENDERER: Remotion, khung 1080x1920 dọc, 30fps. Component tên "MonaSample" nhận props sau.

1) CAPTION (props.lines[]) — phụ đề pill đen chạy dưới:
   {text, start(giây), end(giây), highlight?(1 cụm trong text để tô xanh)}
   Pill tự ẩn ~0.45s sau khi nói xong (không cần lo freeze). Bám lời, sửa chính tả, 4-9 chữ/dòng, không cắt cụm.

2) CARD (props.cards[]) — thẻ giải thích nền trắng chấm bi, DÙNG CHO THÂN video:
   {inSeconds, outSeconds, kicker, title(2-4 chữ), badge("1".."4"), bullets[1..3, mỗi ý <=7 chữ], bulletTimes[giây tuyệt đối cho từng ý]}
   Khi card hiện: khung MẶT tự thu nhỏ thành PiP bo góc dưới-phải và GIỮ NGUYÊN ở đó suốt card; các bullet hiện DẦN đúng bulletTimes (đồng bộ lời), animate trên nền card. Card nên kéo dài gần trọn đoạn nói về ý đó.
   BẮT BUỘC tạo ĐỦ 4 CARD: badge 1/2/3 cho 3 sai lầm + badge 4 cho phần GIẢI PHÁP ở cuối. kicker dạng "SAI LẦM ① — <nhãn ngắn>" (①②③) và "GIẢI PHÁP — <nhãn ngắn>" cho card 4.

3) KEYWORD (props.keywords[]) — chữ bay to, CHỈ dùng ở HOOK và khoảng KHÔNG có card:
   {text(1-3 CHỮ HOA), color(chỉ trong ["#FFFFFF","#FF8A00","#FF2E93","#00E5FF","#8F00FF"]), xPct, yPct, rotation(-12..12), inSeconds, outSeconds, fontSize(70..92), anim(CHỈ 1 trong "pop","drop","slide","whip")}
   Tránh mặt (mặt ở x34-72%,y22-66%). Chỉ đặt: trên y8-20 / trái x6-28 / phải x72-92 / dưới y60-70. Tối đa 3 cùng lúc, mỗi từ 1.2-1.8s. KHÔNG đặt trong lúc có card.

4) SFX có sẵn (đúng TÊN FILE, hệ thống TỰ phát — bạn không cần lịch riêng): "sfx_pop.mp3" (tự kêu mỗi khi 1 keyword hiện), "sfx_whoosh.mp3" (tự kêu mỗi khi 1 card vào). Chỉ có 2 file này, đừng gọi tên SFX khác.

5) LUÔN BẬT: brandPill ở hook + vệt cầu vồng góc dưới-trái (tự có).

6) GRADE/LÀM ĐẸP (áp bằng ffmpeg, trả 1 preset số thực trong dải):
   brightness(-0.1..0.1), contrast(1.0..1.15), saturation(1.0..1.2), gamma(0.9..1.1),
   warmth(-20..30, dương=ấm hồng), skin_smooth(0..0.6, >0.55 sẽ giả), blemish_reduce(0..0.7), sharpen(0..1.2).
   Mục tiêu: da sạch/ấm/tự nhiên, KHÔNG "mặt sáp".

7) CẮT (jump-cut): remove[] các đoạn im lặng/ê-a/lặp. Cắt tại CHỖ NGƯNG bạn NGHE được (ranh giới an toàn, không cắt giữa chữ)."""

PROMPT = MANIFEST + """

⏱️ QUY TẮC MỐC THỜI GIAN — QUAN TRỌNG NHẤT (sai mốc là hỏng cả video):
- Video dài đúng 93.4 giây, 30fps. NGHE KỸ audio, lấy mốc theo ĐÚNG thời điểm phát âm bạn nghe được.
- ĐỘ CHÍNH XÁC tới 0.1s. TUYỆT ĐỐI KHÔNG làm tròn về số nguyên giây (KHÔNG được 5.0, 8.0, 11.0, 18.0...). Phải là số thực tế lẻ, ví dụ 5.62, 8.14, 17.83.
- Mốc TĂNG DẦN, khớp lời: start của 1 caption = lúc BẮT ĐẦU nói từ đầu tiên của cụm; end = lúc DỨT từ cuối.
- keyword.inSeconds = ĐÚNG giây từ-khoá đó được nói ra. card.inSeconds và mỗi bulletTimes = ĐÚNG giây bắt đầu nói ý đó (nghe kỹ để canh).
- Tự kiểm: nếu thấy nhiều mốc là số nguyên tròn trịa → bạn đang ước lượng ẩu, HÃY NGHE LẠI và chỉnh cho khớp thực tế.

NHIỆM VỤ (làm như 1 editor chuyên nghiệp trong 1 phiên): XEM+NGHE trọn video, rồi trả về DUY NHẤT JSON:
{
 "cut": {"remove": [{"start":0.0,"end":0.0,"reason":"..."}]},
 "grade": {"brightness":0.0,"contrast":1.0,"saturation":1.0,"gamma":1.0,"warmth":0,"skin_smooth":0.0,"blemish_reduce":0.0,"sharpen":0.0,"note":"vì sao chọn vậy"},
 "timeline": {
   "lines": [{"text":"...","start":0.0,"end":0.0,"highlight":"..."}],
   "keywords": [{"text":"...","color":"#...","xPct":0,"yPct":0,"rotation":0,"inSeconds":0.0,"outSeconds":0.0,"fontSize":80,"anim":"pop"}],
   "cards": [{"inSeconds":0.0,"outSeconds":0.0,"kicker":"...","title":"...","badge":"1","bullets":["..."],"bulletTimes":[0.0]}]
 }
}
Mốc thời gian của timeline tính trên video GỐC (chưa cắt). Thân video ưu tiên CARD (mặt ở PiP, ý hiện dần); keyword chỉ điểm xuyết hook. Bám sát nội dung "3 sai lầm khi làm chatbot AI bán hàng". Chỉ JSON."""

MODEL = "models/gemini-3.5-flash"   # only the latest 3.5 flash, no 2.5 fallback

def parse(t):
    try: return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}"); return json.loads(t[a:b+1])

def gen(use_thinking):
    cfg = dict(temperature=0.3, response_mime_type="application/json", max_output_tokens=40000)
    if use_thinking:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=16000)
    return client.models.generate_content(model=MODEL, contents=[f, PROMPT],
                                          config=types.GenerateContentConfig(**cfg))

spec = None; used = None
# Prefer thinking ON; retry hard through 503s. Only drop thinking if it never
# succeeds after many tries — but always stay on gemini-3.5-flash.
for use_think in (True, False):
    for attempt in range(6):
        try:
            r = gen(use_think)
            open(os.path.join(SP, "editor_raw.txt"), "w", encoding="utf-8").write(r.text or "")
            spec = parse(r.text); used = f"{MODEL} thinking={use_think}"; break
        except Exception as ex:
            msg = repr(ex)[:120]
            print(f"attempt {attempt+1} thinking={use_think} fail: {msg}", flush=True)
            time.sleep(8 + attempt * 4)   # backoff for 503 spikes
    if spec: break
if not spec: raise SystemExit("gemini-3.5-flash unavailable after retries")

json.dump(spec, open(os.path.join(SP, "editor_spec.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
tl = spec.get("timeline", {})
print("MODEL", used)
print("grade:", json.dumps(spec.get("grade", {}), ensure_ascii=False))
print("cut removes:", len(spec.get("cut", {}).get("remove", [])),
      "| lines:", len(tl.get("lines", [])), "| keywords:", len(tl.get("keywords", [])), "| cards:", len(tl.get("cards", [])))
for c in tl.get("cards", []):
    print(f'  CARD [{c["inSeconds"]:.1f}-{c["outSeconds"]:.1f}] {c.get("title")}')
