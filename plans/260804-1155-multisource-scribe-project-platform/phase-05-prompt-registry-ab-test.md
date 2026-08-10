# Phase 05 — Prompt registry + A/B test

## Context

- [plan.md](plan.md)
- Hiện tại: `prompt_structure.py:75-115` là f-string 40 dòng hardcode; `prompt_captions.py`,
  `cut_verifier.py`, `stages/revise.py` tương tự. Admin không sửa được prompt mà không sửa code.
- User chốt: **full editor + A/B test 2 version trên cùng spine**.

## Overview

- **Priority:** P1
- **Status:** ✅ code + 63 test. **Gate byte-identical đạt 5/5 template** (8 golden case, gồm cả
  biến thể multi-source và bgm/cold-open tắt). Golden được chụp từ bản f-string **trước khi** thay,
  nên là mốc thật, không phải code mới tự đồng ý với chính nó.
- Prompt thành file có version, admin xem prompt đã render với spine thật, sửa thành override, A/B 2
  version rồi so bằng số liệu.

## Key insights

- **Không dùng Jinja2/`str.format`.** Prompt đầy `{...}` của JSON schema — `str.format` sẽ nổ hoặc
  phải escape `{{` khắp nơi (đã thấy trong `prompt_structure.py` hiện tại: `{{"w0":i,...}}`). Dùng
  placeholder `{{name}}` + `str.replace` tuần tự. KISS và không thêm dependency.
- **Việc dịch prompt hiện tại sang template là refactor thuần** — output phải **byte-identical** với
  bản f-string cho cùng input. Đây là gate: so sánh chuỗi, không "trông giống là được".
- **A/B không được render.** Render 6 phút × 2 = vô dụng cho vòng lặp. So ở tầng `spec` + `audit`:
  `audit` đã có sẵn và đã đo chất lượng cut/tài nguyên. Cộng số liệu cơ học rẻ mà nói được nhiều:
  caption coverage %, số caption > 9 từ, số card so `card_plan`, số cut bị verifier loại, token cost.
- **Prompt version phải ghi vào job** để bản dựng cũ giải thích được. Không có nó thì "hôm qua nó ra
  đẹp hơn" là câu không tra được.
- **Override tách khỏi file gốc** để `git diff` sạch và `git pull` không xung đột với bản admin sửa.

## Requirements

**Functional**
- Template ở `prompts/talking_head/<id>.v<N>.md`, override ở `prompts/overrides/talking_head/`.
- `prompt_registry.load(id, version=None)` → version hiện hành từ `prompts/registry.json`, override
  thắng gốc.
- `render(id, vars)` → prompt cuối cùng. `preview(id, job_id)` render với spine thật của job.
- API: list / get / put override / delete override / preview / diff với gốc.
- A/B: `POST /api/prompts/{id}/ab {job_id, version_a, version_b}` → chạy `direct` 2 lần trên cùng
  spine (không đụng job hiện tại), chạy `audit` cả 2, trả bảng so sánh.
- `job.json` ghi `prompt_versions: {"structure":"v1","captions":"v2"}`.

**Non-functional**
- A/B một lần ≤ 2× token của một lần `direct` (~55k → ~110k). Hiện rõ trong UI trước khi chạy.
- Template lỗi (thiếu placeholder) phải fail **trước khi** gọi LLM, không fail nửa đường.

## Architecture

```
prompts/
├── registry.json                  {"structure":{"current":"v1","versions":[…]}, …}
├── talking_head/
│   ├── structure.v1.md
│   ├── captions.v1.md
│   ├── cut_verify.v1.md
│   ├── revise.v1.md
│   └── select_take.v1.md          (từ phase 02)
└── overrides/talking_head/
    └── structure.v2.md            admin tạo qua UI

lib/talking_head_edit/prompt_registry.py
```

Placeholder trong template: `{{spine}}`, `{{sfx_table}}`, `{{bgm_table}}`, `{{card_rule}}`,
`{{topic_line}}`, `{{style_block}}`, `{{user_block}}`, `{{cold_block}}`, `{{bgm_block}}`, `{{n}}`.
Placeholder không được cung cấp → raise, không để trống âm thầm.

Bảng so sánh A/B:
```jsonc
{"a":{"version":"v1","cards":4,"caption_coverage":1.0,"captions_over_9w":0,
      "cuts_proposed":6,"cuts_rejected_by_verifier":1,"audit_score":8.2,
      "tokens":{"in":48000,"out":7400},"cost_usd":0.0},
 "b":{"version":"v2","cards":3,"caption_coverage":0.94,"captions_over_9w":2, …},
 "verdict":"a — b thiếu 1 card so card_plan và có 2 caption quá dài"}
```
`verdict` từ luật cơ học (card đủ > coverage > caption dài > audit score), **không** hỏi LLM: một
model chấm hai prompt của chính nó là vòng lặp không đáng tin.

## Related code files

**Tạo**
- `lib/talking_head_edit/prompt_registry.py`
- `lib/talking_head_edit/prompt_ab.py` — chạy 2 nhánh + tính bảng so sánh
- `prompts/registry.json` + `prompts/talking_head/*.v1.md`
- `server/api_prompts.py`
- `tests/test_talking_head_prompt_registry.py`, `tests/test_talking_head_prompt_ab.py`

**Sửa**
- `prompt_structure.py` — giữ các hàm dựng block (`_style_block`, `_user_block`, `compact_spine`,
  `bgm_table`, `sfx_table` call), bỏ f-string lớn, gọi registry
- `prompt_captions.py`, `cut_verifier.py`, `stages/revise.py` — cùng cách
- `stages/direct.py` — ghi `prompt_versions` vào job state
- `runner.py:55` — `cache_signature("direct")` băm thêm **nội dung template đã render** (sửa prompt
  phải invalidate `direct`; đây là điểm dễ quên nhất của cả phase)
- `server/app.py` — include router

## Implementation steps

1. `prompt_registry.py`: `load`, `render`, `versions`, `save_override`, `delete_override`, `diff`.
   `render` kiểm placeholder còn sót (`{{` trong output) → raise.
2. Dịch `structure.v1.md` từ f-string. **Gate:** test so byte-identical output cũ vs mới với cùng
   input. Chỉ khi xanh mới dịch tiếp file khác.
3. Dịch `captions.v1.md`, `cut_verify.v1.md`, `revise.v1.md`, `select_take.v1.md` — mỗi file cùng gate.
4. `registry.json` + `save_override` sinh version mới tự tăng (`v2`, `v3`…), giữ changelog
   (`{version, created_at, note, author}`).
5. `runner.py`: `cache_signature("direct")` băm prompt đã render. Test: sửa override → `direct` chạy
   lại; không sửa → cache hit.
6. `stages/direct.py` ghi `prompt_versions`.
7. `prompt_ab.py`: chạy `direct` 2 lần vào thư mục tạm `ab/<stamp>/` (không đụng `spec_vN.json`), rồi
   `audit` cả 2, tính số liệu cơ học, luật verdict.
8. `api_prompts.py`: 6 endpoint. `POST /ab` chạy nền + SSE (tái dùng `server/sse.py`), không block.
9. Test: render byte-identical (5 template), placeholder thiếu → raise, override thắng gốc, cache
   invalidate khi sửa prompt, A/B không ghi đè spec của job, verdict đúng theo luật.

## Todo

- [x] `prompt_registry.py` (+ `fingerprint()` cho cache)
- [x] Dịch `structure.v1.md` + **gate byte-identical** (4 biến thể)
- [x] Dịch 4 template còn lại, mỗi cái 1 gate
- [x] `registry.json` + override tự tăng version + changelog
- [x] `cache_signature("direct")` băm `fingerprint(["structure","captions"])` + bảng sfx/bgm
- [x] `prompt_versions` vào `job.json` + `spec._meta` + từng entry trong `versions[]`
- [x] `prompt_ab.py` + 5 luật verdict cơ học (không hỏi LLM)
- [x] `api_prompts.py` 9 endpoint + A/B chạy nền, tiến độ qua `events.jsonl` (SSE sẵn có)
- [x] `prompts/overrides/` vào `.gitignore`
- [x] Test (63 case)

**Lệch có ý thức:** A/B chỉ so `structure`, và **dùng chung caption giữa hai nhánh**. Caption là
nhánh dài và đắt nhất; trả tiền hai lần để so một thứ mà cả hai version đều không đổi là phí. Nếu
sau này cần A/B `captions` thì thêm nhánh riêng.

## Success criteria

- 5 template render **byte-identical** với bản f-string cho cùng input.
- Sửa override → `direct` chạy lại; không sửa → cache hit (đo bằng log).
- A/B trên job thật: trả bảng đủ số liệu, không ghi đè `spec_vN.json`, tổng token ≤ 2× một lần direct.
- Job mới có `prompt_versions` trong `job.json`.
- Xoá `prompts/overrides/` → hệ thống trở về hành vi gốc, không lỗi.

## Risks

| Rủi ro | Xử lý |
|---|---|
| Dịch template làm lệch prompt → chất lượng tụt mà không ai biết | Gate byte-identical, không thoả hiệp |
| Admin sửa prompt làm vỡ JSON schema đầu ra | `preview` hiện prompt cuối; `direct` đã có retry parse; thêm cảnh báo nếu override xoá dòng "TRẢ VỀ DUY NHẤT JSON" (kiểm chuỗi khoá bắt buộc) |
| Quên băm prompt vào cache → sửa prompt mà kết quả không đổi, tưởng prompt vô dụng | Test riêng cho case này |
| A/B đốt token ngoài dự kiến | UI hiện chi phí dự kiến trước khi chạy; ghi token thật vào kết quả |
| Override bị `git pull` ghi đè | `prompts/overrides/` vào `.gitignore` (hoặc commit có ý thức — nêu rõ trong docs) |

## Security

- Override là **prompt**, không phải code — không `exec`. Nhưng vẫn là prompt injection surface: chỉ
  admin trên localhost sửa được, và server không mở LAN.
- Không cho path traversal trong `id`/`version` (chỉ `[a-z0-9_]+` và `v\d+`).

## Next

Phase 09 dựng tab Prompts. Phase 08 dùng `prompt_versions` để giải thích bản dựng.
