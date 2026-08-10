# Phase 02 — Panel "Da": slider + chọn frame + preview real-time

## Context Links
- Plan: [plan.md](plan.md)
- Component để copy pattern: `remotion-composer/ui/src/components/grade-preview.tsx:16-149`
- Tab container: `remotion-composer/ui/src/components/look-preview.tsx:6-12` (`TABS`), `:46-56`
- API client đã có sẵn (KHÔNG cần sửa ở phase này): `ui/src/api/client.ts:400-405`
  (`previewGrade`), type `GradePreview` `:144-152`
- Filter thật sự chạy: `lib/talking_head_edit/resolve_media.py:222-223,241-245,258-265`

## Overview
- **Priority**: P2
- **Status**: completed
- **Effort**: 3h
- Thêm tab "Da" cạnh Màu/Tiếng/Clip. Hai slider (`skin_smooth`, `blemish_reduce`), một slider
  chọn giây trong footage gốc, ảnh preview so sánh trước/sau. **Zero code backend.**

## Key Insights
1. **Endpoint đã đủ.** `POST /api/jobs/{id}/preview/grade` nhận `grade` là dict tuỳ ý
   (`api_previews.py:127-130`) rồi merge lên grade hiện tại (`_current_grade` :77-94). Gửi
   `{skin_smooth: 0.3}` là chạy ngay hôm nay. `api.previewGrade` đã tồn tại
   (`client.ts:400-405`) và type body đã là `Record<string, number>` ⇒ không sửa `client.ts`.
2. **Response luôn trả 2-3 biến thể**: `hien_tai` (grade đang dùng) và `thu_nghiem` (khi có
   `grade`) — `api_previews.py:125-130`. Tức là so sánh trước/sau có sẵn, chỉ cần render 2 `<img>`.
3. **Cache theo từng biến thể** (`preview.py:205-234`): kéo slider chỉ dựng lại `thu_nghiem`,
   `hien_tai` dùng lại ảnh cũ ⇒ mỗi lần kéo tốn ~1 ffmpeg call, không phải 2.
4. **"Real-time" phải là debounce, không phải oninput.** Mỗi preview là 1 process ffmpeg
   (`preview.py:140-141`) ~1s. Bind trực tiếp vào `onChange` của slider sẽ spawn hàng chục ffmpeg.
   ⇒ debounce 400ms + huỷ kết quả cũ (guard bằng request id).
5. **Slider giây chạy trên footage GỐC, không phải timeline đã cắt.** `_source_of` dùng
   `primary_input_path` (`api_previews.py:66-74`) = file raw. `<Player>` phát `src.mp4` đã cắt
   (`job_store.py:161-162`) ⇒ hai trục thời gian KHÁC nhau. Không nối 2 thứ này.
   Nhãn UI phải nói rõ "giây trong footage gốc" (giống `grade-preview.tsx:81`).
6. **Độ dài footage có sẵn ở client**: `probe.duration_seconds` (ghi ở
   `stages/probe.py:100`), `get_job` trả `{**state, …}` (`api_jobs.py:143-151`) nên `probe` có
   trong response, UI type đã khai `probe?: Record<string, unknown>` (`client.ts:33`).
   Không có `probe` (chưa chạy stage) ⇒ ẩn slider giây, fallback ô nhập số + `at: null`
   (server tự lấy giữa video, `_clamp_at` `api_previews.py:106-110`).
7. **Không đánh giá độ nét trên ảnh tĩnh** — `grade_still` docstring (`preview.py:132-136`) nói
   rõ still bỏ qua 2 pass x264. Panel này chỉ nói về da/mịn, phải in cảnh báo tương tự.

## Requirements
**Functional**
- Tab "Da" trong `LookPreview`, hint "~1s mỗi lần".
- Slider `skin_smooth` 0→1 step 0.01, `blemish_reduce` 0→1 step 0.01; hiện số bên cạnh.
- Giá trị khởi tạo = giá trị đang dùng của job: `options.grade_overrides.skin_smooth` nếu có,
  không thì để trống và lấy từ `report.base_grade` sau lần preview đầu.
- Slider giây 0→`probe.duration_seconds`, step 0.5; nút "◀ 1s" / "1s ▶" để nhích chính xác.
- Preview tự chạy sau khi ngừng kéo 400ms; có nút "Xem lại" để buộc chạy.
- Hiện 2 ảnh cạnh nhau: `hien_tai` vs `thu_nghiem`, kèm số `luma` / `warm_bias` vùng mặt
  (`variant.face`, đã có trong response).
- Nút "Áp dụng & cắt lại" → `api.runStages(jobId, {stages:["resolve"], use_cache:false,
  options:{grade_overrides:{...currentOverrides, skin_smooth, blemish_reduce}}})` — copy nguyên
  pattern `grade-preview.tsx:62-66`.
- Chỉ gửi khoá người dùng đã chạm (dirty flag), KHÔNG gửi `blemish_reduce: 0` nếu chưa ai kéo nó.

**Non-functional**
- Component < 200 dòng. Không thêm npm dependency (dùng `<input type="range">` thuần).
- Chỉ dùng class CSS đã có: `.card`, `.row`, `.field`, `.muted`, `.small`, `.error-text`,
  `.preview-grid`, `.preview-tile`, `.badge`.
- `make autoedit-ui-typecheck` phải sạch.

## Architecture
```
look-preview.tsx        TABS += {id:"skin", label:"Da", hint:"~1s mỗi lần"}
   │                    render <SkinPreviewPanel jobId options busy onApplied/>
   ▼
skin-preview.tsx
   state: skinSmooth, blemishReduce, touched:Set, at, report, loading, error
   useEffect([skinSmooth, blemishReduce, at]) → debounce 400ms → run()
   run(): api.previewGrade(jobId, {at, grade: trialGrade()})   ← reqId guard
   apply(): api.runStages(...)  → onApplied()
```
Không state toàn cục, không context — đúng convention hiện tại (mọi component dùng `useState`
cục bộ, parent `job-detail.tsx:15` giữ `job`).

## Related Code Files
**Create**
- `remotion-composer/ui/src/components/skin-preview.tsx`

**Modify**
- `remotion-composer/ui/src/components/look-preview.tsx` — thêm `"skin"` vào type `Tab` (:6),
  entry vào `TABS` (:8-12), nhánh render (:46-56). Truyền `options` sẵn có, `overrides` đã tính ở :30.

**Delete**: không. **KHÔNG sửa** `grade-preview.tsx` (giữ ô JSON cho các khoá còn lại) và
**KHÔNG sửa** `client.ts` ở phase này.

## Implementation Steps
1. `look-preview.tsx`: `type Tab = "skin" | "grade" | "audio" | "clip"`, thêm entry TABS, đặt
   "Da" ngay sau "Màu". Cân nhắc đặt default tab vẫn là `"grade"` (không đổi hành vi cũ).
2. Tạo `skin-preview.tsx` với docstring giải thích: vì sao slider chứ không JSON, vì sao debounce,
   vì sao giây là footage gốc (KHÔNG nhắc số phase/plan).
3. Khởi tạo giá trị từ `currentOverrides.skin_smooth` / `.blemish_reduce`, `touched` rỗng.
4. `trialGrade()`: chỉ gồm khoá trong `touched`; trả `null` khi `touched` rỗng.
5. `run()` với `reqId` tăng dần; bỏ response nếu `reqId` không còn là mới nhất.
6. `useEffect` debounce 400ms; cleanup `clearTimeout`. Không chạy khi `touched` rỗng và chưa có
   report (tránh gọi ffmpeg ngay khi mở tab) — hoặc gọi 1 lần để lấy `base_grade`. Chọn: chạy 1
   lần khi mở tab, `grade` = undefined ⇒ chỉ dựng `raw` + `hien_tai` (đã cache sẵn từ tab Màu).
7. Slider giây: `max = Number(job.probe?.duration_seconds ?? 0)`; nếu 0 ⇒ fallback ô nhập text.
8. Render `preview-grid` với `report.variants`, lọc/ưu tiên `hien_tai` + `thu_nghiem`.
9. Nút áp dụng: disable khi `busy || loading || touched.size === 0`.
10. Chạy `make autoedit-ui-typecheck`.
11. Kiểm tay: `make ui-server` + `make autoedit-ui`, mở 1 job đã probe, kéo slider, xem ảnh đổi.

## Todo List
- [x] `look-preview.tsx`: type `Tab`, `TABS`, nhánh render
- [x] `skin-preview.tsx`: state + 2 slider + hiển thị số
- [x] Slider giây theo `probe.duration_seconds` + nút nhích ±1s + fallback khi chưa probe
- [x] Debounce 400ms + `reqId` guard (bỏ response cũ)
- [x] Chỉ gửi khoá `touched`
- [x] Grid ảnh `hien_tai` vs `thu_nghiem` + số vùng mặt
- [x] Nút "Áp dụng & cắt lại" (pattern `grade-preview.tsx:62-66`)
- [x] Cảnh báo "không đánh giá độ nét trên ảnh tĩnh"
- [x] `make autoedit-ui-typecheck` sạch (`npm run typecheck:ui`, không lỗi)
- [ ] Kiểm tay trên 1 job thật — chưa chạy, cần mở UI + job thật

## Test Matrix
| Loại | Ca |
|---|---|
| Typecheck | `make autoedit-ui-typecheck` không lỗi |
| Manual | Mở tab Da trên job đã probe ⇒ thấy 2 ảnh, không lỗi |
| Manual | Kéo `skin_smooth` 0→0.6 ⇒ ảnh `thu_nghiem` rõ ràng mịn hơn `hien_tai` |
| Manual | Kéo nhanh liên tục 10 lần ⇒ chỉ 1-2 request (kiểm Network tab), không nhảy ảnh cũ |
| Manual | Kéo slider giây tới đoạn có người ⇒ ảnh đổi đúng thời điểm |
| Manual | Job CHƯA chạy probe ⇒ không crash, slider giây ẩn, preview vẫn ra (giữa video) |
| Manual | Bấm "Áp dụng & cắt lại" ⇒ `job.options.grade_overrides` có `skin_smooth`, resolve chạy lại |
| Manual | Không chạm slider nào ⇒ nút áp dụng disabled |

## Success Criteria
- User kéo 1 slider và thấy ảnh khác trong ~1-2s, không phải sửa JSON.
- Ảnh preview đổi đúng theo giây user chọn.
- Áp dụng ghi đúng `grade_overrides` và làm resolve chạy lại (cache invalidate qua
  `runner.py:130` đã bao `grade_overrides` — không cần thêm gì).
- Không có npm dependency mới; typecheck sạch.

## Risk Assessment
| Risk | L×I | Mitigation |
|---|---|---|
| Slider bind trực tiếp → hàng chục process ffmpeg, máy treo | Cao×Cao | Debounce 400ms + `reqId` guard, có ca test manual đếm request |
| User tưởng giây trên slider = giây trên `<Player>` | Cao×Trung | Nhãn "giây trong **footage gốc**" + 1 dòng `.muted small` giải thích timeline đã cắt |
| Ảnh preview không phản ánh render (còn 2 pass x264) | Trung×Trung | In cảnh báo giống `grade-preview.tsx:127-130`; nút "Clip duyệt" đã có cho ai cần bản thật |
| `probe.duration_seconds` thiếu ⇒ `max=0`, slider chết | Trung×Trung | Fallback ô nhập số + `at: null`; có ca test |
| Job nhiều nguồn: chỉ preview source #1 | Trung×Thấp | Ghi rõ trong `.muted small`; xem unresolved Q3 ở plan.md |
| Cache ảnh phình job dir | Thấp×Thấp | Đã có `_trim_grade_cache` giữ 60 frame (`preview.py:202,237-250`) |

## Security Considerations
- Không thêm endpoint, không nhận path từ client ⇒ bề mặt không đổi.
- Giá trị slider là `number` clamp 0-1 ở UI, và `build_grade_chain` tự clamp lại
  (`resolve_media.py:222-223` `float(... or 0)`) ⇒ không có đường inject filter string.
- Ảnh preview lấy qua route đã chặn separator (`api_previews.py:198-206`).

## Next Steps
- Phase 03 gắn dropdown preset + nút lưu vào chính component này.
- Không phụ thuộc Phase 01 ⇒ làm song song được.
