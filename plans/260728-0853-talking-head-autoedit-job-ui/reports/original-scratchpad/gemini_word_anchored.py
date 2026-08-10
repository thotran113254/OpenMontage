# -*- coding: utf-8 -*-
"""WORD-ANCHORED editor session (the standardized pipeline).

There is ONE timing spine: word-level timestamps. Gemini makes every creative
decision but anchors it to WORD INDICES, never to invented seconds — so timing
is accurate by construction and everything (captions, keywords, cards, cut,
audio) stays locked to the same clock.
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

# ---- the timing spine (accurate word-level times) ----------------------------
words = json.load(open(os.path.join(SP, "user_transcript.json"), encoding="utf-8"))["word_timestamps"]
spine = "\n".join(f'{i}: "{w["word"].strip()}" [{w["start"]:.2f}]' for i, w in enumerate(words))
NW = len(words)

MANIFEST = """BẢN KÊ NĂNG LỰC (chỉ dùng đúng primitive này, KHÔNG bịa):
Renderer Remotion 1080x1920 30fps, component "MonaSample".
- CAPTION pill đen dưới, vào bằng spring pop (scale 0.9→1 overshoot, tự động): text (sửa chính tả), highlight? (1 cụm tô xanh #38BDF8 — chọn cụm ĐINH của câu).
- CARD nền trắng chấm bi cho THÂN: kicker, title(2-4 chữ), badge, bullets(1-3, <=7 chữ), khi hiện thì MẶT thu nhỏ thành PiP góc dưới-phải; các card LIỀN KỀ tự giữ PiP + push-slide chuyển nội dung (không bật về fullscreen giữa 2 card); mỗi bullet hiện dần kèm tiếng pop nhỏ tự động.
- KEYWORD chữ bay to (ở hook/khoảng không có card, tránh mặt): màu trong ["#FFFFFF","#FF8A00","#FF2E93","#00E5FF","#8F00FF"], anim ["pop","drop","slide","whip"], fontSize 70-92, xPct/yPct (mặt ở x34-72 y22-66 → tránh; đặt trên y8-20/trái x6-28/phải x72-92/dưới y60-70).
- PUNCH-IN: zoom nhấn A-roll (scale 1.04-1.08, giữ ~1.1s rồi nhả) tại từ được nhấn giọng — chỉ dùng khi mặt đang fullscreen (ngoài card).
- SFX tự phát: sfx_pop.mp3 (mỗi keyword + mỗi bullet), sfx_whoosh.mp3 (mỗi card). Chỉ 2 file này.
- GRADE ffmpeg (1 preset số thực): brightness(-0.1..0.1),contrast(1..1.15),saturation(1..1.2),gamma(0.9..1.1),warmth(-20..30),skin_smooth(0..0.6),blemish_reduce(0..0.7),sharpen(0..1.2)."""

PROMPT = MANIFEST + f"""

DƯỚI ĐÂY LÀ XƯƠNG SỐNG THỜI GIAN — danh sách {NW} TỪ đã đánh số kèm giây bắt đầu (mốc CHUẨN, bạn KHÔNG cần tự tính giây):
{spine}

NHIỆM VỤ: xem+nghe video, làm editor chuyên nghiệp cho VIDEO NGẮN (mục tiêu số 1: GIỮ CHÂN người xem — mỗi giây phải có lý do để ở lại). MỌI quyết định NEO THEO CHỈ SỐ TỪ ở trên (dùng chỉ số i, KHÔNG ghi giây). Trả về DUY NHẤT JSON:
{{
 "cut_remove": [[wStart,wEnd]],           // xoá các đoạn ê-a/lặp/thừa theo chỉ số từ (bao gồm cả 2 đầu). [] nếu không cắt.
 "grade": {{"brightness":0,"contrast":1,"saturation":1,"gamma":1,"warmth":0,"skin_smooth":0,"blemish_reduce":0,"sharpen":0,"note":"..."}},
 "captions": [{{"w0":0,"w1":5,"text":"chữ đã sửa chính tả","highlight":"cụm tô xanh (tùy)"}}],
 "keywords": [{{"atWord":0,"durSec":1.5,"text":"CHỮ HOA","color":"#...","xPct":0,"yPct":0,"rotation":0,"fontSize":80,"anim":"pop"}}],
 "punch_in": [{{"atWord":0,"scale":1.06,"holdSec":1.1}}],
 "cards": [{{"w0":0,"w1":40,"kicker":"SAI LẦM ① — ...","title":"...","badge":"1","bullets":["..."],"bulletWords":[10,20]}}]
}}
QUY TẮC EDIT GIỮ CHÂN (retention):
- 3 GIÂY ĐẦU là hook sống còn: phải có ít nhất 1 keyword bay + 1 punch-in ngay từ nhấn mạnh đầu tiên.
- NHỊP ĐIỂM NHẤN: cứ mỗi ~8-12 giây nói PHẢI có 1 biến cố thị giác (keyword mới / punch-in / bullet hiện / card vào) — không để đoạn nào "phẳng" quá 12 giây. Ngoài card thì dùng keyword+punch-in; trong card thì bullet đã lo nhịp.
- keyword 5-8 cái cho toàn video (không chỉ hook): chọn đúng TỪ được nhấn giọng, atWord = từ đó, đổi màu/anim/vị trí xen kẽ cho đỡ lặp.
- punch_in 4-8 lần tại từ nhấn giọng mạnh NGOÀI card (ngoài các khoảng card w0..w1); scale 1.04-1.08, đừng liên tiếp <3s.
- highlight caption = cụm mang THÔNG TIN ĐINH (con số, động từ mạnh, từ khoá chủ đề) — không tô cụm vô nghĩa.
QUY TẮC CƠ BẢN:
- caption phủ TRỌN lời nói, mỗi caption 1 cụm (w0..w1) 3-9 từ, không cắt giữa cụm, text sửa chính tả chuẩn (vd từ "chất bót"→"chatbot").
- cut_remove: xoá HẾT ê-a/ừm/lặp từ/câu hỏng — video ngắn không có chỗ cho khoảng chết; nhưng giữ nhịp thở tự nhiên (đừng xoá pause có chủ đích <0.5s).
- BẮT BUỘC 4 card: 3 sai lầm (badge 1/2/3) + 1 GIẢI PHÁP (badge 4). Mỗi card w0..w1 trải gần trọn đoạn nói ý đó; bulletWords = chỉ số từ lúc bắt đầu mỗi ý (bullet + tiếng pop tự rơi đúng lúc ý được nói).
- Nội dung: "3 sai lầm khi làm chatbot AI bán hàng". Chỉ JSON."""

MODEL = "models/gemini-3.5-flash"

def parse(t):
    try: return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}"); return json.loads(t[a:b+1])

def gen(use_thinking):
    cfg = dict(temperature=0.2, response_mime_type="application/json", max_output_tokens=40000)
    if use_thinking:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=16000)
    return client.models.generate_content(model=MODEL, contents=[PROMPT],
                                          config=types.GenerateContentConfig(**cfg))

spec = None; used = None
for use_think in (True, False):
    for attempt in range(6):
        try:
            r = gen(use_think)
            open(os.path.join(SP, "wordspec_raw.txt"), "w", encoding="utf-8").write(r.text or "")
            spec = parse(r.text); used = f"{MODEL} thinking={use_think}"; break
        except Exception as ex:
            print(f"attempt {attempt+1} thinking={use_think}: {repr(ex)[:110]}", flush=True)
            time.sleep(8 + attempt * 4)
    if spec: break
if not spec: raise SystemExit("gemini-3.5-flash unavailable")

json.dump(spec, open(os.path.join(SP, "wordspec.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("MODEL", used)
print("grade:", json.dumps(spec.get("grade", {}), ensure_ascii=False))
print("cut_remove:", spec.get("cut_remove"))
print("captions:", len(spec.get("captions", [])), "keywords:", len(spec.get("keywords", [])), "cards:", len(spec.get("cards", [])))
for c in spec.get("cards", []):
    print(f'  CARD w{c["w0"]}-w{c["w1"]} "{c.get("title")}" bw={c.get("bulletWords")}')
