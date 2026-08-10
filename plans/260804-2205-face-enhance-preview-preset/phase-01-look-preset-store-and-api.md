# Phase 01 — Look preset store + CRUD API

## Context Links
- Plan: [plan.md](plan.md)
- Convention mẫu để copy: `lib/talking_head_edit/assembly_config.py:52-90` (`_read_json`,
  `global_defaults`, `_clean`, `resolve`), global config path `config/autoedit-defaults.json`
  (`assembly_config.py:26`)
- Router convention: `server/api_prompts.py:22-56`, đăng ký ở `server/app.py:48-51`
- Khoá grade hợp lệ: `lib/talking_head_edit/resolve_media.py:206-303`

## Overview
- **Priority**: P2
- **Status**: completed
- **Effort**: 2.5h
- Một file JSON global lưu các preset đặt tên (fragment grade), cộng 3 endpoint list/save/delete.
  Không dính gì tới job — preset là tài sản dùng lại cho MỌI job/video.

## Key Insights
1. **KHÔNG dùng `tools/enhancement/face_enhance.py`.** Nó có `skin_smooth_strength` riêng
   (`face_enhance.py:110-119`, `_smartblur_filter` :210-219) sinh `smartblur=lr=…:ls=…` — trùng
   chức năng với `build_grade_chain` (`resolve_media.py:261-263`) nhưng khác tên param. Wire nó
   vào = encode pass thứ hai trên video đã làm mịn ⇒ mịn 2 lần + mất nét + chậm gấp đôi. Tool đó
   để nguyên (không xoá, không sửa) — nó không được pipeline nào gọi.
2. **Preset chỉ nên chứa fragment**, không chứa grade đã merge. `grade_overrides` nằm TRÊN spec
   của director (`resolve.py:148`: `{**spec_grade, **shared, **per_source}`); lưu bản merged sẽ
   đóng băng director khỏi mọi lần re-direct sau (lý do đã ghi ở `grade-preview.tsx:53-55`).
3. **BẪY: preset chứa `sharpen` sẽ tắt auto-sharpen.** `resolve.py` `_auto_sharpen`:
   `human_set = "sharpen" in (options.get("grade_overrides") or {})` → thoát sớm, bỏ luôn phép
   đo độ nét per-source. ⇒ validator PHẢI chặn `sharpen`/`clarity` trong preset (deny-list), vì
   một preset lưu từ video A sẽ vô hiệu hoá phép đo trên video B.
4. Không cần schema validation ở `job_store` — `DEFAULT_OPTIONS` (`job_store.py:40-90`) không
   whitelist gì, options merge thẳng (`api_jobs.py:200-202`). Validation phải nằm ở preset store.
5. `config/` KHÔNG bị gitignore (đã kiểm `.gitignore`) ⇒ preset commit được, chia sẻ được.

## Requirements
**Functional**
- `GET /api/look-presets` → `[{name, grade, created_at}]`, sort theo `created_at` giảm dần.
- `POST /api/look-presets` body `{name, grade}` → tạo mới hoặc ghi đè theo `name`, trả preset đã lưu.
- `DELETE /api/look-presets/{name}` → 404 nếu không có, `{deleted: name}` nếu có.
- File hỏng / thiếu ⇒ trả list rỗng, KHÔNG raise (theo `assembly_config.global_defaults` docstring).

**Non-functional**
- Ghi atomic (temp file + `os.replace`), giống `Job.save` (`job_store.py:205-209`).
- Module < 120 dòng, router < 80 dòng (rule file-size).
- Không thêm dependency.

## Architecture
```
server/api_presets.py  (router, prefix /api qua app.py)
        │  validate + HTTPException 400/404
        ▼
lib/talking_head_edit/look_presets.py
   ALLOWED_KEYS / DENIED_KEYS · load_all() · save(name, grade) · delete(name) · validate(grade)
        │  _read_json / _atomic_write
        ▼
config/look-presets.json   {"presets": [{name, grade, created_at}]}
```
- `ALLOWED_KEYS`: các khoá `build_grade_chain` thực đọc — `skin_smooth`, `blemish_reduce`,
  `warmth`, `tone_curve`, `vibrance`, `vignette`, `brightness`, `contrast`, `saturation`, `gamma`.
- `DENIED_KEYS`: `sharpen`, `clarity` (xem Key Insight 3). Trả 400 kèm lý do bằng tiếng Việt.
- Giá trị: bắt buộc `int|float`, reject `bool`/`str`/`None`. `name`: 1-60 ký tự, chỉ
  `[A-Za-z0-9 _-]` (name đi vào path URL và làm key ⇒ chặn `/`, `\`, `.`).

## Related Code Files
**Create**
- `lib/talking_head_edit/look_presets.py`
- `server/api_presets.py`
- `config/look-presets.json` (seed 3 preset: `da-nhe` skin_smooth 0.12, `da-vua` 0.25,
  `da-manh` 0.45 — bậc thang thấy rõ được khi so ảnh)
- `tests/test_look_presets.py`

**Modify**
- `server/app.py` — thêm import + `app.include_router(presets_router, prefix="/api")` cạnh dòng 48-51.

**Delete**: không.

## Implementation Steps
1. `look_presets.py`: hằng số `PRESETS_PATH = REPO_ROOT / "config" / "look-presets.json"`
   (import `REPO_ROOT` từ `job_store`, đúng như `assembly_config.py:24-26`).
2. `_read_json(path)` — copy pattern `assembly_config.py:59-67`: file thiếu/JSON lỗi → `{}`.
3. `validate(grade)` → dict đã lọc; raise `LookPresetError(ValueError)` khi: không phải dict,
   rỗng, có khoá trong `DENIED_KEYS`, khoá lạ, giá trị không phải số.
4. `validate_name(name)` → str đã strip; raise khi rỗng / >60 / có ký tự ngoài whitelist.
5. `load_all()` → list preset đã lọc qua `validate` (bỏ im lặng entry hỏng, không làm sập UI).
6. `save(name, grade)` → validate cả hai, xoá entry cùng tên, append `{name, grade,
   created_at: datetime.now(timezone.utc).isoformat(timespec="seconds")}`, ghi atomic.
7. `delete(name)` → trả `bool`.
8. `server/api_presets.py`: 3 route, map `LookPresetError` → `HTTPException(400, str(exc))` theo
   pattern `api_prompts._bad` (`api_prompts.py:27-28`). Không cần `_job` — preset global.
9. Đăng ký router ở `app.py`.
10. Seed `config/look-presets.json`.
11. Test.
12. `python -c "import server.app"` để chắc không lỗi import/cú pháp.

## Todo List
- [x] `lib/talking_head_edit/look_presets.py` + `LookPresetError`
- [x] `validate` chặn `sharpen`/`clarity` + khoá lạ + giá trị không phải số
- [x] `validate_name` chặn ký tự path
- [x] `load_all` / `save` (atomic, ghi đè theo tên) / `delete`
- [x] `server/api_presets.py` 3 route
- [x] Đăng ký router `server/app.py`
- [x] Seed `config/look-presets.json` 3 preset
- [x] `tests/test_look_presets.py`
- [x] Chạy `pytest tests/test_look_presets.py` xanh (23 passed)
- [x] `python -c "import server.app"` không lỗi

## Test Matrix
| Loại | Ca |
|---|---|
| Unit | `validate` nhận `{skin_smooth: 0.3}`; reject `{sharpen: 1.6}`; reject `{skin_smooth: "0.3"}`; reject `{}`; reject khoá lạ |
| Unit | `validate_name` reject `../x`, `a/b`, `""`, 61 ký tự |
| Unit | `save` cùng tên 2 lần ⇒ 1 entry, giá trị mới |
| Unit | `load_all` với file JSON rác ⇒ `[]` (không raise) |
| Unit | `delete` tên không tồn tại ⇒ `False` |
| Integration | `TestClient`: POST → GET thấy preset → DELETE → GET rỗng (monkeypatch `PRESETS_PATH` sang `tmp_path`, KHÔNG ghi vào `config/` thật) |
| Integration | POST `{grade:{sharpen:1.6}}` ⇒ 400, message nêu lý do |

## Success Criteria
- 3 endpoint chạy được qua `TestClient`, không cần job nào tồn tại.
- Preset lưu rồi đọc lại từ process khác vẫn còn (đọc file, không cache in-memory).
- POST preset chứa `sharpen` bị từ chối 400.
- `pytest tests/test_look_presets.py` xanh; suite hiện có không đỏ thêm.
- Test KHÔNG ghi vào `config/look-presets.json` thật.

## Risk Assessment
| Risk | L×I | Mitigation |
|---|---|---|
| Preset chứa `sharpen` tắt auto-sharpen trên job khác → video mềm mà không ai biết | Cao×Cao | Deny-list ở `validate`, có test. Ghi rõ lý do trong docstring (không nhắc số phase). |
| Test ghi bẩn `config/look-presets.json` của repo | Trung×Trung | `monkeypatch.setattr(look_presets, "PRESETS_PATH", tmp_path/…)`; assert file thật không đổi mtime |
| `name` chứa `/` hoặc `..` đi vào URL path | Thấp×Cao | `validate_name` whitelist ký tự; FastAPI path param cũng không match `/` |
| 2 tab UI save cùng lúc → mất preset (last-write-wins) | Thấp×Thấp | Chấp nhận: 1 máy 1 dev, server bind 127.0.0.1. Ghi atomic nên file không hỏng. |
| Ghi đè im lặng preset cùng tên | Trung×Thấp | UI confirm ở phase 03; API trả preset đã lưu để UI thấy |

## Security Considerations
- Server chỉ bind `127.0.0.1`, không auth theo thiết kế (`server/app.py:1-7`) ⇒ không thêm bề mặt
  tấn công mới, nhưng vẫn phải chặn path traversal qua `name` vì nó là path param.
- Chỉ ghi đúng 1 file trong `config/`; không nhận path từ client (khác `check_input_path` ở
  `paths_guard.py` vốn dùng cho upload).
- Giá trị grade là số đã validate ⇒ không thể inject filter string vào ffmpeg
  (`build_grade_chain` tự format số, `resolve_media.py:258-303`).

## Next Steps
- Phase 03 tiêu thụ 3 endpoint này.
- Phase 02 độc lập hoàn toàn (chỉ file UI) ⇒ chạy song song được.
