# Phase 09 — UI project workspace

## Context

- [plan.md](plan.md) · phase 04 (API project) · phase 05 (API prompt) · phase 07 (b-roll)
- UI hiện có: `remotion-composer/ui/` — Vite + React, `pages/{new-job,job-list,job-detail}.tsx`,
  `components/{audit-panel,event-inspector,log-stream,revise-box,stage-progress,timeline-preview}.tsx`,
  `api/client.ts`. Remotion Player preview ~1s đã hoạt động.

## Overview

- **Priority:** P3 — làm cuối, khi API đã đứng yên
- **Status:** ✅ code + 25 test endpoint. `npm run typecheck:ui` sạch, `vite build` sạch
  (63 module). Server chạy thật, đã kiểm end-to-end: tạo project → thêm 3 nguồn (2 take + 1 b-roll)
  → gợi ý take 85% → tạo bản dựng → probe phân loại đúng 2 aroll + 1 broll.

**Hai bug thật chỉ chạy thật mới thấy (test không bắt):**

1. `POST /sources/from-path` lấy `label` từ tên **trên đĩa** (`s0_take-1`) chứ không phải tên gốc.
   Hệ quả: dò nhóm take rút gọn về `"s"` (tiền tố `s0_`/`s1_` bị coi là nội dung) — vô nghĩa, và sẽ
   gộp bừa hai file bất kỳ. Đã sửa + có test.
2. `create_job` chỉ đưa nguồn **aroll** vào `input_paths`, nên **mọi b-roll bị loại khỏi bản dựng**
   (`broll_count 0`). `overlay_pool` luôn rỗng → tính năng b-roll của phase 07 không bao giờ chạy được
   từ UI. Đã sửa: đưa hết nguồn vào, aroll trước (để `primary_input_path` vẫn là footage có tiếng).

**Test cũ bị rò dữ liệu:** `test_talking_head_server_api` chỉ patch legacy root, nhưng `/api/jobs`
giờ gộp cả `projects/autoedit/*/jobs` → job thật của máy dev lọt vào assertion. Đã patch cả
`api_projects.store` trong fixture.

> **Đã làm xong ngoài plan (2026-08-04):** panel "Thử màu & tiếng trước khi cắt lại" —
> `server/api_previews.py` (3 endpoint + route phục vụ file preview), `look-preview.tsx` +
> `grade-preview.tsx` / `audio-preview.tsx` / `clip-preview.tsx`, `tests/test_talking_head_preview_api.py`
> (21 test). Đo thật: grade 4.0s (0.10s khi cache hit), audio 3.5s, clip 47.8s. Đừng làm lại.
>
> **Type gate cho UI đã có:** `ui/tsconfig.json` + `npm run typecheck:ui` + `make autoedit-ui-typecheck`
> + job `typecheck-ui` trong CI. Trước đó `ui/` chưa từng được kiểm type (tsconfig gốc chỉ có `src`,
> Vite dùng esbuild nên xoá type mà không kiểm). **Chạy nó sau mỗi component mới ở phase này** — đã
> kiểm bằng cách tiêm lỗi có chủ ý: gate trả exit 2 và chỉ đúng file/dòng.
- Thêm tầng Project vào UI: tạo project, upload nhiều nguồn (drag-drop), gán role/take group, timeline
  editor có transcript, tab Prompts, chat panel, nút Autopilot.

## Key insights

- **Đừng viết lại UI hiện tại.** `job-detail` + `timeline-preview` + `event-inspector` đang chạy tốt.
  Project là **tầng bọc ngoài**: thêm route `/projects`, `/projects/:id`, giữ `/jobs/:id` nguyên vẹn.
- **Timeline editor là phần đắt nhất và dễ over-engineer nhất.** Bản đầu chỉ cần: transcript
  word-level, click từ → seek player, chọn đoạn → đánh dấu cắt/giữ, hiện event trên track. **Không**
  làm kéo-thả multi-track kiểu Premiere ở bản đầu — YAGNI.
- **Transcript là giao diện chính, không phải timeline.** Hệ thống này text-first; người dùng nghĩ theo
  câu nói, không theo frame. Đặt transcript ở giữa, timeline là dải phụ bên dưới.
- **Upload phải hiện progress thật.** Video 200 MB không có progress = người dùng tưởng treo. Dùng
  `XMLHttpRequest.upload.onprogress` (fetch không có progress upload).
- **Tab Prompts phải hiện prompt ĐÃ RENDER, không chỉ template.** Xem template với `{{spine}}` không
  giúp được gì; thấy đúng 4000 từ sẽ gửi mới thấy vấn đề.

## Requirements

**Functional**
- `/projects` — danh sách project, thumbnail, số nguồn, số bản dựng.
- `/projects/new` — tạo project (title, assembly config, keyterms, defaults).
- `/projects/:id` — 4 tab:
  - **Sources**: drag-drop nhiều file + progress, thumbnail + duration + waveform mini, sửa
    role/order/take_group/label, xoá.
  - **Builds**: list job + status + link final.mp4 + nút so sánh 2 bản.
  - **Settings**: assembly config (mode, speaker_aware, broll_overlay, cross_source_cut), keyterms,
    defaults.
  - **Prompts**: chọn template → editor 2 cột (template | đã render) → lưu override → diff → A/B.
- `/jobs/:id` (giữ nguyên) + thêm: chat panel (thay `revise-box`), nút Autopilot, timeline editor tab,
  hiện `timeline_views` từ verify.
- Timeline editor: transcript word-level (click → seek), chọn đoạn → cắt/giữ, track event, track
  b-roll (chọn từ overlay_pool, đặt vào khoảng).

**Non-functional**
- Transcript 4000 từ render không lag (virtualize danh sách từ).
- Không thêm state library — `useState` + context nhỏ như hiện tại (KISS, UI này 1 người dùng).

## Architecture

```
ui/src/
├── pages/
│   ├── project-list.tsx        MỚI
│   ├── project-new.tsx         MỚI
│   ├── project-detail.tsx      MỚI (4 tab)
│   ├── job-list.tsx            giữ
│   ├── job-detail.tsx          + chat, autopilot, tab timeline
│   └── new-job.tsx             giữ (tạo job ad-hoc không cần project)
├── components/
│   ├── source-uploader.tsx     MỚI  drag-drop + XHR progress
│   ├── source-card.tsx         MỚI  thumbnail + sửa role/take_group
│   ├── assembly-settings.tsx   MỚI
│   ├── prompt-editor.tsx       MỚI  2 cột + diff + A/B
│   ├── transcript-editor.tsx   MỚI  word-level, virtualized
│   ├── chat-panel.tsx          MỚI  (thay revise-box, giữ file cũ đến khi xong)
│   ├── build-compare.tsx       MỚI
│   └── … (giữ nguyên phần còn lại)
└── api/client.ts               + projects, prompts, chat, autopilot
```

## Related code files

**Tạo:** 8 component/page ở trên
**Sửa:** `ui/src/api/client.ts`, `app.tsx` (route), `job-detail.tsx`, `styles.css`

## Implementation steps

1. `api/client.ts`: thêm client cho projects/prompts/chat/autopilot. Sinh type từ response thật (không
   đoán) — chạy API rồi copy shape.
2. `project-list` + `project-new` — đơn giản nhất, làm trước để có đường đi end-to-end.
3. `source-uploader`: drag-drop, XHR progress per file, hiện lỗi per file (1 file lỗi không huỷ cả lô).
4. `source-card`: thumbnail, duration, select role, input take_group, nút xoá (confirm).
5. `assembly-settings` — form theo schema `assembly`; hiện rõ giá trị nào đến từ global vs project
   (người dùng cần biết mình đang override cái gì).
6. `project-detail` 4 tab + tab Builds với `build-compare` (2 video cạnh nhau, dùng `<video>` thường,
   không cần Remotion Player cho bản đã render).
7. `prompt-editor`: 2 cột, textarea trái (template), pre phải (đã render với spine job đang chọn),
   nút Lưu override / Diff / A/B. A/B hiện bảng số liệu + chi phí dự kiến **trước** khi chạy.
8. `transcript-editor`: virtualize (window ~200 từ), click → `player.seekTo`, chọn đoạn (shift-click)
   → menu cắt/giữ → gọi `PUT /props` hoặc `POST /chat`.
9. `chat-panel`: lịch sử, ô nhập, hiện diff mỗi lượt, nút dry-run.
10. `job-detail`: nút Autopilot (hiện tiến trình + retry), hiện `timeline_views` (ảnh từ phase 06).
11. Xoá `revise-box.tsx` sau khi `chat-panel` chạy được.

## Todo

- [x] `api/client.ts` mở rộng (type chép từ response thật, + `GET /jobs/{id}/spine` mới)
- [x] `project-list` + `project-new`
- [x] `source-uploader` drag-drop + XHR progress + lỗi per file (một file lỗi không huỷ cả lô)
- [x] `source-card` sửa role/take_group/label/order, xoá có confirm + đường force khi 409
- [x] `assembly-settings` + hiện nguồn giá trị (mặc định chung / project này)
- [x] `project-detail` 4 tab (Nguồn / Bản dựng / Cấu hình / Prompt)
- [x] `build-compare` (`<video>` thường, không cần Remotion Player cho file đã render)
- [x] `prompt-editor` 2 cột + diff + A/B + chi phí dự kiến trước khi chạy
- [x] `transcript-editor` virtualized (window ~row) + click-to-seek + shift-chọn đoạn
- [x] `chat-panel` + dry-run + hiện lịch sử để thấy model đang có ngữ cảnh gì
- [x] `job-detail`: nút Autopilot + hiện `timeline_views` + transcript editor
- [ ] **Xoá `revise-box.tsx`** — CHƯA xoá: `remotion-composer/ui/` chưa được git theo dõi, nên xoá
      là không lấy lại được. File đã không còn được import ở đâu; xoá tay khi bạn thấy ổn.

## Success criteria

- Tạo project → upload 3 file (có progress) → gán role → tạo job → autopilot → xem final.mp4, **không
  rời UI lần nào**.
- Transcript 4000 từ scroll mượt (không lag khi kéo).
- Sửa prompt trong UI → chạy lại `direct` → thấy kết quả khác.
- A/B trong UI hiện bảng so sánh + chi phí trước khi chạy.
- Job cũ (legacy, không có project) vẫn mở được ở `/jobs/:id`.

## Risks

| Rủi ro | Xử lý |
|---|---|
| Timeline editor phình thành NLE | Bản đầu chỉ 4 việc (seek, chọn, cắt/giữ, xem event). Thêm gì phải có lý do từ việc dùng thật |
| Upload đứt giữa không rõ lỗi | Lỗi per file, retry per file, `.part` dọn ở server |
| Transcript 4000 từ lag | Virtualize từ đầu, không "tối ưu sau" |
| Sửa prompt trong UI làm vỡ job đang chạy | Override chỉ ảnh hưởng lần `direct` sau; job đang chạy giữ prompt đã dùng (đã có `prompt_versions`) |
| UI và API lệch schema | Type sinh từ response thật; test smoke gọi từng endpoint sau mỗi phase |

## Security

- Server chỉ localhost. Không thêm auth (một người dùng, một máy) — nhưng **phải ghi rõ trong docs**
  rằng mở ra LAN là không an toàn (upload file + đọc đường dẫn đĩa).
- `source-uploader` kiểm extension client-side chỉ để UX; server vẫn là nơi kiểm thật.

## Next

Xong phase 09 là hết plan. Việc tiếp theo đáng làm (chưa nằm trong plan này): xuất EDL cho Premiere/
DaVinci để người dùng tinh chỉnh tay sau khi AI dựng thô.
