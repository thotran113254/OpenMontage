# Phase 03 — Lưu / chọn / áp preset trong UI + docs

## Context Links
- Plan: [plan.md](plan.md)
- Blockers: [phase-01](phase-01-look-preset-store-and-api.md) (API), [phase-02](phase-02-skin-slider-preview-panel.md) (component)
- API client để bổ sung: `remotion-composer/ui/src/api/client.ts:344-548` (object `api`),
  fetch wrapper `json<T>()` `:328-334`
- Pattern preset dropdown đã có: `ui/src/components/audio-preview.tsx:70-87`
- Docs autoedit: `docs/talking-head-autoedit.md`, `docs/talking-head-autoedit-platform.md`

## Overview
- **Priority**: P2
- **Status**: completed
- **Effort**: 2h
- Nối preset store (P01) vào panel Da (P02): dropdown chọn preset → nạp vào slider → preview →
  áp cho job; nút Lưu preset (đặt tên) từ giá trị slider hiện tại.

## Key Insights
1. **Chọn preset ≠ áp preset.** Chọn = nạp số vào slider để xem thử (không đổi job). Áp = ghi vào
   `grade_overrides` rồi resolve lại. Gộp 2 thao tác sẽ khiến mỗi lần đổi dropdown tốn ~4 phút
   resolve. ⇒ 2 nút riêng, đúng như tab Màu đang tách "Xem thử" và "Áp dụng"
   (`grade-preview.tsx:95-102`).
2. **Không cần endpoint job mới để áp.** `POST /api/jobs/{id}/run` với `options` merge vào state
   đã lưu (`api_jobs.py:200-202`) và `grade_overrides` đã nằm trong cache key resolve
   (`runner.py:130`) ⇒ áp preset là ghi đúng 2 khoá vào `grade_overrides`. Không thêm option mới
   như `skin_preset` — thêm sẽ tạo nguồn sự thật thứ hai (option nói preset A, `grade_overrides`
   nói số B) mà `runner.cache_signature` không hề biết.
3. **Preset là fragment ⇒ merge, không replace.** `{...currentOverrides, ...preset.grade}` giữ
   `warmth`/`vignette` user đã set ở tab Màu. Replace toàn bộ `grade_overrides` sẽ âm thầm xoá
   chúng.
4. Preset store là **global, không theo job** ⇒ dropdown fetch 1 lần khi mở tab, không cần jobId.

## Requirements
**Functional**
- `client.ts`: thêm `listLookPresets()`, `saveLookPreset({name, grade})`, `deleteLookPreset(name)`
  + interface `LookPreset {name: string; grade: Record<string, number>; created_at: string}`.
- Dropdown "Preset da" liệt kê preset; chọn ⇒ nạp `skin_smooth`/`blemish_reduce` vào slider, set
  `touched` cho các khoá đó, trigger preview.
- Nút "Lưu thành preset": ô nhập tên + Lưu; disable khi tên rỗng hoặc `touched` rỗng.
  Nếu tên đã tồn tại ⇒ `window.confirm` trước khi ghi đè.
- Sau khi lưu ⇒ refresh danh sách, chọn sẵn preset vừa lưu.
- Lỗi API ⇒ hiện `.error-text small`, không crash panel.
- (Tuỳ Q2 ở plan.md) nút xoá preset đang chọn, có confirm.

**Non-functional**
- `skin-preview.tsx` vẫn < 200 dòng; nếu vượt ⇒ tách `look-preset-picker.tsx` (dropdown + save box).
- Không thêm npm dependency. Typecheck sạch.

## Architecture
```
skin-preview.tsx
  ├─ useEffect(mount) → api.listLookPresets()            → presets[]
  ├─ onChange dropdown → setSkinSmooth/setBlemishReduce + touched  (KHÔNG gọi job)
  ├─ "Lưu thành preset" → api.saveLookPreset({name, grade: trialGrade()}) → reload list
  └─ "Áp dụng & cắt lại" → api.runStages(jobId, {stages:["resolve"], use_cache:false,
                              options:{grade_overrides:{...currentOverrides, ...trialGrade()}}})
```

## Related Code Files
**Modify**
- `remotion-composer/ui/src/api/client.ts` — thêm interface `LookPreset` (cạnh `AudioSample`
  `:157-161`) + 3 hàm vào object `api` (cạnh `previewGrade` `:400-405`).
- `remotion-composer/ui/src/components/skin-preview.tsx` — dropdown + save box + xử lý lỗi.
- `docs/talking-head-autoedit.md` — mục ngắn: làm mịn da đi qua `skin_smooth`/`blemish_reduce`
  trong grade, preset lưu ở `config/look-presets.json`, và **`tools/enhancement/face_enhance.py`
  không nằm trong luồng này** (nêu lý do DRY/double-encode).

**Create** (chỉ khi vượt 200 dòng)
- `remotion-composer/ui/src/components/look-preset-picker.tsx`

**Delete**: không.

## Implementation Steps
1. `client.ts`: interface `LookPreset` + 3 hàm dùng `json<T>()` sẵn có; `deleteLookPreset` dùng
   `encodeURIComponent(name)`.
2. `skin-preview.tsx`: state `presets`, `presetName`, `selected`, `presetError`.
3. `loadPresets()` gọi trong `useEffect(…, [])`; lỗi ⇒ set `presetError`, panel preview vẫn chạy.
4. Dropdown: `<select>` + `<option>` theo `preset.name`; option đầu "— không dùng preset —".
5. `applyPresetToSliders(preset)`: set 2 slider, thêm vào `touched`, `setSelected(name)`.
6. Save box: `<input>` tên + nút Lưu. Kiểm trùng tên client-side ⇒ `window.confirm("Ghi đè …?")`.
7. Sau save: `await loadPresets()`, `setSelected(name)`, clear ô tên.
8. Nếu bật xoá: nút "Xoá preset" + confirm, sau xoá `setSelected("")`.
9. Cập nhật `apply()` để merge preset + slider (thực tế slider đã mang giá trị preset ⇒ dùng
   `trialGrade()` là đủ; không đọc `preset.grade` lần hai).
10. `make autoedit-ui-typecheck`.
11. Cập nhật docs.
12. Kiểm end-to-end (xem Test Matrix).

## Todo List
- [x] `client.ts`: `LookPreset` + `listLookPresets` / `saveLookPreset` / `deleteLookPreset`
- [x] Dropdown chọn preset → nạp slider (không gọi job) — `look-preset-picker.tsx`
- [x] Nút "Lưu thành preset" + ô tên + confirm ghi đè
- [x] Refresh + auto-select sau khi lưu
- [x] Xử lý lỗi API không làm chết panel preview
- [ ] (tuỳ Q2) nút xoá preset + confirm — cố ý bỏ, `deleteLookPreset` có ở client.ts nhưng chưa có nút UI gọi tới
- [x] `apply()` merge `{...currentOverrides, ...trialGrade()}` — `skin-preview.tsx:97` (`{...currentOverrides, ...extraGrade, ...trialGrade()}`)
- [x] `make autoedit-ui-typecheck` sạch (`npm run typecheck:ui`, không lỗi)
- [x] Cập nhật `docs/talking-head-autoedit.md` (kèm ghi chú face_enhance.py ngoài luồng)
- [ ] E2E: lưu preset ở job A → áp ở job B → resolve ra khác — chưa chạy, cần job thật qua UI

## Test Matrix
| Loại | Ca |
|---|---|
| Typecheck | `make autoedit-ui-typecheck` sạch |
| Manual | Mở tab Da ⇒ dropdown có 3 preset seed |
| Manual | Chọn `da-manh` ⇒ slider nhảy 0.45, ảnh preview đổi, `job.json` KHÔNG đổi |
| Manual | Kéo slider tự do → Lưu tên `da-cua-toi` ⇒ có trong `config/look-presets.json` |
| Manual | Lưu lại cùng tên ⇒ confirm hiện, đồng ý ⇒ 1 entry giá trị mới |
| **E2E (quan trọng)** | Lưu preset ở job A → mở **job B khác** → chọn preset → Áp dụng ⇒ `job B/job.json` có `grade_overrides.skin_smooth`, resolve chạy lại, `src.mp4` mtime đổi |
| Manual | Áp preset khi `grade_overrides` đã có `warmth` ⇒ `warmth` CÒN nguyên (không bị replace) |
| Manual | Tắt server ⇒ dropdown báo lỗi, slider + preview vẫn hoạt động sau khi bật lại |
| Manual | Tên preset có `/` ⇒ server trả 400, UI hiện lỗi, không crash |

## Success Criteria
- Preset lưu ở job A áp được cho job B và làm đổi output thật (không chỉ preview).
- Đổi dropdown KHÔNG kích hoạt resolve.
- Áp preset không xoá các override grade khác.
- `config/look-presets.json` là nguồn sự thật duy nhất của preset; không có option job trùng lặp.
- Docs nói rõ `face_enhance.py` ngoài luồng.

## Risk Assessment
| Risk | L×I | Mitigation |
|---|---|---|
| Đổi dropdown vô tình trigger resolve (~4 phút/lần) | Trung×Cao | Tách rõ 2 nút; ca test manual assert `job.json` không đổi khi chọn preset |
| Áp preset replace cả `grade_overrides`, mất `warmth`/`vignette` | Trung×Cao | Luôn spread `{...currentOverrides, ...}`; có ca test riêng |
| Thêm option `skin_preset` song song ⇒ 2 nguồn sự thật, cache resolve không thấy | Trung×Cao | Cấm rõ trong Key Insight 2; chỉ ghi vào `grade_overrides` |
| Ghi đè preset của người khác không hỏi | Trung×Trung | `window.confirm` client + API trả preset đã lưu |
| Preset store chết làm sập cả panel preview | Thấp×Trung | try/catch riêng, `presetError` tách khỏi `error` |
| Docs lệch sau khi code đổi | Thấp×Thấp | Cập nhật docs trong cùng phase, không để lại sau |

## Security Considerations
- `deleteLookPreset` phải `encodeURIComponent(name)`; server vẫn validate lại (P01
  `validate_name`) — không tin client.
- Không log giá trị nào có thể chứa path/khoá; preset chỉ chứa số.
- Server vẫn `127.0.0.1` + CORS chỉ cho `localhost:5173` (`server/app.py:41-46`) ⇒ không mở rộng.

## Next Steps
- Xong 3 phase ⇒ chạy `/ak:code-review` trên diff, rồi `pytest` toàn bộ.
- Hạng mục sau (chưa làm): preset ở tầng project (3-layer như `assembly_config.resolve`
  `:84-90`), map thời điểm `<Player>` → giây footage gốc bằng span map (`resolve.py:442-443`),
  chọn source để preview trên job nhiều nguồn.
