# Phase 02 — Multi-source word spine + assembly mode

## Context

- [plan.md](plan.md) · [phase-01](phase-01-asr-provider-scribe.md)
- Chặn: `cli.py:28` một `--input`; `job_store.create(input_path)` một file; `spine.json` không biết
  khái niệm nguồn.
- User chốt: **cả 4 ca dùng**, cấu hình 3 tầng global → project → job.

## Overview

- **Priority:** P0 — khối cốt lõi, chặn 03/04/05/07
- **Status:** ✅ code + 63 test (43 multisource + 20 select); regression gate 1 nguồn xanh.
  Còn lại: đo `mode=auto` trên footage thật (cần footage nhiều take).
- Spine thành global index trên N nguồn đã ghép. Director **không cần biết có nhiều file** — vẫn chỉ
  thấy `index:word`. Thêm stage `select` để chọn take tốt nhất khi nhiều nguồn cùng nội dung.

## Key insights

- **Đừng cho model biết giây, cũng đừng cho nó biết đường dẫn file.** Giữ nguyên hợp đồng: model chỉ
  thấy chỉ số từ. Ranh giới nguồn báo bằng marker `--- NGUỒN 2 ---` chèn vào `compact_spine`. Model
  không được cắt xuyên marker trừ khi `cross_source_cut: true`.
- **4 ca dùng gộp thành 2 trục, không phải 4 nhánh code:**
  - trục *assembly* (loại trừ nhau): `sequential` | `best_take` | `auto`
  - trục *modifier* (cộng thêm): `speaker_aware`, `broll_overlay`
  Đây là chỗ dễ nổ phức tạp nhất — nếu làm 4 nhánh riêng thì `direct` prompt sẽ có 4 biến thể và
  không ai bảo trì được.
- **`auto` là mặc định** và là thứ làm hệ thống "chạy tự động": stage `select` đọc spine đa nguồn,
  phát hiện đoạn trùng nội dung → nếu có thì xử như `best_take`, không thì `sequential`. Cần đo tỉ lệ
  đúng trên footage thật trước khi tin.
- **B-roll không vào word spine.** Source `role: "broll"` (không có lời) đi vào `overlay_pool` cho
  director chọn phủ lên. Nếu để lời b-roll vào spine, đồng hồ sẽ có những từ không thuộc mạch nói.
- **Take group phải do người xác nhận, không đoán bừa.** Auto-detect gợi ý (tên file giống nhau,
  độ dài gần nhau, nội dung trùng), UI cho sửa. Đoán sai = cắt mất nội dung thật.

## Requirements

**Functional**
- `--input` nhận nhiều lần, hoặc `--input-dir <folder>` (glob mp4/mov/mkv, sort theo tên).
- Mỗi source có `role` (`aroll`|`broll`), `order`, `take_group`, `speech` (bool).
- Auto-detect `role`: không có audio stream hoặc không có lời (dùng `tools/analysis/audio_probe.py` +
  `speech_gap_detector.py` đã có) → `broll`.
- Config `assembly` 3 tầng: `config/autoedit-defaults.json` → `project.json` → `job.options`.
  Merge shallow-per-key (job thắng project thắng global).
- Stage `select` mới, nằm giữa `transcribe` và `direct`. Bỏ qua khi 1 nguồn hoặc `mode=sequential`.
- Spine v3: mỗi word có `src`; thêm `sources[]`, `takes[]`, `overlay_pool[]`.

**Non-functional**
- 1 nguồn: kết quả phải **giống hệt** trước khi đổi (regression gate — spine v3 với 1 source đọc ra
  cùng danh sách từ, cùng chỉ số).
- Transcribe song song N nguồn (Scribe là call API → chạy song song, không tuần tự).

## Architecture

```jsonc
// spine.json v3
{
  "schema": 3,
  "sources": [
    {"id":"s0","path":"…/sources/s0_take1.mp4","sha256":"…","duration":93.2,
     "role":"aroll","order":0,"take_group":"main","speech":true},
    {"id":"s2","path":"…/s2_broll.mp4","duration":8.0,"role":"broll","speech":false}
  ],
  "takes": [{"src":"s0","w0":0,"w1":811,"take_group":"main"},
            {"src":"s1","w0":812,"w1":1650,"take_group":"main"}],
  "word_timestamps": [{"word":"xin","start":0.42,"end":0.61,"src":"s0","speaker":"speaker_0"}],
  "overlay_pool": [{"src":"s2","duration":8.0,"label":"b-roll bàn làm việc"}],
  "audio_events": [...], "speakers": [...]
}
```

`start`/`end` là **giây trong nguồn của chính nó** (không phải giây trên timeline ghép). `resolve`
biết `src` nên tra được. Đây là lựa chọn có chủ ý: nếu quy về timeline ghép thì mỗi lần đổi thứ tự
nguồn phải sinh lại toàn bộ spine.

```jsonc
// config/autoedit-defaults.json — tầng global
{"assembly": {"mode":"auto","speaker_aware":"auto","broll_overlay":true,
              "cross_source_cut":false,"take_detect":"suggest"}}
```

Stage `select` → `selection_v1.json`:
```jsonc
{"mode_resolved":"best_take",
 "segments":[{"content":"mở đầu về chi phí","candidates":[{"src":"s0","w":[0,120]},
              {"src":"s1","w":[812,930]}],"chosen":{"src":"s1","w":[812,930]},
              "reason":"take 2 không ngập ngừng, nói liền mạch"}],
 "kept_word_ranges":[[812,930],[131,340]]}
```
`kept_word_ranges` là output duy nhất `direct` cần: sau `select`, director nhận **spine đã lọc** và
đánh lại chỉ số liên tục từ 0. Nhờ vậy prompt structure/captions **không đổi một chữ**.

## Related code files

**Tạo**
- `lib/talking_head_edit/sources.py` — quét/validate nguồn, auto-detect role, gợi ý take_group
- `lib/talking_head_edit/spine_build.py` — ghép N spine per-source thành spine v3, đánh index global
- `lib/talking_head_edit/assembly_config.py` — merge 3 tầng
- `lib/talking_head_edit/stages/select.py`
- `prompts/talking_head/select_take.v1.md` (phase 05 sẽ đưa vào registry; giờ để hằng số trong file)
- `config/autoedit-defaults.json`
- `tests/test_talking_head_multisource.py`, `tests/test_talking_head_select.py`

**Sửa**
- `cli.py` — `--input` cho phép lặp, thêm `--input-dir`, `--assembly-mode`
- `job_store.py` — `input_path` → `input_paths[]` (**giữ đọc được `input_path` cũ**); `STAGES` thêm
  `"select"` sau `"transcribe"`; `DEFAULT_OPTIONS` thêm `assembly`
- `stages/probe.py` — probe từng nguồn, chặn sớm nếu **tất cả** nguồn aroll không có tiếng
- `stages/transcribe.py` — vòng qua N nguồn, song song, cache per-source theo sha
- `stages/direct.py` — nhận spine đã lọc theo `kept_word_ranges`
- `prompt_structure.py:19` — `compact_spine` chèn marker ranh giới nguồn
- `runner.py` — `STAGE_RUNNERS["select"]`, `cache_signature("select")`, `cache_signature("transcribe")`
  băm theo **list** sha
- `resolve_events.py` — `TimeMapper` phải mang `src` theo (chi tiết ở phase 03)

## Implementation steps

1. `assembly_config.py` + `config/autoedit-defaults.json`. Test merge 3 tầng trước khi có gì khác.
2. `sources.py`: `scan(paths|dir) -> list[SourceSpec]`. Auto-detect role qua audio stream + energy.
   `suggest_take_groups()` chỉ **gợi ý** (trả về confidence), không tự áp khi `take_detect:"suggest"`.
3. `job_store`: `create(input_paths, ...)`. Shim: `state.get("input_paths") or [state["input_path"]]`
   — bọc thành helper `job_input_paths(state)` để không rải `or` khắp code.
4. `stages/probe.py`: probe từng nguồn, ghi `sources[]` vào job state.
5. `stages/transcribe.py`: `ThreadPoolExecutor` per source (Scribe là I/O). Mỗi nguồn cache riêng
   theo sha → thêm 1 nguồn mới không transcribe lại nguồn cũ. Ghép bằng `spine_build.build()`.
6. `spine_build.build(per_source_spines, sources)`: sort theo `order`, đánh index global liên tục, ghi
   `src` vào từng word, sinh `takes[]`, tách `overlay_pool` từ source `role=broll`.
7. **Regression gate:** chạy lại 1 nguồn, assert spine v3 cho cùng danh sách từ và cùng chỉ số như
   spine v2. Không đạt thì dừng, không đi tiếp.
8. `stages/select.py`:
   - `mode=sequential` → `kept_word_ranges` = toàn bộ, ghi `mode_resolved`, xong.
   - `mode=best_take|auto` → 1 call LLM: đưa spine nhiều nguồn có marker, yêu cầu nhóm các đoạn TRÙNG
     NỘI DUNG rồi chọn bản tốt nhất, lý do. Output word-index. **Guard cơ học**: các range phải không
     chồng nhau, phải phủ ≥ 60% nội dung của ít nhất một take, không được rỗng. Vi phạm → fallback
     `sequential` + cảnh báo, KHÔNG đoán.
   - `mode=auto` + LLM báo không có đoạn trùng → `mode_resolved: "sequential"`.
9. `direct.py`: lọc spine theo `kept_word_ranges`, đánh lại index 0..n-1, giữ `_orig_index` để
   `resolve` tra lại được từ gốc (**bắt buộc** — nếu mất, không cắt được media).
10. `compact_spine`: chèn `\n--- NGUỒN {k} ---\n` tại ranh giới. Thêm vào prompt structure một dòng
    duy nhất: cấm cut xuyên marker (khi `cross_source_cut:false`).
11. Test: merge config, auto-detect role, ghép spine, index mapping 2 chiều, guard của `select`,
    fallback khi LLM trả rác, 1-nguồn-không-đổi.

## Todo

- [x] `assembly_config.py` + defaults + test merge (thêm `sources_of()` cho UI hiện nguồn giá trị)
- [x] `sources.py` scan + auto-detect role (ngưỡng −45 dBFS, 3 mẫu) + suggest take_group
- [x] `job_store` `input_paths[]` + `job_input_paths()`/`primary_input_path()` shim + `STAGES` thêm `select`
- [x] `probe.py` đa nguồn (chặn khi *không nguồn nào* có tiếng; giữ role người đã sửa)
- [x] `transcribe.py` song song (`ThreadPoolExecutor`, ≤4) + cache per-source theo sha
- [x] `spine_build.py` ghép + index global + `overlay_pool` + `upgrade_v2` cho job cũ
- [x] **Regression gate 1 nguồn** — `TestSingleSourceRegression`, so từng từ/start/end
- [x] `stages/select.py` + 5 guard cơ học + fallback sequential
- [x] `direct.py` lọc spine + giữ `_orig_index` + test round-trip
- [x] `compact_spine` marker nguồn + 1 dòng luật (im lặng hoàn toàn khi 1 nguồn)
- [x] Test (63 case)
- [ ] **Đo `mode=auto` đoán đúng bao nhiêu lần trên footage thật** — cần footage nhiều take

## Success criteria

- 1 nguồn: spine v3 ra đúng cùng từ + cùng chỉ số như v2; `final.mp4` không đổi (`verify` cùng số).
- 3 take cùng nội dung: `select` chọn được, `kept_word_ranges` không chồng nhau, log lý do đọc được.
- 5 nguồn khác nhau + `mode=sequential`: ghép đúng thứ tự, không mất từ nào.
- 1 aroll + 2 broll: `overlay_pool` có 2, spine chỉ có từ của aroll.
- Thêm 1 nguồn vào job cũ: chỉ nguồn mới bị transcribe.
- Job cũ (`input_path` đơn) mở lại và chạy tiếp được.

## Risks

| Rủi ro | Xử lý |
|---|---|
| `select` cắt mất nội dung thật vì tưởng là take trùng | Guard phủ ≥60%; UI phase 09 hiện diff "bị loại" để người soi; `mode` mặc định có thể đổi về `sequential` nếu đo thấy `auto` sai nhiều |
| Index mapping 2 chiều sai → cắt sai chỗ (lỗi tệ nhất có thể) | Test round-trip `orig → filtered → orig` cho mọi từ; assert trong `resolve` rằng mọi event map về được nguồn có thật |
| Prompt phình vì nhiều nguồn → vượt context | `select` chạy trên spine rút gọn (chỉ từ, không timestamp — đã vậy sẵn); nếu >4000 từ thì chia đoạn chồng lấn |
| `auto` đoán sai kiểu assembly | `mode_resolved` luôn ghi vào job + log; người đổi tay được, không phải chạy lại transcribe |
| Take group đoán sai gộp 2 nội dung khác nhau | `take_detect:"suggest"` mặc định — không tự áp |

## Security

- `--input-dir` phải nằm trong `AUTOEDIT_INPUT_ROOTS` (cơ chế `_check_input_path` ở
  `server/api_jobs.py:40` đã có — tái dùng, không viết lại).

## Next

Phase 03 cắt media đa nguồn. Phase 04 gói nguồn vào Project.
