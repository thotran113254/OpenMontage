# Talking-head auto-edit — nền tảng nhiều nguồn, nhiều bản dựng

Tầng quản lý: transcribe bằng provider nào, spine ghép từ mấy file, nguồn thuộc về đâu, prompt sửa
kiểu gì. Còn *một bản dựng chạy thế nào* (9 stage, độ nét, grade, cắt, verify, render) ở
[`talking-head-autoedit.md`](talking-head-autoedit.md).

Bất biến chung của cả hai file vẫn là một: **chỉ số từ là đồng hồ duy nhất, `resolve` là nơi duy nhất
đổi chỉ số → giây** — xem "Nguyên tắc chính xác" ở file kia trước khi sửa bất cứ gì ở đây.

---

## ASR: Scribe mặc định, Whisper local là đường thoát

`lib/talking_head_edit/asr/` có đúng một điểm vào (`transcribe(path, options)`) và hai provider sau nó.

| | `elevenlabs_scribe` (mặc định) | `whisper_local` |
|---|---|---|
| Thời gian (93s footage) | ~10-30s, 1 call API | vài phút, chạy trên máy |
| Cần gì | `ELEVENLABS_API_KEY` + `pip install elevenlabs` | `faster-whisper` |
| Audio ra khỏi máy | **có** (đã tách thành mp3 mono 64kbps trước khi upload) | không |
| Diarization (ai đang nói) | có | chỉ khi có `whisperx` |
| `keyterms` gợi ý tên riêng | có (≤100 từ) | không |

Đổi bằng `--asr whisper_local` hoặc `options.asr_provider`.

**Fallback có phân loại lỗi, không phải try/except mù:**

- lỗi *transient* (401/403/429/5xx, mạng, thiếu key, chưa cài SDK) → chạy Whisper, **luôn** emit
  cảnh báo và ghi `asr_fallback: true` vào `job.json`. Fallback im lặng biến "thiếu API key" thành
  "sao dạo này transcribe chậm thế" ba tuần sau.
- lỗi *fatal* (4xx tham số sai, file không có lời nào) → **không** fallback. Che nó bằng Whisper là
  che một bug trong chính lệnh gọi của mình.

**Spine v2** — hai field mới đều optional vì Whisper không có:

```jsonc
{"schema": 2, "provider": "elevenlabs_scribe", "model": "scribe_v2", "language": "vi",
 "duration_seconds": 93.2,
 "word_timestamps": [{"word": "xin", "start": 0.42, "end": 0.61, "speaker": "speaker_0"}],
 "audio_events": [{"kind": "laughter", "start": 12.4, "end": 13.1}],
 "speakers": ["speaker_0"]}
```

Ba quyết định về hình dạng spine, mỗi cái đều có lý do cụ thể:

- Scribe trả token `type: "spacing"` cho khoảng trắng. **Bị lọc bỏ.** Để lại thì chỉ số từ mà
  director thấy lệch so với lời nói và **mọi** caption sai chỗ — đây là lỗi tệ nhất có thể xảy ra ở
  file này, nên có test riêng cho nó.
- `type: "audio_event"` (tiếng cười, vỗ tay) tách sang `audio_events[]`, **không** vào spine: tiếng
  cười không phải một từ để neo caption.
- `probability` bị bỏ hẳn. Chỉ Whisper có; đã kiểm không consumer nào đọc. Một field tồn tại một nửa
  thời gian sẽ sinh ra code ngầm cho rằng nó luôn có.

`keyterms` rỗng → tự tách từ `topic` + `card_plan`, giữ token viết hoa/tên riêng/acronym (kể cả 2 chữ
như "OA", "AI" — acronym ngắn là thứ bị nghe sai nhiều nhất), bỏ từ thường.

---

## Nhiều nguồn quay trong một job

```bash
# nhiều file, thứ tự truyền vào = thứ tự ghép
python -m lib.talking_head_edit.cli --input take1.mp4 --input take2.mp4 --input broll.mp4

# hoặc cả thư mục, sắp theo tên
python -m lib.talking_head_edit.cli --input-dir D:/quay/buoi-1 --assembly-mode best_take
```

**Bốn ca dùng, hai trục — không phải bốn nhánh code.** Đây là quyết định thiết kế quan trọng nhất
của phần này: bốn nhánh riêng sẽ thành bốn biến thể prompt `direct` và không ai bảo trì được.

| Trục | Giá trị | Ý nghĩa |
|---|---|---|
| `mode` (loại trừ nhau) | `auto` (mặc định) / `sequential` / `best_take` | ghép tuần tự hay chọn bản tốt nhất |
| `speaker_aware` (cộng thêm) | `auto` / `true` / `false` | đổi khung theo người đang nói |
| `broll_overlay` (cộng thêm) | `true` / `false` | cho phép phủ b-roll lên A-roll |
| `cross_source_cut` | `false` (mặc định) | cho phép cut/card xuyên ranh giới nguồn |
| `take_detect` | `suggest` (mặc định) / `apply` / `off` | tự gộp take hay chỉ gợi ý |

Cấu hình **3 tầng**, ghi đè theo từng key: `config/autoedit-defaults.json` → `project.json` →
`job.options.assembly`. `null` **không** phải một giá trị — nó nghĩa là "chưa đặt", vì client JSON
gửi `{"broll_overlay": null}` dễ hơn là bỏ hẳn key, và coi `null` là `false` sẽ tắt tính năng mà
không có gì trong log giải thích.

**`role` tự đoán, `take_group` chỉ gợi ý.** Hai việc khác nhau về bản chất:

- `role` (`aroll` vs `broll`) đo được rẻ (nguồn có lời nói không? ngưỡng −45 dBFS, lấy cửa sổ to
  nhất trong 3 mẫu 4 giây) và đoán sai thì lộ ngay ở transcript.
- `take_group` (những file nào là các lần quay của cùng một nội dung) chỉ **gợi ý** kèm độ tin cậy.
  Đoán sai ở đây làm `select` xoá nội dung thật. Nên mặc định `take_detect: "suggest"` — người xác
  nhận, máy không tự áp.

**Spine v3** — chỉ số vẫn liên tục từ 0 trên toàn bộ các nguồn, director **không biết** có nhiều file:

```jsonc
{"schema": 3,
 "sources": [{"id":"s0","path":"…take1.mp4","role":"aroll","order":0,"take_group":"main"},
             {"id":"s2","path":"…broll.mp4","role":"broll","speech":false}],
 "takes": [{"src":"s0","w0":0,"w1":811,"take_group":"main"},
           {"src":"s1","w0":812,"w1":1650,"take_group":"main"}],
 "word_timestamps": [{"word":"xin","start":0.42,"end":0.61,"src":"s0"}],
 "overlay_pool": [{"src":"s2","duration":8.0,"label":"b-roll bàn làm việc"}]}
```

`start`/`end` là **giây trong nguồn của chính nó**, không phải giây trên timeline ghép. Có chủ ý:
quy về timeline ghép thì mỗi lần đổi thứ tự nguồn phải sinh lại toàn bộ spine. `resolve` biết `src`
nên tra được file đúng.

**B-roll không vào spine.** Clip im lặng không có từ nào để neo caption; clip có tiếng nói lẫn vào
sẽ chèn những từ không thuộc mạch nói và mọi chỉ số sau đó trỏ vào thứ người nói chưa từng nói.
B-roll đi vào `overlay_pool`.

### `select`: chọn take, và những gì nó từ chối làm

Đầu ra duy nhất `direct` cần là `kept_word_ranges`. Sau `select`, director nhận spine **đã lọc và
đánh lại chỉ số từ 0**, nên prompt structure/captions **không đổi một chữ** khi có nhiều nguồn.
Mỗi từ đã lọc mang `_orig_index` để `resolve` tra lại từ gốc → giây gốc → file gốc. Mất field đó là
lỗi duy nhất của pipeline này không cứu được ở stage sau, nên nó có test round-trip riêng.

Guard cơ học, vi phạm cái nào cũng **fallback về `sequential`** kèm cảnh báo, **không đoán**:

| Guard | Vì sao |
|---|---|
| các khoảng không được chồng nhau | từ bị nhân đôi làm hỏng ánh xạ chỉ số |
| khoảng không được ra ngoài `0..n-1` | model bịa chỉ số |
| tổng giữ ≥ 35% số từ | giữ quá ít = tưởng hai đoạn khác nhau là hai take |
| mỗi nhóm take: bản được giữ nhiều nhất ≥ 60% | cùng lỗi trên, ở mức từng nhóm |
| call model lỗi / JSON rác | thà ghép tuần tự hơn là cắt bừa |

`mode: auto` + model báo không có đoạn trùng → `mode_resolved: "sequential"`. `mode_resolved` luôn
ghi vào `selection_v1.json` + `job.json` để đổi tay được, không phải chạy lại transcribe.

---

## Project: nguồn upload một lần, dựng nhiều bản

```
projects/autoedit/<project_id>/
├── project.json          sources[] + assembly + keyterms + defaults + jobs[]
├── sources/              s0_take-1.mp4  s0.transcript.json  s0.thumb.jpg
└── jobs/<job_id>/        layout job hiện tại — KHÔNG đổi

projects/autoedit-jobs/<job_id>/   legacy: đọc được, không ghi mới, KHÔNG migrate
```

Không có database. Một thư mục = một thực thể, đúng như `JobStore`. Vài chục project trên một máy dev
thì schema + migration là chi phí thuần.

**Điểm khác biệt duy nhất đáng làm:** nguồn thuộc **Project**, không thuộc Job. Trước đây dựng bản
thứ hai là upload lại file; transcript cache tuy dùng chung theo sha nhưng file video thì trùng lặp.

`find_job(job_id)` tra cả hai layout, nên job legacy vẫn mở được ở đúng URL cũ — đó là lý do đọc cả
hai thay vì migrate. Migrate một thư mục render đã xong để đổi lấy sự gọn gàng là đánh cược dữ liệu.

| Endpoint | |
|---|---|
| `GET/POST /api/projects` | list / tạo (validate `assembly` ngay lúc tạo) |
| `GET /api/projects/{id}` | + `assembly_resolved` + `assembly_origin` (giá trị này đến từ tầng nào) |
| `DELETE /api/projects/{id}?confirm=true` | xoá cả jobs bên trong → đòi confirm |
| `POST /api/projects/{id}/sources` | multipart nhiều file, streaming 1 MB/chunk |
| `POST /api/projects/{id}/sources/from-path` | file đã có trên máy → hardlink, không copy 700 MB |
| `PATCH /DELETE /api/projects/{id}/sources/{sid}` | sửa `role/order/take_group/label`; xoá có guard |
| `GET /api/projects/{id}/sources/{sid}/thumb` | thumbnail |
| `GET/POST /api/projects/{id}/jobs` | list bản dựng / tạo bản dựng kế thừa config |
| `GET /api/projects/{id}/take-suggestions` | gợi ý take trùng, **không** tự áp |
| `PUT /api/projects/{id}/settings` | assembly / keyterms / defaults |

Bốn quyết định về upload, mỗi cái vì một lỗi cụ thể:

- **sha256 tính trong cùng một lượt ghi** — đọc lại file 500 MB để băm là đọc đĩa hai lần cho một con số.
- **ghi `.part` rồi `os.replace`** — upload đứt giữa để lại mp4 cắt cụt mà ffprobe *vẫn nhận*, rồi mọi
  stage sau đọc sai. `.part` sót lại được dọn lúc server khởi động.
- **một file lỗi không huỷ cả lô** — mất 4 upload 200 MB tốt vì file thứ 5 sai đuôi là đánh đổi sai.
- **chỉ `role/order/take_group/label` sửa được** — mọi field khác là số đo; cho client ghi đè số đo là
  đặt một phỏng đoán vào chỗ của một sự thật.

`label` mặc định lấy **tên người dùng upload**, không phải tên trên đĩa: file lưu là `s0_intro.mp4`
và tiền tố `s0_` sẽ làm hai take của cùng nội dung trông như hai chủ đề khác nhau khi dò take trùng.

**Bản dựng nhận HẾT nguồn, aroll trước.** `create_job` từng chỉ đưa nguồn aroll vào `input_paths`;
hệ quả là `overlay_pool` luôn rỗng và tính năng b-roll không bao giờ chạy được từ UI. B-roll phải vào
job để `probe` phân loại và `spine_build` đẩy nó sang `overlay_pool`. Aroll đứng trước vì
`primary_input_path` (preview màu, calibrate) nghĩa là "footage", và đó phải là nguồn có tiếng.

---

## Prompt là dữ liệu sửa được

```
prompts/
├── registry.json                  bản nào đang dùng + changelog
├── talking_head/*.v1.md           bản gốc (git)
└── overrides/talking_head/*.md    bản admin sửa (.gitignore)
```

**Không dùng Jinja2 hay `str.format`.** Prompt đầy `{"w0":i,"w1":j}` của JSON schema —
`str.format` sẽ nổ trên tất cả, và escape `{{` khắp nơi chính là thứ làm bản f-string cũ khó đọc.
Dùng `{{name}}` + `str.replace` tuần tự. `render()` raise nếu **thiếu** biến hoặc **còn sót**
placeholder: một `{{spine}}` lọt ra tới model là lỗi phải chết ngay, không phải phát hiện sau.

**Gate byte-identical:** 5 template phải render **giống từng byte** với bản f-string cũ cho cùng
input. Golden nằm ở `tests/fixtures/prompts/`, được chụp **trước khi** thay f-string — nên nó là mốc
thật, không phải code mới tự đồng ý với chính nó.

| Prompt | Bản hiện dùng | Đổi gì |
|---|---|---|
| `structure` | v2 | thêm `{{broll_rule}}` — rỗng khi job không có b-roll, nên v2 = v1 cho job một nguồn |
| `captions` | v1 | — |
| `cut_verify` | v2 | thêm verdict thứ ba `unsure` |
| `cut_verify_look` | v1 | vòng 2 cho `unsure`: đọc dạng sóng, quyết dứt khoát |
| `revise` | v2 | thêm `{{history}}` — 3 lượt trước, rỗng ở lượt đầu |
| `select_take` | v1 | — |

**Sửa prompt PHẢI làm `direct` chạy lại.** `cache_signature("direct")` băm
`prompt_registry.fingerprint(["structure","captions"])` + bảng sfx/bgm. Không có nó thì sửa prompt
không thay đổi gì quan sát được, và kết luận tự nhiên là "prompt không quan trọng" — đây là thứ dễ
quên nhất của cả thiết kế, và có triệu chứng gây nhầm lẫn nhất.

`job.json` ghi `prompt_versions` để bản dựng cũ giải thích được. Không có nó thì "hôm qua ra đẹp hơn"
là câu không tra được.

### A/B: so hai bản trên cùng spine

**Không render.** 6 phút × 2 là vô dụng cho vòng lặp, và render nằm dưới mọi thứ prompt điều khiển.
So ở tầng `spec` + `audit` — chỗ đã đo sẵn chất lượng cut, tài nguyên, phủ caption, số card.

**Verdict bằng luật cơ học, không hỏi model.** Một model chấm hai prompt của chính nó là vòng lặp
không có tín hiệu độc lập nào. Thứ tự luật = thứ tự các lỗi đó thật sự làm hỏng video:

1. đủ số card theo brief — thiếu một card là thiếu một đoạn của video
2. phủ caption — chỗ không có caption là chỗ không có phụ đề
3. caption quá 9 từ — tràn pill, bị cắt trên màn hình
4. keyword bị card che — thứ tự layer làm nó vô hình
5. cut bị verifier loại ít hơn — prompt đề xuất cut an toàn hơn

Hai nhánh **dùng chung caption**: caption là nhánh dài và đắt nhất; trả tiền hai lần để so thứ mà cả
hai version đều không đổi là phí. Chi phí dự kiến hiện **trước** khi chạy.

---
