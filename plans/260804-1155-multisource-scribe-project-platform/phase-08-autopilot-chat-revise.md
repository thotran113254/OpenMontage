# Phase 08 — Autopilot + điều hướng bằng prompt (chat revise)

## Context

- [plan.md](plan.md) · [phase-04](phase-04-project-data-model-api.md) ·
  [phase-06](phase-06-timeline-view-selfeval.md)
- Hiện có: `stages/revise.py` + `spec_patch.py` (patch spec theo `(loại, chỉ số từ)`, tạo version mới,
  ~8k token thay vì 55k); `POST /api/jobs/{id}/revise`; `revise-box.tsx` ở UI.
- User muốn: hệ thống chạy tự động, admin điều hướng bằng prompt khi cần.

## Overview

- **Priority:** P2
- **Status:** ✅ code + 39 test.

**Bug thật mà chính test bắt được:** `black_frame` và `sfx_missing` không có remedy **và** không nằm
trong danh sách bỏ qua — nghĩa là chúng lặng lẽ rơi vào nhánh "unknown". Thêm `NEEDS_HUMAN` để phân
loại tường minh kèm hướng dẫn người dùng phải làm gì. Có test khoá: mọi `CODE_*` trong `verify.py`
phải thuộc đúng một trong ba nhóm (`REMEDY` / `IGNORED_CODES` / `NEEDS_HUMAN`).

**`/cancel` trước đây KHÔNG dừng thật.** `process.kill()` chỉ diệt CLI wrapper; ffmpeg và node là
process con nên vẫn chạy tiếp ngốn hết core — tệ hơn là không có nút cancel, vì UI báo "đã huỷ".
Đã sửa: chạy run trong process group/session riêng, cancel dùng `taskkill /T` (Windows) hoặc
`killpg` (POSIX). **Có test chạy tiến trình con thật rồi kiểm nó đã chết.**
- Autopilot chạy hết chain, tự sửa **một lần** theo remedy đã biết khi `verify` fail. Revise thành hội
  thoại nhiều lượt có lịch sử, mỗi lượt = 1 version có diff.

## Key insights

- **Chỉ 1 lần retry, không 3.** video-use cho 3 pass self-eval nhưng render của họ nhanh hơn. Ở đây
  render 1080x1920 mất ~370s — 3 pass là 18 phút mù. 1 retry + báo rõ là đúng mức.
- **Chỉ retry những lỗi có remedy ĐÃ BIẾT.** Bảng bẫy trong `docs/talking-head-autoedit.md` là nguồn:
  giật → giảm concurrency; nhạc rớt khỏi mix → dựng lại `BgmDucked`; "No frame found" → nửa
  concurrency (đã tự retry sẵn). Lỗi không có remedy → **không đoán**, dừng và báo.
- **Không để LLM tự sửa vòng lặp không giới hạn.** Đó là cách đốt tiền và tạo ra bản dựng lạ. Autopilot
  chỉ chạy remedy cơ học; sửa sáng tạo vẫn phải qua người hoặc qua chat revise.
- **Chat revise cần lịch sử.** Hiện mỗi `revise` độc lập. "Bỏ card 2" rồi "thêm lại card đó" — lượt sau
  cần biết lượt trước. Lịch sử = danh sách `(yêu cầu, patch, version)`, đưa 3 lượt gần nhất vào prompt.
- **Diff phải xem được trước khi áp** khi người dùng muốn — thêm `dry_run`.

## Requirements

**Functional**
- `POST /api/jobs/{id}/autopilot {stages?, max_retry?=1}` → chạy nền, SSE, tự retry theo remedy.
- Remedy registry: `verify` issue code → hành động. Code chưa có remedy → dừng, báo, không đoán.
- Chat revise: `POST /api/jobs/{id}/chat {message, dry_run?}` → patch + version mới + diff.
  `GET /api/jobs/{id}/chat` → lịch sử.
- Lịch sử 3 lượt gần nhất vào prompt revise.
- CLI: `--autopilot`, `--chat "…"`, `--chat-history`.
- Sau autopilot: báo cáo tổng — stage nào chạy, retry gì, `verify` còn issue nào, chi phí token.

**Non-functional**
- Autopilot không bao giờ chạy quá 2 lần render (1 + 1 retry).
- Job đang autopilot phải hủy được (`POST /cancel` đã có — kiểm nó thật sự dừng giữa render).

## Architecture

```
autopilot(job, stages):
  run_job(stages)
  issues = verify_report.issues
  if issues and retry_budget > 0:
      remedies = [REMEDY[i.code] for i in issues if i.code in REMEDY]
      if not remedies:  → dừng, báo "không có remedy cho <code>"
      áp remedies (đổi options / dựng lại asset)
      run_job(stages_ảnh_hưởng)   ← chỉ stage cần, không chạy lại từ đầu
      verify lần 2 → báo kết quả bất kể đạt hay không
```

```python
# lib/talking_head_edit/remedies.py
REMEDY = {
  "stutter":        {"note": "máy tranh CPU lúc render",
                     "options": {"render_concurrency": "half"}, "stages": ["render", "verify"]},
  "bgm_missing":    {"note": "nhạc không vào mix", "rebuild": "bgm", "stages": ["resolve", "render", "verify"]},
  "av_drift":       {"note": "lệch A/V", "stages": ["resolve", "render", "verify"]},
  "loudness_off":   {"note": "LUFS lệch", "stages": ["resolve", "render", "verify"]},
}
```
`verify` phải phát ra **code**, hiện chỉ có message → sửa `stages/verify.py` gán code cho từng issue.

Chat lịch sử: `chat_history.jsonl` trong job dir (append-only, cùng kiểu `events.jsonl`).

## Related code files

**Tạo**
- `lib/talking_head_edit/autopilot.py`
- `lib/talking_head_edit/remedies.py`
- `lib/talking_head_edit/chat_revise.py` — lịch sử + gọi `revise` với ngữ cảnh
- `tests/test_talking_head_autopilot.py`, `tests/test_talking_head_chat_revise.py`

**Sửa**
- `stages/verify.py` — gán `code` cho mỗi issue (**điều kiện tiên quyết** của cả phase)
- `stages/revise.py` — nhận `history` để đưa vào prompt
- `prompts/talking_head/revise.v2.md` — thêm block lịch sử
- `server/api_jobs.py` — `POST /autopilot`, `POST /chat`, `GET /chat`
- `server/queue_worker.py` — autopilot là job nền dài, phải qua queue (không chạy trong request)
- `cli.py` — `--autopilot`, `--chat`, `--chat-history`
- `runner.py` — `render_concurrency` option (kiểm `stages/render.py` đã có chỗ nhận chưa)

## Implementation steps

1. `stages/verify.py`: gán code cho từng issue. Đây là việc phải làm trước — không có code thì remedy
   không tra được. Giữ nguyên message tiếng Việt cho người đọc.
2. `remedies.py` với 4 code trên. Mỗi entry ghi `note` để báo cáo giải thích được vì sao retry.
3. `autopilot.py`: chạy, đọc issue, tra remedy, áp, chạy lại **chỉ stage ảnh hưởng**, verify lần 2,
   báo cáo. Không có remedy → dừng ngay, không thử ngẫu nhiên.
4. `render_concurrency: "half"` — `stages/render.py:38` đã có `_concurrency()` và **đã tự retry nửa
   concurrency** cho `FRAME_SEEK_ERROR` (`render.py:148-151`). Việc cần thêm: cho phép **ép** nửa
   concurrency từ options ngay từ lần đầu (remedy `stutter` khác lỗi `FRAME_SEEK_ERROR` — giật là
   frame lặp, không phải render fail, nên nhánh retry hiện tại không bắt được).
5. `chat_revise.py`: đọc `chat_history.jsonl`, lấy 3 lượt gần nhất, gọi `revise` với ngữ cảnh, ghi
   lượt mới. `dry_run` → trả patch + diff, không tạo version.
6. `revise.v2.md`: thêm block "3 YÊU CẦU TRƯỚC ĐÓ" (chỉ yêu cầu + kết quả tóm tắt, **không** nhồi cả
   spec cũ vào — mục đích của revise là rẻ).
7. API + queue. Autopilot qua `queue_worker` để không giữ HTTP connection 10 phút.
8. Kiểm `POST /cancel` thật sự dừng được giữa render (hiện chỉ set cờ? → phải kill process ffmpeg/node).
9. Test: remedy tra đúng code, không có remedy thì dừng, trần 1 retry, chỉ chạy lại stage ảnh hưởng,
   lịch sử chat đưa đúng 3 lượt, `dry_run` không tạo version.

## Todo

- [ ] `verify` gán `code` cho issue (tiên quyết)
- [ ] `remedies.py` 4 code + note
- [ ] `autopilot.py` + trần 1 retry + chỉ chạy stage ảnh hưởng
- [ ] `render_concurrency` option
- [ ] `chat_revise.py` + `chat_history.jsonl` + `dry_run`
- [ ] `revise.v2.md` block lịch sử
- [ ] API `/autopilot`, `/chat` + qua queue
- [ ] Kiểm `/cancel` dừng thật giữa render
- [ ] Test (7 case)

## Success criteria

- Cố tình gây giật (chạy tải CPU lúc render) → autopilot phát hiện, retry nửa concurrency, lần 2 đạt.
- Issue không có remedy → dừng, báo cáo ghi rõ code + "chưa có remedy", không retry mù.
- Autopilot không render quá 2 lần trong mọi trường hợp.
- Chat 3 lượt liên tiếp mâu thuẫn nhau ("bỏ card 2" → "thêm lại card 2") → lượt 3 hiểu ngữ cảnh.
- `dry_run` trả diff, `job.json` không có version mới.
- `/cancel` giữa render dừng được trong ≤ 5s.

## Risks

| Rủi ro | Xử lý |
|---|---|
| Autopilot retry mù, đốt render | Trần cứng 1; chỉ code có remedy |
| Remedy sai làm hỏng thứ đang đúng | Mỗi remedy chỉ chạy lại stage ảnh hưởng; version cũ vẫn còn để rollback |
| Chat lịch sử phình prompt, mất lợi thế "revise rẻ" | Chỉ 3 lượt, chỉ yêu cầu + tóm tắt kết quả; đo token mỗi lượt, cảnh báo nếu > 15k |
| Autopilot chạy trong HTTP request → timeout | Bắt buộc qua `queue_worker` |
| `/cancel` không kill process con → render tiếp ngầm | Test có case này; lưu pid của ffmpeg/node |

## Security

- Autopilot chạy nền không giới hạn thời gian → thêm trần thời gian tổng (mặc định 45 phút) để không
  treo máy dev qua đêm.

## Next

Phase 09 dựng chat panel + nút Autopilot trong UI.
