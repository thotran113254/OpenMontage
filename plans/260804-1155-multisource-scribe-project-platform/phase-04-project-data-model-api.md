# Phase 04 — Project data model + API

## Context

- [plan.md](plan.md) · [phase-02](phase-02-multi-source-spine-assembly.md)
- Hiện tại: `job_store.py:27` `JOBS_ROOT = projects/autoedit-jobs/`, một job = một thư mục, không có
  gì bọc ngoài. `server/api_jobs.py` có 15 endpoint quanh job.

## Overview

- **Priority:** P1 — chặn 07/08/09
- **Status:** ✅ code + 65 test (39 store + 26 API).

**Lệch có ý thức so với plan:** plan viết `--input` tạo "project ẩn `_adhoc/`". Không làm — nó *đổi*
nơi job nằm (từ `projects/autoedit-jobs/` sang `projects/autoedit/_adhoc-*/jobs/`), tức phá đúng cái
mà chính nó nói là để bảo vệ ("không phá luồng CLI hiện tại"). Thay vào đó: `--input` giữ nguyên
legacy root; `--project <id>` tạo bản dựng trong project. `--projects` liệt kê project.
- Thêm tầng Project: nguồn upload một lần, dùng cho nhiều bản dựng. Transcript cache dùng chung. Job
  cũ vẫn đọc được, **không migrate file**.

## Key insights

- **Không có database.** Giữ nguyên triết lý hiện tại (một thư mục = một thực thể, `project.json` +
  `job.json`). Thêm DB ở đây là chi phí không đổi lấy gì — mỗi máy dev chỉ có vài chục project.
- **Nguồn thuộc Project, không thuộc Job.** Đó là điểm khác biệt duy nhất đáng làm: hiện upload lại
  file mỗi lần dựng bản mới, và transcript cache tuy chung theo sha nhưng file thì trùng lặp.
- **Legacy job không migrate.** `JobStore.list()` đọc cả 2 root. Migrate file = rủi ro mất dữ liệu để
  đổi lấy sự gọn gàng — không đáng.
- **Upload nhiều file cần streaming.** `api_jobs.py:86` `upload_job` dùng `UploadFile` một file. Video
  vài trăm MB × 5 file cần ghi theo chunk, không `await file.read()` cả file vào RAM.
- Project là nơi tự nhiên để chứa `assembly` config (tầng 2) và `keyterms` — cùng buổi quay thì cùng
  thuật ngữ, cùng kiểu ghép.

## Requirements

**Functional**
- CRUD project: tạo, list, xem, xoá (xoá project = xoá cả jobs bên trong, phải hỏi lại).
- Upload nhiều nguồn vào project, streaming theo chunk, có progress.
- Sửa metadata nguồn: `role`, `order`, `take_group`, nhãn.
- Xoá nguồn (chặn nếu đang có job dùng nó → cảnh báo, cho xoá kèm cảnh báo).
- Tạo job **trong** project, kế thừa `assembly` + `keyterms` + defaults của project.
- So sánh các bản dựng của cùng project (list job + trạng thái + link `final.mp4`).
- `GET /api/projects/{id}/sources/{sid}/thumb` — thumbnail + duration cho UI.

**Non-functional**
- Upload 500 MB không làm server chiếm >200 MB RAM.
- `GET /api/projects` < 200ms với 50 project (đọc `project.json`, không quét sources).

## Architecture

```
projects/autoedit/<project_id>/
├── project.json
├── sources/
│   ├── s0_take-1.mp4
│   ├── s0.transcript.json        per-source spine (phase 01/02)
│   └── s0.thumb.jpg
└── jobs/<job_id>/                layout job hiện tại — KHÔNG đổi

projects/autoedit-jobs/<job_id>/  legacy, đọc được, không ghi mới
```

```jsonc
// project.json
{"project_id":"chatbot-ai-260804-1155","title":"3 sai lầm chatbot AI",
 "created_at":1754300000,
 "sources":[{"id":"s0","file":"sources/s0_take-1.mp4","sha256":"…","duration":93.2,
             "role":"aroll","order":0,"take_group":"main","speech":true,
             "label":"take 1","transcript":"sources/s0.transcript.json"}],
 "assembly":{"mode":"auto","speaker_aware":"auto"},
 "keyterms":["chatbot","CRM","Zalo OA"],
 "defaults":{"topic":"…","card_plan":"…","brand_pill":"…","frame_preset":"dark"},
 "jobs":["chatbot-ai-260804-1200"]}
```

API mới (thêm vào `server/api_projects.py`, không nhồi vào `api_jobs.py` — file đó đã 284 dòng):
```
GET    /api/projects
POST   /api/projects                    {title, assembly?, keyterms?, defaults?}
GET    /api/projects/{id}
DELETE /api/projects/{id}               ?confirm=true
POST   /api/projects/{id}/sources       multipart, nhiều file, streaming
PATCH  /api/projects/{id}/sources/{sid} {role?, order?, take_group?, label?}
DELETE /api/projects/{id}/sources/{sid}
GET    /api/projects/{id}/sources/{sid}/thumb
POST   /api/projects/{id}/jobs          {options?} → tạo job kế thừa project
GET    /api/projects/{id}/jobs          list + status + final.mp4 có/không
```

## Related code files

**Tạo**
- `lib/talking_head_edit/project_store.py` — `ProjectStore`, `Project` (đối xứng `JobStore`/`Job`)
- `server/api_projects.py`
- `tests/test_talking_head_project_store.py`, `tests/test_talking_head_project_api.py`

**Sửa**
- `lib/talking_head_edit/job_store.py` — `JobStore(root)` nhận root tuỳ ý; `list()` gộp 2 root; thêm
  `project_id` vào job state
- `server/app.py` — include router mới
- `lib/talking_head_edit/cli.py` — `--project <id>` để tạo job trong project; `--input` vẫn chạy độc
  lập (tạo project ẩn `_adhoc/`) để không phá luồng CLI hiện tại
- `lib/talking_head_edit/assembly_config.py` — đọc tầng project
- `Makefile` — không đổi

## Implementation steps

1. `project_store.py`: `create/get/list/delete`, `add_source` (streaming ghi chunk 1 MB, tính sha256
   trong lúc ghi — một lần đọc), `update_source`, `remove_source`, `next_source_id`.
2. Thumbnail: ffmpeg 1 frame ở giây 1 → `s0.thumb.jpg`, sinh lúc add source (rẻ, ~0.2s).
3. `JobStore` nhận `root` = `projects/autoedit/<pid>/jobs`. `list()` gộp cả legacy root, mỗi job ghi
   `project_id` (legacy → `None`).
4. `api_projects.py` với `_check_input_path` tái dùng từ `api_jobs.py` (tách ra
   `server/paths_guard.py` để không import chéo).
5. `POST /sources`: nhận `list[UploadFile]`, ghi streaming, auto-detect `role` qua `sources.py`
   (phase 02), trả về source spec để UI hiện ngay.
6. `POST /projects/{id}/jobs`: merge `project.defaults` + `project.assembly` + `keyterms` vào
   `job.options`; `input_paths` = các source `role=aroll` theo `order`; `overlay_pool` = `role=broll`.
7. `DELETE /projects/{id}` đòi `?confirm=true`; xoá nguồn đang dùng → 409 + tên job đang dùng, cho
   `?force=true`.
8. CLI `--project`. Không có `--project` → project ẩn `_adhoc-<stamp>` để CLI hiện tại không đổi hành vi.
9. Test: streaming upload (file 50 MB giả), sha đúng, thumbnail có, list gộp 2 root, job kế thừa
   config, guard xoá nguồn đang dùng, path guard chặn ngoài root.

## Todo

- [x] `project_store.py` + streaming add_source + sha một lần đọc (+ `project_uploads.py` tách riêng)
- [x] Thumbnail lúc add source
- [x] `find_job`/`list_all_jobs` tra cả 2 root + `project_id` vào job state
- [x] `server/paths_guard.py` tách ra (dùng `is_relative_to`, không so prefix chuỗi)
- [x] `api_projects.py` 12 endpoint
- [x] Kế thừa config khi tạo job (assembly **merge** chứ không thay thế)
- [x] Guard xoá (confirm / force) + 409 kèm tên job đang dùng
- [x] CLI `--project` + `--projects` (không dùng `_adhoc` — xem "Lệch có ý thức" ở trên)
- [x] Test (65 case)
- [ ] **Đo thật: upload 3 file 200 MB, RAM server, `GET /api/projects` với 50 project < 200ms** —
      cần file lớn thật; code đã streaming 1 MB/chunk và `list()` không quét `sources/`

## Success criteria

- Upload 3 file 200 MB: RAM server không vượt +200 MB, sha đúng, thumbnail hiện.
- Tạo 2 job từ cùng project: nguồn không nhân đôi trên đĩa, transcript chỉ chạy 1 lần.
- Job legacy hiện trong `GET /api/jobs` và chạy tiếp được.
- Xoá nguồn đang dùng → 409 kèm tên job.
- `GET /api/projects` với 50 project < 200ms.

## Risks

| Rủi ro | Xử lý |
|---|---|
| List gộp 2 root gây trùng id | Prefix legacy khi trả về: `legacy/<job_id>`; test có case này |
| Upload đứt giữa → file rác | Ghi `.part` rồi `os.replace`; dọn `.part` cũ khi khởi động server |
| Xoá project mất dữ liệu ngoài ý muốn | Đòi `?confirm=true`; log đường dẫn đã xoá |
| `api_jobs.py` phình khi thêm chức năng | Router riêng ngay từ đầu; giữ mỗi file < 300 dòng |

## Security

- Server chỉ bind `127.0.0.1` (đã ghi trong `server/app.py:4`) — giữ nguyên, không mở LAN.
- Upload: kiểm extension + ffprobe được mới nhận. Không tin `filename` từ client (slugify, chặn `..`).
- `AUTOEDIT_INPUT_ROOTS` áp cho đường dẫn nạp từ đĩa; file upload luôn vào `sources/`.

## Next

Phase 09 dựng UI trên các endpoint này. Phase 07 dùng `overlay_pool` từ project.
