#!/usr/bin/env python3
"""Seed dense clip library data for UX testing (many days / tags / statuses)."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "tho-260817-031256"
PROJECT = ROOT / "projects" / "autoedit" / PROJECT_ID
SAMPLE = ROOT / "projects/autoedit/ux-trial-clip-drive-26s-260816-044034/sources/s0_sample.mp4"
THUMB = ROOT / "projects/autoedit/ux-trial-clip-drive-26s-260816-044034/sources/s0.thumb.jpg"
EXISTING_THUMB = PROJECT / "sources" / "s0.thumb.jpg"

# Relative to "today" in the environment (2026-08-19 per user_info).
TODAY = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)

SEEDS = [
    # recent
    {"days": 0, "label": "Clip hôm nay — giá CRM", "tags": ["tiktok", "crm", "hot"], "posted": False, "bgm": "bgm_tech_pulse.mp3", "notes": "Mới quay sáng nay", "views": None, "likes": None, "platform": ""},
    {"days": 0, "label": "Clip hôm nay — FAQ khách", "tags": ["reels", "faq"], "posted": False, "bgm": "bgm_clean_explainer.mp3", "notes": "Cần cắt câu chào", "views": None, "likes": None, "platform": ""},
    {"days": 1, "label": "Hôm qua — demo Zalo OA", "tags": ["tiktok", "zalo"], "posted": True, "bgm": "bgm_upbeat_bounce.mp3", "notes": "Đã đăng 9h", "views": 4200, "likes": 310, "platform": "TikTok"},
    {"days": 1, "label": "Hôm qua — sai lầm chatbot", "tags": ["shorts", "ai"], "posted": False, "bgm": "bgm_data_groove.mp3", "notes": "Chờ duyệt khách", "views": None, "likes": None, "platform": ""},
    {"days": 2, "label": "Hook 3 giây — giảm giá", "tags": ["tiktok", "sale"], "posted": True, "bgm": "bgm_energy_drive.mp3", "notes": "", "views": 18000, "likes": 920, "platform": "TikTok"},
    {"days": 3, "label": "Case study phòng khám", "tags": ["reels", "case"], "posted": True, "bgm": "bgm_warm_story.mp3", "notes": "Giọng hơi nhỏ", "views": 2600, "likes": 140, "platform": "Reels"},
    {"days": 4, "label": "Behind the scene setup", "tags": ["bts"], "posted": False, "bgm": "bgm_lofi_chill.mp3", "notes": "B-roll nhiều, cân nhắc bỏ", "views": None, "likes": None, "platform": ""},
    {"days": 5, "label": "So sánh A/B landing", "tags": ["tiktok", "ads"], "posted": True, "bgm": "bgm_playful_pop.mp3", "notes": "", "views": 9100, "likes": 540, "platform": "TikTok"},
    {"days": 7, "label": "Tuần trước — onboarding", "tags": ["shorts", "product"], "posted": True, "bgm": "bgm_future_calm.mp3", "notes": "CTR ổn", "views": 12000, "likes": 700, "platform": "Shorts"},
    {"days": 8, "label": "Phản hồi comment #1", "tags": ["tiktok", "community"], "posted": True, "bgm": "bgm_lofi_night.mp3", "notes": "", "views": 3500, "likes": 210, "platform": "TikTok"},
    {"days": 10, "label": "Myth-busting AI sales", "tags": ["ai", "education"], "posted": False, "bgm": "bgm_urgent_percussive.mp3", "notes": "Thiếu BGM punch", "views": None, "likes": None, "platform": ""},
    {"days": 12, "label": "Testimonial chị Lan", "tags": ["reels", "testimonial"], "posted": True, "bgm": "bgm_hopeful_lift.mp3", "notes": "Khách thích", "views": 7800, "likes": 610, "platform": "Reels"},
    {"days": 14, "label": "2 tuần trước — checklist", "tags": ["tiktok", "checklist"], "posted": True, "bgm": "bgm_clean_explainer.mp3", "notes": "", "views": 15000, "likes": 880, "platform": "TikTok"},
    {"days": 18, "label": "Live clip cắt ngắn", "tags": ["live", "tiktok"], "posted": False, "bgm": "", "notes": "Chưa chọn nhạc", "views": None, "likes": None, "platform": ""},
    {"days": 21, "label": "Tháng 7 — brand story", "tags": ["brand", "reels"], "posted": True, "bgm": "bgm_warm_story.mp3", "notes": "Evergreen", "views": 22000, "likes": 1400, "platform": "Reels"},
    {"days": 25, "label": "Q&A giá phần mềm", "tags": ["faq", "shorts"], "posted": True, "bgm": "bgm_tech_pulse.mp3", "notes": "", "views": 6400, "likes": 390, "platform": "Shorts"},
    {"days": 30, "label": "1 tháng trước — launch", "tags": ["launch", "tiktok", "hot"], "posted": True, "bgm": "bgm_energy_drive.mp3", "notes": "Peak views", "views": 54000, "likes": 3200, "platform": "TikTok"},
    {"days": 35, "label": "Workshop recap", "tags": ["education", "reels"], "posted": True, "bgm": "bgm_data_groove.mp3", "notes": "", "views": 4100, "likes": 250, "platform": "Reels"},
    {"days": 45, "label": "Tháng 6 — soft sell", "tags": ["tiktok", "sale"], "posted": True, "bgm": "bgm_upbeat_bounce.mp3", "notes": "Archive", "views": 8900, "likes": 480, "platform": "TikTok"},
    {"days": 55, "label": "Old take — bỏ?", "tags": ["archive", "draft"], "posted": False, "bgm": "", "notes": "Trùng nội dung với launch", "views": None, "likes": None, "platform": ""},
]


def link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def main() -> None:
    if not SAMPLE.exists():
        raise SystemExit(f"missing sample: {SAMPLE}")
    state = json.loads((PROJECT / "project.json").read_text(encoding="utf-8"))
    sources = list(state.get("sources") or [])
    # Keep existing s0; replace any previous seed ids s1+
    sources = [s for s in sources if str(s.get("id")) == "s0"]
    # Clean old seeded files except s0
    for path in (PROJECT / "sources").glob("s[1-9]*"):
        path.unlink(missing_ok=True)
    for path in (PROJECT / "sources").glob("s1*.*"):
        path.unlink(missing_ok=True)
    # also s10+
    for path in (PROJECT / "sources").iterdir():
        name = path.name
        if name.startswith("s0"):
            continue
        if name.startswith("s") and name[1:2].isdigit():
            path.unlink(missing_ok=True)

    thumb_src = EXISTING_THUMB if EXISTING_THUMB.exists() else THUMB
    order = 1
    for index, row in enumerate(SEEDS, start=1):
        sid = f"s{index}"
        day = TODAY - timedelta(days=row["days"])
        added = day.timestamp()
        video_name = f"{sid}_{row['label'][:40].replace(' ', '_')}.mp4"
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in video_name)
        rel = f"sources/{safe}"
        dest = PROJECT / rel
        link_or_copy(SAMPLE, dest)
        thumb_rel = f"sources/{sid}.thumb.jpg"
        if thumb_src.exists():
            link_or_copy(thumb_src, PROJECT / thumb_rel)
        else:
            thumb_rel = None
        meta: dict = {"posted": bool(row["posted"])}
        if row["posted"]:
            meta["posted_at"] = day.date().isoformat()
            meta["platform"] = row["platform"] or "TikTok"
            if row["views"] is not None:
                meta["views"] = row["views"]
            if row["likes"] is not None:
                meta["likes"] = row["likes"]
            meta["url"] = f"https://example.com/p/{sid}"
        sources.append({
            "id": sid,
            "file": rel,
            "sha256": f"seed-{sid}",
            "size_bytes": SAMPLE.stat().st_size,
            "duration": 25.7,
            "width": 720,
            "height": 1280,
            "fps": 30.0,
            "has_audio": True,
            "mean_volume_db": -28.0,
            "role": "aroll",
            "speech": True,
            "order": order,
            "take_group": "main",
            "label": row["label"],
            "thumb": thumb_rel,
            "transcript": f"sources/{sid}.transcript.json",
            "warnings": [],
            "added_at": added,
            "tags": row["tags"],
            "notes": row["notes"],
            "meta": meta,
            "bgm_name": row["bgm"],
            "bgm_volume": 0.15 if row["bgm"] else None,
        })
        order += 1

    # Touch s0 date to ~3 days ago so list has mix
    for spec in sources:
        if spec["id"] == "s0":
            spec["added_at"] = (TODAY - timedelta(days=3)).timestamp()
            spec.setdefault("tags", ["tiktok", "ux"])
            break

    state["sources"] = sources
    state["updated_at"] = time.time()
    tmp = PROJECT / "project.json.tmp"
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PROJECT / "project.json")
    print(f"seeded {len(sources)} sources into {PROJECT_ID}")
    by_day: dict[str, int] = {}
    for spec in sources:
        key = datetime.fromtimestamp(spec["added_at"], tz=timezone.utc).strftime("%Y-%m-%d")
        by_day[key] = by_day.get(key, 0) + 1
    for key in sorted(by_day, reverse=True):
        print(f"  {key}: {by_day[key]}")


if __name__ == "__main__":
    main()
