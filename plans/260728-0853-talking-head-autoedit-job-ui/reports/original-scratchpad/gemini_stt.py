# -*- coding: utf-8 -*-
"""Transcribe Vietnamese audio via Gemini into clean, phrase-segmented caption lines.

Gemini corrects spelling that local Whisper got wrong and segments on natural
clause boundaries so no caption pill splits a phrase mid-way.
"""
import json, os, sys, time

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
key = env.get("GEMINI_API_KEY")

from google import genai
from google.genai import types

client = genai.Client(api_key=key)
audio_path = os.path.join(SP, "uaudio.mp3")

MODEL = sys.argv[1] if len(sys.argv) > 1 else "models/gemini-3.1-flash-lite"

up = client.files.upload(file=audio_path)
# wait for ACTIVE
for _ in range(30):
    f = client.files.get(name=up.name)
    if f.state.name == "ACTIVE":
        break
    time.sleep(1)

PROMPT = """Bạn là chuyên gia phụ đề tiếng Việt. Nghe kỹ đoạn audio (một bạn nam nói về việc tạo chatbot AI bán hàng).

Nhiệm vụ: tạo phụ đề dạng caption cho video dọc (kiểu TikTok/Reels), CHIA THEO CỤM NGỮ NGHĨA.

YÊU CẦU:
1. Chép lại ĐÚNG lời nói, SỬA hết lỗi chính tả và từ sai (ví dụ nghe "chất bót" phải ghi "chatbot", "test" đúng là "test", "sao nhãng"...). Dùng tiếng Việt CÓ DẤU chuẩn.
2. Mỗi dòng caption là MỘT CỤM hoàn chỉnh, KHÔNG cắt giữa cụm động từ/danh từ. Độ dài mỗi dòng khoảng 4-8 chữ (tối đa ~9). Ngắt ở ranh giới mệnh đề tự nhiên hoặc chỗ ngừng nghỉ.
3. Cho mốc thời gian start/end (giây, số thực) cho từng dòng, bám sát lời nói.
4. Bỏ từ đệm vô nghĩa nếu làm rối (ạ, à) nhưng giữ nguyên ý.

Trả về DUY NHẤT một JSON array, không giải thích:
[{"text":"...","start":0.37,"end":1.5}, ...]"""

resp = client.models.generate_content(
    model=MODEL,
    contents=[up, PROMPT],
    config=types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json",
    ),
)
raw = resp.text
open(os.path.join(SP, "gemini_lines_raw.txt"), "w", encoding="utf-8").write(raw)
lines = json.loads(raw)
json.dump(lines, open(os.path.join(SP, "gemini_lines.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("MODEL", MODEL)
print("N_LINES", len(lines))
for l in lines:
    print(f"[{l['start']:6.2f}-{l['end']:6.2f}] {l['text']}")
