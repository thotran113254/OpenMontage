# Phase 03 — Web UI: Submit / Track / Preview

## Context Links
- Plan: [plan.md](plan.md) · Trước: [phase-02](phase-02-job-server-and-tool-wrapper.md)
- Component preview: `remotion-composer/src/mona/MonaTimeline.tsx`
- UI cũ để thay thế: `tools/editor/edit-decisions-editor.html`

## Overview
- **Priority:** P1 · **Status:** not started
- **Mô tả:** App web local: nạp footage + prompt → theo dõi 6 stage realtime → preview tức thì bằng
  Remotion Player (đúng component sẽ render) → chỉnh event → bấm Render.

## Key Insights
- `@remotion/player` đã có sẵn → preview dùng **chính** `MonaTimeline`, không có sai lệch giữa
  bản xem trước và bản render. Đây là điểm cốt lõi của "chuẩn xác".
- Job workspace nằm trong `remotion-composer/public/jobs/<id>/` nên `staticFile()` trong Player
  và trong Remotion CLI **trỏ cùng một chỗ** — không phải sửa component (xác minh lại khi code:
  nếu Player resolve `staticFile` khác, thêm prop `assetBase` thay vì hack path).
- Vite `publicDir` trỏ `../public` để Player thấy `sfx_*.mp3` / `bgm_*.mp3` / `jobs/*` như khi render.

## Requirements
**Functional**
1. **Trang New Job**: chọn file (drag-drop hoặc đường dẫn), ô prompt tiếng Việt (yêu cầu riêng cho
   video này), tuỳ chọn: BGM (bật/tắt + chọn track), cold-open, tempo, model director, style profile;
   hiện ước tính chi phí + thời lượng dự kiến; nút Submit.
2. **Trang Jobs**: bảng job (trạng thái, thời lượng, chi phí, thumbnail), lọc theo trạng thái.
3. **Trang Job Detail**:
   - Thanh 6 stage với trạng thái/thời gian/cache-hit, log stream (SSE), nút Cancel.
   - Panel **Audit**: liệt kê cut bị từ chối + lý do, SFX/BGM bị loại, guard đã tự sửa (keyword dời,
     caption gộp) — đây là thứ cho user tin được kết quả.
   - **Preview**: `<Player>` phát `MonaTimeline` + `props_vN.json`; timeline event bên cạnh
     (lọc theo loại: caption/keyword/card/sfx/punchIn...), click event → nhảy tới thời điểm đó.
   - **Sửa event trực tiếp**: đổi text/màu/vị trí/xoá event → Player cập nhật ngay (state local)
     → nút "Lưu bản chỉnh" ghi `props_vN+1.json` (không cần gọi Gemini).
   - Nút **Render MP4** → chạy S6, hiện % tiến độ, xong thì phát `final.mp4` ngay trong trang.
4. Nút "Mở thư mục job" và tải artifact.

**Non-functional**
- Chạy `npm run ui` (vite dev) + `make ui-server` là đủ; không cần build production.
- Mọi text UI tiếng Việt.
- File component < 200 dòng, tách theo màn hình.

## Architecture
```
remotion-composer/ui/
├── vite.config.ts        # root: ui, publicDir: ../public, proxy /api → 127.0.0.1:8000
├── index.html
└── src/
    ├── main.tsx
    ├── api/client.ts          # fetch + SSE hook
    ├── pages/new-job.tsx
    ├── pages/job-list.tsx
    ├── pages/job-detail.tsx
    ├── components/stage-progress.tsx
    ├── components/audit-panel.tsx
    ├── components/timeline-preview.tsx   # <Player> + transport
    ├── components/event-inspector.tsx    # danh sách + form sửa event
    └── components/log-stream.tsx
```

## Related Code Files
**Tạo:** cây `remotion-composer/ui/` ở trên.
**Sửa:** `remotion-composer/package.json` (devDep `vite`, `@vitejs/plugin-react`; script `ui`),
`Makefile` (target `ui`).
**Đọc:** `MonaTimeline.tsx` (kiểu `TimelineEvent` — UI dùng chung type, import trực tiếp, không copy).
**Thay thế:** `tools/editor/edit-decisions-editor.html` → giữ lại tới khi UI mới có đủ tính năng
export style profile (P04), sau đó đánh dấu deprecated trong README của nó.

## Implementation Steps
1. Dựng vite app tối thiểu, verify `<Player component={MonaTimeline} inputProps={props}/>` phát được
   `jobs/<id>/src.mp4` + nghe được sfx/bgm. **Đây là rủi ro kỹ thuật lớn nhất → làm trước tiên (spike).**
2. `api/client.ts` + hook `useJobEvents(jobId)` (EventSource + reconnect).
3. Trang New Job + validate + submit.
4. Trang Jobs (poll nhẹ 3s hoặc SSE tổng).
5. Job Detail: stage progress + log stream + cancel.
6. Audit panel (đọc `audit_report.json`).
7. Preview + event inspector + lưu bản chỉnh (`props_vN+1.json`).
8. Nút Render + theo dõi % + phát `final.mp4`.

## Todo List
- [ ] Spike Player phát được src.mp4 + audio trong vite dev
- [ ] api client + SSE hook
- [ ] New Job
- [ ] Jobs list
- [ ] Stage progress + log
- [ ] Audit panel
- [ ] Preview + event inspector + save props
- [ ] Render + phát kết quả

## Success Criteria
- Từ lúc mở UI đến khi thấy preview một video mới: chỉ thao tác trong UI, không gõ lệnh.
- Sửa 1 caption → thấy đổi trong Player **< 1s**, không render lại.
- Bản render cuối khớp đúng những gì Player hiển thị (đối chiếu 4 frame mẫu).
- Reload trang giữa lúc job chạy → tiến độ vẫn đúng (SSE resume).

## Risk Assessment
| Rủi ro | Giảm thiểu |
|---|---|
| `staticFile()` trong Player resolve khác với CLI → media 404 | Spike ở bước 1; nếu lệch, thêm prop `assetBase` vào MonaTimeline (thay đổi nhỏ, có kiểm soát) |
| Player kéo lag khi timeline nhiều event | Giới hạn preview ở scale 0.5, `acknowledgeRemotionLicense` + `compositionWidth` nhỏ |
| Vite import ngoài root (`../src/mona`) bị chặn | `server.fs.allow: ['..']` + alias |

## Security Considerations
- Dev server chỉ localhost; không mount thư mục ngoài `public/` và `src/`.

## Next Steps
→ Phase 04 gắn ô prompt "sửa" vào trang Job Detail.
