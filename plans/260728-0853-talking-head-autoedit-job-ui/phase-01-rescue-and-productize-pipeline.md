# Phase 01 — Rescue + Productize the Word-Anchored Pipeline

## Context Links
- Plan: [plan.md](plan.md)
- Nguồn cần cứu: `%TEMP%/claude/D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT/2e50444e-ceec-4f33-83b7-e7ab1620d735/scratchpad/`
- Component đích: `remotion-composer/src/mona/MonaTimeline.tsx`
- Journal liên quan: `docs/journals/260709-gemini-video-analysis-and-ugc-style-implementation.md`

## Overview
- **Priority:** P0 (chặn mọi phase sau; nguồn đang nằm trên thư mục temp có thể bị xoá)
- **Status:** not started
- **Mô tả:** Đưa pipeline MONA V5 (word-anchored) vào repo dưới dạng package Python 6 stage,
  có job workspace, cache theo hash, và CLI chạy được độc lập không cần server/UI.

## Key Insights
- Timestamp của Gemini trôi ~±0.6s và làm tròn về giây → **word-index anchoring là lý do duy nhất
  luồng này chính xác**. Mọi refactor phải giữ nguyên tính chất đó.
- `aselect` của build ffmpeg hiện tại **im lặng pass toàn bộ audio frame** → phải dùng
  `atrim`-per-span + `concat`, kèm assert lệch A/V < 0.35s. Đây là bug đã tốn nhiều giờ, không
  được "dọn dẹp" mất.
- Audit máy cho cut là bắt buộc: prompt golden-rule một mình không chặn được cut nội dung.
- Scratchpad script hardcode `REPO`/`RAW`/`SP` bằng đường dẫn tuyệt đối và ghi thẳng vào
  `remotion-composer/public/` với tên file cố định → không chạy song song 2 video được. Job
  workspace theo `job_id` là thay đổi bắt buộc.

## Requirements
**Functional**
- Chạy được: `python -m lib.talking_head_edit.cli --input <mp4> --prompt "..." [--stage-from resolve]`
- 6 stage tách rời, mỗi stage đọc/ghi artifact JSON có schema, chạy lại được độc lập.
- Cache: stage bỏ qua khi input hash không đổi (`--no-cache` để ép chạy lại).
- Job workspace: `remotion-composer/public/jobs/<job_id>/` chứa `job.json`, `spine.json`,
  `spec_vN.json`, `audit_report.json`, `props_vN.json`, `src.mp4`, `final.mp4`, `logs/<stage>.log`.
- Ghi progress dạng JSONL (`events.jsonl`) để P02 stream lại không cần parse stdout.

**Non-functional**
- Không import nặng khi `registry.discover()` chạy (không đặt code này trong `tools/`).
- Mỗi file < 200 dòng; tách theo stage.
- Không phụ thuộc mạng ở S1/S4/S5 (chỉ S2 tuỳ chọn model local, S3 gọi Gemini).

## Architecture
```
lib/talking_head_edit/
├── __init__.py
├── cli.py                  # entry: chạy job từ terminal
├── job_store.py            # tạo/đọc/ghi job.json, job_id, workspace paths, events.jsonl
├── cache.py                # hash input của từng stage → skip/run
├── runner.py               # điều phối 6 stage, bắt lỗi, ghi progress event
├── resources.py            # đọc whitelist SFX/BGM từ public/ (nguồn sự thật dùng chung)
└── stages/
    ├── probe.py            # S1 ffprobe
    ├── transcribe.py       # S2 WhisperX word spine (dùng tools/analysis/transcriber.py)
    ├── direct.py           # S3 Gemini word-anchored (từ gemini_timeline_director.py)
    ├── audit.py            # S4 cut audit + whitelist + schema + guard tiền-resolve
    ├── resolve.py          # S5 ffmpeg cut/grade/tempo/cold-open + remap event (từ resolve_timeline_events.py)
    └── render.py           # S6 npx remotion render MonaTimeline
schemas/artifacts/talking_head_timeline_spec.schema.json   # spec Gemini trả về (word-anchored)
schemas/artifacts/talking_head_props.schema.json           # props MonaTimeline (đã resolve)
```

Ranh giới quan trọng:
- `direct.py` **chỉ** sinh spec theo chỉ số từ; không được biết giây.
- `resolve.py` là nơi duy nhất chuyển word-index → giây, và là nơi duy nhất chạy ffmpeg cắt.
- `audit.py` thuần deterministic, không gọi API → test được đầy đủ.

## Related Code Files
**Tạo mới:** toàn bộ cây `lib/talking_head_edit/` ở trên; 2 schema JSON; `tests/test_talking_head_audit.py`,
`tests/test_talking_head_resolve.py`, `tests/test_talking_head_cache.py`.
**Sửa:** `.gitignore` (thêm `remotion-composer/public/jobs/`); `Makefile` (target `autoedit`).
**Đọc để tham chiếu:** `remotion-composer/src/mona/MonaTimeline.tsx` (event schema, whitelist),
`tools/analysis/transcriber.py` (word timestamps), scratchpad scripts (nguồn logic).
**Không đụng:** `tools/analysis/footage_edit_*.py` (đường cũ của pipeline `hybrid`).

## Implementation Steps
1. **Copy nguyên trạng** 4 script scratchpad vào `plans/260728-0853-talking-head-autoedit-job-ui/reports/original-scratchpad/`
   làm bản gốc tham chiếu trước khi refactor (chống mất nguồn).
2. `job_store.py` + `cache.py`: job_id = `<slug>-<yymmdd-HHMM>`; hash stage input bằng sha256
   (file bytes cho video, JSON canonical cho spec/params).
3. `resources.py`: quét `remotion-composer/public/` lấy danh sách `sfx_*.mp3` / `bgm_*.mp3` thật,
   đối chiếu với `SFX_FILES`/`BGM_FILES` trong MonaTimeline.tsx; lệch nhau → cảnh báo rõ.
4. `stages/probe.py`: ffprobe duration/fps/resolution/audio; chặn sớm nếu không có audio stream.
5. `stages/transcribe.py`: gọi WhisperX qua `tools/analysis/transcriber.py`, chuẩn hoá về
   `{"word_timestamps":[{word,start,end}]}`; cache theo sha256 file gốc.
6. `stages/direct.py`: port `gemini_timeline_director.py` — giữ prompt tiếng Việt nguyên vẹn, thêm
   chỗ chèn `user_prompt` (yêu cầu riêng của video) và `style_profile`; thứ tự key
   `GEMINI_API_KEY_ALT` → `GEMINI_API_KEY`; `thinking_level` (không phải `thinking_budget`);
   backoff 429; model qua tham số, mặc định `gemini-3.5-flash`.
7. `stages/audit.py`: port machine cut audit (chỉ filler/lặp/bỏ dở đi qua), whitelist SFX/BGM,
   validate schema, chuẩn hoá cut về cặp `[w0,w1]`; xuất `audit_report.json` liệt kê **mọi** cut
   bị từ chối kèm lý do (UI sẽ hiển thị).
8. `stages/resolve.py`: port `resolve_timeline_events.py` **giữ nguyên hằng số và guard**
   (`PAD=0.08`, `MIN_CUT=0.12`, `TEMPO=1.06`, atrim+concat, assert A/V, cold-open pad clamp,
   keyword-in-card, caption merge <0.5s, `fit_keyword_font`); bỏ mọi hardcode path → nhận từ job.
9. `stages/render.py`: `npx remotion render src/index.tsx MonaTimeline <out> --props=<props>`,
   concurrency = `cpu_count-2` (không hardcode), stream tiến độ % ra `events.jsonl`.
10. `runner.py` + `cli.py`: chạy tuần tự, `--stage-from`, `--no-cache`, `--dry-run` (in kế hoạch + chi phí ước tính).
11. Test: audit (cut nội dung phải bị từ chối), resolve (remap thời gian đúng sau khi cắt; guard
    keyword-in-card; A/V assert), cache (đổi prompt → chỉ chạy lại từ S3).

## Todo List
- [ ] Sao lưu script gốc vào reports/original-scratchpad/
- [ ] job_store + cache + resources
- [ ] stage probe / transcribe
- [ ] stage direct (port + user_prompt hook)
- [ ] stage audit + audit_report
- [ ] stage resolve (giữ nguyên guard/hằng số)
- [ ] stage render + progress events
- [ ] runner + cli + Makefile target
- [ ] 2 schema JSON
- [ ] 3 file test + chạy `make test-contracts`

## Success Criteria
- Chạy CLI trên footage cũ (`1783586132708_...mp4`) ra `final.mp4` khớp chất lượng bản
  `mona_FINAL_V5_1080.mp4` (độ dài lệch < 0.5s, không lệch A/V, BGM nghe được).
- Đổi `--prompt` → chỉ S3-S6 chạy lại; S2 lấy từ cache (đo bằng log).
- Chạy 2 job khác nhau song song không ghi đè file của nhau.
- Test pass; `registry.discover()` không chậm đi (không import package mới).

## Risk Assessment
| Rủi ro | Giảm thiểu |
|---|---|
| Refactor làm mất một guard nhỏ → lỗi sync tái xuất hiện | Sao lưu bản gốc + test so sánh output props trên cùng spec |
| Quota/key Gemini hết | `--stage-from resolve` chạy lại từ spec đã có; CLI báo lỗi key rõ ràng theo Escalate Blockers |
| WhisperX chậm/nặng trên máy user | Cache theo hash; cho phép nạp sẵn `--spine <json>` |

## Security Considerations
- API key chỉ đọc từ `.env`, không ghi vào job artifact, không log.
- Job workspace nằm trong `public/` → **không** đặt file nhạy cảm ở đó; server P02 chỉ mount thư mục jobs.

## Next Steps
→ Phase 02 (job server) tiêu thụ `job_store` + `events.jsonl` của phase này.
