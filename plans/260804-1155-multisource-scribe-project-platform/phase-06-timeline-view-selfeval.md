# Phase 06 — timeline_view + self-eval quanh seam

## Context

- [plan.md](plan.md) · [phase-03](phase-03-resolve-per-span-multisource.md)
- Mượn từ `browser-use/video-use`: `timeline_view.py <video> <start> <end>` sinh filmstrip + waveform
  + nhãn từ, dùng ở điểm quyết định.
- Hiện có: `preview.py` (`--preview-grade`, `--preview-still`, `--preview-clip`),
  `stages/verify.py` (LUFS, A/V, mpdecimate, nhạc trong khoảng ngắt lời).

## Overview

- **Priority:** P2
- **Status:** ✅ code + 27 test. Chạy thật trên `final.mp4`: ảnh **1.24s**, dấu tiếng Việt đủ
  ("những", "câu", "thông", "phẩm"), nhãn từ **khớp chính xác** caption cháy trên hình.

**Bug thật mà chỉ chạy thật mới thấy:** nhãn từ ban đầu dùng `word.start` = giây trong **file
nguồn**, không phải giây trên bản dựng. Job này cắt ít (1 đoạn) nên trông "gần đúng" — với video cắt
nhiều thì lệch hẳn. Đã thêm `output_time_mapper(job, version)` dựng `TimeMapper` từ `spans` +
`cold_open_offset` trong resolve report; CLI và `verify` dùng chung.
- Sinh ảnh composite theo yêu cầu để (a) `audit` quyết định cut nhập nhằng, (b) `verify` soi ±1.5s
  quanh mỗi seam, (c) UI hiện cho người xem.

## Key insights

- **Đây KHÔNG phải nhờ model chấm chất lượng.** Blind test đã chứng minh model không nghe/nhìn được
  chất lượng kỹ thuật (`docs/talking-head-autoedit.md`). `timeline_view` dùng cho việc khác: *nội dung
  gì ở quanh mốc này* — câu hỏi model trả lời được, và các phép đo cơ học thì không.
- **Waveform là thứ có giá trị nhất, không phải filmstrip.** Câu hỏi thực tế ở mỗi seam là "chỗ này có
  im lặng không, cắt vào có bị hụt phụ âm không" — waveform + nhãn từ trả lời được ngay. Filmstrip
  chủ yếu để người xem.
- **`verify` hiện đo được 4 thứ nhưng không chỉ ra CHỖ NÀO sai.** mpdecimate báo 15% frame lặp nhưng
  không nói ở đâu. `timeline_view` ở seam đáng nghi biến báo cáo thành thứ hành động được.
- Rẻ: 1 PNG ~200 KB, ffmpeg 2 lệnh (`showwavespic` + `tile`), ~0.5s. Không gọi model nào.
- **Giới hạn số ảnh.** 40 seam × 1 ảnh mỗi lần verify = rác. Chỉ sinh ở seam **đáng nghi** (đỉnh biên
  độ cao trong 50ms quanh mối, hoặc mpdecimate báo lặp ở gần đó), tối đa 6 ảnh/lần.

## Requirements

**Functional**
- `timeline_view(video, start, end, words=None, out=None) -> Path`: filmstrip N frame + waveform +
  nhãn từ trên trục thời gian + đường kẻ ở mốc seam.
- CLI: `--timeline-view <start> <end>` trên job.
- `verify` tự sinh ảnh ở seam đáng nghi, ghi đường dẫn vào `verify_report_vN.json`.
- `audit` sinh ảnh khi `cut_verifier` trả "không chắc" (hiện nó chỉ pass/reject) → đưa ảnh vào call
  thứ hai để quyết.
- API `GET /api/media/{job_id}/frames/{name}` đã có (`api_jobs.py:279`) → tái dùng.

**Non-functional**
- ≤ 1s mỗi ảnh. Tối đa 6 ảnh/lần verify. Ảnh vào `preview/timeline/`, không đụng `final.mp4`.

## Architecture

```
lib/talking_head_edit/timeline_view.py

  ┌──────────────────────────────────────┐
  │ [f1][f2][f3][f4][f5][f6]             │  filmstrip, 6 frame đều nhau
  ├──────────────────────────────────────┤
  │ ▁▂▅█▇▃▁    ▁▂▃▅▇█▅▂▁                 │  waveform (showwavespic)
  ├──────────────────────────────────────┤
  │ chi  phí   thì  ‖  cao  hơn  nhiều   │  nhãn từ, ‖ = mốc seam
  └──────────────────────────────────────┘
```

Dựng bằng ffmpeg + PIL. **Đã kiểm: `Pillow>=10.0` có ở `requirements.txt:6`** → dùng PIL để vẽ nhãn,
không cần `drawtext` (dễ xử lý font có dấu hơn).

Chọn seam đáng nghi trong `verify`:
1. đỉnh biên độ trong 50ms quanh mối > (trung bình + 12 dB) → nghi pop
2. mpdecimate báo frame lặp trong ±1s
3. lệch LUFS cửa sổ 3s hai bên mối > 1.5 dB
Xếp theo mức nghi, lấy 6 đầu.

## Related code files

**Tạo**
- `lib/talking_head_edit/timeline_view.py`
- `tests/test_talking_head_timeline_view.py`

**Sửa**
- `stages/verify.py` — tìm seam đáng nghi + sinh ảnh + ghi vào report
- `stages/audit.py` + `cut_verifier.py` — thêm nhánh "không chắc" → sinh ảnh → call lại
- `preview.py` — thêm hàm gọi từ CLI
- `cli.py` — `--timeline-view`
- `resolve.py` — ghi `seams[]` (giây trên timeline sau cắt) vào `resolve_report_vN.json`; **cần thiết**:
  không có danh sách seam thì `verify` không biết soi ở đâu

## Implementation steps

1. Chọn font có dấu tiếng Việt cho PIL (`fitText` trong renderer đã gặp vấn đề font — dùng lại font đó).
2. `resolve.py` ghi `seams[]` vào report. Không có bước này thì các bước sau vô nghĩa.
3. `timeline_view()`: filmstrip (`fps=…,tile=6x1`), waveform (`showwavespic=s=1200x160`), ghép dọc,
   vẽ nhãn từ từ spine (map giây timeline → từ qua `TimeMapper`).
4. `verify.py`: hàm `suspect_seams(report, video, words) -> list[float]` theo 3 luật, giới hạn 6, sinh
   ảnh, ghi `timeline_views: [{at, path, reason}]`.
5. `cut_verifier.py`: thêm verdict thứ ba `"unsure"`. Với `unsure` → `timeline_view` quanh cut → call
   lại kèm ảnh. Vẫn giữ luật cứng: **verifier hỏng → không cắt**.
6. CLI `--timeline-view <start> <end>`.
7. Test: sinh ảnh không lỗi cho video mẫu ngắn, nhãn từ đúng vị trí, giới hạn 6 ảnh, `suspect_seams`
   đúng theo 3 luật (dữ liệu report giả).

## Todo

- [x] Font có dấu cho PIL (segoeui → arial → tahoma → DejaVu, fallback không chết)
- [x] `resolve.py` ghi `seams[]` (đã làm ở phase 03)
- [x] `timeline_view.py` + `output_time_mapper`
- [x] `verify.py` `suspect_seams` (3 luật) + sinh ảnh (trần 6) + ghi `timeline_views` vào report
- [x] `cut_verifier` verdict `unsure` → `cut_verify.v2.md` + vòng 2 kèm ảnh
      (`cut_verify_look.v1.md`); còn không chắc sau vòng 2 → **giữ**
- [x] CLI `--timeline-view <từ> <đến>`
- [x] Test (27 case, gồm 8 case chạy ffmpeg thật)

**Lệch có ý thức:** vòng 2 chạy trên **footage nguồn**, không phải bản đã cắt — `audit` đứng trước
`resolve` nên `src.mp4` chưa tồn tại, và câu hỏi ("mép cắt rơi vào im lặng hay giữa từ?") vốn thuộc
audio gốc.

## Success criteria

- Cố tình tạo pop ở 1 seam (bỏ afade) → `verify` chỉ ra đúng seam đó trong top 3.
- Ảnh ≤ 1s/cái, ≤ 6 cái/lần verify.
- Nhãn từ tiếng Việt hiện đủ dấu.
- `cut_verifier` với `unsure` không tự cắt khi call thứ hai vẫn không chắc.

## Risks

| Rủi ro | Xử lý |
|---|---|
| Font thiếu dấu tiếng Việt | Dùng font đã dùng trong renderer; test có chữ có dấu |
| Sinh quá nhiều ảnh làm chậm verify | Trần cứng 6; đo thời gian verify trước/sau |
| Verdict `unsure` làm verifier dễ dãi hơn | `unsure` sau 2 call vẫn `unsure` → **reject** (mặc định an toàn giữ nguyên) |
| Ảnh chiếm đĩa | `preview/timeline/` dọn khi job hoàn tất > 7 ngày (hoặc để tay — nêu trong docs) |

## Security

Không có bề mặt mới.

## Next

Phase 08 dùng `timeline_views` trong vòng autopilot self-eval.
