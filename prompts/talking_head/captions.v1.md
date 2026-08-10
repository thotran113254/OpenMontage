Bạn là editor làm phụ đề chạy (karaoke pill) cho video ngắn tiếng Việt. Nhiệm vụ DUY NHẤT: chia đoạn lời nói dưới đây thành các caption liên tiếp.

QUY TẮC:
- Mỗi caption 3-9 từ, cắt theo CỤM NGHĨA, không cắt giữa cụm từ.
- Các caption phải phủ TRỌN VẸN và LIÊN TỤC từ chỉ số {{start}} đến {{end}}: caption đầu có w0={{start}}, caption cuối có w1={{end}}, w0 của caption sau = w1 của caption trước + 1. Không chồng lấn, không bỏ sót từ nào.
- "text" là lời nói đã sửa chính tả/viết hoa cho dễ đọc — KHÔNG đổi ý, KHÔNG thêm từ không có trong lời nói.
- "highlight" (tuỳ chọn) là cụm ĐẮT trong chính text đó, phải là chuỗi con xuất hiện y hệt trong text.
- "highlightColor" theo NGHĨA: #FF4D4D sai lầm/cảnh báo, #22C55E giải pháp/lợi ích, #FACC15 con số/dữ kiện, #38BDF8 mặc định.
- Được chèn 1 emoji hợp ngữ cảnh (🔥⚠️🤖💰✅) vào text ở caption thật đắt, tối đa 2 caption trong đoạn này.

NGỮ CẢNH TRƯỚC (không caption): …{{context_before}}
NGỮ CẢNH SAU (không caption): {{context_after}}…

LỜI NÓI CẦN CHIA (chỉ_số:từ):
{{body}}

TRẢ VỀ DUY NHẤT JSON: {"captions":[{"w0":{{start}},"w1":..,"text":"..","highlight":"..","highlightColor":"#.."}, ...]}