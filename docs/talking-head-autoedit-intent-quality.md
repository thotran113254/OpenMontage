# Talking-head auto-edit: ý khách, mức cắt, chất lượng và CPU

Bổ sung cho [`talking-head-autoedit.md`](talking-head-autoedit.md). Tài liệu này ghi lại: yêu cầu của
khách đi qua pipeline ra sao, thứ gì được phép ghi đè thứ gì, model thật sự nhìn/nghe được đến đâu,
và pipeline dùng bao nhiêu CPU trên VPS dùng chung.

## Nguyên tắc: yêu cầu phải có tác dụng, hoặc được báo là chưa làm

Mỗi ý trong yêu cầu của khách phải đi đến một trong ba chỗ sau. Không ý nào được phép lặng lẽ biến mất.

| Kết quả | Ở đâu | UI hiện là |
|---|---|---|
| Đã làm | patch / option đã đổi | "Đã làm" |
| Bị chặn, có lý do | `outcome.cuts_blocked` (`by`: `verifier` · `an_toan` · `qua_ngan`) | "Bị chặn" |
| Không làm được | `outcome.not_done` | "Chưa làm được" |

`state.versions[i].outcome` = `{cuts_proposed, cuts_applied, cuts_blocked, cuts_total_applied,
options_changed, not_done}`. Revise ghi `options_changed`/`not_done`; `audit` gộp thêm phần cut sau
khi quyết. Nếu một version thêm cut (revise hoặc tự đánh dấu), `cuts_*` chỉ nói về **các cut lượt đó
yêu cầu** (cờ `moi` trên đề xuất), còn `cuts_total_applied` là tổng số cut của cả video. Mỗi version
còn giữ `options_previous`, tức giá trị cũ của các option nó đổi.

Mọi version mới (revise, tự đánh dấu, rollback) đều đi qua `versions.add_version`.

Ca thật đã dẫn tới quy tắc này (một job khách hàng thật, 2026-09): khách xin "full frame, cắt kỹ đoạn vấp,
thêm key, emoji, viết hoa đầu từ". Kết quả khi đó chỉ có emoji. Khung hình là option mà revise không
được sửa, AI không thêm keyword nào vì prompt revise không có schema keyword, còn 2 cut verifier đã
duyệt thì bị một phép đo sai chặn lại. UI vẫn báo "đổi cut_remove". Sau khi sửa, cùng câu đó cho ra:
khung `none`, `cut_level=tight`, thêm 8 keyword, caption "Viết Hoa Đầu Từ", 2/3 cut được áp, và cut
còn lại được báo kèm lý do.

## Thứ tự ưu tiên (cái trên thắng cái dưới)

1. Chọn tay trong job (UI/API/CLI cờ tường minh), revise/chat.
2. Yêu cầu riêng (`prompt`) + `card_plan`: nằm ngay sau khối ưu tiên trong `structure.v8` và được
   gửi vào **cả** các call caption (`captions.v3`). Trước đó, caption không hề thấy yêu cầu này.
3. Mẫu dựng (edit style) / mặc định project.
4. `DEFAULT_OPTIONS`.

Ba chỗ từng làm đảo thứ tự này, đã sửa:

- CLI `--job` từng coi `frame_preset`/`audio_preset` mặc định là "cờ khách truyền", nên mỗi lượt
  server chạy lại đều reset khung về `dark` và tiếng về `shotgun`. Giờ chỉ cờ thật sự có mặt mới
  được tính (`cli.py`, `merge_options`).
- Chọn `audio_preset` tay thì tự tắt `auto_audio_preset`. Trước đây phép đo phòng đè luôn lựa chọn
  (`job_store.merge_options`, dùng ở mọi chỗ server cập nhật option).
- Revise đổi nhạc thì ghi ngược vào option. Trước đây `audit` áp lại nhạc của mẫu dựng ở mỗi lượt.

## Mức cắt (`cut_level`)

| Mức | Director được dặn | Cổng cơ học (`cut_safety`) |
|---|---|---|
| `light` | chỉ ê-a đứng riêng + lặp nguyên văn | cut ≤2 từ phải là tiếng đệm trong từ điển |
| `normal` (mặc định) | ê-a, lặp, câu nói dở | như trên |
| `tight` | duyệt toàn bộ, mọi ê-a/lặp/nói vấp/khoảng lặng dài | tin verifier cho cut ngắn có lý do `repeat`/`false_start`/`soft_restart`/`filler` |

Để trống thì mức được suy từ `prompt` ("cắt kỹ/cắt vấp" → `tight`, "giữ nguyên lời/ít cắt" → `light`).
Revise cũng đổi được mức này.

**Độ dài cut chỉ đo ở một chỗ:** `resolve_spans.cut_window`, tức khoảng thật bị bỏ gồm cả quãng lặng
quanh từ, trừ `PAD`. Cut dưới `MIN_CUT=0.28s` bị bỏ và được báo `qua_ngan`. `cut_safety` từng tự đo
bằng độ dài chữ ASR, trong khi ASR cho "ờ…" chỉ 0.06s dù nằm giữa 1.4s im lặng. Phép đo sai đó
chặn cut có cửa sổ thật 1.36s.

Cut do khách tự đánh dấu (bôi đen trên tab Lời, `POST /api/jobs/{id}/cuts`, gọi
`versions.apply_user_cuts`) là quyết định, không phải gợi ý. Nó **không qua LLM**, được lưu với
`nguon: "khach"`, và không đi qua verifier lẫn cổng từ điển. Model **không được** tự gắn nhãn này:
patch của revise luôn bị ép về `nguon: "ai"`. Cut của khách trùng khoảng mà đạo diễn đã đề xuất sẽ
nâng đề xuất đó lên thành của khách. Cắt trọn một đoạn quay (take) thì đoạn đó bị bỏ khỏi video,
trừ khi video chỉ có đúng đoạn đó.

`cut_proposed` giữ mọi đề xuất; `cut_remove` là phần `audit` đã duyệt. `cut_proposed: []` nghĩa là
"không đề xuất gì". Trước đây danh sách rỗng bị coi như "chưa có", nên hệ thống quay về dùng
`cut_remove` cũ, và "Giữ" cut cuối cùng thì cut đó lại quay về. Revise dùng `cut_add` /
`cut_restore` cộng dồn vào đề xuất. `set.cut_remove` (thay toàn bộ) vẫn còn nhưng prompt không
khuyến khích, vì trước đây nó xoá cả cut đã duyệt.

## Revise được sửa gì

- Patch event (`add`/`remove`/`modify`), kèm schema keyword/punch/sfx/card/caption trong prompt.
- `set`: `cold_open`, `endcard`, `bgm`, `grade`.
- `cut_add`, `cut_restore`.
- `options` (whitelist, có kiểm giá trị): `frame_preset` (`none`/`dark`/`light`/`blur`),
  `tempo` 0.9–1.25, `cut_level`, `cold_open`. Ngoài danh sách này thì ghi vào `not_done`. Nhạc do
  revise chọn phải là file có thật (`usable_bgm`), âm lượng bị kẹp trong 0.08–0.22. Bật hook mà chưa
  có câu hook thì báo `not_done`. Một cut sai dạng chỉ bị bỏ qua và báo lỗi, không làm hỏng cả lượt.
- Prompt nhận **toàn bộ** xương sống lời (tối đa 3000 từ). Trước đây chỉ 400 từ đầu, nên cut ở nửa
  sau không thể đề xuất.
- Chat nhớ 5 lượt gần nhất. Cảnh báo khi một lượt vượt 40k token; take khoảng 4 phút tốn khoảng
  25–30k, còn dựng lại từ đầu tốn khoảng 89k.

Runner đọc lại option sau mỗi stage, nên revise → audit → resolve trong cùng một lượt dùng đúng
option mới. Cache `audit` giờ tính cả `cut_level` và nhạc; cache `resolve` tính cả
`auto_audio_preset`.

**Rollback** (`versions.rollback_to`) chép spec cũ lên thành version mới, **khôi phục các option** mà
những version sau đã đổi (khung, nhạc, mức cắt…), rồi cắt lại `src.mp4`. Trước đây rollback không
cắt lại, nên preview/xuất bị lệch thời gian với caption. **Dry-run** chat chỉ hoàn nguyên đúng những
option lượt đó đổi.

## Model có nghe được để chấm chất lượng tiếng không — phép thử mù

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

### Vì sao calibrate cần model, không chỉ số đo

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

**Độ nét không tự động là chất lượng.** Các số sharpen/clarity từng được tinh chỉnh
trên một nguồn 720p không phải mặc định an toàn cho mọi video. Hiện tại không thêm
độ nét nếu không được yêu cầu; `auto_sharpen` là lựa chọn bật riêng.

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

## Model có nhìn được để "làm đẹp" không — phép thử mù

```bash
python -m lib.talking_head_edit.cli --job <id> --check-seeing --at 60 --check-strength 0.3
```

Cách làm giống phép thử nghe (`blind_probe.py` dùng chung): một khung hình thật, 1 bản sạch, 1 bản
sạch trùng (bẫy bịa), 6 bản mỗi bản tiêm một lỗi (quá sáng, quá tối, da cam, nhoè, nét quá tay,
nhiễu). Nhãn trung tính. Phần hai là chọn bản đẹp hơn theo cặp, mỗi cặp gửi 2 lần có đảo vị trí.

Đo ngày 2026-09-24, `ag/gemini-3.7-flash-high`, 2 vòng:

| Mức lỗi | Chấm mức nặng | Chọn bản đẹp (đảo vị trí) |
|---|---|---|
| 1.0 (rõ) | 6/6, lift ~7.5, 2 bản giống hệt lệch 0 | 100% cả 2 vòng |
| 0.5 | 6/6, lift 6.3 | — |
| 0.25–0.35 (cỡ chỉnh màu thật: sáng ±0.04, bão hoà 1.12) | 3/6 → 2/6, bịa lệch ~1 điểm | 83% → **58%** |

**Kết luận:** model thấy được **ảnh hỏng rõ**, nhưng **không phân biệt được chỉnh đẹp tinh tế** ở mức
pipeline thật sự dùng (grade trong khoảng sáng −0.05..0.08, bão hoà ≤1.03, còn nhỏ hơn mức 0.25).
Vì vậy grade mặc định là `{}`, `calibrate` tắt, và làm đẹp chỉ đến từ đo đạc (`auto_grade` đo luma)
hoặc preset do người chọn. Nếu dùng model về hình ảnh, chỉ nên để kiểm lỗi thô. Chạy lại phép thử khi
đổi model trước khi bật `calibrate_grade`.

## CPU: VPS 10 core dùng chung

`lib/talking_head_edit/cpu_budget.py` là nơi duy nhất quyết định dùng bao nhiêu CPU:

- `AUTOEDIT_CPU_BUDGET`: số core pipeline được dùng. Mặc định 60% số core khả dụng, tức 6 trên máy này.
- `AUTOEDIT_NICE` (mặc định 10; 0 = tắt): hàng đợi chạy mọi lượt pipeline qua `nice` + `ionice`
  (có `--`, vì `nice` uutils trên máy này từ chối `nice -n 10 python -m …`). Preview chạy trong server
  cũng hạ ưu tiên.
- ffmpeg: số span encode song song ≤ ngân sách; encoder `-threads` = ngân sách ÷ số span, còn
  decoder (trước `-i`) và `-filter_threads` được gấp đôi con số đó (không vượt ngân sách). Đo 4 span
  HEVC 1080p → x264 medium song song: 1/1/1 mất 170s (2.2 core, decoder HEVC bị bóp nghẹt);
  1/2/2 mất 77s (4.2 core, tổng CPU thấp nhất); không giới hạn thì riêng một encode đã dùng ~7.7 core.
- Remotion: concurrency = min(`OPENMONTAGE_RENDER_MAX_CONCURRENCY`, ngân sách); override qua env
  cũng không vượt ngân sách. Render cloud dùng số core của máy thuê, không bị giới hạn này. Preview clip dùng
  một nửa số đó.
- Cold-open giờ chỉ encode đoạn teaser rồi ghép `-c copy`. Trước đây nó encode lại **toàn bộ** video
  lần hai.

Đo lượt resolve thật (234s timeline, 3 nguồn, 16 span) ở `nice 10`: xong trong 205s, ffmpeg chạy ở
mức ưu tiên thấp và máy vẫn đáp ứng các tiến trình khác.
