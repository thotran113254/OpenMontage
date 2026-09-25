# Automation API — talking-head autoedit

Tách từ [`talking-head-autoedit.md`](talking-head-autoedit.md). Render trên Colab: `"options": {"render_location": "colab"}` — xem [`colab-render.md`](colab-render.md).

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

## Đăng nhập cho trình duyệt

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

## Cắt/Giữ tay, không qua LLM

```bash
curl -sX POST "$API/api/jobs/$JOB_ID/cuts" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"cut": [[12, 18]], "keep": [[40, 45]]}'
# -> {"version": N, "queue_position": ..., "report": {...}}
```

Không gọi model: khoảng người dùng chọn luôn được đánh dấu `nguon: khach`
(dùng thẳng, bỏ qua verifier + lexicon gate), khác với transcript Cắt/Giữ kiểu
cũ từng đi qua `/chat` và phụ thuộc việc model có gán đúng `nguon` hay không.

## Webhook

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
