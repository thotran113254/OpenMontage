# Phase 01 — ASR provider abstraction + ElevenLabs Scribe

## Context

- [plan.md](plan.md)
- Hiện tại: `lib/talking_head_edit/stages/transcribe.py` gọi thẳng `tools.analysis.transcriber`
  (whisperx). Spine v1: `{word_timestamps[], language, duration_seconds, model_size}`.
- Scribe API: `.agents/skills/speech-to-text/SKILL.md`

## Overview

- **Priority:** P0 — chặn phase 02
- **Status:** ✅ xong (code + 35 test mock; chưa đo thật vì chưa có `ELEVENLABS_API_KEY`)
- Tách ASR thành 2 provider sau một interface. Scribe mặc định, Whisper fallback. Spine lên v2 để
  chứa `speaker` + `audio_events` + (phase 02) `src`.

## Key insights

- **Scribe trả `word.type`** ∈ `word | spacing | audio_event`. `spacing` phải **lọc bỏ** khỏi word
  spine (nếu để lại, chỉ số từ mà director thấy sẽ lệch so với lời nói và mọi caption sẽ sai).
  `audio_event` (laughter/applause) tách ra `audio_events[]` — tín hiệu biên tập, KHÔNG vào spine.
- **`keyterms` (≤100 từ)** là lợi thế thật với tiếng Việt: tên brand, thuật ngữ ("chatbot", "CRM")
  Whisper thường nghe sai. Lấy từ `topic` + `card_plan`.
- **Scribe không có `probability`** per word. `transcribe.py:38-39` ghi field này vào spine nhưng
  **đã kiểm: không có consumer nào đọc nó** (chỉ `transcriber.py:175` sinh ra). → Bỏ hẳn khỏi spine
  v2, không cần đường thoát.
- Whisper `medium` local mất vài phút; Scribe là 1 call API → `transcribe` từ "vài phút" xuống
  ~10-30s. Đây là lợi ích lớn nhất về vòng lặp làm việc, hơn cả độ chính xác.
- Giới hạn Scribe: 3GB / 10 giờ — footage talking-head không bao giờ chạm.

## Requirements

**Functional**
- `asr.transcribe(path, opts) -> Spine` với 2 provider, chọn qua `options.asr_provider`.
- Auto-fallback: Scribe lỗi mạng/401/429 → log cảnh báo → chạy Whisper. Lỗi 422 (tham số sai) thì
  **không** fallback, phải fail để lộ bug.
- Cache key thêm `provider`: transcript Whisper cũ vẫn dùng được, không bị vô hiệu.
- Diarization bật khi `assembly.speaker_aware != false` (phase 02 dùng).

**Non-functional**
- Không tăng thời gian `transcribe` khi cache hit (hiện 0.06s).
- Tool Scribe vào registry → pipeline khác (`clip-factory`, `podcast-repurpose`) dùng lại được.

## Architecture

```
lib/talking_head_edit/asr/
├── __init__.py         transcribe(path, opts) -> dict   ← điểm vào duy nhất
├── base.py             Spine schema + normalise() dùng chung + AsrError
├── elevenlabs_scribe.py
└── whisper_local.py    (bọc Transcriber hiện tại, không sửa tool)

tools/analysis/elevenlabs_scribe.py   BaseTool, capability="analysis",
                                      provider="elevenlabs" → vào registry
```

Spine v2:
```jsonc
{
  "schema": 2,
  "provider": "elevenlabs_scribe",
  "model": "scribe_v2",
  "language": "vi",
  "duration_seconds": 93.2,
  "word_timestamps": [
    {"word":"xin","start":0.42,"end":0.61,"speaker":"speaker_0"}
  ],
  "audio_events": [{"kind":"laughter","start":12.4,"end":13.1}],
  "speakers": ["speaker_0"]
}
```
`speaker` và `audio_events` optional — Whisper không có, downstream phải chịu được thiếu.

## Related code files

**Tạo**
- `lib/talking_head_edit/asr/{__init__,base,elevenlabs_scribe,whisper_local}.py`
- `tools/analysis/elevenlabs_scribe.py`
- `tests/test_talking_head_asr.py`

**Sửa**
- `lib/talking_head_edit/stages/transcribe.py` — gọi `asr.transcribe`, bỏ import trực tiếp
- `lib/talking_head_edit/cache.py` — `transcript_cache_path` thêm `provider`
- `lib/talking_head_edit/job_store.py` — `DEFAULT_OPTIONS`: `asr_provider: "elevenlabs_scribe"`,
  `asr_model: "scribe_v2"`, `keyterms: []`; giữ `whisper_model` cho nhánh fallback
- `lib/talking_head_edit/runner.py:48` — `cache_signature("transcribe")` thêm provider + keyterms
- `requirements.txt` — `elevenlabs`
- `.env.example` — `ELEVENLABS_API_KEY`
- `docs/talking-head-autoedit.md` — bảng stage + phần cache

## Implementation steps

1. ~~Grep `probability`~~ — đã kiểm, không consumer. Bỏ field khỏi spine v2.
2. `base.py`: định nghĩa `Spine` (TypedDict), `normalise_words()` (chuyển từ `transcribe.py:22-41`,
   thêm lọc `type != "word"`), `AsrError`, `AsrTransient` (được fallback) vs `AsrFatal` (không).
3. `whisper_local.py`: bọc `Transcriber().execute(...)` hiện có, map ra Spine v2 (`speaker=None`,
   `audio_events=[]`).
4. `tools/analysis/elevenlabs_scribe.py`: BaseTool. `execute({input_path, model_id, language,
   diarize, keyterms})`. `install_instructions` ghi rõ `ELEVENLABS_API_KEY`. Trích audio bằng ffmpeg
   trước khi upload (mp4 1080p vài trăm MB → mp3 mono 64kbps vài MB; Scribe nhận video nhưng upload
   video là đốt băng thông vô ích). **Đo lại độ chính xác** giữa audio-only vs video-in trên 1 clip
   thật trước khi chốt — nếu lệch thì giữ video-in.
5. `elevenlabs_scribe.py` (lib): gọi tool, tách `spacing` ra, tách `audio_event` sang `audio_events`,
   gom `speakers` unique.
6. `asr/__init__.py`: `transcribe()` chọn provider, bắt `AsrTransient` → fallback Whisper + `job.emit`
   cảnh báo rõ ("Scribe lỗi X, đã dùng Whisper medium thay thế").
7. Sửa `stages/transcribe.py` gọi `asr.transcribe`; giữ nguyên guard `< 5 từ`.
8. Cache: `transcript_cache_path(sha, provider, model, language)`. Đọc được layout cũ (không có
   provider) → coi là `whisper_local` để transcript cũ không bị mất.
9. Test: mock API (không tốn tiền) cho lọc `spacing`, tách `audio_event`, fallback transient,
   không-fallback trên 422, cache key theo provider.
10. Chạy thật 1 clip, đối chiếu số từ Whisper vs Scribe, ghi kết quả vào `docs/`.

## Todo

- [x] `asr/base.py` + normalise (lọc `spacing`, bỏ `probability`)
- [x] `asr/whisper_local.py`
- [x] `tools/analysis/elevenlabs_scribe.py` + vào registry (test khẳng định registry thấy nó)
- [x] `asr/elevenlabs_scribe.py`
- [x] `asr/__init__.py` + fallback phân loại lỗi (transient → Whisper, fatal → raise)
- [x] Sửa `stages/transcribe.py`
- [x] Cache key theo provider + đọc được layout cũ (`find_cached_transcript`)
- [x] Test mock (35 case) — không cần API key
- [ ] **Đo thật: số từ, thời gian, audio-only vs video-in** — chặn bởi `ELEVENLABS_API_KEY`
- [x] Cập nhật `docs/talking-head-autoedit.md`
- [x] CLI `--asr`, `--keyterm`; `requirements.txt`; `.env.example`

## Success criteria

- `make autoedit-test` xanh, không cần API key.
- Cùng footage: Scribe cho số từ ≥ 95% Whisper, không có từ nào `end <= start`.
- Xoá `ELEVENLABS_API_KEY` → job vẫn chạy hết qua Whisper, có cảnh báo trong log.
- Transcript Whisper đã cache trước đó vẫn hit (không chạy lại).
- `registry.capability_catalog()` thấy `elevenlabs_scribe`.

## Risks

| Rủi ro | Xử lý |
|---|---|
| `spacing` lọt vào spine → mọi caption lệch | Test riêng cho case này; assert không có word nào rỗng/whitespace |
| Scribe timestamp lệch so với Whisper → số sharpen/tempo đã tinh chỉnh không còn đúng | Chỉ số đó phụ thuộc pixel/audio, không phụ thuộc ASR. Nhưng phải chạy `verify` full 1 lần để chắc |
| Tiếng Việt: Scribe tách từ khác Whisper (âm tiết vs từ) → caption dài/ngắn khác | Đo số từ + độ dài caption trung bình; nếu lệch nhiều thì tinh chỉnh ngưỡng chia caption ở `prompt_captions` |
| Fallback im lặng che mất lỗi cấu hình | Fallback luôn `emit` warning; job status ghi `asr_fallback: true` |

## Security

- `ELEVENLABS_API_KEY` chỉ đọc từ env, không log, không ghi vào `job.json`.
- Audio gửi ra ngoài máy → nêu rõ trong docs. Ai cần offline tuyệt đối thì
  `asr_provider: "whisper_local"`.

## Next

Phase 02 dùng spine v2 làm nền cho multi-source (thêm `src` vào mỗi word).
