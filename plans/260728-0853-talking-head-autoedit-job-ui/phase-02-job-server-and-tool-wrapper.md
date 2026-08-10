# Phase 02 — Job Server + Tool Wrapper

## Context Links
- Plan: [plan.md](plan.md) · Trước: [phase-01](phase-01-rescue-and-productize-pipeline.md)
- Ràng buộc: `tools/tool_registry.py:118-134` (discover import mọi module dưới `tools/`)

## Overview
- **Priority:** P1 · **Status:** not started
- **Mô tả:** HTTP server local bọc job runner: tạo job, theo dõi tiến độ realtime, phục vụ media
  cho Player, và một BaseTool mỏng để agent cũng gọi được cùng luồng.

## Key Insights
- Không được đặt FastAPI trong `tools/` — registry import hết mọi module, preflight sẽ chậm và
  fail khi thiếu dep. → server ở `server/`, chỉ wrapper mỏng ở `tools/video/`.
- Progress không parse stdout: runner đã ghi `events.jsonl`, server chỉ tail file → tránh mất event
  khi UI reload, và job vẫn chạy được khi không có UI.
- Player cần HTTP range request cho `src.mp4` → dùng static mount của Starlette (hỗ trợ sẵn),
  không tự viết endpoint đọc file.

## Requirements
**Functional**
- `POST /api/jobs` — nhận `{input_path | upload, prompt, options{bgm, cold_open, tempo, model, style_profile}}` → `job_id`.
- `GET /api/jobs` — danh sách job + trạng thái + thời lượng + chi phí.
- `GET /api/jobs/{id}` — chi tiết: 6 stage (status/started/ended/cached/cost), artifact paths, audit_report.
- `GET /api/jobs/{id}/events` — **SSE**, stream event tiến độ (tail `events.jsonl`, có `Last-Event-ID`).
- `POST /api/jobs/{id}/render` — chạy S6 riêng (sau khi user duyệt preview).
- `POST /api/jobs/{id}/cancel` — kill process hiện tại, đánh dấu stage `cancelled`.
- `GET /api/resources` — whitelist SFX/BGM/style profile cho UI.
- Static mount `/jobs/*` → `remotion-composer/public/jobs/` (props, src.mp4, final.mp4).

**Non-functional**
- Hàng đợi FIFO, **1 job chạy tại một thời điểm** (tránh 2 ffmpeg/Remotion tranh CPU).
- Chỉ bind `127.0.0.1`. Không auth (local-only) nhưng chặn path traversal ở tham số `input_path`.
- Server chết → job đang chạy vẫn ghi được state; khởi động lại đọc lại từ `job.json`.

## Architecture
```
server/
├── app.py            # FastAPI app, CORS cho vite dev, static mount
├── api_jobs.py       # các route job
├── queue_worker.py   # hàng đợi FIFO + subprocess runner + cancel
└── sse.py            # tail events.jsonl → SSE
tools/video/talking_head_autoedit.py   # BaseTool mỏng: gọi lib.talking_head_edit.runner
```
`tools/video/talking_head_autoedit.py` chỉ import `lib.talking_head_edit.runner` bên trong
`execute()` (lazy import) để `registry.discover()` không kéo theo phụ thuộc nặng.

## Related Code Files
**Tạo:** `server/*.py`, `tools/video/talking_head_autoedit.py`, `tests/test_job_server_api.py`.
**Sửa:** `requirements.txt` (`fastapi`, `uvicorn[standard]`), `Makefile` (target `ui-server`),
`.gitignore` nếu cần.

## Implementation Steps
1. `queue_worker.py`: thread worker + `queue.Queue`; chạy runner trong subprocess
   (`python -m lib.talking_head_edit.cli --job <id>`) để cancel = kill process, không kẹt GIL.
2. `sse.py`: async generator tail `events.jsonl` theo offset; hỗ trợ `Last-Event-ID` để UI reload
   không mất lịch sử.
3. `api_jobs.py`: các route theo hợp đồng trên; validate `input_path` phải nằm trong thư mục cho phép
   (mặc định `~/Downloads`, `projects/`, cấu hình qua env `AUTOEDIT_INPUT_ROOTS`).
4. Upload file: ghi thẳng vào workspace job, giới hạn kích thước, chỉ nhận đuôi video hợp lệ.
5. `app.py`: mount static `/jobs`, bật CORS cho `http://localhost:5173` (vite dev).
6. BaseTool wrapper: `capability="video_post"`, `runtime=LOCAL`, `get_status()` kiểm tra ffmpeg +
   node + `GEMINI_API_KEY`; `estimate_cost()` gọi lại ước tính của S3.
7. Test API bằng `TestClient`: tạo job giả (stage stub), kiểm tra SSE trả đủ event, cancel đổi trạng thái.

## Todo List
- [ ] queue_worker (FIFO + cancel)
- [ ] sse tail + Last-Event-ID
- [ ] routes jobs/render/cancel/resources
- [ ] static mount + CORS
- [ ] BaseTool wrapper (lazy import)
- [ ] requirements + Makefile target
- [ ] test API

## Success Criteria
- `make ui-server` → tạo job qua curl, thấy event stage chảy realtime, `final.mp4` phát được qua
  `http://127.0.0.1:8000/jobs/<id>/final.mp4` (seek được — range request OK).
- `registry.discover()` vẫn chạy bình thường khi **chưa** cài fastapi (wrapper báo UNAVAILABLE, không crash).
- Cancel giữa chừng không để lại process ffmpeg mồ côi.

## Risk Assessment
| Rủi ro | Giảm thiểu |
|---|---|
| Wrapper kéo fastapi vào registry → preflight vỡ | Lazy import + test chạy `discover()` trong môi trường không có fastapi |
| Path traversal qua `input_path` | Whitelist thư mục gốc, resolve + kiểm tra prefix |
| SSE đứt khi job dài | Heartbeat 15s + UI tự reconnect theo `Last-Event-ID` |

## Security Considerations
- Bind 127.0.0.1; không mở ra LAN.
- Không trả nội dung `.env` hay key qua API; `audit_report` phải sạch key.

## Next Steps
→ Phase 03 dùng đúng hợp đồng API này.
