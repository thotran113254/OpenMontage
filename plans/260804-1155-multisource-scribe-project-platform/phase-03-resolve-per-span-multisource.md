# Phase 03 — Resolve: per-span extract đa nguồn

## Context

- [plan.md](plan.md) · [phase-02](phase-02-multi-source-spine-assembly.md)
- `resolve_media.py:306-346` `cut_and_grade` hiện dùng **một** input:
  `[0:v]{grade},select='between(t,…)'` + `[0:a]atrim…concat`. Không thể nhận nguồn thứ hai.
- Ý tưởng mượn từ `browser-use/video-use`: per-segment extract rồi concat, thay vì một filtergraph.

## Overview

- **Priority:** P0 — chặn 06/07
- **Status:** ✅ code + 51 test. **Gate độ nét ĐẠT trên footage thật:**

| Đo trên | Cũ (một filtergraph) | Mới (per-span) |
|---|---|---|
| `src.mp4` face-crop detail | 12.794 | **12.943** (+1.2%) |
| `src.mp4` LUFS | −14.9 | **−14.9** (lệch 0.0 dB) |
| `src.mp4` độ dài | 90.80s | 90.96s (lệch 0.16s — do afade 20ms/span) |
| `final.mp4` face-crop detail | — | **2.964** ≥ ngưỡng 2.85 ✅ |
| `verify` full | — | pass, 0 issue; A/V drift 0.057s, LUFS −14.9, 0% frame lặp, nhạc vào mix |

**Bug thật mà phép đo bắt được (test không bắt):** cửa sổ run word-tight làm bản dựng 90.8s tụt còn
89.0s — mất tiếng phòng đầu/đuôi. Đã sửa: run ĐẦU giữ từ giây 0, run CUỐI giữ tới hết file, run
giữa mới word-tight + `PAD`. Có `TestRunWindows` khoá lại.
- Viết lại đường cắt: mỗi span (nguồn, start, end) encode riêng → concat demuxer `-c copy` → xử lý
  audio một lần trên chuỗi đã ghép với `-c:v copy`. **Video encode đúng một lần.**

## Key insights

- **Đây là phase rủi ro nhất của cả plan.** Cách làm sai hiển nhiên là: encode span → concat →
  re-encode để chạy loudnorm/tempo. Như vậy video encode **2 lần** và xoá sạch toàn bộ công đo độ nét
  (2.85 hiện tại tụt về ~1.5). Thiết kế bắt buộc: bước cuối `-c:v copy`.
- **loudnorm/acompressor/alimiter KHÔNG được chạy per-span.** loudnorm đo theo cửa sổ; chạy riêng từng
  span thì mỗi span có gain khác nhau → âm lượng nhảy bậc ở mỗi mối cắt. Chúng chỉ chạy trên chuỗi đã
  ghép.
- **`atempo` và `setpts` tempo THÌ được** per-span: chúng tuyến tính, không phụ thuộc ngữ cảnh. Phải
  ở per-span để video và audio ghép xong đã cùng độ dài.
- **`afade` mỗi seam là lợi ích kèm theo** (rule #3 của video-use). Hiện chỉ có ở mối cold-open
  (`resolve_media.py:358`). Fade 20ms in/out mỗi span — quá ngắn để nghe ra, đủ để triệt pop.
- **Per-span encode chạy song song được** → `resolve` ~4 phút có thể xuống đáng kể. Nhưng song song
  quá tay đã từng làm Remotion trượt frame (đã ghi trong docs); giới hạn `min(4, cpu-2)`.
- **`-c copy` fast path**: khi `grade` rỗng, `frame_preset:"none"`, `tempo==1.0`, cùng codec/size →
  extract bằng `-c copy` luôn, zero loss, gần như tức thì. Ca dùng hẹp nhưng miễn phí khi đã có
  kiến trúc per-span.
- concat demuxer đòi mọi segment cùng codec/timebase/SAR → cùng một lệnh encode nên đạt. Cần
  `-fflags +genpts`, và **kiểm** `stream_durations` sau concat, không tin tưởng mù.

## Requirements

**Functional**
- `cut_and_grade` → `cut_and_grade_multi(spans: list[Span], …)`, `Span = (src_path, start, end)`.
- Grade áp per-source: mỗi nguồn có thể lệch màu/độ nét khác nhau (điện thoại khác nhau, buổi quay
  khác nhau). `grade_overrides` cho phép override theo `src` id.
- `auto_sharpen` đo **per-source** (nguồn khác thì độ mềm khác), không đo một lần cho cả job.
- Giữ nguyên: `PAD`, `MIN_CUT`, assert A/V < 0.35s, thứ tự chain của `build_grade_chain`.
- `compute_removes`/`kept_spans` làm việc per-source rồi ghép.

**Non-functional**
- **Gate độ nét:** mặt cắt 1:1 trên `final.mp4` phải ≥ 2.85. Thấp hơn = phase fail, không merge.
- `resolve` không chậm hơn hiện tại (~4 phút cho 93s) — kỳ vọng nhanh hơn nhờ song song.

## Architecture

```
spans (từ select + cut_remove, per source)
   │
   ├─ [song song] mỗi span: grade(src) + trim + setpts tempo (video)
   │              + atrim + afade 20ms + atempo (audio, RAW)
   │              → seg_000.mp4   ← LẦN ENCODE VIDEO DUY NHẤT
   │
   ├─ concat demuxer -c copy → joined.mp4
   │
   └─ ffmpeg -i joined -c:v copy -af "<cleanup,compressor,limiter,loudnorm,aresample>"
              → src.mp4          ← audio re-encode, VIDEO COPY
```

`build_audio_chain` tách làm 2: `build_audio_span_chain(tempo)` (chỉ `afade` + `atempo`) và
`build_audio_master_chain(preset)` (cleanup → compressor → limiter → loudnorm → aresample, **không**
tempo).

## Related code files

**Sửa**
- `lib/talking_head_edit/resolve_media.py` — `cut_and_grade_multi`, `extract_span`, `concat_segments`,
  `apply_master_audio`, tách `build_audio_chain`, `compute_removes` nhận `src`
- `lib/talking_head_edit/stages/resolve.py` — dựng span list từ spine v3 + selection, gọi hàm mới,
  `_auto_sharpen` per-source
- `lib/talking_head_edit/resolve_events.py` — `TimeMapper` map (src, giây trong nguồn) → giây trên
  timeline ghép; hiện chỉ có 1 trục thời gian
- `lib/talking_head_edit/sharpen_calibrate.py` — nhận nhiều nguồn
- `tests/test_talking_head_resolve.py` — mở rộng

**Tạo**
- `tests/test_talking_head_resolve_multisource.py`

## Implementation steps

1. Tách `build_audio_chain` thành span-chain + master-chain. **Test trước:** cùng một input, đường
   cũ vs đường mới (1 span) phải cho LUFS trong ±0.3 dB. Không đạt thì dừng.
2. `extract_span(src, start, end, grade_chain, tempo, …) -> Path`. Fast path `-c copy` khi không
   grade/tempo/frame.
3. `concat_segments(segs) -> Path` — concat demuxer + file list, `-fflags +genpts`, kiểm
   `stream_durations` sau khi ghép.
4. `apply_master_audio(joined, out, preset)` — `-c:v copy`. **Assert `-c:v copy` thật sự xảy ra**:
   so bitrate/kích thước video stream trước-sau, lệch >2% là có re-encode ngầm → raise.
5. `cut_and_grade_multi`: song song `extract_span` (`ThreadPoolExecutor`, `min(4, cpu-2)`), giữ đúng
   thứ tự, rồi concat, rồi master audio. Giữ assert A/V.
6. `TimeMapper`: nhận `spans` đã kept (src, s, e, out_start) → `map(src, t) -> t_out`. Hiện tại nó
   chỉ biết 1 trục nên đây là thay đổi thật, không phải thêm tham số.
7. `stages/resolve.py`: dựng span list từ `kept_word_ranges` + `cut_remove` (word index → (src, giây)
   qua `_orig_index` từ phase 02). `_auto_sharpen` chạy per-source, cache theo sha nguồn.
8. `grade_overrides` theo src: `{"__all__": {...}, "s1": {"brightness": 0.03}}`.
9. **Đo lại độ nét** trên footage tham chiếu, so bảng trong `docs/talking-head-autoedit.md`. Ghi số
   mới vào docs.
10. Chạy `verify` full: A/V drift, LUFS, mpdecimate (giật), nhạc trong khoảng ngắt lời.

## Todo

- [x] Tách audio chain + gate tương đương (`build_audio_chain` = master + atempo, byte-identical)
- [x] `extract_span` + fast path `-c copy` (chỉ khi TOÀN BỘ span đủ điều kiện)
- [x] `concat_segments` + kiểm duration (lệch > max(0.5s, 2%) → raise)
- [x] `apply_master_audio` + **assert không re-encode video** (so bitrate trước/sau, >2% → raise)
- [x] `cut_and_grade_multi` song song (`min(4, len)`) + assert A/V < 0.35s
- [x] `afade` 20ms mỗi seam
- [x] `TimeMapper` đa nguồn (span-based, `from_removes` giữ đường 1 nguồn)
- [x] `resolve.py` dựng span từ spine v3 + `plan_spans` per-run
- [x] `_auto_sharpen` per-source (report riêng mỗi nguồn)
- [x] `grade_overrides` theo src (`{"__all__": …, "s1": …}`) + shape phẳng cũ vẫn chạy
- [x] **Gate độ nét ≥ 2.85** — đo được **2.964**
- [x] `verify` full xanh (0 issue)
- [x] Cập nhật bảng đo trong docs

## Success criteria

- 1 nguồn: `final.mp4` có độ nét ≥ 2.85, LUFS trong ±0.3 dB so bản cũ, A/V drift < 0.35s.
- 3 nguồn: ghép đúng thứ tự, không nhảy âm lượng ở mối nối (đo LUFS cửa sổ 3s quanh mỗi seam, lệch
  < 1.5 dB).
- Không nghe thấy pop ở seam (kiểm bằng đỉnh biên độ trong 50ms quanh seam).
- Fast path: nguồn không grade → `resolve` xong < 10s cho 93s video.
- `resolve` không chậm hơn 4 phút.

## Risks

| Rủi ro | Xử lý |
|---|---|
| **Video bị encode 2 lần → mất nét** | Assert ở bước 4 + gate độ nét ≥2.85. Đây là gate cứng |
| Âm lượng nhảy giữa các span | loudnorm chỉ ở master chain; test đo LUFS quanh seam |
| concat demuxer từ chối vì codec/timebase lệch | Cùng một lệnh encode cho mọi span; kiểm duration sau concat và raise rõ ràng |
| Nhiều span nhỏ (40+ file) làm chậm I/O | Segment vào thư mục tạm trong job dir; dọn sau khi concat; nếu >80 span thì gộp span liền kề trước |
| Song song làm nóng máy → render sau bị giật (đã gặp) | Giới hạn `min(4, cpu-2)`; `resolve` và `render` không chạy đồng thời |
| Fast path `-c copy` cho ra file lệch tham số với span khác | Chỉ dùng fast path khi **toàn bộ** span đủ điều kiện, không trộn |

## Security

Không có bề mặt mới. File tạm trong job dir, dọn ở `finally`.

## Next

Phase 06 (`timeline_view` soi seam) xác nhận trực quan kết quả phase này. Phase 07 thêm lớp b-roll.
