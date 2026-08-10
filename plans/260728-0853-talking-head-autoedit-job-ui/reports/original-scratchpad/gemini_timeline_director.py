# -*- coding: utf-8 -*-
"""FREE-TIMELINE director session. Gemini composes ONE flat event list (events
may overlap on the same beat) instead of filling fixed template slots. Times
are still word-anchored: every event points at word indices; a local resolver
turns them into real seconds. Compact prompt: full catalog + layering playbook,
no filler.
"""
import os, sys, time, json, re, unicodedata

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
env = load_env(os.path.join(REPO, ".env"))
from google import genai
from google.genai import types

def make_client(k):
    # Both AIza* and new-format AQ.* keys work against the Developer API
    # (verified: AQ key answered via generativelanguage; Vertex blocks it).
    return genai.Client(api_key=k)

# ALT (AQ.*) first — the old AIza key was revoked by Google
KEYS = [k for k in (env.get("GEMINI_API_KEY_ALT", ""), env.get("GEMINI_API_KEY", "")) if k]
client = make_client(KEYS[0])

words = json.load(open(os.path.join(SP, "user_transcript.json"), encoding="utf-8"))["word_timestamps"]
spine = "\n".join(f'{i}: "{w["word"].strip()}" [{w["start"]:.2f}]' for i, w in enumerate(words))
NW = len(words)

PROMPT = f"""Bạn là video editor 10 năm kinh nghiệm dựng video ngắn viral. Dựng video này thành MỘT DANH SÁCH events — các event ĐƯỢC PHÉP CHỒNG NHAU trên cùng nhịp (1 khoảnh khắc có thể vừa keyword + punch_in + sfx). Thời gian NEO THEO CHỈ SỐ TỪ w bên dưới, KHÔNG ghi giây.

EVENT SCHEMA (đúng field, không bịa):
- {{"type":"caption","w0":i,"w1":j,"text":"đã sửa chính tả","highlight":"cụm đinh","highlightColor":"#FF4D4D"}} — pill đen dưới. 3-9 từ/cụm, KHÔNG cắt giữa cụm; caption phải phủ TRỌN 100% lời nói (từ 0 đến {NW-1}). highlightColor THEO NGHĨA: #FF4D4D sai lầm/cảnh báo, #22C55E giải pháp/lợi ích, #FACC15 con số/dữ kiện, #38BDF8 mặc định. Được chèn 1 emoji hợp ngữ cảnh vào text ở caption ĐẮT (🔥⚠️🤖💰✅), tối đa ~8 caption có emoji cả video, không spam.
- {{"type":"keyword","atWord":i,"durSec":1.5,"text":"CHỮ HOA","color":"#FF8A00","xPct":15,"yPct":12,"rotation":-4,"fontSize":80,"anim":"pop"}} — chữ bay to. Màu: #FFFFFF #FF8A00 #FF2E93 #00E5FF #8F00FF; anim: pop|drop|slide|whip. Mặt ở x34-72 y22-66 → đặt tại y8-20 (trên) / x6-28 (trái) / x72-92 (phải) / y60-70 (dưới). CẤM đặt atWord rơi vào trong khoảng card nào (w0..w1) — keyword trong card bị lớp card che, vô nghĩa; chỉ đặt ở đoạn mặt fullscreen.
- {{"type":"card","w0":i,"w1":j,"kicker":"SAI LẦM ① — ...","title":"2-4 chữ","badge":"1","bullets":["<=7 chữ"],"bulletWords":[k,m]}} — màn trắng chấm bi chiếm hình, mặt tự thu vào PiP góc dưới-phải (card liền kề tự giữ PiP + push-slide). RENDERER TỰ LO các hành vi sau (bạn không cần bù): bullet chưa nói tới hiện sẵn dạng GHOST mờ-nhòe (tease), hiện đúng lúc bulletWords được nói kèm word-stagger + pop; PiP to lúc card vào rồi thu nhỏ dần khi list đầy; PiP thở + nhún theo bullet; thanh progress cầu vồng chạy đáy màn hình. → bulletWords nên RẢI ĐỀU theo lời nói (một ý 3-6s), tránh dồn cuối.
- {{"type":"punch_in","atWord":i,"scale":1.06,"holdSec":1.1}} — zoom nhấn A-roll khi fullscreen; scale 1.04-1.08, cách nhau >=3s, KHÔNG đặt trong khoảng card.
- {{"type":"sfx","atWord":i,"offsetSec":0,"name":"sfx_pop.mp3","volume":0.5}} — offsetSec âm = kêu TRƯỚC từ đó.
- {{"type":"shake","atWord":i,"durSec":0.4,"intensity":10}} — rung máy, chỉ cho ý SỐC nhất, <=2 lần.
- {{"type":"flash","atWord":i,"durSec":0.15}} — chớp trắng chuyển ý lớn, <=3 lần.

SFX CÓ THẬT (chỉ 7 file này): sfx_pop.mp3 (keyword/bullet bật) | sfx_pop_high.mp3 (pop lảnh — xen kẽ cho đỡ lặp) | sfx_pop_low.mp3 (pop trầm) | sfx_whoosh.mp3 (card vào / chuyển cảnh) | sfx_riser.mp3 (tiếng dâng ~0.5s ĐẶT TRƯỚC khoảnh khắc lớn, offsetSec=-0.5) | sfx_ding.mp3 (chốt ý / số liệu quan trọng) | sfx_tick.mp3 (nhấn nhẹ theo nhịp).

COLD-OPEN — VŨ KHÍ HOOK MẠNH NHẤT (BẮT BUỘC dùng):
"cold_open": {{"w0":i,"w1":j,"caption":"lời câu đó đã sửa chính tả","highlight":"cụm sốc","highlightColor":"#FF4D4D","keyword":{{"text":"CHỮ HOA NGẮN","color":"#FF2E93","xPct":18,"yPct":12,"anim":"whip","fontSize":84}}}}
- Chọn ĐÚNG 1 câu ĐẮT NHẤT toàn video (khoảng 1.5-2.5s nói, chọn theo NỘI DUNG THẬT trong xương sống từ): hậu quả đau nhất / con số sốc / phủ định mạnh — TUYỆT ĐỐI không chọn câu chào đầu video. w1 PHẢI là từ CUỐI CÙNG của câu (sau nó người nói ngắt hơi) — đừng dừng giữa câu.
- Renderer sẽ tự CẮT đoạn đó phát TRƯỚC làm teaser mở màn và tự thêm punch-in + riser + flash trắng + whoosh tại điểm nối vào bài — bạn KHÔNG thêm sfx/punch cho teaser.
- 3 GIÂY ĐẦU quyết định tất cả: teaser sốc → flash → vào bài phải có ngay keyword + punch_in ở từ nhấn đầu tiên. Người xem phải biết ngay họ MẤT GÌ nếu lướt qua.

ENDCARD — CTA chốt (BẮT BUỘC):
"endcard": {{"title":"câu chốt giá trị <=12 chữ, theo đúng nội dung video","subtitle":"CTA ngắn tự nhiên"}}
- Renderer tự vẽ card gradient + nút FOLLOW 2.8s cuối.

CÔNG THỨC PHỐI LỚP (chồng event như editor thật):
1. HOOK 3s đầu: keyword màu nổi + punch_in + sfx_pop CÙNG nhịp từ được nhấn giọng — bắt mắt ngay.
2. VÀO CARD: sfx_riser (offsetSec=-0.5) dâng → sfx_whoosh đúng lúc card vào → mỗi bullet kèm sfx_pop/pop_high đúng từ bulletWords.
3. NHỊP GIỮ CHÂN: mỗi 8-12s PHẢI có 1 biến cố thị giác (keyword/punch_in/bullet/card). Không để đoạn nào phẳng >12s.
4. CHỐT Ý/SỐ LIỆU: sfx_ding + caption highlight cụm số liệu đó.
5. TIẾT CHẾ: sfx cách nhau >=0.8s (trừ pop theo bullet); keyword 5-8 cái cả video; đừng để 2 keyword cùng hiện 1 chỗ.

NHẠC NỀN — chọn 1 bản hợp mood nội dung (kho 12 bản, chọn ĐÚNG tên file):
- Explainer/tips/listicle: bgm_upbeat_bounce.mp3 (tươi nảy) | bgm_clean_explainer.mp3 (trung tính sạch) | bgm_playful_pop.mp3 (tinh nghịch)
- Chủ đề AI/công nghệ: bgm_tech_pulse.mp3 (tối giản) | bgm_data_groove.mp3 (groove đều) | bgm_future_calm.mp3 (thoáng chậm)
- Cảnh báo/CTA gắt/nhịp nhanh: bgm_energy_drive.mp3 | bgm_urgent_percussive.mp3
- Tâm sự/kể chuyện: bgm_lofi_chill.mp3 | bgm_lofi_night.mp3
- Truyền cảm hứng/brand: bgm_warm_story.mp3 | bgm_hopeful_lift.mp3
Trả trong JSON: "bgm": {{"name":"bgm_....mp3","volume":0.22}} (volume 0.18-0.28 — kho đã chuẩn hoá loudness, 0.22 đặt nhạc ~15dB dưới giọng; renderer tự đẩy nhạc to lên lúc ngắt lời). Nếu video KHÔNG nên có nhạc thì "bgm": null.

NHIỆM VỤ KHÁC:
- "cut_remove":[{{"w":[wA,wB],"ly_do":"filler|repeat|false_start","joined":"[6 từ trước] [6 từ sau] ghép liền sau khi cắt"}}] — chỉ xoá: ê-a/ừm đứng một mình, cụm LẶP NGUYÊN VĂN, câu bỏ dở nói lại. Trường "joined" là BẰNG CHỨNG BẮT BUỘC: tự chép chuỗi ghép và tự đọc — nếu KHÔNG thành câu tự nhiên trọn nghĩa thì BỎ cú cắt đó (thà dài hơn chứ không gãy ý). Hệ thống sẽ KIỂM MÁY từng cú cắt: cắt chứa từ mang thông tin sẽ bị TỪ CHỐI tự động. Renderer đã tự rút ngắn bằng tempo 1.06x. Không cắt trùng đoạn cold_open.
- "punch_in" scale 1.05-1.10 (1.08 là đẹp).
- "grade": {{"brightness":-0.1..0.1,"contrast":1..1.15,"saturation":1..1.2,"gamma":0.9..1.1,"warmth":-20..30,"skin_smooth":0..0.6,"blemish_reduce":0..0.7,"sharpen":0..1.2,"note":"..."}} — da mịn tự nhiên, ấm, không lố.
- BẮT BUỘC 4 card: 3 sai lầm (badge 1/2/3) + GIẢI PHÁP (badge 4), mỗi card trải gần trọn đoạn nói ý đó.
- Chủ đề: "3 sai lầm khi làm chatbot AI bán hàng".

XƯƠNG SỐNG {NW} TỪ (i: "từ" [giây bắt đầu]):
{spine}

TRẢ VỀ DUY NHẤT JSON: {{"cut_remove":[[..]],"grade":{{..}},"bgm":{{..}} hoặc null,"cold_open":{{..}},"endcard":{{..}},"events":[..]}}"""

MODEL = sys.argv[1] if len(sys.argv) > 1 else "models/gemini-3.6-flash"
TAG = MODEL.split("/")[-1].replace(".", "").replace("-", "_")

def parse(t):
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        return json.loads(t[a:b + 1])

spec = None
used = None
for ki, key in enumerate(KEYS):
    client = make_client(key)
    model = MODEL
    bad_key = False
    for use_think in (True, False):
        for attempt in range(4):
            try:
                cfg = dict(temperature=0.25, response_mime_type="application/json", max_output_tokens=50000)
                if use_think:
                    # Gemini 3.x dùng thinking_level (minimal/low/medium/high) —
                    # thinking_budget chỉ dành cho dòng 2.5.
                    cfg["thinking_config"] = types.ThinkingConfig(thinking_level="high")
                r = client.models.generate_content(model=model, contents=[PROMPT],
                                                   config=types.GenerateContentConfig(**cfg))
                open(os.path.join(SP, f"timeline_spec_raw_{TAG}.txt"), "w", encoding="utf-8").write(r.text or "")
                spec = parse(r.text)
                used = f"key#{ki} {model} thinking={use_think}"
                break
            except Exception as ex:
                msg = repr(ex)[:120]
                print(f"key#{ki} attempt {attempt+1} thinking={use_think}: {msg}", flush=True)
                if "API key not valid" in msg:
                    bad_key = True
                    break  # key hỏng — chuyển key khác, đừng retry vô ích
                # 429 quota needs a much longer cool-down than 503 blips
                time.sleep(45 if "429" in msg else 10 + attempt * 5)
        if spec or bad_key:
            break
    if spec:
        break
if not spec:
    raise SystemExit(f"{MODEL} unavailable (quota/overload)")

# ---- MACHINE CUT AUDIT: reject any cut that could break meaning --------------
# A cut survives only if it is (a) pure filler syllables, or (b) a verbatim
# repeat of the words right after it (stutter/restart). The model's "joined"
# proof is logged but never trusted.
def norm(s):
    s = unicodedata.normalize("NFC", s.strip().lower())
    return re.sub(r"[^\wàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]+", "", s)
FILLERS = {"a", "à", "á", "ạ", "ừ", "ừm", "ờ", "ơ", "ê", "ậm", "ờm", "hử", "ừa", "ý"}
def wtext(i, j):
    return [norm(words[k]["word"]) for k in range(max(0, i), min(NW, j + 1))]
raw_cuts, ok_cuts = spec.get("cut_remove", []), []
cold = spec.get("cold_open") or {}
for c in raw_cuts:
    span = c["w"] if isinstance(c, dict) else c
    if not span or len(span) < 2:
        continue
    a, b = int(span[0]), int(span[1])
    toks = wtext(a, b)
    tail = wtext(b + 1, b + 1 + (b - a))
    is_filler = all(t in FILLERS for t in toks)
    is_repeat = toks == tail and len(toks) > 0
    in_cold = "w0" in cold and not (b < cold["w0"] or a > cold["w1"])
    verdict = "OK" if (is_filler or is_repeat) and not in_cold else "REJECTED"
    ctx = " ".join(w["word"].strip() for w in words[max(0, a - 6):a]) + " ⟦" + " ".join(toks) + "⟧ " + \
          " ".join(w["word"].strip() for w in words[b + 1:b + 7])
    print(f"CUT w{a}-{b} {verdict} ({'filler' if is_filler else 'repeat' if is_repeat else 'CONTENT!'}"
          f"{'/in-cold-open' if in_cold else ''}): {ctx}")
    if verdict == "OK":
        ok_cuts.append([a, b])
spec["cut_remove"] = ok_cuts  # resolver keeps consuming plain pairs

# cold-open seam report: silence after w1 (resolver now clamps pad + fades)
if "w1" in cold:
    w1 = min(NW - 1, int(cold["w1"]))
    gap = (words[w1 + 1]["start"] - words[w1]["end"]) if w1 + 1 < NW else 9.9
    print(f"COLD-OPEN w{cold.get('w0')}-{w1} seam-gap={gap:.2f}s "
          f"{'(khít — pad tự kẹp + fade)' if gap < 0.15 else '(ngắt hơi tự nhiên)'}")

json.dump(spec, open(os.path.join(SP, f"timeline_spec_{TAG}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
ev = spec.get("events", [])
from collections import Counter
print("MODEL", used)
print("cut_remove (sau kiểm):", spec.get("cut_remove"))
print("grade:", json.dumps(spec.get("grade", {}), ensure_ascii=False))
print("event mix:", dict(Counter(e.get("type") for e in ev)))
cards = [(e["w0"], e["w1"]) for e in ev if e.get("type") == "card"]
for e in ev:
    if e.get("type") == "card":
        print(f'  CARD w{e.get("w0")}-w{e.get("w1")} "{e.get("title")}" bw={e.get("bulletWords")}')
# evaluation: caption word coverage + keyword-in-card violations
covered = set()
for e in ev:
    if e.get("type") == "caption":
        covered.update(range(int(e["w0"]), int(e["w1"]) + 1))
missing = [i for i in range(NW) if i not in covered]
print(f"caption coverage: {NW - len(missing)}/{NW}" + (f" — HỞ: {missing[:20]}" if missing else " ✅"))
bad_kw = [e for e in ev if e.get("type") == "keyword"
          and any(a <= int(e.get("atWord", -1)) <= b for a, b in cards)]
print(f"keyword-in-card violations: {len(bad_kw)}" + (" ✅" if not bad_kw else f" — {[e.get('text') for e in bad_kw]}"))
