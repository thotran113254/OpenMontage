"""Can the model SEE picture quality well enough to be trusted with "làm đẹp"?

Two blind tests on one real frame of the job's footage:

1. Severity — one clean control, a byte-identical twin and one copy per injected
   defect, rated 0-10 on every defect (scored by `blind_probe`). Run it at
   several strengths: the pipeline's own grade moves are small, so the strength
   where detection stops is the useful number.
2. Preference — "which of the two looks better", clean vs. degraded, every pair
   sent twice with the sides swapped. A model that sees picks the clean frame
   both times; one that doesn't lands near 50% and favours a side.

Only the calibrate stage asks a model to judge pictures; the director never sees
a frame. Run this before letting any model-driven look decision back on.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import Any

from lib.talking_head_edit.blind_probe import label_probes, score_blind

# ffmpeg video filter per defect at strength 1.0 — clearly visible, still within
# what bad phone footage or a heavy-handed grade produces.
DEFECT_FILTERS: dict[str, str] = {
    "qua_sang": "eq=brightness={0.16}",
    "qua_toi": "eq=brightness={-0.16}",
    "da_cam": "eq=saturation={1+0.5},colorbalance=rm={0.12}:bm={-0.08}",
    "mo_nhoe": "gblur=sigma={3.0}",
    "vien_net": "unsharp=7:7:{3.0}",
    "nhieu_hat": "noise=alls={40}:allf=u",
}
ALL_DEFECTS = tuple(DEFECT_FILTERS)

PROMPT_VI = """Bạn là colorist kiểm hình talking-head. Dưới đây là nhiều ảnh của CÙNG MỘT khung hình, gửi theo đúng thứ tự nhãn. Mỗi ảnh có thể đã bị thêm lỗi hình ảnh, hoặc không.

Với TỪNG ảnh, chấm mức độ nghiêm trọng 0-10 cho TỪNG lỗi sau (0 = hoàn toàn không có, 10 = rất nặng):
- qua_sang: phơi sáng quá, vùng sáng cháy, ảnh bệch
- qua_toi: thiếu sáng, mặt tối, vùng tối bị bí
- da_cam: màu quá rực, da ngả cam/đỏ
- mo_nhoe: mềm/nhoè, mất chi tiết tóc, mắt
- vien_net: làm nét quá tay, viền sáng quanh cạnh
- nhieu_hat: hạt nhiễu li ti trên toàn ảnh

Chấm đúng thứ bạn THẤY ở từng ảnh. Đừng cho điểm giống hệt nếu chúng thực sự khác — và đừng bịa khác biệt nếu chúng giống nhau.

CHỈ trả JSON: {"ket_qua":[{"ban":"<nhãn>","qua_sang":n,"qua_toi":n,"da_cam":n,"mo_nhoe":n,"vien_net":n,"nhieu_hat":n}]}"""

PAIR_PROMPT_VI = """Bạn là colorist. Mỗi CẶP gồm hai ảnh của cùng một khung hình talking-head, ảnh "a" gửi trước, ảnh "b" gửi sau. Với TỪNG cặp, chọn ảnh ĐẸP HƠN để đăng lên mạng (tự nhiên, da đẹp, rõ nét, đúng sáng). Nếu thật sự không thấy khác biệt, vẫn phải chọn một.

CHỈ trả JSON: {"ket_qua":[{"cap":"<mã cặp>","tot_hon":"a|b"}]}"""


def defect_filter(defect: str, strength: float) -> str:
    """The defect's filter with every `{number}` scaled toward neutral by `strength`."""
    template = DEFECT_FILTERS[defect]

    def scaled(token: str) -> str:
        expression = token.strip("{}")
        if expression.startswith("1+"):          # multiplicative: 1 is neutral
            return str(round(1 + float(expression[2:]) * strength, 3))
        return str(round(float(expression) * strength, 3))

    out, rest = "", template
    while "{" in rest:
        head, _, tail = rest.partition("{")
        token, _, rest = tail.partition("}")
        out += head + scaled(token)
    return out + rest


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi: {result.stderr.strip()[-400:]}")


def extract_base(source: Path, out_dir: Path, at_seconds: float, width: int = 720) -> Path:
    """One lossless frame, downscaled so the model sees the whole picture cheaply."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / "_base.png"
    _ffmpeg(["-ss", f"{at_seconds:.2f}", "-i", str(source), "-frames:v", "1",
             "-vf", f"scale={width}:-2:flags=lanczos", str(base)])
    return base


def render_variant(base: Path, out: Path, vf: str | None) -> Path:
    _ffmpeg(["-i", str(base), *(["-vf", vf] if vf else []), "-q:v", "2", str(out)])
    return out


def build_probe_set(base: Path, out_dir: Path, strength: float = 1.0) -> list[tuple[str, Path, str | None]]:
    """Severity set: control, one copy per defect, twin — neutrally labelled."""
    tag = f"s{int(strength * 100)}"
    built: list[tuple[Path, str | None]] = [(render_variant(base, out_dir / f"{tag}_ctl.jpg", None), None)]
    for defect in ALL_DEFECTS:
        built.append((render_variant(base, out_dir / f"{tag}_x_{defect}.jpg",
                                     defect_filter(defect, strength)), defect))
    built.append((render_variant(base, out_dir / f"{tag}_ctl2.jpg", None), None))
    return label_probes(built)


def score_seeing(rows: list[dict[str, Any]],
                 probes: list[tuple[str, Path, str | None]]) -> dict[str, Any]:
    return score_blind(rows, probes, ALL_DEFECTS, "nhìn")


def build_pair_set(base: Path, out_dir: Path, strength: float,
                   seed: int = 7) -> list[dict[str, Any]]:
    """Every defect as a clean-vs-degraded pair, sent twice with the sides swapped.

    Pair ids are shuffled so a pair and its mirror are not adjacent.
    """
    tag = f"p{int(strength * 100)}"
    clean = render_variant(base, out_dir / f"{tag}_clean.jpg", None)
    pairs: list[dict[str, Any]] = []
    for defect in ALL_DEFECTS:
        bad = render_variant(base, out_dir / f"{tag}_{defect}.jpg", defect_filter(defect, strength))
        pairs.append({"loi": defect, "a": clean, "b": bad, "dung": "a"})
        pairs.append({"loi": defect, "a": bad, "b": clean, "dung": "b"})
    random.Random(seed).shuffle(pairs)
    for index, pair in enumerate(pairs, start=1):
        pair["cap"] = f"c{index}"
    return pairs


def pair_images(pairs: list[dict[str, Any]]) -> list[tuple[str, Path]]:
    return [image for pair in pairs
            for image in ((f"{pair['cap']} a", pair["a"]), (f"{pair['cap']} b", pair["b"]))]


def score_preference(rows: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Correct-pick rate, both-orders consistency, and the share of "a" picks."""
    picks = {str(r.get("cap")): str(r.get("tot_hon", "")).strip().lower() for r in rows}
    answered = [p for p in pairs if picks.get(p["cap"]) in ("a", "b")]
    correct = [p for p in answered if picks[p["cap"]] == p["dung"]]
    by_defect: dict[str, list[bool]] = {}
    for pair in answered:
        by_defect.setdefault(pair["loi"], []).append(picks[pair["cap"]] == pair["dung"])
    both_right = [d for d, hits in by_defect.items() if len(hits) == 2 and all(hits)]
    a_share = (sum(picks[p["cap"]] == "a" for p in answered) / len(answered)) if answered else None
    rate = len(correct) / len(answered) if answered else 0.0

    if not answered:
        conclusion = "không chấm được"
    elif rate >= 0.9 and len(both_right) >= len(by_defect) * 0.8:
        conclusion = "CHỌN ĐÚNG — phân biệt được bản đẹp ở mức lỗi này"
    elif rate <= 0.65:
        conclusion = "KHÔNG phân biệt được — gần như đoán, không tin để tự làm đẹp"
    else:
        conclusion = "phân biệt một phần — chỉ bắt được lỗi rõ"
    return {
        "ty_le_dung": round(rate, 2),
        "loi_dung_ca_hai_chieu": sorted(both_right),
        "ty_le_chon_a": None if a_share is None else round(a_share, 2),
        "so_cap_tra_loi": len(answered),
        "tong_so_cap": len(pairs),
        "ket_luan": conclusion,
    }
