---
title: "Talking-Head Auto-Edit: Job Runner + Web UI"
description: "Productize the word-anchored MONA auto-edit pipeline into a cached 6-stage job runner, then a local web UI for submit / track / preview / prompt-revise."
status: in-progress
priority: P1
effort: 37h
branch: main
tags: [talking-head, job-runner, web-ui, remotion-player, gemini, word-anchored]
created: 2026-07-28
---

# Talking-Head Auto-Edit: Job Runner + Web UI

## Goal
Biến luồng auto-edit talking-head đã chứng minh chính xác (word-anchored MONA V5)
từ script scratchpad tạm thành **capability thường trực trong repo**, chạy được qua
**job runner có cache theo stage**, và điều khiển bằng **web UI local**: nạp footage →
theo dõi tiến độ → preview tức thì bằng Remotion Player → sửa bằng prompt (vá spec) → render.

## Quyết định của user (2026-07-28)
1. Phạm vi: **chỉ luồng talking-head auto-edit** (không làm UI tổng cho 12 pipeline).
2. Preview: **Remotion Player trực tiếp** (WYSIWYG, cùng component với bản render).
3. Sửa bằng prompt: **vá spec hiện có** (patch), có version v1..vN + rollback.

## Phases
| # | Phase | Effort | Blockers |
|---|-------|--------|----------|
| 01 | [Rescue + productize pipeline](phase-01-rescue-and-productize-pipeline.md) | 10h | ✅ xong |
| 02 | [Job server](phase-02-job-server-and-tool-wrapper.md) | 6h | ✅ xong |
| 03 | [Web UI](phase-03-web-ui-submit-track-preview.md) | 10h | ✅ xong |
| 04 | [Prompt-revise loop](phase-04-prompt-revise-and-versioning.md) | 6h | ✅ xong |
| 05 | [Quality gates + e2e + docs](phase-05-quality-gates-e2e-docs.md) | 5h | 🔶 verify + test + docs xong; e2e slow test chưa viết |

## Data flow (end to end)
```
raw mp4  ──► [S1 probe]     ffprobe: duration/fps/res/audio  → job.json
         ──► [S2 transcribe] WhisperX word timestamps          → spine.json      (cache: sha256 file)
         ──► [S3 direct]     Gemini word-anchored + user prompt→ spec_vN.json    (cache: spine+prompt+model)
         ──► [S4 audit]      cut audit + SFX/BGM whitelist +   → spec_vN.audited.json + audit_report.json
                             schema + guard (deterministic)
         ──► [S5 resolve]    ffmpeg cut/grade/tempo/cold-open  → src.mp4 + props_vN.json  (cache: spec hash)
                             + remap event time theo word spine
         ──► [S6 render]     Remotion MonaTimeline             → final.mp4 + verify_report.json
                             ──► [verify] ffprobe + luma + LUFS + A/V drift
```
Preview cắt ngang giữa S5 và S6: Player phát `src.mp4 + props_vN.json` tức thì (~5s),
chỉ chạy S6 khi user bấm Render.

## Nguyên tắc chính xác (bất biến — không được phá)
1. **Một đồng hồ duy nhất**: Whisper word spine. Gemini **không bao giờ** phát ra giây, chỉ chỉ số từ.
2. **Cắt video + audio tại cùng biên** bằng `atrim`+`concat` (KHÔNG dùng `aselect` — build ffmpeg
   này pass toàn bộ audio frame, đã verify), kèm assert lệch A/V < 0.35s.
3. **Padding an toàn** `PAD=0.08` / `MIN_CUT=0.12` để không cắt cụt onset của từ.
4. **Audit máy cho từng cut**: chỉ filler / lặp nguyên văn / câu bỏ dở được đi qua; prompt rule
   một mình là KHÔNG đủ (đã chứng minh 3.6-flash đề xuất cắt nội dung).
5. **Whitelist tài nguyên**: chỉ SFX/BGM có thật trong `public/` mới được emit.
6. **Guard cơ học ở resolver** giữ nguyên: keyword-trong-card, caption <0.5s, clamp pad cold-open.
   (Fit chữ keyword đã CHUYỂN sang renderer — `fitText` đo font thật, vì ước lượng 0.62em/ký tự
   sai với tiếng Việt có dấu và vẫn để chữ tràn mép.)

## Key architectural facts (verified this session)
- `registry.discover()` import MỌI module dưới `tools/` (`tools/tool_registry.py:118-134`)
  → runner/server đặt ở `lib/` và `server/`, chỉ 1 wrapper mỏng nằm trong `tools/`.
- `@remotion/player` đã có trong `remotion-composer/package.json` — preview không cần dep mới.
- `MonaTimeline` nhận MỘT mảng `events[]` phẳng (`remotion-composer/src/mona/MonaTimeline.tsx:47-76`),
  whitelist `SFX_FILES` (:37-45) và `BGM_FILES` (:80+) → UI và audit dùng chung nguồn sự thật này.
- Nguồn cần cứu: `%TEMP%/claude/D--CODE-WITH-AI-VIDEO-EDITOR-AI-AGENT/2e50444e-.../scratchpad/`
  (`gemini_timeline_director.py`, `resolve_timeline_events.py`, `gemini_word_anchored.py`,
  `generate_bgm_library.py`) — **thư mục temp, có thể bị xoá bất cứ lúc nào → P01 làm trước tiên**.
- `projects/` và `output/` đã gitignore.
- Job workspace = `projects/autoedit-jobs/<job_id>/` (ĐÃ ĐỔI so với dự kiến ban đầu là
  `public/jobs/`): Remotion copy toàn bộ public dir mỗi lần render — đo được 415 MB sau vài job.
  Mỗi job stage một public dir tí hon (`render_public/`, hardlink); UI lấy media qua
  `/api/media/<job_id>/` nhờ prop `assetBase` mới của MonaTimeline.
- `tools/analysis/footage_edit_analyzer.py` (Gemini trả giây) là đường CŨ kém chính xác — giữ
  nguyên cho pipeline `hybrid`, KHÔNG dùng cho luồng này. Không xoá trong phạm vi plan này.

## Kết quả thực đo (2026-07-28)
- Job `3-sai-lam-chatbot-260728-093239` chạy trọn 7 stage trên footage thật 93.4s.
- verify PASSED: 93.65s vs props 93.6s, lệch A/V 0.05s, −14.8 LUFS, không frame đen.
- Tách call director: 0 → 4 card đúng badge, caption phủ 100%, caption quá 9 từ 7 → 0.
- Verifier chấp nhận đúng 1 cut ("á"), khớp baseline V5 làm tay.
- Revise "bỏ card 2 + đổi nhạc": đúng 1 card bị bỏ, phần còn lại nguyên vẹn, 7.960 token (14%).
- 66 test pass không cần API key.

## Unresolved questions
1. Model director mặc định: bake-off cho thấy `gemini-3.5-flash` giữ caption tốt nhất, nhưng quota
   free-tier đã cạn hồi 2026-07-21 — cần xác nhận key/quota hiện tại trước P01 (fallback `3.6-flash`
   phải kèm cảnh báo caption dài).
2. Render ở đâu khi batch: local (~6 phút/93s 1080p) vs Colab notebook đã có
   (`remotion-composer/colab-render-pipeline.ipynb`, ~3.5-4 phút trên 44-core). P03 chỉ làm local;
   nút "gửi Colab" là hạng mục sau, cần user quyết.
3. Có cần multi-job song song không (hiện thiết kế: 1 job chạy tại một thời điểm, hàng đợi FIFO).
