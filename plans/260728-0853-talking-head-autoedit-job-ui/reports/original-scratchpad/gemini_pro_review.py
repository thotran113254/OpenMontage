# -*- coding: utf-8 -*-
"""Professional edit review: Gemini 3.5-flash watches BOTH the MONA reference
video and our current render, extracts the reference's editing techniques in
detail, critiques our render against it, and returns actionable improvements
mapped to our real Remotion primitives (no invented APIs).
"""
import os, time, json, sys

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
SP = os.path.dirname(os.path.abspath(__file__))
REF = r"C:\Users\PC\Downloads\FDown.vn_Tai_video_Facebook_1080p_db80.mp4"
OURS = os.path.join(REPO, "remotion-composer", "out", "mona_TIMELINE_v2_540.mp4")

env = load_env(os.path.join(REPO, ".env"))
from google import genai
from google.genai import types
client = genai.Client(api_key=env.get("GEMINI_API_KEY"))

def upload_wait(path, label):
    f = client.files.upload(file=path)
    print(f"uploaded {label}: {f.name}", flush=True)
    while f.state and f.state.name == "PROCESSING":
        time.sleep(5)
        f = client.files.get(name=f.name)
    if f.state and f.state.name != "ACTIVE":
        raise RuntimeError(f"{label} file state {f.state.name}")
    print(f"{label} ACTIVE", flush=True)
    return f

ref_f = upload_wait(REF, "reference")
ours_f = upload_wait(OURS, "ours")

MANIFEST = """NĂNG LỰC RENDERER CỦA CHÚNG TÔI (Remotion, chỉ những primitive này chỉnh được):
- PiP morph: A-roll thu nhỏ vào góc dưới-phải khi card hiện; tham số chỉnh được: TRANSITION_S (thời lượng morph, giây), có thể giữ PiP xuyên suốt các card liền kề, vị trí/kích thước hộp PiP.
- CARD trắng chấm bi: kicker pill, badge tròn, title gradient, bullets hiện dần (bulletTimes); có thể thêm hiệu ứng chuyển giữa 2 card liền kề (slide/fade).
- CAPTION pill đen: từng cụm, highlight xanh; chỉnh được font-size, vị trí, kiểu vào/ra (spring translateY), có thể thêm scale-pop.
- KEYWORD bay: anim pop/drop/slide/whip, spring damping, màu, xoay, float; số lượng tuỳ ý.
- SFX: sfx_pop.mp3 (keyword), sfx_whoosh.mp3 (card) — chỉ 2 file, chỉnh được volume + offset.
- A-roll: có thể thêm PUNCH-IN ZOOM nhẹ (scale 1.0->1.06) tại các mốc nhấn mạnh (theo giây).
- GRADE ffmpeg đã áp: warmth/skin_smooth/... (đã ổn, không cần bàn thêm trừ khi lệch rõ).
KHÔNG THỂ: thêm nhạc nền (user từ chối), motion graphics phức tạp ngoài các layer trên, đổi nội dung lời nói."""

PROMPT = MANIFEST + """

VIDEO 1 = video THAM CHIẾU (kênh MONA, phong cách chuẩn cần học).
VIDEO 2 = video CHÚNG TÔI VỪA DỰNG theo phong cách đó.

Bạn là một video editor chuyên nghiệp. Hãy XEM KỸ CẢ HAI VIDEO rồi trả về DUY NHẤT JSON:
{
 "reference_techniques": [
   {"name":"...", "detail":"mô tả kỹ thuật edit trong video tham chiếu: transition giữa các đoạn, cách chữ xuất hiện/biến mất, nhịp cắt, punch-in zoom, sound effect rơi vào lúc nào, caption hành xử ra sao...", "timestamps":"ví dụ phút:giây trong video 1"}
 ],
 "our_video_issues": [
   {"issue":"vấn đề cụ thể nhìn thấy trong video 2 (ví dụ PiP phóng to thu nhỏ liên tục, chữ vào thô, thiếu nhấn nhá...)", "timestamps":"phút:giây trong video 2", "severity":"high|medium|low"}
 ],
 "improvements": [
   {"target":"pip|card_transition|caption|keyword|sfx|punch_in|pacing",
    "action":"việc cần làm, CHỈ dùng các primitive trong bản kê",
    "params":{"vi_du":"giá trị số cụ thể: TRANSITION_S=0.5, scale=1.06, damping=12..."},
    "why":"lý do dựa trên video tham chiếu"}
 ],
 "punch_in_moments": [{"atSecondsInVideo2":0.0,"reason":"..."}],
 "overall_score":"x/10 so với tham chiếu + 1 câu nhận xét"
}
Chú ý đặc biệt: trong video 2, quan sát các thời điểm A-roll (mặt người nói) thu nhỏ vào góc rồi phóng to lại — nếu việc phóng to/thu nhỏ xảy ra quá nhanh liên tiếp thì ghi rõ timestamps. So sánh với cách video 1 xử lý các đoạn chuyển giữa các phần nội dung. Chỉ JSON, tiếng Việt."""

MODEL = "models/gemini-3.5-flash"

def parse(t):
    try: return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        return json.loads(t[a:b + 1])

spec = None
for use_think in (True, False):
    for attempt in range(6):
        try:
            cfg = dict(temperature=0.3, response_mime_type="application/json", max_output_tokens=40000)
            if use_think:
                cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=16000)
            r = client.models.generate_content(
                model=MODEL,
                contents=[ref_f, ours_f, PROMPT],
                config=types.GenerateContentConfig(**cfg))
            open(os.path.join(SP, "pro_review_raw.txt"), "w", encoding="utf-8").write(r.text or "")
            spec = parse(r.text)
            print(f"OK thinking={use_think}", flush=True)
            break
        except Exception as ex:
            msg = repr(ex)[:110]
            print(f"attempt {attempt+1} thinking={use_think}: {msg}", flush=True)
            time.sleep(45 if "429" in msg else 10 + attempt * 5)
    if spec:
        break
if not spec:
    sys.exit("gemini unavailable")

json.dump(spec, open(os.path.join(SP, "pro_review.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("=== REFERENCE TECHNIQUES ===")
for t in spec.get("reference_techniques", []):
    print(f'- {t.get("name")}: {t.get("detail")} [{t.get("timestamps")}]')
print("=== OUR ISSUES ===")
for t in spec.get("our_video_issues", []):
    print(f'- [{t.get("severity")}] {t.get("issue")} [{t.get("timestamps")}]')
print("=== IMPROVEMENTS ===")
for t in spec.get("improvements", []):
    print(f'- ({t.get("target")}) {t.get("action")} | params={json.dumps(t.get("params",{}),ensure_ascii=False)} | {t.get("why")}')
print("=== PUNCH-IN ===", json.dumps(spec.get("punch_in_moments", []), ensure_ascii=False))
print("=== SCORE ===", spec.get("overall_score"))
