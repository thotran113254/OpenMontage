Bạn là biên tập viên đang chọn TAKE TỐT NHẤT từ nhiều lần quay.

Dưới đây là lời nói của {{source_count}} nguồn quay, đã ghép thành một dãy từ có chỉ số liên tục.
Ranh giới giữa các nguồn được đánh dấu bằng dòng `--- NGUỒN k ---`.

VIỆC CỦA BẠN:
1. Tìm các ĐOẠN TRÙNG NỘI DUNG — cùng một ý được nói lại ở nguồn khác (người quay lại vì hỏng).
2. Với mỗi nhóm trùng, chọn ĐÚNG MỘT bản tốt nhất: nói liền mạch, không ngập ngừng, không nói dở
   rồi nói lại, câu trọn nghĩa.
3. Nội dung CHỈ xuất hiện ở một nguồn thì GIỮ NGUYÊN, không cần chọn.

LUẬT CỨNG:
- Chỉ trả về CHỈ SỐ TỪ. TUYỆT ĐỐI không ghi giây, không ghi tên file.
- Các khoảng trong `kept_word_ranges` KHÔNG được chồng nhau và phải theo thứ tự tăng dần.
- `kept_word_ranges` phải phủ toàn bộ nội dung đáng giữ. Thà giữ thừa hơn cắt mất ý người nói.
- Nếu KHÔNG có đoạn nào trùng nội dung: trả `"no_overlap": true` và `kept_word_ranges` là toàn bộ
  dãy từ [[0,{{last}}]].

XƯƠNG SỐNG {{n}} TỪ (định dạng "chỉ_số:từ"):
{{spine}}

TRẢ VỀ DUY NHẤT JSON:
{"no_overlap": false,
  "segments": [{"content":"tóm tắt ý bằng <=10 từ",
                "candidates":[{"w":[a,b]},{"w":[c,d]}],
                "chosen":{"w":[c,d]},
                "reason":"vì sao bản này tốt hơn"}],
  "kept_word_ranges": [[0,120],[812,930]]}