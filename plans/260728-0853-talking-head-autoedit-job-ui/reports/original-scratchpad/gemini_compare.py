# -*- coding: utf-8 -*-
"""Feed the MONA reference and our rendered sample to Gemini 3.5 flash and ask
for a concrete, actionable diff: what to change in our sample to match the
reference's motion-graphics format."""
import os, sys, time

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
key = env.get("GEMINI_API_KEY")

from google import genai
client = genai.Client(api_key=key)

REF = r"C:\Users\PC\Downloads\FDown.vn_Tai_video_Facebook_1080p_db80.mp4"
SAMPLE = os.path.join(REPO, "remotion-composer", "out", "mona_sample.mp4")
MODEL = sys.argv[1] if len(sys.argv) > 1 else "models/gemini-3.5-flash"

def up_wait(path, label):
    f = client.files.upload(file=path)
    for _ in range(60):
        g = client.files.get(name=f.name)
        if g.state.name == "ACTIVE":
            print(label, "ACTIVE", flush=True)
            return g
        if g.state.name == "FAILED":
            raise RuntimeError(label + " upload FAILED")
        time.sleep(2)
    raise RuntimeError(label + " upload timeout")

ref = up_wait(REF, "REF")
smp = up_wait(SAMPLE, "SAMPLE")

PROMPT = """Bạn là art director cho video dạng talking-head + motion graphics (kiểu MONA, dọc 9:16).

- VIDEO 1 = MẪU CHUẨN (format tôi muốn đạt).
- VIDEO 2 = BẢN THỬ của tôi (đang chưa giống mẫu).

Hãy XEM KỸ cả hai và so sánh FORMAT DỰNG (không quan tâm nội dung khác nhau). Chỉ cho tôi CỤ THỂ phải sửa gì ở VIDEO 2 để giống VIDEO 1. Đi vào chi tiết kỹ thuật để tôi code lại (Remotion):

1. CAPTION/PHỤ ĐỀ: kiểu chữ, cỡ, màu, nền pill, vị trí, cách highlight, cách chạy chữ. Mẫu khác bản thử ở điểm nào?
2. CARD ĐỒ HOẠ (full-screen): bố cục, nền (màu/hoạ tiết), tiêu đề (font/gradient/size), badge số, bullet, animation vào-ra. Sai khác gì?
3. PiP (camera nhỏ trong card): kích thước, vị trí, bo góc, có/không.
4. NHÃN/PILL SECTION: kiểu, màu, vị trí.
5. TRANSITION giữa A-roll và card: loại chuyển cảnh, tốc độ.
6. NHỊP/TIMING: mẫu chèn card khi nào, giữ bao lâu, tần suất.
7. MÀU SẮC & THƯƠNG HIỆU: bảng màu gradient chính xác (đoán mã hex), vệt góc.
8. BẤT KỲ yếu tố nào mẫu có mà bản thử THIẾU, hoặc bản thử làm SAI.

Trả lời bằng TIẾNG VIỆT, dạng danh sách gạch đầu dòng theo từng mục trên, mỗi điểm nêu: "Mẫu: ... | Bản thử: ... | Sửa: ...". Ngắn gọn, đúng trọng tâm, ưu tiên các khác biệt DỄ THẤY NHẤT trước."""

CANDIDATES = [MODEL, "models/gemini-3-flash-preview", "models/gemini-2.5-flash", "models/gemini-flash-latest"]
resp = None
used = None
for m in CANDIDATES:
    ok = False
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=m, contents=[ref, PROMPT, smp])
            ok = True
            used = m
            break
        except Exception as ex:
            print(f"try {m} attempt {attempt+1} failed: {repr(ex)[:110]}", flush=True)
            time.sleep(6)
    if ok:
        break
if resp is None:
    raise SystemExit("All models failed")
print("\n===== GEMINI COMPARE (" + used + ") =====\n")
print(resp.text)
