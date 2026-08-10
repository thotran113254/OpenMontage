# Phase 07 — B-roll overlay + speaker mode

## Context

- [plan.md](plan.md) · [phase-02](phase-02-multi-source-spine-assembly.md) ·
  [phase-04](phase-04-project-data-model-api.md)
- Hai ca dùng còn lại của user: b-roll phủ lên A-roll, và phỏng vấn nhiều người nói.
- Renderer hiện có: card, keyword, punch-in, PiP, cold-open, endcard. **Không có lớp video overlay.**

## Overview

- **Priority:** P2
- **Status:** ✅ code + 36 test (12 chạy ffmpeg thật cho 3 ca độ dài). `tsc` sạch trong `mona/`;
  15 lỗi TS còn lại đã có sẵn trong repo trước khi bắt đầu (`Explainer`, `Root`, `ProviderChip`) —
  đã kiểm bằng cách stash rồi chạy lại: vẫn 15.

**Bug thật phát hiện khi làm phase này (không thuộc phase 07):** `audit` và `revise` đọc spine
**đầy đủ**, nhưng spec dùng chỉ số của spine **đã lọc bởi `select`**. Với job nhiều take, audit sẽ
nhận chỉ số ra ngoài phạm vi và cắt sai cửa sổ. Đã sửa cả hai dùng `director_words(job)`.

**Chưa đo:** thời gian render thêm bao nhiêu với 3 b-roll (cần render full 6 phút + footage b-roll thật).
- Thêm event `broll` (video phủ lên A-roll, giữ tiếng A-roll) và `speaker_aware` (đổi khung theo người
  đang nói, dùng diarization của Scribe).

## Key insights

- **B-roll giữ tiếng A-roll, không đổi audio.** Đây là điều làm nó đơn giản: chỉ là lớp hình phủ lên
  trong khoảng [w0, w1], audio không đụng tới → không ảnh hưởng đồng hồ, không ảnh hưởng loudnorm.
  Nếu cho b-roll mang tiếng riêng thì phải mix và mọi bài toán sync quay lại — **không làm ở phase này**.
- **B-roll dài hơn khoảng phủ thì cắt, ngắn hơn thì `loop` hoặc giữ frame cuối.** Chọn: cắt nếu dài,
  và **co giãn tốc độ trong ±15%** nếu ngắn (an toàn hơn loop, loop lộ rõ khi b-roll có chuyển động).
  Ngắn hơn 15% thì giữ frame cuối + cảnh báo.
- **`OffthreadVideo`, không `@remotion/media <Video>`.** Bẫy đã ghi trong docs: `<Video>` bỏ qua
  `objectFit:cover` gây viền trắng. Dùng lại kết luận đó, đừng thử lại.
- **Speaker mode: đừng tự động đổi khung mỗi lượt nói.** Người nói xen kẽ nhanh sẽ làm khung nhảy liên
  tục. Luật: chỉ đổi khi lượt nói ≥ 2.5s, và có cross-fade 0.25s.
- **Diarization của Scribe không hoàn hảo.** `speaker_aware: "auto"` chỉ bật khi có ≥2 speaker **và**
  mỗi speaker chiếm ≥15% số từ (dưới đó khả năng cao là nhiễu chứ không phải người thứ hai).

## Requirements

**Functional**
- Event mới `{"type":"broll","w0":i,"w1":j,"src":"s2","fit":"cover","opacity":1.0}`.
- Director chọn từ `overlay_pool` (chỉ nguồn có thật — cùng luật với sfx/bgm hiện tại).
- `resolve` chuẩn bị b-roll: cắt/co giãn về đúng độ dài khoảng phủ, encode cùng tham số A-roll, đưa
  vào `render_public/`.
- Renderer: lớp `BrollLayer` giữa A-roll và card, dùng `OffthreadVideo`.
- `speaker_aware`: `resolve` sinh `speaker_segments[]` (giây timeline + speaker), renderer đổi khung
  (crop/framing khác nhau, hoặc split-screen 2 người khi cả 2 trong khung).
- `verify`: kiểm b-roll có thật xuất hiện (so frame giữa khoảng phủ với frame A-roll cùng lúc — phải
  khác).

**Non-functional**
- Mỗi b-roll thêm ≤ 8% thời gian render (đo, `blur` frame preset đã tốn 1 lần decode/frame — cộng
  b-roll vào có thể vượt).

## Architecture

```
resolve:
  overlay_pool + broll events
    → cắt/co giãn mỗi b-roll về đúng độ dài (giây timeline sau cắt)
    → encode cùng tham số aroll_pixel_size
    → render_public/broll_<id>.mp4
    → props.broll[] = [{start, duration, src, fit, opacity}]

renderer (remotion-composer/src/mona/):
  <AbsoluteFill>
    <ARoll/>            ← nền
    <BrollLayer/>       ← MỚI, OffthreadVideo, fade in/out 0.2s
    <CardLayer/> <KeywordLayer/> <Captions/>   ← không đổi, luôn ở trên
  </AbsoluteFill>
```

Speaker mode:
```jsonc
"speaker_segments":[{"start":0.0,"end":12.4,"speaker":"speaker_0","framing":"left"},
                    {"start":12.4,"end":18.9,"speaker":"speaker_1","framing":"right"}]
```
`framing` do `resolve` gán (luân phiên/theo face tracker nếu có — `tools/analysis/face_tracker.py` đã
tồn tại, dùng nếu chạy được, không thì luân phiên).

## Related code files

**Tạo**
- `remotion-composer/src/mona/BrollLayer.tsx`
- `lib/talking_head_edit/resolve_broll.py` — cắt/co giãn/encode b-roll
- `tests/test_talking_head_broll.py`

**Sửa**
- `prompt_structure.py` (template `structure.v2.md` sau phase 05) — thêm loại event `broll` + bảng
  `overlay_pool`
- `resolve_events.py` — resolve event `broll` (w0/w1 → giây timeline)
- `stages/resolve.py` — gọi `resolve_broll`, sinh `speaker_segments`
- `remotion-composer/src/mona/resource-manifest.json` — b-roll là per-job, không phải kho chung → thay
  vào đó `props.broll[]` mang đường dẫn; **giữ luật "chỉ tài nguyên có thật"** bằng cách validate ở
  `audit` rằng mọi `src` trong event `broll` tồn tại trong `overlay_pool`
- `stages/audit.py` — validate `broll.src`
- `stages/verify.py` — kiểm b-roll thật xuất hiện
- `remotion-composer/src/mona/` composition chính — chèn `BrollLayer`

## Implementation steps

1. `resolve_events.py`: resolve `broll` (dùng `TimeMapper` đã đa nguồn từ phase 03).
2. `resolve_broll.py`: tính độ dài cần, cắt nếu dài, `setpts`/`atempo`-free speed change trong ±15%
   nếu ngắn, giữ frame cuối + cảnh báo nếu thiếu quá 15%. Encode cùng `aroll_pixel_size`.
3. `audit.py`: validate `src` có trong `overlay_pool`; sai → loại event (cùng cách đang làm với sfx).
4. `BrollLayer.tsx`: `OffthreadVideo`, `objectFit: cover`, fade in/out 0.2s, `opacity` từ props.
5. Chèn vào composition dưới card layer. **Kiểm bằng `--preview-still`** ở giữa khoảng phủ trước khi
   render full.
6. `verify.py`: so frame giữa khoảng phủ vs frame A-roll cùng mốc (extract 2 frame, so histogram) —
   giống hệt nhau = b-roll không vào, báo lỗi.
7. Speaker mode: `resolve` gom lượt nói từ `word.speaker`, gộp lượt < 2.5s vào lượt trước, gán
   `framing`. Renderer đổi khung với cross-fade 0.25s.
8. `speaker_aware: "auto"` → bật khi ≥2 speaker và mỗi speaker ≥15% số từ.
9. Đo thời gian render trước/sau khi có 3 b-roll.

## Todo

- [ ] `resolve_events` resolve `broll`
- [ ] `resolve_broll.py` cắt/co giãn/encode
- [ ] `audit` validate `broll.src`
- [ ] `BrollLayer.tsx` (OffthreadVideo, không `<Video>`)
- [ ] Chèn vào composition + kiểm `--preview-still`
- [ ] `verify` kiểm b-roll thật xuất hiện
- [ ] `speaker_segments` + luật gộp lượt < 2.5s
- [ ] `speaker_aware: auto` theo ngưỡng 15%
- [ ] Đo thời gian render thêm bao nhiêu
- [ ] Prompt: thêm loại event `broll` + `overlay_pool`

## Success criteria

- 1 aroll + 2 broll: b-roll xuất hiện đúng khoảng, `verify` xác nhận khác frame A-roll.
- B-roll ngắn hơn khoảng phủ 10% → co giãn, không loop, không giật.
- B-roll không có trong `overlay_pool` → `audit` loại, không render lỗi.
- Không viền trắng quanh b-roll (bẫy `objectFit`).
- 2 người nói: khung đổi ≤ 1 lần / 2.5s, có cross-fade, không nhảy.
- Render thêm ≤ 8% thời gian với 3 b-roll.

## Risks

| Rủi ro | Xử lý |
|---|---|
| Dùng `<Video>` → viền trắng (bẫy đã gặp) | `OffthreadVideo` bắt buộc; ghi comment trong code nêu lý do |
| B-roll che caption | Caption luôn ở layer trên cùng (giữ nguyên thứ tự hiện tại) |
| Khung nhảy liên tục ở hội thoại nhanh | Ngưỡng 2.5s + cross-fade; test với đoạn xen kẽ nhanh |
| Diarization gán sai người → khung đổi vô nghĩa | Ngưỡng 15%; `speaker_aware` tắt được ở project |
| B-roll làm render chậm/giật (đã gặp lỗi giật do tranh CPU) | Đo thời gian; `verify` mpdecimate đã bắt được giật |

## Security

Không có bề mặt mới.

## Next

Phase 09 cho người kéo-thả b-roll vào timeline. Phase 08 autopilot phải chịu được b-roll fail.
