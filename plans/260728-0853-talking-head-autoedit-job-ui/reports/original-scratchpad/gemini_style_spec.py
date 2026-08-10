# -*- coding: utf-8 -*-
"""Ask Gemini to WATCH the MONA reference and extract a precise, reusable
style + animation spec — with special attention to the floating kinetic
keyword text that pops around the speaker. Output structured JSON."""
import os, sys, time, json

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
env = load_env(os.path.join(REPO, ".env"))
from google import genai
from google.genai import types
client = genai.Client(api_key=env.get("GEMINI_API_KEY"))

REF = r"C:\Users\PC\Downloads\FDown.vn_Tai_video_Facebook_1080p_db80.mp4"
CANDIDATES = ["models/gemini-3.5-flash", "models/gemini-3-flash-preview",
              "models/gemini-2.5-flash", "models/gemini-flash-latest"]

f = client.files.upload(file=REF)
for _ in range(60):
    g = client.files.get(name=f.name)
    if g.state.name == "ACTIVE":
        break
    time.sleep(2)
print("REF ACTIVE", flush=True)

PROMPT = """Bạn là motion-graphics director. XEM KỸ video này (talking-head tiếng Việt, dọc 9:16, phong cách MONA). Tôi cần TÁI TẠO CHÍNH XÁC phong cách + animation bằng code Remotion.

Tập trung nhất vào LỚP CHỮ KEYWORD BAY quanh người nói (kinetic typography): những từ in đậm cỡ lớn pop lên rải rác quanh mặt (ví dụ "VIDEO NGẮN", "SHORT", "MẠNG XÃ HỘI", "TỚI MỨC NÀO?"), nhiều màu, có từ xoay nghiêng.

Trả về DUY NHẤT một JSON đúng cấu trúc sau (điền quan sát thực tế, không bịa; nếu không chắc để "?"):
{
  "floating_keywords": {
    "present": true/false,
    "examples": [{"text":"...","color_hex":"?","approx_position":"top-right/left/center...","rotation_deg":0,"appears_around_second":0.0}],
    "entrance_animation": "pop/scale-in/slide/spring...",
    "exit_animation": "...",
    "typical_duration_seconds": 0.0,
    "font_style": "bold/heavy, uppercase?",
    "how_many_on_screen_at_once": "1-3",
    "synced_to": "từ người nói đang nhấn mạnh?"
  },
  "bottom_caption": {"style":"pill/plain","bg":"?","text_color":"?","weight":"?","position":"?","word_highlight":"có/không, màu?"},
  "explainer_cards": {"background":"trắng/tối + hex","pattern":"chấm/lưới?","title_style":"gradient? hex?","badge":"số tròn?","bullets":"icon?","entrance":"?","how_speaker_shown":"PiP? scale-morph từ A-roll?"},
  "section_pills": {"style":"gradient? hex","shape":"bo tròn","content":"[số | tên]?"},
  "transitions_aroll_to_card": "loại + mô tả chuyển động",
  "brand_marks": {"rainbow_corner":"có/không, vị trí","logo":"?"},
  "color_palette_hex": ["#..."],
  "overall_motion_feel": "1-2 câu",
  "top_3_things_that_define_this_style": ["...","...","..."]
}
Chỉ trả JSON, không giải thích."""

resp = None; used = None
for m in CANDIDATES:
    for attempt in range(2):
        try:
            resp = client.models.generate_content(
                model=m, contents=[f, PROMPT],
                config=types.GenerateContentConfig(temperature=0.15, response_mime_type="application/json"))
            used = m; break
        except Exception as ex:
            print(f"try {m} #{attempt+1} fail: {repr(ex)[:100]}", flush=True); time.sleep(6)
    if resp: break
if not resp:
    raise SystemExit("all failed")
spec = json.loads(resp.text)
out = os.path.join(REPO, "remotion-composer", "public", "mona_style_spec.json")
json.dump(spec, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("MODEL", used)
print(json.dumps(spec, ensure_ascii=False, indent=2))
