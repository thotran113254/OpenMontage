"""LLM-designed outro + cover stills. Images come from the 9router GPT-image gateway.

Text in generated images is unreliable for Vietnamese, so the model only paints
atmosphere (no letters). Remotion / Pillow draw the real copy on top.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.talking_head_edit.director_client import chat_json
from lib.talking_head_edit.stills import extract_still

FREEZE_NAME = "endcard_freeze.jpg"
HOOK_NAME = "cover_hook.jpg"
ART_NAME = "endcard_art.png"
COVER_AI_NAME = "cover_ai.png"
THUMB_NAME = "thumbnail.jpg"
REPORT_NAME = "visuals_report.json"

_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

_VISUAL_PROMPT = """Bạn là art director cho video talking-head dọc 9:16 (TikTok/Reels) tiếng Việt.
Chỉ thiết kế. KHÔNG bịa số liệu. Bám xương sống lời nói.

Trả JSON:
{{
  "kicker": "2-4 chữ IN HOA (vd LINK TRONG BIO)",
  "accent": "#E10600",
  "thumbnail_text": "hook 2 dòng, tối đa 8 chữ, đúng ý video",
  "endcard_image_prompt": "English, vertical 9:16 cinematic background, NO text NO letters NO watermark NO logos. Atmosphere only, matching this video.",
  "thumbnail_image_prompt": "English, vertical 9:16 poster background, NO text NO letters NO faces of fictional people. Graphic energy matching the hook. Leave the lower third dark/empty."
}}

accent: một hex lấy từ cảnh (áo đỏ, văn phòng) — CẤM tím/purple generic.
image prompts: cinematic, photographic lighting, no illustration-stock look.

Nội dung video:
{brief}
"""


def _brief(job, spec: dict[str, Any], words: list[dict[str, Any]]) -> str:
    text = " ".join(str(w.get("word") or "") for w in words[:120])
    end = spec.get("endcard") or {}
    captions = [e.get("text") for e in (spec.get("captions") or []) if e.get("text")]
    return (
        f"title/endcard: {end.get('title','')} — {end.get('subtitle','')}\n"
        f"captions: {' | '.join(captions[:6])}\n"
        f"lời: {text[:900]}"
    )


def design_visuals(job, spec: dict[str, Any], words: list[dict[str, Any]],
                   model: str | None = None) -> dict[str, Any]:
    parsed, usage, _raw = chat_json(
        _VISUAL_PROMPT.format(brief=_brief(job, spec, words)),
        model=model, temperature=0.4, max_tokens=1200, timeout=90, retries=2,
        raw_dump=job.dir / "logs" / "raw_visuals.txt",
    )
    if not isinstance(parsed, dict):
        raise RuntimeError("Visual director không trả object JSON.")
    accent = str(parsed.get("accent") or "#E10600").strip()
    if not accent.startswith("#") or len(accent) not in (4, 7):
        accent = "#E10600"
    if accent.lower() in ("#7c3aed", "#2563eb", "#8b5cf6", "#a855f7"):
        accent = "#E10600"
    return {
        "kicker": str(parsed.get("kicker") or "LINK TRONG BIO")[:40],
        "accent": accent,
        "thumbnail_text": str(parsed.get("thumbnail_text") or endcard_title(spec))[:80],
        "endcard_image_prompt": str(parsed.get("endcard_image_prompt") or ""),
        "thumbnail_image_prompt": str(parsed.get("thumbnail_image_prompt") or ""),
        "usage": usage,
    }


def endcard_title(spec: dict[str, Any]) -> str:
    return str((spec.get("endcard") or {}).get("title") or "").strip()


def _generate_png(prompt: str, dest: Path) -> str | None:
    if not prompt.strip():
        return None
    from lib.env_loader import load_env
    from tools.graphics.nine_router_image import NineRouterImage

    load_env()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tool = NineRouterImage()
    result = tool.execute({
        "prompt": prompt + " Absolutely no text, letters, numbers, captions, or watermarks.",
        "output_dir": str(dest.parent),
        "output_format": "png",
        "size": "1024x1536",
        "quality": "auto",
        "background": "opaque",
        "image_detail": "high",
    })
    if not result.success:
        result = tool.execute({
            "prompt": prompt + " Absolutely no text, letters, numbers, captions, or watermarks.",
            "output_dir": str(dest.parent),
            "output_format": "png",
            "size": "auto",
            "quality": "auto",
            "background": "auto",
            "image_detail": "high",
        })
    if not result.success or not result.artifacts:
        return result.error or "image gen failed"
    Path(result.artifacts[0]).replace(dest)
    return None


def _hex_rgb(accent: str) -> tuple[int, int, int]:
    h = accent.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 225, 6, 0


def composite_thumbnail(hook: Path, dest: Path, *, title: str, subtitle: str,
                        accent: str) -> None:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    base = Image.open(hook).convert("RGB")
    base = base.resize((1080, 1920), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(1100, 1920):
        alpha = int(210 * ((y - 1100) / 820))
        draw.line([(0, y), (1080, y)], fill=(12, 6, 8, alpha))
    font_lg = ImageFont.truetype(str(_FONT), 72) if _FONT.exists() else ImageFont.load_default()
    font_sm = ImageFont.truetype(str(_FONT), 36) if _FONT.exists() else ImageFont.load_default()

    def wrap(text: str, font, width: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            trial = (current + " " + word).strip()
            if draw.textlength(trial, font=font) <= width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines[:3]

    lines = wrap(title, font_lg, 920)
    y = 1280
    for line in lines:
        draw.text((80, y), line, font=font_lg, fill=(255, 248, 246, 255))
        y += 88
    if subtitle:
        y += 12
        bar = (80, y, 1000, y + 72)
        draw.rounded_rectangle(bar, radius=8, fill=(*_hex_rgb(accent), 255))
        sub_lines = wrap(subtitle, font_sm, 860)
        draw.text((104, y + 16), sub_lines[0] if sub_lines else subtitle,
                  font=font_sm, fill=(20, 8, 10, 255))

    out = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    out = out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, "JPEG", quality=92)


def _hook_time(props: dict[str, Any]) -> float:
    for event in props.get("events") or []:
        if event.get("type") == "punchIn":
            return max(0.15, float(event.get("at") or 0) + 0.12)
        if event.get("type") == "keyword":
            return max(0.15, float(event.get("at") or 0) + 0.08)
    return 0.9


def _patch_endcard_event(props: dict[str, Any], **fields: Any) -> None:
    for event in props.get("events") or []:
        if event.get("type") == "endcard":
            event.update({k: v for k, v in fields.items() if v})
            return


def run(job, options: dict[str, Any]) -> dict[str, Any]:
    """Design copy + generate art + write thumbnail.jpg. Patches current props."""
    state = job.load()
    version = int(state.get("current_version") or 1)
    spec_path = job.spec_path(version)
    props_path = job.props_path(version)
    if not spec_path.exists() or not props_path.exists():
        raise RuntimeError("Chưa có spec/props — chạy resolve trước khi tạo ảnh đại diện.")

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    props = json.loads(props_path.read_text(encoding="utf-8"))
    words = []
    if job.spine_path.exists():
        words = json.loads(job.spine_path.read_text(encoding="utf-8")).get("word_timestamps") or []

    job.emit("log", "visuals", "LLM soạn kicker / accent / prompt ảnh…")
    design = design_visuals(job, spec, words, model=options.get("model"))

    end = spec.setdefault("endcard", {})
    if isinstance(end, dict):
        end["kicker"] = design["kicker"]
        end["accent"] = design["accent"]
        spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

    src = job.src_path if job.src_path.exists() else None
    freeze_at = 0.0
    report_path = job.dir / f"resolve_report_v{version}.json"
    if report_path.exists():
        try:
            freeze_at = max(0.0, float(json.loads(report_path.read_text()).get("timeline_seconds") or 0) - 0.08)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            freeze_at = max(0.0, float(props.get("durationSeconds") or 0) - 3.7)
    freeze = job.dir / FREEZE_NAME
    hook = job.dir / HOOK_NAME
    if src:
        extract_still(src, freeze, freeze_at)
        extract_still(src, hook, _hook_time(props))

    errors: list[str] = []
    job.emit("log", "visuals", "Tạo ảnh outro bằng GPT image…")
    err = _generate_png(design["endcard_image_prompt"], job.dir / ART_NAME)
    if err:
        errors.append(f"endcard_art: {err[:180]}")
        job.emit("warning", "visuals", f"Không tạo được ảnh outro: {err[:120]}")

    job.emit("log", "visuals", "Tạo ảnh đại diện (bản AI)…")
    err = _generate_png(design["thumbnail_image_prompt"], job.dir / COVER_AI_NAME)
    if err:
        errors.append(f"cover_ai: {err[:180]}")
        job.emit("warning", "visuals", f"Không tạo được cover AI: {err[:120]}")

    thumb_src = hook if hook.exists() else freeze
    if thumb_src.exists():
        composite_thumbnail(
            thumb_src, job.dir / THUMB_NAME,
            title=design["thumbnail_text"] or endcard_title(spec),
            subtitle=str((spec.get("endcard") or {}).get("subtitle") or ""),
            accent=design["accent"],
        )
        job.emit("log", "visuals", f"Đã ghi {THUMB_NAME}")

    _patch_endcard_event(
        props,
        kicker=design["kicker"],
        accent=design["accent"],
        freezeSrc=FREEZE_NAME if freeze.exists() else None,
        artSrc=ART_NAME if (job.dir / ART_NAME).exists() else None,
    )
    props_path.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")

    report = {
        "kicker": design["kicker"],
        "accent": design["accent"],
        "thumbnail_text": design["thumbnail_text"],
        "files": {
            "thumbnail": THUMB_NAME if (job.dir / THUMB_NAME).exists() else None,
            "cover_ai": COVER_AI_NAME if (job.dir / COVER_AI_NAME).exists() else None,
            "endcard_art": ART_NAME if (job.dir / ART_NAME).exists() else None,
            "freeze": FREEZE_NAME if freeze.exists() else None,
        },
        "errors": errors,
        "usage": design.get("usage") or {},
    }
    (job.dir / REPORT_NAME).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
