# UX audit — talking-head autoedit UI

**Ngày:** 2026-08-27 (bản chụp một lần — không cập nhật số liệu bên dưới; xem ghi chú trạng thái ở
mỗi mục).  
**Surface:** Web UI (`make autoedit-dev`) + API `:8861`  
**Job thử:** `ux-audit-gemini-3-7-prepare-260827-021353` (project `ux-trial-clip-drive-26s-260816-044034`)

## Cách chạy audit

1. `curl /api/health` — API sống  
2. Tạo job prepare qua `POST /api/projects/{id}/jobs` với stages `probe…resolve` (không render)  
3. Poll `GET /api/jobs/{id}` đến khi `direct` + `audit` + `resolve` completed  
4. Kiểm tra wizard UI: mẫu dựng, chủ đề, yêu cầu AI, model hiển thị  

## Kết quả job thử (Gemini 3.7)

| Stage | Kết quả | Ghi chú |
|---|---|---|
| probe | ✅ | ~1s |
| transcribe | ✅ | Scribe cache / ASR |
| select | ✅ | 1 nguồn → no-op |
| direct | ✅ | `ag/gemini-3.7-flash-high` |
| audit | ✅ | cùng `ag/gemini-3.7-flash-high` (verifier để trống) |
| resolve | ✅ | ffmpeg encode ~4–5 phút clip 26s 1080p |

**Chất lượng audit:** caption coverage 100%, 0 caption >9 từ, 0 card (đúng brief “card chỉ khi cần”).

Model Gemini qua 9router **ổn định** cho Direct/Audit trên clip thử.

## Điểm UX (trước chỉnh)

| # | Vấn đề | Mức |
|---|---|---|
| U1 | `topic` / `prompt` ẩn trong “Tuỳ chỉnh” — user không biết gửi gì cho AI | P1 |
| U2 | Nav “Kết quả” trùng tab project “Kết quả” | P2 |
| U3 | 9 stage kỹ thuật không có lớp 3 bước dễ hiểu | P2 |
| U4 | Model mặc định không hiện trên wizard | P1 |
| U5 | Chỉ 1 edit style mẫu | P2 |

## Thay đổi đã áp dụng (2026-08-27)

- `.env`: `AUTOEDIT_DIRECTOR_MODEL=ag/gemini-3.7-flash-high`
- `GET /api/config` — UI đọc model mặc định + trạng thái gateway
- Wizard: đưa **Chủ đề** + **Yêu cầu AI** lên mặt chính; hiện model mặc định
- Verifier mặc định trong wizard: để trống (= cùng model director)
- 3 **mẫu dựng** (`config/edit-styles.json`): bán hàng, chia sẻ kiến thức, hot take
- Thanh tiến trình **3 phase** trên job detail
- Nav: “Tất cả bản dựng” / tab project “Bản dựng”

## Còn lại (backlog)

- Safe-zone overlay TikTok/Reels trên preview — vẫn chưa có (chưa thấy trong `remotion-composer/ui/src`).
- Gợi ý hook tự động từ transcript — vẫn chưa có.
- Gallery video mẫu thay vì chỉ text preset — vẫn chưa có, `style-select.tsx` vẫn chọn theo text.
- ~~Hiển thị `cost_usd` khi cấu hình `AUTOEDIT_PRICE_*`~~ — **đã xong**: `job-detail.tsx` hiện
  `chi phí: $...` khi job có `cost_usd`, cũng hiện ở `cloud-queue.tsx` và `prompt-editor.tsx`.

## Checklist LLM chuẩn hóa

- [x] Gateway 9router (`NINE_ROUTER_*`)
- [x] ASR ElevenLabs Scribe
- [x] Director + verifier (mặc định): `ag/gemini-3.7-flash-high` — verifier để trống = cùng model
- [x] Prompt registry v6 / captions v2 (structure v6 ưu tiên yêu cầu user) — **số bản đã cũ**, bản
      đang dùng nay cao hơn nhiều (xem `prompts/registry.json` hoặc
      [`talking-head-autoedit.md`](talking-head-autoedit.md#cấu-hình) thay vì tin số ở đây)
- [x] Edit styles theo thương hiệu
- [x] Giá token (`AUTOEDIT_PRICE_*`) để theo dõi chi phí batch — xong, xem mục backlog ở trên
