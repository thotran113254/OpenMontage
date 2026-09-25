"""Scoring shared by the blind checks: can the model perceive a defect, or not?

Each check builds one clean control, a byte-identical twin, and one copy per
injected defect, all under labels that carry no information. The model rates
EVERY defect on EVERY sample. What counts is the lift: the injected defect's
severity where it was injected, minus the same defect's severity on the
control. A model reading the prompt back at us lifts to zero; the twin catches
one that invents differences.

Controls are told apart by file stem (`*_ctl` / `*_ctl2`), never by label.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# The order is fixed but deliberately not the order samples are generated in,
# so position leaks nothing either.
LABEL_ORDER = ("m4", "m7", "m2", "m8", "m1", "m5", "m3", "m6")
LIFT_TO_COUNT = 2.0

Probe = tuple[str, Path, "str | None"]


def label_probes(built: list[tuple[Path, str | None]]) -> list[Probe]:
    """Attach neutral labels and sort by label, so the control is not first."""
    labelled = sorted(zip(LABEL_ORDER, built), key=lambda item: item[0])
    return [(label, path, injected) for label, (path, injected) in labelled]


def _severity(row: dict[str, Any], defect: str) -> float | None:
    value = row.get(defect)
    return float(value) if isinstance(value, (int, float)) else None


def score_blind(rows: list[dict[str, Any]], probes: list[Probe],
                defects: tuple[str, ...], verb: str) -> dict[str, Any]:
    """Turn the model's severity grid into a verdict. `verb` is "nghe" or "nhìn"."""
    by_label = {str(r.get("ban")): r for r in rows if r.get("ban")}
    control_label = next((l for l, p, i in probes if i is None and p.stem.endswith("_ctl")), "")
    twin_label = next((l for l, p, i in probes if i is None and p.stem.endswith("_ctl2")), "")
    control = by_label.get(control_label, {})

    detections: list[dict[str, Any]] = []
    for label, _, injected in probes:
        if not injected:
            continue
        here = _severity(by_label.get(label, {}), injected)
        there = _severity(control, injected)
        detections.append({
            "loi": injected,
            "diem_khi_co": here,
            "diem_doi_chung": there,
            "lift": None if here is None or there is None else round(here - there, 2),
        })

    lifts = [d["lift"] for d in detections if d["lift"] is not None]
    perceived = [d for d in detections if (d["lift"] or 0) >= LIFT_TO_COUNT]
    twin = by_label.get(twin_label, {})
    gaps = [abs(a - b) for defect in defects
            if (a := _severity(control, defect)) is not None
            and (b := _severity(twin, defect)) is not None]

    return {
        "chi_tiet": detections,
        "so_loi_nhan_ra": len(perceived),
        "tong_so_loi": len(detections),
        "lift_trung_binh": round(sum(lifts) / len(lifts), 2) if lifts else None,
        "sai_lech_hai_ban_giong_nhau": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "ket_luan": verdict(len(perceived), len(detections), gaps, verb),
    }


def verdict(perceived: int, total: int, gaps: list[float], verb: str) -> str:
    drift = sum(gaps) / len(gaps) if gaps else 0.0
    if not total:
        return "không chấm được"
    if perceived >= total * 0.75 and drift <= 1.5:
        return f"{verb.upper()} ĐƯỢC — bắt đúng lỗi và không bịa khác biệt"
    if perceived >= total * 0.75:
        return f"{verb} được nhưng thiếu ổn định (lệch {drift:.1f} điểm giữa hai bản giống nhau)"
    if perceived >= total * 0.4:
        return f"{verb} được một phần — chỉ bắt được lỗi rõ nhất"
    return f"KHÔNG {verb.upper()} ĐƯỢC — điểm không đổi khi lỗi được thêm vào"
