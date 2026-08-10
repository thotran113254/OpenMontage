# -*- coding: utf-8 -*-
"""Gemini looks at the raw face frames and recommends a natural (not overdone)
color-grade + skin-retouch. Returns 3 intensity presets with concrete numbers
that map onto our ffmpeg beauty chain, so the user can compare and learn."""
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

frames = [os.path.join(SP, "uframes", n) for n in ("u_1.jpg", "u_20.jpg", "u_46.jpg")]
imgs = [client.files.upload(file=p) for p in frames]

PROMPT = """Đây là 3 khung hình từ video selfie quay tay của một bạn nam Việt Nam (da hơi bóng dầu, có mụn nhẹ, ánh sáng đèn trần văn phòng hơi phẳng, quay 720p). Tôi muốn CHỈNH MÀU + LÀM ĐẸP DA cho video talking-head nhìn thu hút, hài hoà, TỰ NHIÊN — KHÔNG bị "fake"/quá lố (không trắng bệch, không mịn như búp bê).

Phân tích và đề xuất chỉ số cụ thể. Trả về DUY NHẤT JSON:
{
  "analysis": {"da":"...","anh_sang":"...","mau_sac":"...","van_de_chinh":"..."},
  "khuyen_nghi": "1-2 câu: nên đi hướng nào, tránh gì để không lố",
  "preset_de_xuat": "natural | balanced | clean",
  "presets": [
    {"name":"natural","mo_ta":"tự nhiên nhất",
     "brightness":0.00,"contrast":1.00,"saturation":1.00,"gamma":1.00,
     "warmth":0,"skin_smooth":0.0,"blemish_reduce":0.0,"sharpen":0.0},
    {"name":"balanced","mo_ta":"cân bằng, khuyên dùng",
     "brightness":0.0,"contrast":1.0,"saturation":1.0,"gamma":1.0,
     "warmth":0,"skin_smooth":0.0,"blemish_reduce":0.0,"sharpen":0.0},
    {"name":"clean","mo_ta":"sạch/đậm hơn nhưng vẫn thật",
     "brightness":0.0,"contrast":1.0,"saturation":1.0,"gamma":1.0,
     "warmth":0,"skin_smooth":0.0,"blemish_reduce":0.0,"sharpen":0.0}
  ]
}
Quy ước chỉ số (điền số thực trong khoảng):
- brightness: -0.1..0.1 (đèn phẳng nên hơi tăng nhẹ)
- contrast: 1.0..1.15
- saturation: 1.0..1.2 (da người Việt đừng quá bão hoà)
- gamma: 0.9..1.1
- warmth: -20..30 (dương = ấm hơn, da hồng hào; đừng quá 25)
- skin_smooth: 0.0..0.8 (0=không, 0.8=mịn nhiều; khuyên <=0.55 để không giả)
- blemish_reduce: 0.0..0.8 (giảm mụn/tì vết; đi kèm smooth)
- sharpen: 0.0..1.2 (lấy lại nét mắt sau khi mịn)
Số phải TĂNG DẦN hợp lý qua 3 preset. Chỉ JSON."""

CAND = ["models/gemini-3.5-flash", "models/gemini-3-flash-preview", "models/gemini-2.5-flash"]

def parse(t):
    try: return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}"); return json.loads(t[a:b+1])

res = None; used = None
for m in CAND:
    for _ in range(2):
        try:
            r = client.models.generate_content(model=m, contents=imgs + [PROMPT],
                config=types.GenerateContentConfig(temperature=0.2, response_mime_type="application/json", max_output_tokens=4096))
            res = parse(r.text); used = m; break
        except Exception as ex:
            print("fail", m, repr(ex)[:90], flush=True); time.sleep(5)
    if res: break
json.dump(res, open(os.path.join(SP, "grade_reco.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("MODEL", used)
print(json.dumps(res, ensure_ascii=False, indent=2))
