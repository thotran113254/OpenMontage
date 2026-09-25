# Talking-head auto-edit

Tự động dựng video ngắn từ footage talking-head quay một mạch: cắt từ đệm, caption chạy chữ,
card giải thích, keyword bay, punch-in, cold-open, endcard, nhạc nền — rồi render bằng Remotion.

Chạy được 3 cách: CLI, web UI, hoặc qua registry (`talking_head_autoedit`).

Ý khách đi qua pipeline ra sao, mức cắt, phép thử mù phần nhìn, ngân sách CPU:
[`talking-head-autoedit-intent-quality.md`](talking-head-autoedit-intent-quality.md).

---

## Nguyên tắc chính xác (đừng phá)

Toàn bộ độ chính xác của pipeline dựa trên **một đồng hồ duy nhất**:

1. **Word spine là đồng hồ.** ASR cho timestamp từng từ. AI director **không bao giờ** phát ra
   giây — chỉ chỉ số từ. Đo thực tế: timestamp do model tự đoán trôi ~±0.6s và làm tròn về giây.
2. **`stages/resolve.py` là nơi DUY NHẤT** đổi chỉ số từ → giây, và là nơi duy nhất cắt media.
3. **Cắt hình và tiếng tại cùng biên**, bằng `atrim`+`concat`. **Không dùng `aselect`** — build ffmpeg
   trên máy này im lặng cho qua toàn bộ audio frame (đã kiểm chứng), làm tiếng lệch hình.
   Sau khi cắt có assert lệch A/V < 0.35s.
4. **Padding an toàn** `PAD=0.08s` cạnh mỗi mối cắt, bỏ qua cut ngắn hơn `MIN_CUT=0.28s`
   (`resolve_spans.py`) — cut hụt mức này bị báo `qua_ngan` thay vì âm thầm giữ nguyên.
5. **Mọi cut đều bị kiểm lại** bởi một call LLM riêng (`cut_verifier.py`). Không kiểm được → không cắt.
6. **Chỉ tài nguyên có thật** mới được dùng: `remotion-composer/src/mona/resource-manifest.json` là
   nguồn sự thật chung cho cả renderer (TS) và pipeline (Python).

---

## 9 stage

| Stage | Làm gì | Thời gian thực đo (video 93s) |
|---|---|---|
| `probe` | ffprobe từng nguồn, gán `role`, chặn sớm nếu **không nguồn nào** có tiếng | ~1s/nguồn |
| `transcribe` | ASR word timestamps (song song N nguồn), cache theo hash file + engine | 0.06s khi cache, ~10-30s Scribe, vài phút Whisper |
| `select` | chọn take tốt nhất khi nhiều nguồn cùng nội dung; 1 nguồn = no-op | 0s (1 nguồn), ~8s khi có take trùng |
| `direct` | AI dựng khung + caption (nhiều call song song) | ~24s |
| `audit` | LLM kiểm từng cut + kiểm tài nguyên có thật + đo chất lượng | ~7s |
| `calibrate` | **TẮT mặc định** — trượt phép thử mù, xem phần "Gemini có thực sự nghe/nhìn được để chấm chất lượng không" | ~35s khi bật |
| `resolve` | ffmpeg cắt/grade/tempo/cold-open + ánh xạ mọi mốc thời gian | ~4 phút |
| `render` | Remotion → final.mp4 | ~370s @1080x1920 |
| `verify` | đo lại file: luma, LUFS, lệch A/V, độ dài, nhạc nền có vào mix không, **video có bị giật không**, b-roll có thật xuất hiện không, ảnh soi mối nối đáng nghi | ~25s |

Ngoài chuỗi trên còn stage `revise` — vá bản dựng theo yêu cầu bằng lời (xem dưới).

### Vì sao `direct` chia thành nhiều call

Ban đầu gộp một call làm hết. Kết quả đo được: model đốt 6.4k token suy luận, xuất 54 caption rồi
**bỏ luôn card và cut** dù brief yêu cầu 4 card. Tách ra:

- 1 call **khung**: card, keyword, punch-in, sfx, cold-open, endcard, bgm, grade, đề xuất cut
- N call **caption**, mỗi call phụ trách một dải từ (~110 từ), cắt tại chỗ im lặng dài nhất

Chạy song song nên không chậm hơn. Kết quả sau khi tách: 4/4 card, caption phủ 100%, **0 caption quá
9 từ** (trước là 7).

---

## Chạy bằng CLI

```bash
# dựng mới
make autoedit INPUT="C:/Users/.../quay-goc.mp4" PROMPT="nhấn mạnh phần chi phí"

# hoặc gọi thẳng, đầy đủ tuỳ chọn
python -m lib.talking_head_edit.cli \
  --input footage.mp4 \
  --topic "3 sai lầm khi làm chatbot AI bán hàng" \
  --card-plan "4 card: 3 sai lầm (badge 1/2/3) + 1 giải pháp (badge 4)" \
  --brand-pill "3 SAI LẦM • CHATBOT AI" \
  --whisper-model medium --language vi

# chạy tiếp / chạy lại một phần
python -m lib.talking_head_edit.cli --job <job_id> --stage-from resolve
python -m lib.talking_head_edit.cli --job <job_id> --stages audit,resolve --no-cache
python -m lib.talking_head_edit.cli --job <job_id> --revise "bỏ card thứ 2, đổi nhạc êm hơn"

# tự chạy hết + tự sửa MỘT lần nếu verify fail (lỗi không có remedy thì dừng và báo)
python -m lib.talking_head_edit.cli --job <job_id> --autopilot

# sửa có ngữ cảnh 5 lượt trước (khác --revise: --revise không nhớ gì)
python -m lib.talking_head_edit.cli --job <job_id> --chat "bỏ card 2"
python -m lib.talking_head_edit.cli --job <job_id> --chat "thêm lại card đó" --dry-run
python -m lib.talking_head_edit.cli --job <job_id> --chat-history

# ảnh soi một khoảng: dải frame + dạng sóng + nhãn từ, vạch đỏ = mối cắt
python -m lib.talking_head_edit.cli --job <job_id> --timeline-view 25.5 28.5

python -m lib.talking_head_edit.cli --list        # các job đã có (cả 2 layout)
python -m lib.talking_head_edit.cli --projects    # các project
python -m lib.talking_head_edit.cli --resources   # kho sfx/bgm dùng được
```

**Dựng trong một project** (nguồn upload một lần, dùng cho nhiều bản dựng):

```bash
python -m lib.talking_head_edit.cli --project <project_id> --prompt "nhấn phần chi phí"
```

Không có `--project` thì job vẫn nằm ở `projects/autoedit-jobs/` như trước — luồng CLI cũ không đổi.

## Render trên cloud (Vast.ai)

Thêm `--render-location cloud` để thuê một máy CPU Vast.ai render thay cho máy local — hữu ích khi
render local ước tính vượt ~12 phút (2x overhead thuê máy ~6 phút), hoặc khi đã có ≥3 job xếp
hàng cho batch. Cloud **không bao giờ chạy im lặng**: cần thêm `--cloud-offer <id>` (chọn đúng
offer) hoặc `--cloud-yes` (chấp nhận offer đề xuất), nếu không CLI in ra offer + chi phí ước tính
rồi thoát với mã lỗi khác 0 — không hỏi lại (CLI này chạy không có tty khi server gọi làm
subprocess). Muốn render nhiều job trên một lượt thuê máy chung: `--cloud-queue` để xếp vào batch,
`--cloud-queue-status` để xem, `--cloud-flush --cloud-yes` để buộc render cả batch ngay. Xem
`docs/cloud-render.md` để biết cách cài đặt, chi phí đo được, và cách xử lý khi có sự cố (`make
cloud-render-reap`).

## Chạy bằng web UI

```bash
make autoedit-server      # API + media (port AUTOEDIT_PORT, default 8861)
make autoedit-ui          # giao diện (port AUTOEDIT_UI_PORT, default 5617)

# hoặc 1 lệnh duy nhất (không cần make, không cần 2 terminal):
python run_autoedit_dev.py   # tương đương make autoedit-dev
```

Trên VPS này (xem `CLAUDE.md`): bind `0.0.0.0`, public IP `<VPS_HOST>`, API cần `AUTOEDIT_API_TOKEN`.

- UI: http://<VPS_HOST>:5617
- API: http://<VPS_HOST>:8861
- Local: http://127.0.0.1:5617

Đặt `AUTOEDIT_API_TOKEN` thì mọi `/api/*` (trừ `GET /api/health`, `/openapi.json`, `/docs`, `/redoc`)
đòi `Authorization: Bearer <token>` / `X-API-Key: <token>`, hoặc `?token=` trên GET (vd `<video src>`) —
so token kiểu constant-time (`server/auth.py`). Không đặt token thì mở như trước, chỉ nên dùng khi máy
chỉ có người tin cậy truy cập. Vite proxy `/api` nên dùng UI origin là đủ, UI tự thêm header khi cần.

`run_autoedit_dev.py` chạy song song cả job server và Vite dev server, gộp log
có tiền tố `[server]`/`[ui]`, Ctrl+C tắt cả hai (kill nguyên cây process —
Vite bọc node, `taskkill /T` mới diệt hết, giống cách `queue_worker.py` huỷ
một render đang chạy).

Trong UI — luồng project:

1. **Tạo project** → upload A-roll (+ b-roll) → tab Cấu hình (assembly/keyterms/defaults).
2. **+ Dựng bản mới** mở wizard: option (prompt, topic, ASR, tempo, bgm, `cut_level`…) + **cách chạy**
   (`RUN_MODES`, `remotion-composer/ui/src/lib/pipeline-plan.ts`):
   - *Xem trước (khuyên dùng)* — stages đến `resolve`, có preview; **không** render MP4.
   - *Xem trước rồi xếp lịch cloud* — như trên, khi xong tự enqueue batch cloud (chưa thuê máy).
   - *Xuất MP4 luôn (máy này)* — cả pipeline gồm render, không dừng giữa chừng để duyệt.
   - *Chỉ tạo job — chạy tay từng bước* — `run=false`, trang job bấm từng nhóm (LLM → encode → render).
3. Trang job: thanh 3 phase (Lọc nội dung / AI dựng khung / Cắt & xuất) + stage board (nhóm LLM /
   encode / render, nhãn thuần Việt). Trước khi có preview thì nút chính là **Cắt & xem trước**; có
   preview rồi thì job mở thẳng tab **Chat** (sửa tiếp là việc thường làm hơn là chạy lại stage), nút
   chính đổi thành **Xuất MP4**; xong thì có **Tải MP4**. Ba nút render trong tab tương ứng:

| Nút (trang job) | Ý nghĩa |
|---|---|
| **Xuất MP4 trên máy này** / Nháp 540p | Chạy ngay trên máy, FIFO queue local, $0 (+ confirm) |
| **Xếp lịch cloud** | Đưa job vào batch queue (free, không thuê máy) |
| **Cloud ngay…** | dry_run + modal xác nhận chi phí → thuê 1 máy cho job này |

Trang **Lịch render** (`#/cloud`): batch queue, ngưỡng flush, **Render batch ngay** (checkbox
xác nhận). Không cron cuối ngày. API: `/api/cloud/*`; create build: `POST .../jobs` body
`{ options, stages, run }`.

---

## Gemini có thực sự nghe/nhìn được để chấm chất lượng không

**Không.** Blind test cho cả tiếng lẫn hình: model đọc đúng lời/nhìn đúng ảnh, nhưng không phân biệt
được chất lượng kỹ thuật ở mức pipeline này tạo ra — nhãn trung tính thì lift gần 0 và hai bản giống
hệt nhau vẫn bị chấm lệch nhau vài điểm. Hệ quả trực tiếp: stage `calibrate` **tắt mặc định**; số đo
hiện dùng (grade mặc định `{}`, `auto_grade`/`auto_sharpen` tắt) đến từ đo đạc cộng mắt/tai người,
không phải từ model. Chạy lại phép thử bất cứ lúc nào:

```bash
python -m lib.talking_head_edit.cli --job <id> --check-hearing --at 41     # tiếng
python -m lib.talking_head_edit.cli --job <id> --check-seeing --at 60      # hình
```

Phương pháp, số liệu đầy đủ, và cách `calibrate` dùng model khi được bật thủ công:
[`talking-head-autoedit-intent-quality.md`](talking-head-autoedit-intent-quality.md#model-có-nghe-được-để-chấm-chất-lượng-tiếng-không--phép-thử-mù).

Muốn bật lại chọn tự động theo từng video: `calibrate_grade` / `calibrate_audio`, sau khi phép thử
mù cho kết quả tốt trên model bạn dùng. Calibrate chỉ merge lựa chọn của nó *vào dưới*
`grade_overrides` đang có — thông số bạn tự đặt không bị ghi đè; tắt hẳn bằng `--no-calibrate`.

---

## Hình ảnh: giữ nguồn làm mặc định

`build_grade_chain({})` chỉ resize bằng Lanczos. Không tự khử nhiễu theo thời gian,
không làm mịn da, thêm S-curve, tối góc, unsharp hoặc CAS. Bộ lọc chỉ chạy khi khoá
tương ứng được yêu cầu. `skin_smooth` và `blemish_reduce` độc lập; không ngầm kích hoạt
khử nhiễu có thể làm nhoè mặt khi chuyển động.

| Thông số | Mặc định |
|---|---|
| brightness / warmth | 0 |
| contrast / saturation / gamma | 1 |
| skin_smooth / blemish_reduce | 0 |
| tone_curve / vibrance / vignette | 0 |
| sharpen / clarity | 0 |
| auto_grade / auto_sharpen | false (thiếu hoặc null cũng không bật) |

Prompt structure (từ v7, giữ nguyên ở v8 hiện dùng) yêu cầu `grade: {}` cho bản dựng thông thường. Chỉ đề xuất
chỉnh màu/da/nét khi người dùng yêu cầu. Không tự thêm shake/flash; punch-in là
tuỳ chọn nhẹ, không phải hạn ngạch. PiP không còn phóng “thở” hoặc nảy theo bullet;
chuyển bố cục và punch/shake có event cụ thể vẫn hoạt động. Cold-open được bật riêng
vẫn giữ hiệu ứng nối teaser.

Không ghi đè options/spec của job đã lưu. Job cũ có `auto_grade: true`,
`auto_sharpen: true` hoặc grade trong spec vẫn áp những lựa chọn đó. Muốn đưa một
job cũ về trung tính, tắt hai cờ auto và ghi giá trị trung tính ở bảng trên vào
`grade_overrides`, rồi resolve/render lại; file đã render không tự thay đổi.

### Tự dò màu/nét — chỉ khi bật

`auto_grade: true` đo brightness/gamma/warmth theo từng nguồn. `auto_sharpen: true`
đo độ mềm và viền sáng rồi chọn sharpen/clarity. Đây là phép đo kỹ thuật, không phải
đảm bảo hình đẹp hơn. Giá trị người dùng trong `grade_overrides` được ưu tiên.
Nếu không đo được độ nét, giữ grade đã yêu cầu, không tự thêm mức suy đoán.

Vẫn giữ các biện pháp hạn chế mất chất lượng encode: JPEG trung gian 100,
`intermediate_crf: 12`, `render_crf: 17` và encode A-roll ở kích thước hiển thị.
Đây là bảo toàn dữ liệu, khác với tăng tương phản để tạo cảm giác nét.

---

## Xem thử trước khi render full

Render full 1080x1920 mất ~5 phút — quá chậm để chỉnh màu hay bố cục. Ba mức xem thử, dùng từ nhanh
đến chậm:

```bash
# 1. MÀU — 1 frame từ footage gốc qua chuỗi grade (~1 giây/biến thể)
python -m lib.talking_head_edit.cli --job <id> --preview-grade --at 23.5
python -m lib.talking_head_edit.cli --job <id> --preview-grade --grade-json '{"warmth":4,"vibrance":0.15}'

# 2. BỐ CỤC — 1 frame qua Remotion: đủ khung, caption, card (~25 giây)
python -m lib.talking_head_edit.cli --job <id> --preview-still 750

# 3. DUYỆT — clip ngắn có tiếng, nửa kích thước (~30 giây cho 8 giây video)
python -m lib.talking_head_edit.cli --job <id> --preview-clip 8 --clip-from 20

# ưng rồi mới render full
python -m lib.talking_head_edit.cli --job <id> --stages render,verify --no-cache
```

`--preview-grade` in kèm số đo: **độ sáng** và **ám ấm** (`warm_bias`, chênh lệch kênh đỏ/xanh dương).
So với dòng `raw` để biết grade đang đẩy ảnh đi bao xa. Ví dụ thật: khi bản dựng bị "cháy vàng",
ám ấm là 22.6 so với 15.1 của gốc — nhìn số là thấy ngay, không cần tranh cãi bằng mắt.

Mọi thứ xem thử ghi vào `preview/` trong thư mục job, không đụng tới `final.mp4`.

### Trên web UI: panel "Thử màu & tiếng trước khi cắt lại"

Ba mức trên có sẵn trong UI (`look-preview.tsx`, 3 tab), vì **Remotion Player không thể thay chúng
được**: grade và audio preset đã bị ffmpeg nướng vào `src.mp4` từ stage `resolve`, nên tới lúc Player
đọc file thì quyết định đã chốt rồi — đổi một con số là mất ~4 phút resolve để xem lại.

| Tab | Chạy trên | Thời gian đo thật |
|---|---|---|
| **Màu** | frame footage GỐC qua chuỗi grade, kèm số đo vùng mặt | 4.0s / 3 bản · **0.10s** khi bấm lại · 1.5s khi đổi 1 bản |
| **Tiếng** | 5 preset, mỗi bản 8s tiếng từ footage gốc | 3.5s · tức thì khi bấm lại |
| **Clip duyệt** | Remotion thật, nửa kích thước, đúng crf của bản cuối | 47.8s / 4s video |

Ảnh và mẫu tiếng cache theo tham số; ảnh còn chứa chuỗi filter thực tế trong cache key
để thay đổi logic xử lý không trả lại ảnh cũ. Giữ 60 frame mới nhất rồi xoá dần.

`render_grade_context()` dùng đúng kích thước A-roll (khung dark: 1012x1800).
Ảnh `raw` chỉ resize, không làm nét hoặc làm mịn ngầm.

Thứ tự độ nét: override của bạn → số đo nếu đã bật `auto_sharpen` và có báo cáo →
thông số grade đã yêu cầu (thiếu thì không làm nét). UI không còn báo “suy từ tỉ lệ
phóng”. Ảnh preview chưa qua hai lượt encode nên vẫn cần xem clip để chấm chuyển động.

Chọn xong thì bấm **Áp dụng & cắt lại** — nó ghi vào `grade_overrides` / `audio_preset` rồi chạy lại
`resolve`. `grade_overrides` chỉ lưu **phần bạn đổi**, không lưu bản đã merge: overrides nằm *trên*
grade do director sinh (`resolve.py`), nên lưu bản merge sẽ khoá director khỏi mọi lần dựng lại sau.

Hai thứ **không** đưa lên UI, có lý do:

- `composition_still` (~25s cho 1 frame) — Player cho mọi frame tức thì, nên nó chỉ chậm hơn.
- `judge_audio` — phép thử bịt nhãn đã chứng minh model không chấm được chất lượng kỹ thuật (nó chấm
  lệch 1.8–3.2 điểm giữa hai file giống hệt nhau). Đưa lên UI là hợp pháp hoá một tín hiệu đã biết là
  nhiễu.

Panel dùng đúng `preview.py` mà CLI dùng, `POST /api/jobs/{id}/preview/{grade,audio,clip}`, ảnh và
tiếng phục vụ qua `GET /api/media/{id}/preview/{name}`.

### Preset da dùng lại được cho mọi job

Làm mịn da **không phải một tool riêng** — nó đi qua đúng 2 khoá `skin_smooth`/`blemish_reduce`
trong chuỗi grade như mọi thông số màu khác (xem "Khung bao và grade" dưới đây). Panel "Da" thêm
một dropdown preset trên tab Da: chọn preset chỉ nạp giá trị vào 2 slider để xem thử (không đụng
job), còn nút **"Áp dụng & cắt lại"** mới thật sự ghi vào `grade_overrides` và chạy lại `resolve`.

Preset lưu ở `config/look-presets.json` — global, không theo job, nên lưu ở job A rồi áp lại được
ở job B/video khác bất kỳ. Mỗi preset chỉ chứa **fragment** grade (đúng 2 khoá da), ghi đè
`grade_overrides` hiện có bằng cách merge (`{...currentOverrides, ...presetGrade}`), không thay
thế toàn bộ — giữ nguyên `warmth`/`vignette` bạn đã chỉnh ở tab Màu. Xoá preset hiện chưa có nút
riêng trên UI: sửa trực tiếp `config/look-presets.json`.

**`tools/enhancement/face_enhance.py` KHÔNG nằm trong luồng này.** Nó là một bản triển khai
smartblur độc lập, khác tham số (`skin_smooth_strength`, `lr/ls/lt`) với chuỗi grade thật
(`build_grade_chain`). Nối nó vào panel này sẽ chạy thêm một lượt encode + làm mịn da **lần thứ
hai** trên cùng khung hình đã mịn rồi — vừa chậm gấp đôi vừa vi phạm DRY (hai nơi cùng làm một
việc, khác công thức). Tool đó vẫn còn trong repo nhưng không có pipeline nào gọi nó.

---

## Khung bao và grade

**Khung bao** (`--frame`, mặc định `none` = full khung, kể cả các mẫu dựng): chọn `dark`/`light`/`blur`
thì footage được lùi vào trong, đặt trên một nền, bo góc và viền mảnh. Tác dụng thật: căn phòng xấu thôi chiếm trọn khung hình và mắt người xem bị đẩy về phía
mặt — bố cục trông có chủ đích thay vì như quay vội.

| Preset | Trông thế nào | Khi nào dùng |
|---|---|---|
| `dark` | nền xanh đen + viền trắng bo góc | mặc định, hợp hầu hết footage |
| `light` | nền xám sáng | phòng tối, muốn nhẹ nhàng |
| `blur` | chính footage làm nền, làm mờ + tối | phòng có màu đẹp; **tốn thêm 1 lần giải mã video mỗi frame** |
| `none` | tràn viền như cũ | footage đã đẹp sẵn |

Lưu ý về `blur`: nếu phòng sáng trắng thì nền mờ ra xám bệt, nhìn tệ hơn `dark` — đã thử và loại.

**Grade** chạy trong ffmpeg ở stage `resolve`, mặc định chỉ resize. Các bước
tone curve, vibrance, vignette, mịn da và làm nét đều phải được yêu cầu riêng;
xem [Hình ảnh: giữ nguồn làm mặc định](#hình-ảnh-giữ-nguồn-làm-mặc-định).
Không chồng bộ lọc làm mịn rồi tăng nét để cố khôi phục texture đã mất.

---

## Làm nét

`sharpen` dùng unsharp bán kính 3×3; `clarity` dùng CAS. Cả hai mặc định 0.
Chỉ tăng khi đã xem đối chiếu với nguồn, nhất là tóc, viền mặt và texture da.
Làm nét tăng tương phản cạnh, không khôi phục chi tiết thật đã mất.

Giữ chất lượng encode trước khi thêm bộ lọc: `intermediate_crf: 12`,
`render_crf: 17`, `render_jpeg_quality: 100`. Không tự bật AI upscale hoặc
beautification cho video gốc.

---

## Sửa bằng yêu cầu (revise)

Gửi một câu tiếng Việt, AI trả về **bản vá** cho spec hiện tại thay vì dựng lại:

```
"Bỏ card thứ 2 (Kỳ Vọng Quá Cao). Đổi nhạc nền sang bgm_energy_drive.mp3."
→ v3: bỏ 1 event, đổi bgm, 3 card còn lại + 56 caption giữ nguyên tuyệt đối
→ 7.960 token (so với 55.410 của một lần dựng đầy đủ — bằng 14%)
```

Event không có id ổn định, nên bản vá định vị theo `(loại, chỉ số từ)`. Địa chỉ trỏ vào 0 hoặc >1
event thì **bị từ chối**, không đoán. Mỗi lần vá tạo một version mới; `rollback` cũng tạo version
mới chứ không xoá lịch sử. Revise đổi được một số option (khung, tempo, mức cắt, cold-open), thêm/bỏ
cut cộng dồn, và phải báo ý nào chưa làm — xem
[`talking-head-autoedit-intent-quality.md`](talking-head-autoedit-intent-quality.md#revise-được-sửa-gì).

---

## Cache

| Đổi thứ này | Chạy lại từ |
|---|---|
| prompt / topic / card_plan / model / cut_level | `direct` |
| asr_provider / asr_model / whisper_model / keyterms / ngôn ngữ / file nguồn | `transcribe` |
| tempo / kích thước / cold_open / bgm | `resolve` |
| chỉ scale render | `render` |

Transcript cache dùng chung theo sha256 file nguồn (`output/transcript_cache/`) — nộp lại cùng
footage với prompt khác thì không chạy lại ASR. Tên file cache là
`<sha16>_<provider>_<model>_<lang>.json`: hai engine cho biên từ khác nhau nên **không** dùng chung
một ô cache. Transcript Whisper cache từ trước khi tách provider (tên không có provider) vẫn được
đọc lại, không mất.

---

## Nền tảng nhiều nguồn / nhiều bản dựng

ASR provider (Scribe / Whisper), spine nhiều nguồn, `select`, Project và prompt registry nằm ở
[`talking-head-autoedit-platform.md`](talking-head-autoedit-platform.md). Tách ra vì đó là tầng
*nhiều nguồn → nhiều bản dựng*, còn file này là *một bản dựng, từ footage đến file giao*.

---

## Cắt per-span: video encode ĐÚNG MỘT LẦN

Ba module, phụ thuộc một chiều, tách theo *kiểu hỏng* của mỗi phần:

| Module | Sở hữu | Hỏng thì thế nào |
|---|---|---|
| `resolve_media.py` | **công thức**: chuỗi grade, preset audio, các con số đã đo | trông xấu / nghe xấu — mọi hằng số ở đây là một phép đo, comment ghi đo trên gì |
| `resolve_spans.py` | **chọn đoạn nào**: `Span`, `source_runs`, `plan_spans`, `compute_removes` | rơi vào giây sai của **file sai** — thuần số học, test không cần media |
| `resolve_cut.py` | **gọi ffmpeg**: `extract_span`, concat, master audio | mất chất lượng hoặc ra file stage sau đọc không được |

`resolve_spans` biến chỉ số từ thành giây; `resolve_cut` tiêu những giây đó bằng ffmpeg và đọc công
thức từ `resolve_media`. Không có mũi nào ngược lại. Ranh giới spans/cut chọn theo cách một bug bị
phát hiện: sai span là sai một con số, đọc ra được; sai encode thì phải có file mới thấy.

```
spans (nguồn, giây bắt đầu, giây kết thúc)
   │
   ├─ [song song, ≤4] mỗi span: grade(src) + trim + setpts tempo   (video)
   │                            + afade 20ms + atempo              (audio thô)
   │                            → seg_000.mp4     ← LẦN ENCODE VIDEO DUY NHẤT
   │
   ├─ concat demuxer -c copy → joined.mp4
   │
   └─ ffmpeg -i joined -c:v copy -af "<cleanup,compressor,limiter,loudnorm,aresample>"
              → src.mp4                            ← audio encode lại, VIDEO COPY
```

**Vì sao bước cuối phải `-c:v copy`, và vì sao nó được assert chứ không được tin:** cách làm hiển
nhiên là encode span → concat → encode lại để chạy loudnorm. Như thế video encode **2 lần** và xoá
sạch toàn bộ công đo độ nét. `apply_master_audio` so bitrate luồng video trước/sau, lệch > 2% là
raise — một filter vô tình đặt lên nhánh video sẽ bị bắt tại chỗ thay vì lộ ra 6 phút sau ở bản render.

**Tách `build_audio_chain` làm hai, theo tính chất của từng filter:**

| Chain | Filter | Vì sao ở đó |
|---|---|---|
| **span** | `afade` 20ms in/out, `atempo` | tuyến tính, không phụ thuộc ngữ cảnh; `atempo` PHẢI ở đây để video và audio ra khỏi span cùng độ dài |
| **master** | cleanup preset → `acompressor` → `alimiter` → `loudnorm` → `aresample` | `loudnorm` đo theo cửa sổ: chạy per-span thì mỗi span có gain riêng → **âm lượng nhảy bậc ở mỗi mối cắt** |

`build_audio_chain(preset, tempo)` vẫn tồn tại và bằng đúng `master + ",atempo=…"` — byte-identical
với bản trước khi tách, nên so LUFS giữa hai đường là so cùng filter cùng thứ tự. Đo thật: **lệch
0.0 dB**.

**Cửa sổ của mỗi run** (bug thật mà chỉ phép đo bắt được, test không bắt):

- run **ĐẦU** giữ từ giây 0; run **CUỐI** giữ tới hết file nguồn
- run **giữa** word-tight + `PAD` — đó là mối nối giữa hai take, mang tiếng im lặng cuối take này
  vào từ đầu tiên của take sau là một khoảng lặng không ai yêu cầu

Ban đầu làm word-tight cho mọi run: bản dựng 90.8s tụt còn 89.0s vì mất tiếng phòng đầu/đuôi. Số đo
mới lộ ra, không có test nào bắt được.

### Số đo thật khi đổi sang per-span (footage 93s, 1 nguồn)

| | Cũ (một filtergraph) | Mới (per-span) |
|---|---|---|
| `src.mp4` face-crop detail | 12.794 | **12.943** (+1.2%) |
| `src.mp4` LUFS | −14.9 | **−14.9** |
| `final.mp4` face-crop detail | — | **2.964** (ngưỡng 2.85) |
| `verify` | — | pass, 0 issue |

**Fast path `-c copy`** chỉ bật khi **toàn bộ** span đủ điều kiện (không grade, tempo = 1.0, đúng
kích thước đích, codec h264). Trộn segment copy với segment encode cho ra file mà concat demuxer từ
chối ghép.

`grade_overrides` nhận cả hai dạng — phẳng (áp cho mọi nguồn, dạng cũ) và theo nguồn:
`{"__all__": {"warmth": 4}, "s1": {"brightness": 0.03}}`. `auto_sharpen` đo **per-source**: hai điện
thoại khác nhau có độ mềm khác nhau, một con số đo trên file đầu là sai cho các file còn lại.

---

## Soi mối nối: `timeline_view`

```
┌──────────────────────────────────────┐
│ [f1][f2][f3]‖[f4][f5][f6]            │  dải frame, 6 khung đều nhau
├──────────────────────────────────────┤
│ ▁▂▅█▇▃▁    ‖ ▁▂▃▅▇█▅▂▁               │  dạng sóng (showwavespic)
├──────────────────────────────────────┤
│ khách hàng cần trả lời ‖ những cái… │  nhãn từ, ‖ = mối cắt
└──────────────────────────────────────┘
```

**Đây KHÔNG phải nhờ model chấm chất lượng.** Phép thử mù đã chứng minh nó không nghe/nhìn được chất
lượng kỹ thuật. `timeline_view` trả lời câu khác: *quanh mốc này có gì* — thứ các phép đo cơ học không
nói được. `verify` đo được "15% frame lặp" nhưng không nói **ở đâu**; ảnh này biến báo cáo thành thứ
hành động được.

**Dạng sóng là phần giá trị nhất, không phải dải frame.** Câu hỏi thật ở mỗi mối là "chỗ này có im
lặng không, cắt vào có hụt phụ âm không" — dạng sóng + nhãn từ trả lời ngay.

**Nhãn từ map qua `output_time_mapper`**, không dùng `word.start`. `word.start` là giây trong **file
nguồn**; trên bản dựng đã cắt hai thứ đó cách nhau xa. Đo thật: job cắt ít thì "gần đúng", nên bug này
chỉ lộ ra khi xem ảnh, không test nào bắt.

`verify` tự sinh ảnh ở mối **đáng nghi**, tối đa **6** ảnh (40 mối × 1 ảnh là rác, không phải báo cáo):

| Luật | Bắt lỗi gì |
|---|---|
| đỉnh biên độ trong 50ms quanh mối > nền + 12 dB | tiếng pop do cắt giữa dạng sóng |
| mpdecimate báo frame lặp trong ±2s | giật ở mối nối |
| LUFS cửa sổ 3s hai bên lệch > 1.5 dB | âm lượng nhảy bậc — đúng thứ loudnorm per-span gây ra |

**`cut_verifier` có verdict thứ ba `unsure`.** Đoạn `unsure` được xem lại kèm ảnh dạng sóng của chính
nó (chạy trên **footage nguồn** — `audit` đứng trước `resolve` nên `src.mp4` chưa có, và câu hỏi vốn
thuộc audio gốc). Sau vòng 2 vẫn không chắc → **giữ**. Mặc định an toàn không bao giờ trôi.

---

## B-roll phủ lên A-roll

**Giữ nguyên tiếng A-roll.** Đó là thứ làm nó đủ đơn giản để an toàn: chỉ là lớp hình trong khoảng
[w0, w1], audio không đụng tới → không ảnh hưởng đồng hồ từ, không ảnh hưởng loudnorm. Cho b-roll mang
tiếng riêng thì mọi bài toán sync quay lại — **ngoài phạm vi**.

`OffthreadVideo`, **không** `@remotion/media <Video>`: `<Video>` render qua canvas bỏ qua
`objectFit:cover` → viền trắng. Đã gặp và revert một lần cho A-roll; có test khoá lại.

Ba ca độ dài, chọn theo lý do đo được:

| Clip so với khoảng phủ | Làm gì | Vì sao |
|---|---|---|
| dài hơn | cắt | không mất gì ai yêu cầu |
| ngắn hơn ≤ 15% | đổi tốc độ | an toàn hơn loop — loop lộ rõ ngay khi clip có chuyển động |
| ngắn hơn > 15% | giữ frame cuối **+ cảnh báo** | kéo 3s ra 8s là hỏng theo cách khác |

B-roll nằm **trong hộp A-roll** nên khung/bo góc/viền của `frame_preset` áp cho nó y hệt, và **dưới**
lớp card/caption — caption bị che là caption không tồn tại.

`audit` xác nhận mọi `src` có trong `overlay_pool` của job (b-roll là per-job, không phải kho chung),
cùng một luật "chỉ tài nguyên có thật" áp cho sfx và bgm. `verify` so frame giữa khoảng phủ với frame
A-roll cùng lúc — giống nhau nghĩa là lớp phủ **không được vẽ**.

**Speaker mode** (`speaker_aware: auto`) chỉ bật khi có ≥2 nhãn người nói **và** mỗi người chiếm ≥15%
số từ: diarization không hoàn hảo, và một nhãn chiếm 2% của bài độc thoại là nhiễu, không phải người
thứ hai. Lượt nói < 2.5s gộp vào lượt trước — hội thoại xen kẽ nhanh sẽ làm khung nhảy liên tục, tệ
hơn là không đổi khung.

---

## Autopilot: chạy hết, sửa một lần, báo cáo thật

Ba trần cứng, mỗi cái có con số phía sau:

- **1 lần retry, không 3.** Render 1080x1920 đo được ~370s, nên 3 pass mù là 18 phút không có gì để
  xem. 1 lần + báo cáo rõ là đúng mức kiên nhẫn.
- **Chỉ code có remedy đã biết.** `lib/talking_head_edit/remedies.py` là toàn bộ từ vựng. Lỗi lạ thì
  **dừng** — retry đoán là cách đốt render và cho ra bản dựng lạ hơn bản ban đầu.
- **Trần thời gian 45 phút**, để autopilot bỏ chạy qua đêm không giữ máy.

Mỗi `verify` issue mang một **code** để tra remedy. Ba nhóm, phân loại **tường minh** — có test khoá
rằng mọi `CODE_*` phải thuộc đúng một nhóm, vì một code mới không được lặng lẽ thừa hưởng "dừng và báo":

| Nhóm | Code | Nghĩa |
|---|---|---|
| có remedy | `stutter` `bgm_missing` `av_drift` `loudness_off` `duration_mismatch` `broll_missing` | máy sửa được, chạy lại **chỉ stage ảnh hưởng** |
| cần người | `black_frame` `sfx_missing` | có hướng dẫn cụ thể phải làm gì |
| bỏ qua | `unmeasured` `seam_suspect` | thông tin, không phải lỗi; retry không cải thiện |

`render_concurrency: "half"` là remedy của `stutter`. Nhánh retry sẵn có trong `render.py` chỉ bắt
`FRAME_SEEK_ERROR` — đó là *fail cứng*; giật là render **thành công** nhưng lặp frame, nên cần nửa
concurrency ngay từ frame đầu chứ không phải sau một cú crash không bao giờ đến.

Báo cáo được ghi **dù thành công hay không**: "đã thử cái này, còn lại đây" là thứ dùng được; im lặng
dừng ở kết quả tạm ổn thì không.

### `/cancel` diệt cả cây process

`process.kill()` **không đủ**: run là subprocess CLI, còn ffmpeg và node là con của nó. Diệt cha để
lại một render ngốn hết core mà không ai theo dõi — tệ hơn là không có nút cancel, vì UI báo "đã huỷ".
Run chạy trong process group/session riêng; cancel dùng `taskkill /T` (Windows) hoặc `killpg` (POSIX).
Có test chạy tiến trình con **thật** rồi kiểm nó đã chết.

### Chat revise: sửa có ngữ cảnh

`--revise` không nhớ gì, nên chuỗi hiển nhiên "bỏ card 2" → "thêm lại card đó" không chạy được. Chat
đưa **5 lượt gần nhất** vào prompt — chỉ yêu cầu + một dòng tóm tắt kết quả, **không** nhồi spec cũ
vào. Lượt nào > 40k token thì cảnh báo (revise gửi đủ xương sống lời: take ~4 phút ≈ 25–30k, dựng lại
từ đầu ≈ 89k).

Lịch sử ở `chat_history.jsonl` (append-only, cùng kiểu `events.jsonl`) nên crash giữa lượt không làm
hỏng phần trước. `dry_run` trả patch + diff mà **không** tạo version; spec đề xuất vẫn đọc được ở
`spec_dryrun_vN.json`.

---

## Nơi chứa dữ liệu

```
projects/autoedit-jobs/<job_id>/
├── job.json            trạng thái, tuỳ chọn, danh sách version
├── events.jsonl        log tiến độ (server tail file này để stream SSE)
├── spine.json          word timestamps — đồng hồ của cả pipeline
├── spec_vN.json        bản dựng do AI sinh (neo theo chỉ số từ)
├── props_vN.json       bản đã resolve (giây thật) — đầu vào của renderer
├── audit_report_vN.json / resolve_report_vN.json / verify_report_vN.json
├── revise_diff_vN.json bản vá + diff của lần sửa
├── selection_v1.json   kết quả chọn take (kept_word_ranges + lý do)
├── chat_history.jsonl  lịch sử hội thoại sửa (append-only)
├── autopilot_report.json  các lượt, remedy đã áp, vấn đề còn lại
├── ab/<stamp>/         kết quả A/B prompt (không đụng spec_vN.json)
├── preview/timeline/   ảnh soi mối nối do verify sinh
├── src.mp4             footage đã cắt + grade
├── final.mp4           bản render
├── render_public/      thư mục public nhỏ cho Remotion (hardlink, không copy) + b-roll đã cắt
└── logs/               prompt đã gửi, raw response, log render
```

Với job thuộc project, đường dẫn là `projects/autoedit/<project_id>/jobs/<job_id>/` — layout bên
trong **không đổi**. Job cũ ở `projects/autoedit-jobs/` vẫn đọc được và mở được ở đúng URL cũ;
`find_job(job_id)` tra cả hai layout.

Job **không** nằm trong `remotion-composer/public/`: Remotion copy toàn bộ public dir vào bundle mỗi
lần render (đo được 415 MB sau vài job). Thay vào đó mỗi job stage một thư mục public tí hon chỉ
chứa đúng asset nó dùng.

---

## Bẫy đã gặp (đừng lặp lại)

| Triệu chứng | Nguyên nhân thật | Cách xử lý |
|---|---|---|
| Tiếng lệch hình sau khi cắt | `aselect` cho qua toàn bộ audio frame trên build này | `atrim`+`concat`, có assert |
| Nhạc nền im ru | Remotion `<Audio loop>` + volume callback render ra im lặng | `BgmDucked` tile nhiều `<Audio>` |
| PiP có viền trắng | `@remotion/media <Video>` bỏ qua `objectFit:cover` | dùng `OffthreadVideo` |
| Render chết "No frame found at position" | compositor trượt frame khi concurrency cao | tự thử lại ở nửa concurrency |
| Keyword dài bị cắt mép phải | ước lượng 0.62em/ký tự sai với font có dấu | `fitText` đo font thật trong renderer |
| Nhạc biến mất sau khi sửa | model trả `"bgm": "tên.mp3"` (chuỗi) thay vì object | `normalise_bgm` chấp nhận cả hai, dạng lạ thì cảnh báo |
| Không biết nhạc có vào mix hay không | kiểm tra cũ chỉ hỏi "file có tiếng không" — đúng cả với bản chỉ có giọng | đo mức âm **trong khoảng ngắt lời**: có nhạc thì nghe rõ ở đó |
| Log UI đầy "Invalid Date" | job xong → server đóng SSE → EventSource tự reconnect vô hạn | client đóng khi gặp `stream_end` |
| Gateway trả 403 `error code: 1010` | Cloudflare chặn user-agent mặc định của urllib | gửi UA trình duyệt (bắt buộc) |
| **Video render ra bị giật** | máy bị tranh CPU nặng lúc render (trình duyệt/dev-server chạy cùng) → renderer phục vụ lại frame cũ, ~20% frame là frame lặp | đóng bớt ứng dụng rồi render lại; `verify` nay tự phát hiện bằng `mpdecimate` |
| Cold-open / nhạc / auto-sharpen tự tắt không rõ lý do | `options.get("bgm", True)` trả `None` khi key tồn tại mang giá trị `null` — và `None` là falsy. Client JSON hay form gửi `{"bgm": null}` dễ hơn là bỏ hẳn key, mà `POST /run` merge thẳng payload vào options | dùng `option_enabled(options, "bgm")` (`job_store.py`): chỉ `False`/`0` mới tắt, `null` = chưa đặt |
| Preview màu mềm hơn bản render | preview dựng ở 1080x1920 với `source_width=None` → sharpen 0.8; render dựng ở 1012x1800 với source 720 → sharpen 1.5 | `render_grade_context()` lấy đúng kích thước + số đo `auto_sharpen` mà resolve sẽ dùng |
| Bản dựng ngắn hơn và cụt đuôi sau khi đổi sang per-span | cửa sổ mỗi run word-tight → mất tiếng phòng đầu/cuối (90.8s → 89.0s) | run ĐẦU giữ từ giây 0, run CUỐI giữ tới hết file; chỉ run giữa mới word-tight + `PAD` |
| Ảnh soi mối nối chỉ sai chỗ | nhãn từ dùng `word.start` = giây trong **file nguồn**, không phải giây bản dựng. Job cắt ít trông "gần đúng" nên rất dễ bỏ qua | `output_time_mapper(job, version)` dựng `TimeMapper` từ `spans` + `cold_open_offset` |
| Job nhiều take: audit cắt sai cửa sổ | `audit`/`revise` đọc spine **đầy đủ**, nhưng spec dùng chỉ số spine **đã lọc bởi `select`** | cả hai dùng `director_words(job)` — một định nghĩa duy nhất cho "director thấy gì" |
| Sửa prompt mà kết quả không đổi → tưởng prompt vô dụng | `cache_signature("direct")` không băm prompt | băm `prompt_registry.fingerprint([...])` + bảng sfx/bgm; có test riêng cho case này |
| UI báo "đã huỷ" mà máy vẫn ngốn hết core | `process.kill()` chỉ diệt CLI wrapper; ffmpeg/node là process **con** | process group/session riêng + `taskkill /T` (Windows) / `killpg` (POSIX) |
| Tính năng b-roll không bao giờ chạy từ UI | `create_job` chỉ đưa nguồn **aroll** vào `input_paths` → `overlay_pool` luôn rỗng | đưa hết nguồn vào, aroll trước |
| Restart server giữa lúc render → nút Huỷ không còn tác dụng, có thể chạy đè | render là CLI subprocess riêng process group; server chết thì subprocess **mồ côi** (không phải con của server mới) nên `_current` của queue rỗng — cancel không tìm thấy, submit lại có thể sinh process thứ 2 ghi đè cùng file | `_execute` ghi `worker_pid` vào `job.json`; khởi động `JobQueue` tự dò job có `worker_pid` còn sống (`_reconcile_orphans`) để nhận lại — cancel/queue status đúng, `submit` từ chối chạy đè (`AlreadyRunningError` → HTTP 409) |
| Nhóm take có tên vô nghĩa (`"s"`) và gộp bừa | `label` lấy từ tên **trên đĩa** (`s0_take-1`); tiền tố `s0_` bị coi là nội dung | `label` mặc định = tên người dùng upload |
| Autopilot lặng lẽ dừng, không rõ vì sao | code `verify` mới không có remedy **và** không được bỏ qua tường minh → rơi vào nhánh "unknown" | ba nhóm tường minh (`REMEDY`/`NEEDS_HUMAN`/`IGNORED_CODES`) + test khoá mọi `CODE_*` |

---

## Cấu hình

```bash
# .env
NINE_ROUTER_BASE_URL=https://<gateway>/v1
NINE_ROUTER_API_KEY=<key>
AUTOEDIT_DIRECTOR_MODEL=ag/gemini-3.7-flash-high
AUTOEDIT_REASONING_EFFORT=low                    # Grok-4.5: high hay 524 Cloudflare ~120s
ELEVENLABS_API_KEY=<key>                         # Scribe v2 ASR (mặc định).
                                                 # Bỏ trống = tự chạy Whisper local

# tuỳ chọn
AUTOEDIT_PORT=8861
AUTOEDIT_UI_PORT=5617
AUTOEDIT_BIND=0.0.0.0                        # 127.0.0.1 nếu chỉ local
AUTOEDIT_PUBLIC_HOST=<VPS_HOST>            # IP/hostname VPS (CORS + Vite HMR)
AUTOEDIT_API=http://127.0.0.1:8861           # proxy nội bộ Vite → API
AUTOEDIT_INPUT_ROOTS=D:/footage;E:/quay      # thư mục được phép nạp file
AUTOEDIT_PRICE_IN_PER_MTOK=0                     # đặt giá để UI hiện chi phí thật
AUTOEDIT_PRICE_OUT_PER_MTOK=0
```

Model director đổi được bằng `--model`. Mặc định hiện tại: `ag/gemini-3.7-flash-high` (qua 9router). Verifier cắt có thể tách model riêng qua `verifier_model`; để trống thì dùng chung model director.
Prompt hiện dùng (xem `prompts/registry.json`, changelog từng bản): `structure` **v8** (yêu cầu riêng
đặt ngay sau khối ưu tiên, luật cắt theo `cut_level`), `captions` **v3** (nhận yêu cầu riêng + tên
riêng), `revise` **v3** (option whitelist, `cut_add`/`cut_restore`, `not_done`), `cut_verify` **v2**
(verdict thứ ba `unsure`), `card_guidance` **v6** (nested vào `structure`, model tự quyết có card hay
không — đổi bản này cũng phải nằm trong danh sách băm ở `cache_signature("direct")`, xem
[`talking-head-autoedit-platform.md`](talking-head-autoedit-platform.md#prompt-là-dữ-liệu-sửa-được)).
Revert: đổi `"current"` về bản trước trong registry.

---

## Automation API

Một lệnh `POST` để tạo + chạy hết pipeline (thay vì tạo job rồi tự poll từng
stage), có Idempotency-Key, mã lỗi máy đọc được (`{"detail": ..., "code": ...}`
trên mọi lỗi), và webhook khi job xong. Đặt `AUTOEDIT_API_TOKEN` trong `.env`
để bật xác thực — không đặt thì mọi request vẫn mở như trước (`GET
/api/health` và `/api/auth/*` luôn mở, kể cả khi có token).

**Token không bao giờ xuất hiện trong URL hay JSON trả về** — không còn
`?token=` (rò vào access log, Referer, lịch sử trình duyệt). Ba cách xác thực:

| Người gọi | Cách xác thực |
|---|---|
| Script/automation | Header `Authorization: Bearer <token>` hoặc `X-API-Key: <token>` |
| Trình duyệt (UI) | Cookie `autoedit_session` (HttpOnly) sau khi `POST /api/auth/login` |
| `<video src>` / webhook `mp4_url` | URL ký sẵn `?exp=<unix>&sig=<hmac>`, hết hạn sau `AUTOEDIT_SIGNED_URL_TTL` (mặc định 24h) |

```bash
export TOKEN=<AUTOEDIT_API_TOKEN>
export API=http://127.0.0.1:8861

# 1. Tạo + chạy hết (bao gồm render) từ một file có sẵn trên máy chạy server
curl -sX POST "$API/api/runs" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"sources": [{"path": "/home/automation/Edit-Video/projects/footage.mp4"}],
       "prompt": "nhấn mạnh phần tiết kiệm chi phí", "render": true,
       "webhook_url": "https://example.com/hooks/openmontage"}'
# -> 202 {"run_id": "...", "status": "queued", "status_url": "/api/runs/...",
#         "events_url": "/api/jobs/.../events"}
# Lặp lại đúng nội dung + Idempotency-Key trong 24h -> trả về run cũ, không tạo
# job mới. Cùng key, khác nội dung -> 409 idempotency_conflict. Cùng key, yêu
# cầu trước ĐANG chạy (còn chưa tạo xong job) -> 409 idempotency_in_progress
# (kèm header Retry-After).

# 2. Poll bằng header (hoặc chờ webhook — xem bên dưới)
curl -s "$API/api/runs/$RUN_ID" -H "Authorization: Bearer $TOKEN"
# -> {"run_id", "state": queued|running|awaiting_render|succeeded|failed|cancelled,
#     "stage", "percent", "error": {code, message}|null,
#     "outputs": {"mp4_url", "duration_seconds", "verify": {"passed", "issues"}},
#     "current_version", "versions": [{version, kind, instruction, outcome}], ...}

# 3. Tải MP4 khi state == "succeeded" — mp4_url đã là URL ký sẵn dùng được
#    ngay, không cần header (hoặc là URL R2 presigned nếu job đã sync lên R2)
curl -s -o output.mp4 \
  "$(curl -s "$API/api/runs/$RUN_ID" -H "Authorization: Bearer $TOKEN" | jq -r .outputs.mp4_url)"
```

`sources[]` nhận `path` (qua path guard hiện có) hoặc `url` (http/https —
server tải về, chặn địa chỉ nội bộ/loopback/CGNAT để tránh SSRF — bao gồm cả
khi DNS trả lời khác đi giữa lúc kiểm tra và lúc tải, giới hạn dung lượng bằng
`AUTOEDIT_MAX_DOWNLOAD_MB`, mặc định 2048). `webhook_url` bị kiểm SSRF y hệt,
cả lúc tạo run lẫn mỗi lần gửi. `render: false` dừng trước `render`/`verify`
(như chế độ "prepare" của UI). `project_id` (tuỳ chọn) gắn build vào một
project có sẵn thay vì tạo job rời.

`POST /api/runs/{id}/revise {"message": "..."}` chạy lại đúng chat-revise dùng
cho `/jobs/{id}/chat`, rồi xếp `audit,resolve` (+ `render,verify` nếu run này
tạo với `render: true` — mặc định `false` cho job không tạo qua `/api/runs`).
`POST /api/runs/{id}/cancel` huỷ như job thường. Cả `chat`, `revise` (cả hai
dạng) và `POST /jobs/{id}/cuts` (bên dưới) trả `409 job_busy` nếu job đang
chạy hoặc đang trong hàng đợi.

### Đăng nhập cho trình duyệt

```bash
curl -sX POST "$API/api/auth/login" -H "Content-Type: application/json" \
  -d "{\"token\": \"$TOKEN\"}" -c cookies.txt
# -> {"ok": true}, Set-Cookie: autoedit_session=... (HttpOnly, Path=/, 30 ngày)

curl -s "$API/api/jobs" -b cookies.txt   # cookie thay cho header
curl -sX POST "$API/api/auth/logout" -b cookies.txt
curl -s "$API/api/auth/status"           # {"auth_required": bool, "authenticated": bool} — luôn mở
```

Cookie không có `Domain` nên đi qua được Vite dev proxy (`/api` cùng-origin từ
góc nhìn trình duyệt) mà không cần cấu hình thêm.

### Cắt/Giữ tay, không qua LLM

```bash
curl -sX POST "$API/api/jobs/$JOB_ID/cuts" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"cut": [[12, 18]], "keep": [[40, 45]]}'
# -> {"version": N, "queue_position": ..., "report": {...}}
```

Không gọi model: khoảng người dùng chọn luôn được đánh dấu `nguon: khach`
(dùng thẳng, bỏ qua verifier + lexicon gate), khác với transcript Cắt/Giữ kiểu
cũ từng đi qua `/chat` và phụ thuộc việc model có gán đúng `nguon` hay không.

### Webhook

Khi job vào trạng thái cuối (thành công/thất bại/huỷ), server `POST` đúng
payload của `GET /api/runs/{id}` tới `webhook_url` (không bao giờ kèm token —
`mp4_url` bên trong là URL ký sẵn, xem bảng ở trên), kèm header
`X-Autoedit-Signature: sha256=<hex HMAC-SHA256 của body bằng AUTOEDIT_API_TOKEN>`
(bỏ qua header này nếu không đặt token). 3 lần thử, có backoff; thất bại chỉ
ghi warning vào log job, không bao giờ làm hỏng job. Xác minh chữ ký phía
người nhận (Python):

```python
import hashlib, hmac

def verify(body: bytes, signature_header: str, token: str) -> bool:
    expected = "sha256=" + hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

---

## Test

```bash
make autoedit-test              # test Python, không cần API key
make autoedit-ui-typecheck      # kiểm type của web UI
```

Không có `make` trên máy (Git Bash không kèm) thì gọi thẳng:

```bash
python -m pytest tests/test_talking_head_*.py -q
cd remotion-composer && npm run typecheck:ui
```

**Vì sao UI cần target riêng:** `remotion-composer/tsconfig.json` chỉ `include: ["src"]`, và Vite
transpile bằng esbuild — nó *xoá* type chứ không *kiểm* type. Nên trước khi có `ui/tsconfig.json` thì
thư mục `ui/` **chưa từng được kiểm type lần nào** (lỗi đầu tiên lộ ra ngay khi bật: thiếu
`@types/react-dom`). Không gộp `ui/` vào tsconfig gốc vì `src/Root.tsx` và `src/Explainer.tsx` đang có
lỗi type sẵn — gộp vào thì mọi lỗi UI mới sẽ bị chìm trong đó. CI có job `typecheck-ui` riêng.

Bao gồm: ánh xạ thời gian, guard cơ học, chia caption, hợp đồng của cut verifier (verifier hỏng →
không cắt gì), chuẩn hoá bgm, đo nhạc nền trong khoảng ngắt lời, dọn staging, cache, và API server.

### Độ mượt được kiểm thế nào

`verify` lấy 3 đoạn A-roll toàn màn hình (bỏ qua card/endcard vì chúng gần như tĩnh) rồi đếm frame
lặp bằng `mpdecimate`. A-roll khoẻ mạnh không lặp frame nào; bản bị giật đo được 15-20%. Vượt 8% là
báo lỗi — file vẫn giữ lại để xem, nhưng job chuyển sang `completed_with_warnings`.

Đây là loại lỗi mà mọi phép đo khác đều bỏ lọt: đúng thời lượng, đúng loudness, không frame đen,
nhưng mắt người nhìn vào là thấy giật ngay.

### Nhạc nền được kiểm thế nào

`verify` tìm các khoảng KHÔNG có caption (lúc người nói ngắt hơi) rồi đo mức âm ở đó. Có nhạc thì
những khoảng đó rõ trên ngưỡng −50 dB; nhạc rớt khỏi mix thì chúng gần như im. Không đo được thì
kết luận là `null` (chưa rõ) — không bao giờ báo "đạt" mà chưa đo.
