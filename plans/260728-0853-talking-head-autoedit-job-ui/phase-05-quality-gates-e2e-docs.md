# Phase 05 — Quality Gates, E2E, Docs

## Context Links
- Plan: [plan.md](plan.md) · Trước: [phase-04](phase-04-prompt-revise-and-versioning.md)
- Tham chiếu kiểm định sẵn có: `tools/video/video_compose.py` (visual spotcheck luma, audio spotcheck)

## Overview
- **Priority:** P2 · **Status:** not started
- **Mô tả:** Biến "tôi tin bản render này" thành thứ đo được: verify tự động sau render, e2e trên
  footage thật, và tài liệu để lần sau không phải đọc lại code.

## Key Insights
- Repo đã có bài học: heuristic sai còn tệ hơn không kiểm tra (kích thước file PNG coi frame navy là
  không đen; `music_present` báo true khi chỉ có narration). Verify mới phải đo thật:
  luma trung bình (YAVG), LUFS, độ lệch A/V, độ dài.
- Ba thứ **không** kiểm được bằng máy (overlay vỡ, chữ khó đọc, asset thiếu) → phải để UI hiển thị
  frame cho người xem, không được báo "passed" giả.

## Requirements
**Functional**
- Stage `verify` sau render, xuất `verify_report.json`:
  - độ dài final vs `durationSeconds` trong props (lệch < 0.3s)
  - lệch A/V < 0.35s
  - không frame đen: YAVG ≥ 12 tại ≥ 8 mốc lấy mẫu
  - loudness thoại ≈ -14 LUFS (±1.5); nếu có BGM: đo được nhạc trong khoảng ngắt lời
  - caption phủ ≥ 95% thời lượng nói (đối chiếu spine)
  - mọi file sfx/bgm tham chiếu đều tồn tại
- UI hiển thị verify report + 8 frame mẫu để mắt người duyệt (không tự kết luận "đạt").
- Test: unit (audit/patch/resolve), API, và **1 e2e** chạy thật trên footage mẫu ngắn (~15s) có
  đánh dấu `@pytest.mark.slow`.

**Non-functional**
- Test không cần API key phải pass (`make test-contracts`): mock stage `direct`/`revise` bằng spec cố định.

## Architecture
```
lib/talking_head_edit/stages/verify.py
tests/test_talking_head_verify.py
tests/e2e/test_talking_head_autoedit_e2e.py   # slow, cần ffmpeg + node
docs/talking-head-autoedit.md                 # tài liệu vận hành
```

## Related Code Files
**Tạo:** `stages/verify.py`, 2 file test, `docs/talking-head-autoedit.md`.
**Sửa:** `docs/ARCHITECTURE.md` (thêm mục job runner + UI), `README.md` (một đoạn ngắn + lệnh chạy),
`AGENT_GUIDE.md` (ghi rõ: luồng talking-head auto-edit chạy qua job runner, KHÔNG improvise script),
`Makefile` (`test-e2e`).

## Implementation Steps
1. `verify.py`: dùng lại cách đo đã đúng trong `video_compose.py` (signalstats YAVG, volumedetect),
   thêm đo LUFS bằng `ebur128`; không tái sinh heuristic cũ.
2. Ghép verify vào cuối S6; fail → job `completed_with_warnings`, KHÔNG chặn (user vẫn xem được),
   nhưng UI hiển thị cảnh báo nổi bật.
3. Test unit + API với spec cố định (không gọi Gemini).
4. E2E: footage mẫu 15s trong `tests/fixtures/` (hoặc trích từ footage user, không commit media lớn —
   dùng script sinh clip test bằng ffmpeg từ file có sẵn).
5. Viết `docs/talking-head-autoedit.md`: kiến trúc 6 stage, bất biến chính xác, cách chạy CLI/UI,
   cách thêm SFX/BGM, cách đọc audit/verify, các bẫy đã biết (aselect, loop Audio im lặng, letterbox
   `@remotion/media`, key AQ vs AIza, thinking_level).
6. Cập nhật AGENT_GUIDE + ARCHITECTURE + README.

## Todo List
- [ ] stage verify (đo thật, không heuristic)
- [ ] nối verify vào render + trạng thái warning
- [ ] test unit + API không cần key
- [ ] e2e slow trên clip 15s
- [ ] docs/talking-head-autoedit.md
- [ ] cập nhật AGENT_GUIDE / ARCHITECTURE / README

## Success Criteria
- `make test-contracts` pass không cần API key.
- `make test-e2e` chạy trọn 6 stage trên clip mẫu và verify pass.
- Người mới đọc `docs/talking-head-autoedit.md` chạy được job đầu tiên mà không hỏi.
- Không còn phụ thuộc vào bất kỳ file nào trong thư mục temp scratchpad.

## Risk Assessment
| Rủi ro | Giảm thiểu |
|---|---|
| E2E chậm/flaky trên CI | Đánh dấu slow, chỉ chạy local; clip 15s; cache spine |
| Verify quá gắt → báo động giả | Ngưỡng lấy từ số đo thực của bản V5 đã duyệt, không đặt cảm tính |

## Security Considerations
- Tài liệu không chứa key thật; ví dụ dùng placeholder.

## Next Steps
- Cân nhắc (ngoài phạm vi, cần user quyết): nút gửi render lên Colab; batch nhiều video;
  gỡ/hợp nhất đường cũ `footage_edit_analyzer` sau khi luồng mới ổn định.
