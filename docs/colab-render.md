# Render trên Colab TPU v6e-1 (tự động hoàn toàn)

Đưa bước render MP4 sang Colab để VPS không phải gánh. Không cần thao tác tay: server đóng gói, điều
khiển trình duyệt, chờ, kiểm tra kết quả và tắt runtime.

## Dùng

- UI: trang job → **Xuất MP4 (Colab)** (nút chính khi Colab bật), hoặc tab Chạy → "Xuất MP4 trên Colab".
- API: `POST /api/jobs/{id}/render?location=colab`, hoặc `POST /api/runs` với
  `"options": {"render_location": "colab"}`.
- CLI: `python -m lib.talking_head_edit.cli --job <id> --stages render --render-location colab`.

Chỉ bản full size chạy trên Colab. Nháp 540p vẫn render trên máy này.

## Cách chạy (`lib/cloud_render/colab.py`, `colab_browser.py`)

1. Bộ composer (theo hash, dùng lại được) và bộ job được đẩy lên R2, y như Vast.ai.
2. Server sinh `run.sh` lên R2. Colab chỉ nhận **presigned URL có hạn**: không giữ credential nào, và
   footage không đi qua dịch vụ công khai nào.
3. agent-browser gắn vào Chrome của **tài khoản Colab 1** (CDP `9222`, session `colab-cdp`), mở
   notebook render riêng (lưu trong `projects/.colab-render-state.json`), chọn runtime `v6e-1 TPU`,
   kết nối qua menu *Connect to a hosted runtime*, rồi chạy **một** ô: `!curl -fsSL <run.sh> | bash`.
   **Không bao giờ** đụng `colab2` (tài khoản 2, dành cho việc khác).
4. Script ghi `status.json` lên R2 khoảng 15 giây một lần. Server đọc để báo tiến độ, và bỏ cuộc khi
   quá `max_runtime_minutes` hoặc im lặng quá `stall_minutes`.
5. MP4 về một khoá staging trên R2 và qua đúng các kiểm tra của Vast.ai (`remote.finalize_output`:
   kích thước, lệch thời lượng) rồi mới thành `final.mp4`.
6. Luôn luôn — kể cả khi lỗi hay quá hạn — ngắt ô đang chạy, gọi `runtime.unassign()`, lưu log vào
   `logs/render_colab.log` của job, và xoá các khoá tạm trên R2.

Chỉ một render Colab chạy tại một thời điểm (khoá `projects/.colab-render.lock`).

## Cấu hình `config/colab-render.json`

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `enabled` | `true` | Tắt thì UI ẩn nút Colab và API từ chối |
| `runtime` | `v6e-1 TPU` | Tên đúng như hộp *Change runtime type* |
| `max_runtime_minutes` | `60` | Trần thời gian một lượt |
| `stall_minutes` | `6` | Bao lâu không có `status.json` mới thì coi như hỏng |
| `compute_units_per_hour` | `4.08` | Để ghi chi phí vào log job |
| `fallback_to_local` | `false` | `true` thì render trên máy khi Colab lỗi (tốn CPU VPS) |
| `x264_preset` | `""` | Để trống = giống render trên máy (xem số đo bên dưới) |

## Số đo (2026-09-25, video 240 s, 1080×1920, 7 222 frame)

| | Colab v6e-1 (44 vCPU EPYC 9B14) | VPS (pipeline dùng 6/10 core) |
|---|---|---|
| Kết nối runtime | ~40 s | — |
| Cài Node/npm ci/kit | ~55 s | — |
| Render | ~10.5–11.5 phút (~12 frame/s) | ~20 phút |
| Tổng tới khi có final.mp4 | ~14 phút | ~20 phút |
| Chi phí | ~0.9 compute unit | CPU VPS |

- `--x264-preset=veryfast` **không** nhanh hơn (831 s so với 838 s): nút thắt là khâu Chrome dựng frame,
  không phải encode. Vì vậy giữ preset mặc định để chất lượng giống hệt render trên máy.
- Runtime A100 chỉ có 12 vCPU Xeon 2.2 GHz, không hợp để render. v5e-1 có 24 vCPU với ~2.92 CU/giờ.

## Khi có sự cố

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| "Tài khoản Colab 1 đã đăng xuất" | Cookie hết hạn. Đăng nhập lại theo `FineTune-Model/docs/colab-login-runbook.md`; bước 2FA cần mã từ hộp thư khôi phục, chỉ người dùng đọc được |
| "tab is not responding" | File `~/.agent-browser/colab-cdp.target` trỏ tới tab đã mất. `ensure_chrome()` tự xoá; nếu vẫn lỗi thì xoá tay |
| Không nối được runtime | Hết CU hoặc TPU không sẵn. Xem *View resources*; có thể đổi `runtime` sang `v5e-1 TPU` |
| Nghi runtime còn chạy | *Additional connection options → Manage sessions* phải báo "No active sessions" |
| Đóng tab cuối của Chrome 9222 | Chrome tự tắt, PM2 bật lại; không cần làm gì |
