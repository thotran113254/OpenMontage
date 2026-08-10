# Talking-head auto-edit

Tự động dựng video ngắn từ footage talking-head quay một mạch: cắt từ đệm, caption chạy chữ,
card giải thích, keyword bay, punch-in, cold-open, endcard, nhạc nền — rồi render bằng Remotion.

Chạy được 3 cách: CLI, web UI, hoặc qua registry (`talking_head_autoedit`).

---

## Nguyên tắc chính xác (đừng phá)

Toàn bộ độ chính xác của pipeline dựa trên **một đồng hồ duy nhất**:

1. **Word spine là đồng hồ.** ASR cho timestamp từng từ. AI director **không bao giờ** phát ra
   giây — chỉ chỉ số từ. Đo thực tế: timestamp do model tự đoán trôi ~±0.6s và làm tròn về giây.
2. **`stages/resolve.py` là nơi DUY NHẤT** đổi chỉ số từ → giây, và là nơi duy nhất cắt media.
3. **Cắt hình và tiếng tại cùng biên**, bằng `atrim`+`concat`. **Không dùng `aselect`** — build ffmpeg
   trên máy này im lặng cho qua toàn bộ audio frame (đã kiểm chứng), làm tiếng lệch hình.
   Sau khi cắt có assert lệch A/V < 0.35s.
4. **Padding an toàn** `PAD=0.08s` cạnh mỗi mối cắt, bỏ qua cut ngắn hơn `MIN_CUT=0.12s`.
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
| `calibrate` | **TẮT mặc định** — trượt phép thử mù, xem phần "Gemini có thực sự nghe được không" | ~35s khi bật |
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

# sửa có ngữ cảnh 3 lượt trước (khác --revise: --revise không nhớ gì)
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
make autoedit-server      # http://127.0.0.1:8756  (API + phục vụ media)
make autoedit-ui          # http://localhost:5173  (giao diện)

# hoặc 1 lệnh duy nhất (không cần make, không cần 2 terminal):
python run_autoedit_dev.py   # tương đương make autoedit-dev
```

`run_autoedit_dev.py` chạy song song cả job server và Vite dev server, gộp log
có tiền tố `[server]`/`[ui]`, Ctrl+C tắt cả hai (kill nguyên cây process —
Vite bọc node, `taskkill /T` mới diệt hết, giống cách `queue_worker.py` huỷ
một render đang chạy).

Trong UI — luồng project:

1. **Tạo project** → upload A-roll (+ b-roll) → tab Cấu hình (assembly/keyterms/defaults).
2. **+ Dựng bản mới** mở wizard: option (prompt, topic, ASR, tempo, bgm…) + **cách chạy**:
   - *Dựng + duyệt (dừng trước render)* — stages đến `resolve`, có preview; **không** render MP4.
   - *Dựng rồi xếp lịch cloud* — như trên, khi xong tự enqueue batch cloud (chưa thuê máy).
   - *Full local* — cả pipeline gồm render (cảnh báo CPU).
   - *Chỉ tạo job* — `run=false`, chạy tay từng bước trên trang job.
3. Trang job: stage board (nhóm LLM / encode / render), **Chạy theo bước** (① prepare · ② render),
   banner “dừng trước render”, rồi chọn render:

| Nút (trang job) | Ý nghĩa |
|---|---|
| **Render MP4 local** / nháp 540p | Chạy ngay trên máy, FIFO queue local, $0 (+ confirm) |
| **Xếp lịch cloud** | Đưa job vào batch queue (free, không thuê máy) |
| **Cloud ngay…** | dry_run + modal xác nhận chi phí → thuê 1 máy cho job này |

Trang **Lịch render** (`#/cloud`): batch queue, ngưỡng flush, **Render batch ngay** (checkbox
xác nhận). Không cron cuối ngày. API: `/api/cloud/*`; create build: `POST .../jobs` body
`{ options, stages, run }`.

---

## Gemini có thực sự nghe được không — phép thử mù

**Kết luận: nghe được LỜI NÓI, không chấm được CHẤT LƯỢNG.** Chạy lại bất cứ lúc nào:

```bash
python -m lib.talking_head_edit.cli --job <id> --check-hearing --at 41
```

Cách làm: lấy một đoạn 5s, nhân thành 8 bản — 1 bản sạch, 1 bản sạch **trùng lặp**
(bẫy bịa), và 6 bản mỗi bản tiêm MỘT lỗi đã biết. Nhãn gửi đi là `m1..m8`, không mang
thông tin. Chỉ số quan trọng là **lệch** (lift): điểm của lỗi ở bản có tiêm, trừ điểm
chính lỗi đó ở bản đối chứng.

| Phép thử | Nghe ra | Lệch TB | Chấm lệch giữa 2 bản GIỐNG HỆT |
|---|---|---|---|
| Nhãn = tên lỗi *(sai thiết kế)* | 6/6 | +7.2 | 0.0 |
| Nhãn trung tính `m1..m8` | **1/6** | **+0.17** | **1.8–3.2** |

Bản đầu tiên của phép thử này đặt tên mẫu theo đúng tên lỗi bên trong — tức đưa sẵn đáp
án. Nó đạt 6/6 ở **mọi** mức, kể cả mức yếu tới ngưỡng không thể nghe. Dấu hiệu lộ ra là
lift **không giảm** khi lỗi yếu đi.

Với nhãn trung tính: bỏ sót cả tiếng ù +18 dB, có lift **âm** (chấm lỗi ở bản sạch cao
hơn bản có lỗi), và chấm lệch tới 3.2 điểm giữa hai file **giống hệt nhau từng byte**.

Áp cùng cách kiểm cho chính bước chọn preset — nhãn `voice`/`shotgun`/`shotgun_dry` cũng
tự nói lên đáp án:

| | Kết quả 3 vòng |
|---|---|
| Nhãn thật | `shotgun` ×6/6 — nhất quán |
| Nhãn A/B/C, xoay vòng | 3.5: `shotgun, voice, shotgun` · 3.6: `voice, shotgun, shotgun` |

Phần chỉnh màu cũng không sống sót khi bịt nhãn, và 3.6 chọn nhãn "A" ở 3/4 vòng —
thiên lệch vị trí chứ không phải phán đoán.

**Audio CÓ tới model.** Bảo chép lời, cả hai model trả về gần đúng nguyên câu, khớp
Whisper. Nên đây không phải lỗi truyền dữ liệu — nó nghe được nội dung, không nghe được
chất lượng kỹ thuật.

`llg/gemini-3.6-flash` không khá hơn 3.5 ở phép thử này. Prompt tiếng Anh không khá hơn
tiếng Việt.

**Hệ quả:** stage `calibrate` **tắt mặc định**. Thông số đang dùng không bị ảnh hưởng —
chúng đến từ đo đạc cộng mắt và tai của bạn, không phải từ stage này. Thứ bị rút lại là
việc **tự động chọn lại cho từng video**. Muốn bật lại: `calibrate_grade` /
`calibrate_audio`, sau khi `--check-hearing` đạt.

---

## Dò thông số trước khi áp cho cả video *(đang tắt — xem phần trên)*

Stage `calibrate` chạy sau `audit`, trước `resolve`. Nó lấy **một mẫu** từ chính footage của bạn, dựng
vài phương án, rồi nhờ model **xem ảnh và nghe tiếng** để chốt — sau đó `resolve` mới áp cho toàn video.

```
audit → calibrate → resolve → render
         │
         ├─ 4 ảnh: gốc + 3 phương án màu   → Gemini chấm sáng/màu da/kết cấu da/tự nhiên
         └─ 3 mẫu tiếng: voice/shotgun/dry → Gemini chấm sạch/vang/rõ/tự nhiên
```

Mất ~35 giây, thay cho vòng lặp render-5-phút-rồi-đoán-lại. Chạy lại cho **mỗi video** —
phòng, ánh sáng, khoảng cách mic đổi theo từng buổi quay.

**Vì sao nhờ model chứ không tự đo — với MÀU và TIẾNG:** số đo đã chọn sai ba lần.

| Việc | Bản đo tốt nhất | Model nghe/nhìn thấy |
|---|---|---|
| Khử nhiễu | SNR 16.7 dB (cao nhất) | "giọng mỏng, ướt, méo pha" — 4/10 tự nhiên |
| Khử vang | đuôi giảm 52 dB (cao nhất) | "mất đuôi âm ở các từ" — 4/10 liền mạch |
| Màu | — | bắt được "cháy vàng" mà số đo toàn khung bỏ sót |

Số đo cho biết đã bỏ đi bao nhiêu, không cho biết thứ còn lại có còn giống người thật không.

**Nhưng ĐỘ NÉT thì ngược lại — không hỏi model.** Chấm trên ảnh tĩnh, model chọn 1.2 trong
khi file render thật cần 1.6; rồi khi đưa cặp ảnh chênh nhau 2.2 lần nó trả lời "hầu như
không có sự khác biệt". Khâu xử lý ảnh của nó lấy mẫu xuống đúng phần chi tiết đang cần chấm.
Độ nét vì vậy được **tính từ tỉ lệ phóng** (`resolve_media.default_sharpening`), lấy từ số đo
trên clip đã render.

| Nguồn | Tỉ lệ phóng | sharpen / clarity |
|---|---|---|
| 720x1280 | 1.41x | 1.6 / 0.85 *(đã đo)* |
| 1080x1920 | 1.0x | 0.8 / 0.5 *(suy ra, chưa kiểm chứng)* |

**Quy tắc:** mọi thay đổi về độ nét phải đo trên clip đã render, không đo trên
`--preview-still`. Ảnh tĩnh chưa qua hai lần encode x264 — chính khâu đó lượng tử hoá mất
phần chi tiết mà làm nét vừa thêm vào.

**Quyết định của bạn được giữ:** calibrate merge lựa chọn của nó *vào dưới* `grade_overrides`
đang có, nên thông số bạn tự đặt không bị ghi đè. Muốn tắt hẳn: `--no-calibrate`.

### Điều khiển Gemini khi nghe audio

Lời nhắc audio có ba thứ then chốt, mỗi thứ sửa một lỗi đã gặp thật:

1. **Chẩn đoán trước, chấm sau.** Model phải nêu bản thu gốc hỏng ở đâu
   (`u_am_tram` / `xi_nen` / `vang_phong` / `bi_boc` / `xi_gio` / `bung_hoi` / `vo_tieng`)
   rồi mới so các bản. Chẩn đoán được ghi vào log — trên footage này nó ra
   `u_am_tram, xi_nen, vang_phong, bi_boc`, khớp đúng bốn thứ đã đo bằng máy.
2. **Thang `do_vang` hai chiều.** Bản cũ ghi "10 = khô như phòng thu" nên càng khô càng
   được điểm — đúng cái bẫy. Sửa thành "vang vừa đủ là tốt nhất, khô tuyệt đối bị trừ
   ngang với vang quá nhiều": `shotgun_dry` rơi từ 8–9 xuống **5**.
3. **Chỗ cần nghe, nói cụ thể.** Khoảng lặng giữa câu (nhiễu nền), đuôi câu (vang),
   đuôi từ và **đường dấu thanh** — tiếng Việt mất đường thanh là mất nghĩa, lỗi này
   không tồn tại ở tiếng Anh nên model không tự để ý.

**Quyền phủ quyết:** bản nào bị model tự chấm `tu_nhien` < 6 thì không được chọn, kể cả
khi chính nó đề cử (`pick_winner`). Lý do: bản xử lý mạnh nhất luôn thắng mọi tiêu chí
"sạch" và thua ở tự nhiên, nên điểm trung bình sẽ chọn nhầm. Áp cho cả màu lẫn tiếng.

**Chất lượng mẫu gửi đi không phải vấn đề** — đã kiểm: 40 kbps / 128 / 256, mỗi mức chạy
2 lần, cả 6 lần đều chọn `shotgun`. Nguồn gần như không có năng lượng trên 10 kHz
(-60.6 dB) nên 24 kHz không cắt mất gì. Khác biệt giữa hai lần gọi đơn lẻ là **nhiễu**,
không phải tín hiệu — muốn kết luận gì về prompt thì phải chạy lặp.

---

## Độ nét: ba chỗ mất và trần của nó

Đo trên vùng mặt cắt 1:1 từ file render thật (nguồn 720x1280 @ 2 Mbps, phóng 1.5x):

| Cấu hình | Độ nét | So bản cũ |
|---|---|---|
| Bản cũ (khử nhiễu sau khi phóng, JPEG 80, sharpen 0.8) | 1.28 | — |
| + sửa thứ tự lọc, JPEG 100, encode đúng kích thước hiển thị | 1.46 | +14% |
| + sharpen 1.6 / clarity 0.85, giảm mài da 0.18→0.08 | 2.65 | +106% |
| + `intermediate_crf` 17→12 *(mặc định hiện tại)* | 2.85 | +122% |
| + `render_crf` 17→12 | 3.30 | +158% (file gấp 2.5) |
| bỏ hẳn mài da | 3.35 | +161% |

Ba chỗ mất, đã sửa cả ba:

1. **Thứ tự lọc** — khử nhiễu chạy *sau* khi phóng to nên chà mất chi tiết vừa nội suy.
   Giờ khử ở độ phân giải gốc rồi mới phóng (`build_grade_chain`).
2. **Remotion chụp frame ra JPEG chất lượng 80** trước khi encode → `--jpeg-quality=100`.
3. **Vòng phóng-thu** — encode 1080 rồi trình duyệt thu về ~1013 để nhét vừa khung inset.
   Giờ encode thẳng ở kích thước hiển thị (`aroll_pixel_size`).

**Trần:** vùng mặt trong nguồn chỉ có ~400x470 pixel thật. Bản hiện tại đã cho độ nét gấp
~2.7 lần chính nguồn phóng lên (1.07). Muốn nét hơn nữa phải **sinh thêm chi tiết** —
tức AI super-resolution (Real-ESRGAN / CodeFormer), không phải chỉnh tham số lọc.

### Tự dò độ nét cho từng video (`auto_sharpen`, bật sẵn)

Một con số cố định chỉ đúng với clip đã tinh chỉnh nó. `resolve` giờ **đo độ mềm của
chính video đang xử lý** rồi chọn mức làm nét — mất ~15 giây, chạy trên 3 khung có mặt
người, không gọi model nào.

Hai chỉ số, và chỉ số thứ hai mới là thứ khiến việc này tự động hoá được an toàn:

- `detail` — Laplacian trung bình vùng mặt. Làm nét mạnh thì nó luôn tăng, nên một mình
  nó không có điểm dừng.
- `overshoot` — pixel bị đẩy vượt ra ngoài mức sáng/tối cực trị của chính lân cận nó
  **trước khi** làm nét. Đó đúng là định nghĩa của viền sáng. **Đây là cái phanh.**

Kiểm chứng bằng cách cố tình bóp méo chính footage này:

| footage | độ nét gốc | → sharpen | kết quả |
|---|---|---|---|
| đã làm nét sẵn | 4.63 | **0.50** | đạt mục tiêu |
| gốc | 2.83 | **1.50** | đạt mục tiêu |
| làm mềm | 2.44 | **1.94** | chạm trần viền |
| rất mềm | 2.23 | **1.94** | chạm trần viền |

Trên footage gốc nó tự chọn **1.5**, sát mức **1.6** đã được duyệt bằng mắt — mà không
được cho biết con số đó.

**Đề xuất của đạo diễn AI bị bỏ qua ở đây.** Spec có `sharpen: 0.6`, nhưng model đã được
chứng minh không chấm được độ nét, nên số của nó không được phép đè lên phép đo. Chỉ khi
**bạn** đặt `sharpen` trong `grade_overrides` thì auto mới nhường.

Tắt: `auto_sharpen: false`. Báo cáo mỗi lần chạy nằm ở `sharpen_report.json`.

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

Ảnh và mẫu tiếng cache theo hash tham số (`{at, grade, kích thước, sha nguồn}`), nên đổi một con số
chỉ dựng lại **đúng bản đổi** — `raw` và `hien_tai` dùng lại. Giữ 60 frame mới nhất rồi xoá dần.

**Preview phải khớp option đang chọn — không thì vô nghĩa.** `render_grade_context()` lấy đúng thứ
`resolve` sẽ dùng, vì bỏ qua chúng thì ảnh preview **mềm hơn bản render** mà chính da là thứ người ta
nhìn để chấm:

| | render thật | preview nếu để mặc định |
|---|---|---|
| kích thước encode (khung `dark`) | 1012x1800 | 1080x1920 |
| sharpen / clarity | **1.5 / 0.85** (số `auto_sharpen` đo được) | 0.6 / 0.5 |

Thứ tự ưu tiên độ nét giống hệt `resolve`: `grade_overrides` của bạn → số `auto_sharpen` đo trên
chính video này (`sharpen_report.json`) → suy từ tỉ lệ phóng. Đề xuất của đạo diễn AI **không** được
tính, vì model đã được chứng minh không chấm được độ nét. Panel in ra nguồn số đang dùng để bạn biết
đang xem cái gì.

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

**Khung bao** (`--frame`, mặc định `dark`): footage được lùi vào trong, đặt trên một nền, bo góc và
viền mảnh. Tác dụng thật: căn phòng xấu thôi chiếm trọn khung hình và mắt người xem bị đẩy về phía
mặt — bố cục trông có chủ đích thay vì như quay vội.

| Preset | Trông thế nào | Khi nào dùng |
|---|---|---|
| `dark` | nền xanh đen + viền trắng bo góc | mặc định, hợp hầu hết footage |
| `light` | nền xám sáng | phòng tối, muốn nhẹ nhàng |
| `blur` | chính footage làm nền, làm mờ + tối | phòng có màu đẹp; **tốn thêm 1 lần giải mã video mỗi frame** |
| `none` | tràn viền như cũ | footage đã đẹp sẵn |

Lưu ý về `blur`: nếu phòng sáng trắng thì nền mờ ra xám bệt, nhìn tệ hơn `dark` — đã thử và loại.

**Grade** chạy trong ffmpeg ở stage `resolve`. Ngoài brightness/contrast/saturation/gamma do director
chọn, chuỗi còn có 3 bước cố định (tắt được qua `tone_curve`/`vibrance`/`vignette` = 0):

- **đường cong S + nén vùng sáng** — cho mặt có khối, trần/tường trắng không bị cháy
- **`vibrance` thay vì tăng saturation toàn cục** — nâng màu nhạt nhưng chừa tông da, mặt không bị cam
- **vignette nhẹ** — dồn mắt về người nói, làm dịu hậu cảnh lộn xộn

Da vẫn giữ kết cấu thật: `smartblur` có ngưỡng nên chỉ làm mịn vùng phẳng, sau đó `unsharp` lấy lại
chi tiết mắt/tóc. Muốn mịn hơn thì tăng `skin_smooth` trong spec, nhưng quá 0.5 là bắt đầu giả.

---

## Làm nét

Footage điện thoại vào đây thường mềm sẵn (ví dụ thật: 2.15 Mbps @720x1280) rồi còn bị phóng lên
1080x1920. Bốn chỗ ăn mất chi tiết, đã xử lý cả bốn:

| Chỗ mất nét | Cách xử lý | Đo được |
|---|---|---|
| Nén file trung gian `src.mp4` | `veryfast/crf20` → `medium/crf17` | +45% độ nét |
| Nén bản render cuối | Remotion mặc định → `--crf 17` | hết mềm ở tóc/da |
| Làm mịn da quá tay | `skin_smooth` 0.35 → 0.18 | đây là thứ xoá chi tiết nhiều nhất |
| Unsharp bán kính rộng | `unsharp 5:5` → `3:3` + thêm `cas` | +66% trên bản render thật |

`cas` (Contrast Adaptive Sharpening) làm nét theo cạnh nên không tạo quầng sáng như unsharp mạnh —
chỉnh qua trường `clarity` (mặc định 0.4, tắt bằng 0).

Đánh đổi: stage `resolve` chậm hơn (~2 phút → ~4 phút) và `src.mp4` nặng gấp đôi. Đó là file trung
gian nên đổi lấy chi tiết là xứng.

**Về AI upscale (Real-ESRGAN):** repo có sẵn `tools/enhancement/upscale.py`, nhưng máy này đang cài
torch bản CPU nên 2808 frame sẽ mất hàng giờ. Muốn dùng phải cài torch CUDA trước. Kể cả vậy, trần
chất lượng vẫn bị chặn bởi footage gốc — quay ở bitrate cao hơn có lợi hơn nhiều so với vá bằng AI.

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
mới chứ không xoá lịch sử.

---

## Cache

| Đổi thứ này | Chạy lại từ |
|---|---|
| prompt / topic / card_plan / model | `direct` |
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
đưa **3 lượt gần nhất** vào prompt — chỉ yêu cầu + một dòng tóm tắt kết quả, **không** nhồi spec cũ
vào: cả điểm mạnh của revise là ~8k token thay vì ~55k. Lượt nào > 15k token thì cảnh báo.

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
AUTOEDIT_DIRECTOR_MODEL=cx/gpt-5.6-luna
AUTOEDIT_REASONING_EFFORT=low                    # Grok-4.5: high hay 524 Cloudflare ~120s
ELEVENLABS_API_KEY=<key>                         # Scribe v2 ASR (mặc định).
                                                 # Bỏ trống = tự chạy Whisper local

# tuỳ chọn
AUTOEDIT_PORT=8756
AUTOEDIT_INPUT_ROOTS=D:/footage;E:/quay          # thư mục được phép nạp file
AUTOEDIT_PRICE_IN_PER_MTOK=0                     # đặt giá để UI hiện chi phí thật
AUTOEDIT_PRICE_OUT_PER_MTOK=0
```

Model director đổi được bằng `--model`. Mặc định hiện tại: `cx/gpt-5.6-luna` (qua gateway).
Prompt `structure` mặc định **v3**: 4 cổng retention (A/B/C/D), mode edu|hot_take|story|demo,
cut thêm `dead_air`/`soft_restart`, endcard CTA cụ thể — schema JSON 7 khoá không đổi.
Revert prompt: sửa `prompts/registry.json` → `"structure": {"current": "v2"}`.

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
