# Phase 04 — Prompt-Revise (vá spec) + Version History

## Context Links
- Plan: [plan.md](plan.md) · Trước: [phase-03](phase-03-web-ui-submit-track-preview.md)
- Style profile hiện có: `styles/user-edit-profile.json`

## Overview
- **Priority:** P1 · **Status:** not started
- **Mô tả:** Cho phép user gõ yêu cầu sửa bằng tiếng Việt ("bỏ card 2", "thêm keyword ở đoạn nói về
  chi phí", "đừng cắt câu này") → Gemini **vá** spec hiện có thay vì dựng lại, sinh version mới,
  so sánh và rollback được.

## Key Insights
- Vá rẻ hơn dựng lại nhiều lần: giữ nguyên phần user đã duyệt, chỉ đổi phần được yêu cầu → tránh
  hiện tượng "sửa 1 chỗ hỏng 3 chỗ" đã gặp ở các vòng v1→v5.
- Bản vá vẫn phải đi qua **cùng stage audit** như bản gốc — không có đường tắt cho hallucination.
- Neo theo chỉ số từ giúp diff có nghĩa: so 2 spec là so tập event theo word-index, không phải so giây.

## Requirements
**Functional**
- `POST /api/jobs/{id}/revise {instruction}` → stage `revise` chạy: gửi Gemini
  `(word spine rút gọn + spec_vN + instruction)` → **JSON Patch-like** `{add[], remove[], modify[]}`
  → áp vào spec → `spec_vN+1.json` → audit → resolve → props mới.
- Vá KHÔNG chạy lại S2 (transcribe) và không upload lại video (tiết kiệm token + tiền).
- Version history: danh sách vN với instruction, thời gian, chi phí, số event thay đổi.
- Diff view: event thêm/xoá/sửa giữa 2 version (theo word-index + loại event).
- Rollback: chọn vN làm bản hiện hành (props + spec), render lại từ đó.
- "Học phong cách": nút export bản đang duyệt thành `styles/user-edit-profile.json` (thay chức năng
  của `tools/editor/edit-decisions-editor.html`), lần chạy sau nạp làm ràng buộc prompt.

**Non-functional**
- Chi phí mỗi lần vá phải < chi phí dựng lại; hiển thị số token/chi phí thực từng lần.
- Patch không hợp lệ (tham chiếu word index không tồn tại, loại event lạ) → từ chối, giữ nguyên vN,
  báo lỗi rõ theo Escalate Blockers.

## Architecture
```
lib/talking_head_edit/stages/revise.py     # gọi Gemini ở chế độ patch
lib/talking_head_edit/spec_patch.py        # áp patch + validate + diff 2 spec
server/api_jobs.py                          # thêm route /revise, /versions, /rollback
remotion-composer/ui/src/components/revise-box.tsx
remotion-composer/ui/src/components/version-history.tsx
```

## Related Code Files
**Tạo:** `stages/revise.py`, `spec_patch.py`, 2 component UI, `tests/test_spec_patch.py`.
**Sửa:** `server/api_jobs.py`, `pages/job-detail.tsx`, `job_store.py` (version index).

## Implementation Steps
1. Định nghĩa định dạng patch tối giản (không dùng RFC6902 vì event không có id ổn định):
   `{"remove":[{"type","w0"}...], "add":[<event>...], "modify":[{"match":{"type","w0"},"set":{...}}]}`.
   → `spec_patch.py` khớp event theo `(type, w0)`, báo lỗi nếu khớp 0 hoặc >1.
2. Prompt vá: đưa spec hiện tại ở dạng rút gọn (bỏ field mặc định) + spine chỉ quanh vùng liên quan
   nếu instruction có định vị nội dung → giảm token.
3. Áp patch → audit → resolve → props. Ghi `versions.json` (vN, instruction, cost, changed_counts).
4. Diff + rollback API.
5. UI: ô "Yêu cầu sửa" dưới preview, hiện danh sách version, nút xem diff / dùng bản này.
6. Export style profile từ bản đang duyệt (thống kê: tỉ lệ zoom, số card, dùng sfx nào, độ dài caption).

## Todo List
- [ ] spec_patch + test (khớp mơ hồ, index sai, event lạ)
- [ ] stage revise + prompt vá
- [ ] versions.json + API versions/rollback
- [ ] UI revise box + version history + diff
- [ ] export style profile

## Success Criteria
- "bỏ card thứ 2" → spec mới mất đúng card đó, mọi event khác **không đổi** (diff chứng minh).
- Một lần vá tốn < 40% chi phí dựng lại (đo bằng token thực).
- Patch tham chiếu sai → job không hỏng, vN vẫn là bản hiện hành.
- Rollback về v2 rồi render ra đúng bản v2.

## Risk Assessment
| Rủi ro | Giảm thiểu |
|---|---|
| Gemini trả patch mơ hồ (khớp nhiều event) | Bắt buộc `w0`; khớp >1 → từ chối kèm gợi ý; UI cho chọn thủ công |
| Vá dồn nhiều lần làm spec lệch ý ban đầu | Version history + diff + nút "dựng lại từ đầu" |
| Instruction đòi cắt nội dung | Audit vẫn chạy → cut nội dung bị từ chối; UI báo "yêu cầu bị chặn bởi audit" kèm lý do |

## Security Considerations
- Instruction của user đi thẳng vào prompt → giới hạn độ dài, escape, không cho ghi đè phần
  ràng buộc whitelist trong prompt hệ thống.

## Next Steps
→ Phase 05 khoá chất lượng và viết tài liệu.
