# Multi-source + Scribe + Project platform

Nâng `talking_head_edit` từ *một file → một video* thành *một project nhiều nguồn → nhiều bản dựng*,
đổi ASR sang ElevenLabs Scribe (giữ Whisper fallback), prompt thành dữ liệu sửa được + A/B test,
và UI project workspace.

**Nguyên tắc bất biến — mọi phase phải giữ:** word index là đồng hồ duy nhất; `resolve` là nơi DUY
NHẤT đổi index → giây và là nơi duy nhất cắt media; LLM không bao giờ phát ra giây; mọi cut phải qua
`cut_verifier`; chỉ tài nguyên có thật được dùng.

## Quyết định đã chốt với user (2026-08-04)

| Hạng mục | Chốt |
|---|---|
| ASR | Dual-provider, **Scribe mặc định**, Whisper local fallback |
| Multi-source | **Cả 4 ca dùng**: best-take, sequential, b-roll overlay, multi-speaker — cấu hình được ở **global + project + job** (3 tầng) |
| Prompt | **Full editor + A/B test** 2 version trên cùng spine |
| Data model | **Project bọc ngoài Job** |

## Phases

| # | Phase | Trạng thái | Test |
|---|---|---|---|
| 01 | [ASR provider abstraction + Scribe](phase-01-asr-provider-scribe.md) | ✅ | 35 |
| 02 | [Multi-source spine + assembly mode](phase-02-multi-source-spine-assembly.md) | ✅ | 63 |
| 03 | [Resolve per-span extract đa nguồn](phase-03-resolve-per-span-multisource.md) | ✅ gate độ nét **2.964 ≥ 2.85** | 51 |
| 04 | [Project data model + API](phase-04-project-data-model-api.md) | ✅ | 66 |
| 05 | [Prompt registry + A/B test](phase-05-prompt-registry-ab-test.md) | ✅ gate byte-identical **5/5** | 63 |
| 06 | [timeline_view + self-eval](phase-06-timeline-view-selfeval.md) | ✅ ảnh 1.24s, nhãn khớp caption | 27 |
| 07 | [B-roll overlay + speaker mode](phase-07-broll-overlay-speaker-mode.md) | ✅ | 36 |
| 08 | [Autopilot + chat revise](phase-08-autopilot-chat-revise.md) | ✅ `/cancel` diệt cả process con | 39 |
| 09 | [UI project workspace](phase-09-ui-project-workspace.md) | ✅ typecheck + build sạch | 25 |

**Toàn bộ test suite: 872 pass, 3 skip.** `npm run typecheck:ui` sạch. `tsc` gốc: 15 lỗi có sẵn từ
trước (`Explainer`, `Root`, `ProviderChip`) — đã kiểm bằng stash, không phải do plan này.

## Bug thật đã bắt được (số đo / chạy thật, không phải test)

| Bug | Phát hiện bằng | Hậu quả nếu bỏ qua |
|---|---|---|
| Cửa sổ run word-tight làm mất tiếng phòng đầu/đuôi (90.8s → 89.0s) | đo `src.mp4` thật | mọi bản dựng ngắn hơn và cụt đuôi |
| Nhãn từ trên `timeline_view` dùng giây **nguồn** thay vì giây bản dựng | xem ảnh thật | ảnh soi mối nối chỉ sai chỗ |
| `audit`/`revise` đọc spine đầy đủ nhưng spec dùng chỉ số spine **đã lọc** | đọc code khi làm phase 07 | job nhiều take: cắt sai cửa sổ |
| `black_frame`/`sfx_missing` không có remedy **và** không được bỏ qua tường minh | test tự viết | autopilot lặng lẽ dừng, không ai biết vì sao |
| `/cancel` chỉ diệt CLI wrapper, ffmpeg/node vẫn chạy | đọc code + test process thật | UI báo "đã huỷ" mà máy vẫn ngốn hết core |
| `from-path` lấy label từ tên trên đĩa → nhóm take tên `"s"` | chạy API thật | gộp bừa hai file bất kỳ |
| `create_job` chỉ đưa aroll vào `input_paths` | chạy API thật | **b-roll của phase 07 không bao giờ chạy được từ UI** |

## Kiến trúc sau khi xong

```
projects/autoedit/<project_id>/
├── project.json          sources[] + assembly config + jobs[]
├── sources/              s0_*.mp4  + s0.transcript.json (cache theo sha256)
└── jobs/<job_id>/        layout job hiện tại, không đổi

config/autoedit-defaults.json      ← tầng global
prompts/talking_head/*.v1.md       ← template gốc (git)
prompts/overrides/talking_head/    ← bản admin sửa
lib/talking_head_edit/asr/         ← scribe | whisper, cùng một interface
```

Stage chain: `probe → transcribe → select → direct → audit → calibrate → resolve → render → verify`
(thêm `select`, chỉ chạy khi có nhiều nguồn cùng nội dung).

## Dependencies mới

- `elevenlabs` (Python SDK) — `requirements.txt`
- `ELEVENLABS_API_KEY` — `.env`
- Không thêm template engine: placeholder `{{name}}` + `str.replace` (prompt đầy `{}` của JSON nên
  `str.format`/Jinja sẽ vỡ hoặc phải escape khắp nơi)

## Rủi ro lớn nhất (chi tiết trong từng phase)

1. **Mất nét ở phase 03** — chuyển sang per-span extract có thể thành 2 lần encode video. Thiết kế
   bắt buộc: video encode **đúng một lần** (span), concat `-c copy`, bước audio dùng `-c:v copy`.
   Gate: đo lại độ nét mặt cắt 1:1, phải ≥ 2.85 (mức hiện tại).
2. **loudnorm per-span làm âm lượng nhảy** — loudnorm/compressor chỉ chạy trên chuỗi đã ghép.
3. **Scribe không có `probability` per word** như Whisper — chỗ nào dùng `probability` phải chịu được
   thiếu (kiểm trước khi đổi).
4. **Job cũ phải đọc được** — `JobStore` giữ legacy root `projects/autoedit-jobs/`, không migrate file.

## Còn lại — cần thứ tôi không có

| Việc | Chặn bởi |
|---|---|
| Đo thật Scribe: số từ vs Whisper, thời gian, audio-only vs video-in | `ELEVENLABS_API_KEY` |
| Đo `mode=auto` đoán đúng bao nhiêu lần | footage thật có nhiều take cùng nội dung |
| Đo render chậm thêm bao nhiêu với 3 b-roll | footage b-roll thật + 1 lần render 6 phút |
| Upload 3 file 200 MB: RAM server, `GET /api/projects` với 50 project | file lớn thật |
| Giá Scribe theo phút → `AUTOEDIT_PRICE_ASR_PER_MIN` | dashboard ElevenLabs của bạn |

`keyterms` đã giải quyết: tự tách từ `topic` + `card_plan` (giữ token viết hoa/tên riêng/acronym, kể
cả 2 chữ như "OA"), **và** có ô nhập tay ở project (tab Cấu hình). Nhập tay thắng tự tách.
