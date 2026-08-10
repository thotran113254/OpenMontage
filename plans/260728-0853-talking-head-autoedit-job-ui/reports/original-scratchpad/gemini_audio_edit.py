# -*- coding: utf-8 -*-
"""Second Gemini pass over the MONA reference — the EDITOR pass. Focus on what
frames can't show: sound design (SFX, music bed), cut rhythm, punch-in zooms,
speed ramps, b-roll, color grade. Output structured JSON."""
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
env = load_env(os.path.join(REPO, ".env"))
from google import genai
from google.genai import types
client = genai.Client(api_key=env.get("GEMINI_API_KEY"))
REF = r"C:\Users\PC\Downloads\FDown.vn_Tai_video_Facebook_1080p_db80.mp4"
CANDIDATES = ["models/gemini-3.5-flash", "models/gemini-3-flash-preview",
              "models/gemini-2.5-flash", "models/gemini-flash-latest"]

f = client.files.upload(file=REF)
for _ in range(60):
    if client.files.get(name=f.name).state.name == "ACTIVE":
        break
    time.sleep(2)
print("REF ACTIVE", flush=True)

PROMPT = """Bạn là senior video editor. NGHE và XEM kỹ video MONA này (talking-head 9:16). Tôi cần tái tạo cả phần EDIT chuyên nghiệp mà nhìn frame tĩnh không thấy được. Tập trung ÂM THANH và KỸ THUẬT CẮT.

Trả về DUY NHẤT JSON:
{
  "sound_effects": {
    "present": true/false,
    "types_heard": ["whoosh chuyển cảnh","pop khi chữ hiện","riser","ding","swoosh"],
    "on_keyword_popup": "có SFX pop mỗi khi chữ keyword hiện? mô tả",
    "on_card_transition": "SFX gì khi vào/ra card?",
    "notes": "mô tả cụ thể các tiếng nghe được và thời điểm"
  },
  "music_bed": {"present": true/false, "style": "lo-fi/upbeat/none", "volume_vs_voice": "nhỏ/vừa", "notes": "?"},
  "voice_processing": {"noise_reduction": "?","eq_bright": "?","de_ess": "?","notes":"?"},
  "cut_style": {
    "jump_cuts": "có bỏ khoảng lặng (jump cut) không? tần suất",
    "cut_rhythm": "nhanh/vừa/chậm",
    "avg_shot_seconds": 0.0,
    "silence_removed": true/false
  },
  "camera_moves_post": {
    "punch_in_zoom": "có zoom nhẹ vào mặt lúc nhấn mạnh không?",
    "shake_or_pop": "có hiệu ứng giật/pop scale lúc nhấn không?",
    "speed_ramp": "có tua nhanh/chậm đoạn nào không?"
  },
  "broll": {"present": true/false, "examples": ["màn hình code","laptop"], "how_inserted": "full-screen? khung nhỏ?"},
  "color_grade": {"look": "ấm/lạnh/tương phản cao","notes":"?"},
  "pacing_timeline": [{"section":"hook","approx_start":0.0,"what_happens":"..."}],
  "editor_checklist_to_match": ["việc 1 cần làm để giống","việc 2","..."],
  "top_5_edit_details_easy_to_miss": ["...","...","...","...","..."]
}
Chỉ JSON, quan sát thực tế, không bịa; không chắc để "?"."""

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
if not resp: raise SystemExit("all failed")
spec = json.loads(resp.text)
json.dump(spec, open(os.path.join(REPO, "remotion-composer", "public", "mona_edit_spec.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("MODEL", used)
print(json.dumps(spec, ensure_ascii=False, indent=2))
