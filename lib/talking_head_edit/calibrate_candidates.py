"""Candidate looks and sounds to try before committing to a full render.

Deliberately a handful, not a grid. Each axis here is one that was measured to
matter on real footage, and every extra candidate is another render plus more
for the judge to hold in its head at once:

* exposure — a director-chosen gamma below 1.0 darkened the face on footage
  that was already dim
* colour cast — saturation above ~1.03 turned skin orange long before the
  picture read as vivid
* skin smoothing — what erases texture, and what makes a face read as plastic

Sharpening is deliberately NOT a candidate here. A model judging stills got it
wrong twice: it picked 1.2 as the optimum when the rendered deliverable needed
1.6, then rated a genuine 2.2x difference as "hầu như không có sự khác biệt".
Its vision pipeline resamples away exactly the high-frequency detail under test.
Sharpening is set from the upscale factor instead — see
`resolve_media.default_sharpening`, which is derived from measurements taken on
rendered clips rather than on stills.
"""

from __future__ import annotations

from typing import Any

# Tunings applied on top of whatever grade the director proposed.
GRADE_CANDIDATES: dict[str, dict[str, Any]] = {
    "nhu_de_xuat": {},
    "sang_hon": {"gamma": 1.03, "vignette": 0.3},
    "trung_tinh": {"gamma": 1.03, "vignette": 0.3, "warmth": 0, "saturation": 1.0},
    # Skin smoothing is here because over-smoothed skin is something a viewer
    # notices ("bết như nhựa") — sharpening is NOT, see the note below.
    "da_min_hon": {"gamma": 1.03, "vignette": 0.3, "warmth": 0, "saturation": 1.0,
                   "skin_smooth": 0.22, "blemish_reduce": 0.2},
}

AUDIO_CANDIDATES = ["voice", "shotgun", "shotgun_dry"]

GRADE_PROMPT = """Bạn là colorist. Các ảnh dưới đây là CÙNG MỘT khung hình từ video talking-head, chỉnh màu khác nhau, gửi theo đúng thứ tự nhãn. Ảnh đầu tiên nhãn "goc" là chưa chỉnh gì.

Chấm từng bản thang 1-10:
- do_sang: mặt người có đủ sáng không (quá tối hoặc cháy sáng đều trừ điểm)
- mau_da: tông da có tự nhiên không (ngả cam/vàng/đỏ là trừ điểm nặng)
- ket_cau_da: da còn giữ kết cấu thật hay đã bị mài bết như nhựa/sáp
- tu_nhien: có lộ dấu vết chỉnh sửa không (viền sáng quanh tóc, màu giả, mặt tách khỏi nền)

Đây là video cho mạng xã hội: cần bắt mắt nhưng người xem KHÔNG được nhận ra là đã qua chỉnh sửa.

ĐỪNG chấm độ nét/độ phân giải — phần đó đã được quyết định bằng đo đạc trên video
đã render, ảnh tĩnh gửi cho bạn không phản ánh đúng độ nét của bản cuối.

CHỈ trả JSON: {"danh_gia":[{"ban":"<nhãn>","do_sang":n,"mau_da":n,"ket_cau_da":n,"tu_nhien":n,"nhan_xet":"..."}],"nen_dung":"<nhãn>","ly_do":"...","canh_bao":"..."}"""

AUDIO_PROMPT = """Bạn là kỹ sư âm thanh. Các bản dưới đây là CÙNG MỘT đoạn thoại tiếng Việt, xử lý khác nhau, gửi theo đúng thứ tự nhãn.

Bản nhãn "voice" xử lý nhẹ nhất — dùng nó để nghe xem BẢN THU GỐC vốn có vấn đề gì.

Video này đăng mạng xã hội, người xem nghe bằng LOA ĐIỆN THOẠI, phần lớn không có tai nghe. Vậy nên ưu tiên giọng dày và rõ ở dải trung; mất tiếng trầm sâu không sao, nhưng giọng mỏng hoặc chói là hỏng.

BƯỚC 1 — chẩn đoán bản thu gốc. Chỉ liệt kê vấn đề nào CÓ THẬT, đừng kể cho đủ:
- u_am_tram: ù/rền dưới 150 Hz (điều hoà, quạt, rung bàn)
- xi_nen: tiếng xì đều đều ở dải cao
- vang_phong: đuôi tiếng kéo dài, nghe như nói trong phòng trống
- bi_boc: nghe như nói trong thùng (dư 200–500 Hz)
- xi_gio: âm /s/, /x/, /ch/, /tr/ chói gắt
- bung_hoi: tiếng "bụp" ở âm /p/, /b/
- vo_tieng: méo do thu quá to

BƯỚC 2 — chấm từng bản 1-10, nghe đúng vào những chỗ sau:
- do_sach: nghe khoảng LẶNG GIỮA CÁC CÂU — còn ù/xì không
- do_vang: nghe ĐUÔI CÂU — vang vừa đủ là tốt nhất (9-10). Khô tuyệt đối như phòng thu nghe giả tạo và bị trừ nặng, ngang với vang quá nhiều
- do_ro: rõ chữ, giọng nghe "gần" và có hiện diện không
- tu_nhien: MỤC QUAN TRỌNG NHẤT, nghe kỹ ba thứ:
  · đuôi từ có bị nuốt hoặc cắt cụt không
  · dấu thanh (sắc/huyền/hỏi/ngã/nặng) có còn đi đúng đường không hay bị bẹt — tiếng Việt mất đường thanh là mất nghĩa
  · giọng có mỏng, ướt, bập bùng, hay nghe như qua điện thoại không

Dưới 6 điểm ở "tu_nhien" là bị loại, dù ba mục kia cao đến đâu. Bản khử nhiễu/khử vang mạnh nhất thường sạch nhất VÀ tệ nhất cùng lúc.

LƯU Ý: file gửi cho bạn đã nén lại để truyền. ĐỪNG chấm chất lượng nén đó — chỉ so khác biệt GIỮA các bản.

CHỈ trả JSON:
{"chan_doan":["<vấn đề có thật của bản thu gốc>"],
 "danh_gia":[{"ban":"<nhãn>","do_sach":n,"do_vang":n,"do_ro":n,"tu_nhien":n,"nhan_xet":"..."}],
 "nen_dung":"<nhãn>","ly_do":"...","canh_bao":"..."}"""


def score_of(row: dict[str, Any], keys: tuple[str, ...]) -> float:
    """Average of the scored dimensions, ignoring any the model omitted."""
    values = [float(row[k]) for k in keys if isinstance(row.get(k), (int, float))]
    return sum(values) / len(values) if values else 0.0


NATURALNESS_FLOOR = 6.0


def pick_winner(verdict: dict[str, Any], valid: list[str], keys: tuple[str, ...],
                veto_key: str = "tu_nhien",
                floor: float = NATURALNESS_FLOOR) -> tuple[str | None, str]:
    """Trust the model's own pick — unless it picked something it called unnatural.

    The veto exists because the failure mode is consistent and one-directional:
    the most aggressively processed candidate scores best on every "cleanliness"
    dimension and worst on naturalness, so a plain average lets it win. Twice the
    highest signal-to-noise audio was separately rated 4/10 for naturalness
    ("mất đuôi âm ở các từ"). A candidate the model itself scores below the floor
    should not be able to win by averaging, nor by being named.
    """
    rows = [r for r in verdict.get("danh_gia", []) if str(r.get("ban")) in valid]
    vetoed = {str(r["ban"]) for r in rows
              if isinstance(r.get(veto_key), (int, float)) and float(r[veto_key]) < floor}

    chosen = str(verdict.get("nen_dung", "")).strip()
    if chosen in valid and chosen not in vetoed:
        return chosen, "model chọn"

    survivors = [r for r in rows if str(r["ban"]) not in vetoed]
    if not survivors:
        if not rows:
            return None, "không có đánh giá hợp lệ"
        return None, f"mọi bản đều dưới {floor} điểm {veto_key} — giữ mặc định"

    best = str(max(survivors, key=lambda r: score_of(r, keys))["ban"])
    if chosen in vetoed:
        return best, f"model chọn '{chosen}' nhưng tự chấm {veto_key} < {floor} — đổi sang bản khác"
    return best, "điểm trung bình cao nhất (model không chọn rõ)"
