---
title: "Skin-Smoothing Slider + Reusable Look Preset"
description: "Sliders for skin_smooth/blemish_reduce on a user-picked frame in the autoedit UI, plus a named preset store reusable across jobs."
status: completed
priority: P2
effort: 7.5h
branch: main
tags: [autoedit, ui, grade, preset, ffmpeg, skin-smooth]
created: 2026-08-04
---

# Skin-Smoothing Slider + Reusable Look Preset

## Scope note (read first)
Đây là **dev task nội bộ** mở rộng UI/tool của autoedit talking-head job runner
(`lib/talking_head_edit/`, `server/`, `remotion-composer/ui/`).
**AGENT_GUIDE.md Rule Zero / `pipeline_defs` KHÔNG áp dụng** — không sản xuất video mới,
không chạy pipeline production, không cần pipeline registry.

## Finding quyết định phạm vi (verified 2026-08-04)
**Làm mịn da ĐÃ tồn tại first-class trong grade pipeline.** Không cần viết filter mới,
không cần endpoint preview mới, không cần wire `tools/enhancement/face_enhance.py`.

| Thành phần | Đã có ở đâu |
|---|---|
| Filter smartblur + hqdn3d theo `skin_smooth` (0-1) & `blemish_reduce` (0-1) | `lib/talking_head_edit/resolve_media.py:222-223,241-245,258-265` |
| Áp khi **render thật** | `stages/resolve.py:153` (qua `grade_chains_for` :120-156) |
| Áp khi **preview 1 frame** | `preview.py:139` (`grade_still` :129-144), source = footage GỐC |
| Endpoint preview đã nhận grade tuỳ ý | `server/api_previews.py:113-142` (`{at, grade}`) |
| Lưu theo job + invalidate cache resolve | `options.grade_overrides` → `runner.py:130` |

⇒ **`tools/enhancement/face_enhance.py` KHÔNG được dùng.** Nó là bản smartblur thứ hai
(`face_enhance.py:210-219`, param ngắn `lr/ls/lt`) và sẽ là encode pass thứ 2 + làm mịn 2 lần
trên cùng khung hình. Vi phạm DRY. Xem `phase-01` §Key Insights.

## Khoảng trống thật sự (chỉ 2)
1. **UX**: panel Màu hiện dùng 1 ô text JSON thô (`grade-preview.tsx:86-90`) và ô giây là text
   (`:82`) — không có slider, không thấy frame đang chọn.
2. **Tái dùng cross-job**: `grade_overrides` chỉ nằm trong `job.json` của 1 job. Không có preset
   đặt tên nào dùng lại được cho job/video khác.

## Phases
| # | Phase | Effort | Blockers |
|---|-------|--------|----------|
| 01 | [Look preset store + API](phase-01-look-preset-store-and-api.md) | 2.5h | — |
| 02 | [Slider da + frame picker + preview](phase-02-skin-slider-preview-panel.md) | 3h | — (song song được với 01) |
| 03 | [Lưu/áp preset trong UI + docs](phase-03-preset-apply-and-docs.md) | 2h | 01, 02 |

File ownership: 01 = `lib/talking_head_edit/look_presets.py`, `server/api_presets.py`,
`server/app.py`, `config/look-presets.json`, `tests/`. 02 = `ui/src/components/skin-preview.tsx`,
`ui/src/components/look-preview.tsx`. 03 = `ui/src/api/client.ts` + edit `skin-preview.tsx`.
01 và 02 không chạm file nhau ⇒ parallel an toàn.

## Data flow
```
[UI skin panel]  slider skin_smooth/blemish_reduce + slider giây (0..probe.duration_seconds)
      │
      ├─ Preview ─► POST /api/jobs/{id}/preview/grade  {at, grade:{skin_smooth, blemish_reduce}}
      │                └─► grade_still → build_grade_chain → ffmpeg 1 frame (footage GỐC) → PNG url
      │
      ├─ Save preset ─► POST /api/look-presets {name, grade}
      │                    └─► config/look-presets.json   (global, dùng lại cho MỌI job)
      │
      └─ Áp cho job ──► POST /api/jobs/{id}/run {stages:["resolve"], options:{grade_overrides:{…}}}
                           └─► resolve.grade_chains_for → build_grade_chain → encode span thật
```
Preset là **fragment grade**, không phải giá trị đã merge — nằm TRÊN spec của director, đúng
nguyên tắc đã có ở `grade-preview.tsx:53-55`.

## Non-goals (cố ý cắt)
- Face detection tự động — user đã chốt bỏ.
- Map thời điểm trên `<Player>` (đang phát `src.mp4` đã CẮT) sang giây footage gốc. Hai trục
  thời gian khác nhau; span map có sẵn (`resolve.py:442-443`) nhưng chưa cần. Slider chạy trực
  tiếp trên giây footage gốc.
- Preset ở tầng project (3-layer như `assembly_config.py:84-90`) — global + apply tay là đủ.
- Web server mới, thay đổi kiến trúc pipeline, đổi `build_grade_chain`.

## Unresolved questions
1. Tên/độ rộng preset: lưu **fragment grade tuỳ ý** (file `config/look-presets.json`, tên
   "look preset") hay khoá cứng 2 khoá da? Plan chọn generic-store + UI chỉ hiện 2 slider da
   (0 dòng code thêm). Cần user xác nhận tên gọi.
2. Có cần nút "xoá preset" trong UI ở vòng này, hay chỉ list + save + apply (xoá bằng sửa file)?
3. Job nhiều nguồn: preview chỉ lấy `primary_input_path` (`api_previews.py:66-74`) nên chỉ thấy
   source #1, còn `grade_overrides` phẳng thì áp cho MỌI source (`resolve.py:137`). Chấp nhận,
   hay cần chọn source để preview?
