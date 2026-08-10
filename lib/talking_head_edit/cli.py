"""CLI for the talking-head auto-edit pipeline.

Runs without the server or the UI, so the pipeline stays usable (and testable)
on its own:

    python -m lib.talking_head_edit.cli --input footage.mp4 --prompt "..."
    python -m lib.talking_head_edit.cli --job <job_id> --stage-from resolve
    python -m lib.talking_head_edit.cli --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lib.talking_head_edit.job_store import (
    DEFAULT_OPTIONS, STAGES, JobStore, find_job, list_all_jobs,
)
from lib.talking_head_edit.resources import inventory
from lib.talking_head_edit.runner import run_job, stages_from


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="talking-head-autoedit",
        description="Tự động dựng video talking-head theo phong cách MONA (word-anchored).",
    )
    parser.add_argument("--input", action="append", default=[], metavar="FILE",
                        help="File footage gốc (mp4). Lặp nhiều lần cho nhiều nguồn — "
                             "thứ tự truyền vào là thứ tự ghép")
    parser.add_argument("--input-dir", default=None, metavar="THUMUC",
                        help="Nạp mọi file video trong thư mục, sắp theo tên")
    parser.add_argument("--project", default=None, metavar="ID",
                        help="Tạo bản dựng trong project này (kế thừa assembly, "
                             "keyterms, defaults của project). Không có --project "
                             "thì job nằm ở projects/autoedit-jobs/ như trước")
    parser.add_argument("--projects", action="store_true",
                        help="Liệt kê các project đã có")
    parser.add_argument("--assembly-mode", default=None,
                        choices=["auto", "sequential", "best_take"],
                        help="auto (mặc định) tự phát hiện take trùng; sequential ghép "
                             "tuần tự; best_take luôn chọn bản tốt nhất")
    parser.add_argument("--job", help="Chạy tiếp một job đã có (job_id)")
    parser.add_argument("--title", default="", help="Tên job (mặc định lấy tên file)")
    parser.add_argument("--prompt", default="", help="Yêu cầu riêng cho video này")
    parser.add_argument("--topic", default="", help="Chủ đề video (giúp director bám nội dung)")
    parser.add_argument("--brand-pill", default="", help="Chữ trên pill thương hiệu")
    parser.add_argument("--card-plan", default="", help="Yêu cầu về số/cấu trúc card")
    parser.add_argument("--model", default=None, help="Model director qua gateway")
    parser.add_argument("--asr", default=None,
                        choices=["elevenlabs_scribe", "whisper_local"],
                        help="Engine nhận dạng lời nói (mặc định Scribe — nhanh hơn nhiều; "
                             "whisper_local chạy offline, không gửi audio ra ngoài)")
    parser.add_argument("--keyterm", action="append", default=[], metavar="TU",
                        help="Từ khoá gợi ý cho ASR (lặp nhiều lần). Rỗng = tự tách "
                             "từ --topic/--card-plan")
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
                        help="Model cho nhánh whisper_local (kể cả khi là fallback)")
    parser.add_argument("--language", default="vi", help="Mã ngôn ngữ ISO 639-1, rỗng = tự nhận")
    parser.add_argument("--tempo", type=float, default=1.06)
    parser.add_argument("--scale", type=float, default=1.0, help="1.0 = full 1080x1920")
    parser.add_argument("--frame", default=None,
                        choices=["none", "dark", "light", "blur"],
                        help="Kiểu khung bao quanh footage (mặc định dark)")
    parser.add_argument("--audio", default=None, choices=["off", "voice", "voice_strong", "shotgun", "shotgun_dry"],
                        help="Xử lý giọng (mặc định shotgun — mô phỏng mic hướng tính)")
    parser.add_argument("--no-calibrate", action="store_true",
                        help="Bỏ qua bước dò thông số, dùng thẳng mặc định")
    parser.add_argument("--no-bgm", action="store_true", help="Không dùng nhạc nền")
    parser.add_argument("--no-cold-open", action="store_true", help="Không ghép teaser mở màn")
    parser.add_argument("--revise", default="", help="Yêu cầu sửa bản dựng hiện có (vá spec)")
    parser.add_argument("--autopilot", action="store_true",
                        help="Chạy hết chain, tự sửa MỘT lần theo remedy đã biết nếu "
                             "verify fail, rồi báo cáo. Lỗi không có remedy thì dừng")
    parser.add_argument("--autopilot-budget", type=float, default=45.0,
                        metavar="PHUT", help="Trần thời gian cho autopilot (mặc định 45 phút)")
    parser.add_argument("--chat", default="",
                        help="Một lượt sửa có ngữ cảnh 3 lượt trước (khác --revise: "
                             "--revise không nhớ gì)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Với --chat: hiện patch + diff, KHÔNG tạo version mới")
    parser.add_argument("--chat-history", action="store_true",
                        help="In lịch sử hội thoại sửa của job")
    parser.add_argument("--stage-from", default=None, help="Chạy lại từ stage này trở đi")
    parser.add_argument("--only", default=None, help="Chỉ chạy đúng stage này")
    parser.add_argument("--stages", default=None,
                        help="Danh sách stage cụ thể, ngăn cách bởi dấu phẩy (vd: revise,audit,resolve)")
    parser.add_argument("--skip-render", action="store_true", help="Dừng trước khi render")
    parser.add_argument("--no-cache", action="store_true", help="Bỏ qua cache, chạy lại tất cả")
    parser.add_argument("--list", action="store_true", help="Liệt kê các job đã có")
    parser.add_argument("--resources", action="store_true", help="In kho SFX/BGM dùng được")
    parser.add_argument("--preview-grade", action="store_true",
                        help="Xem thử màu: 1 frame từ footage gốc qua chuỗi grade (~1 giây)")
    parser.add_argument("--preview-clip", nargs="?", const=5.0, type=float, default=None,
                        metavar="GIAY",
                        help="Render clip ngắn để duyệt (mặc định 5 giây, scale 0.5)")
    parser.add_argument("--clip-scale", type=float, default=0.5,
                        help="Kích thước clip xem thử: 0.5 nhanh (xem nhịp/bố cục), "
                             "1.0 đúng độ nét thật (chậm hơn)")
    parser.add_argument("--clip-from", type=float, default=0.0,
                        help="Clip xem thử bắt đầu từ giây thứ mấy của bản dựng")
    parser.add_argument("--preview-audio", action="store_true",
                        help="Xuất mẫu audio cho từng preset xử lý giọng để nghe thử (~3 giây)")
    parser.add_argument("--judge-audio", action="store_true",
                        help="Nhờ model NGHE và chấm từng preset xử lý giọng")
    parser.add_argument("--check-hearing", action="store_true",
                        help="Kiểm tra MÙ: tiêm lỗi đã biết vào audio rồi xem model có "
                             "nghe ra không. Chạy trước khi tin bất kỳ đánh giá âm thanh nào")
    parser.add_argument("--check-strength", type=float, default=1.0,
                        help="Độ mạnh của lỗi tiêm vào khi --check-hearing (1.0 = rất rõ)")
    parser.add_argument("--timeline-view", nargs=2, type=float, default=None,
                        metavar=("TU_GIAY", "DEN_GIAY"),
                        help="Ảnh soi một khoảng của bản dựng: dải frame + dạng sóng "
                             "+ nhãn từ. Dùng để xem mối cắt có rơi vào chỗ im lặng")
    parser.add_argument("--preview-still", type=int, default=None, metavar="FRAME",
                        help="Xem thử bố cục: 1 frame qua Remotion, đủ khung/caption/card (~25 giây)")
    parser.add_argument("--at", type=float, default=None,
                        help="Giây trong FOOTAGE GỐC để lấy frame xem thử (mặc định: giữa video)")
    parser.add_argument("--grade-json", default=None,
                        help="Ghi đè grade khi xem thử, JSON, vd: {\"warmth\":4}")

    # --- Cloud render (Vast.ai) --------------------------------------------
    # See _validate_cloud_args() for the full refusal matrix. Cloud is never
    # silent: --render-location cloud always needs --cloud-offer or
    # --cloud-yes, and this CLI never prompts (it runs from a subprocess with
    # no tty — server/queue_worker.py:226).
    parser.add_argument("--render-location", default="local", choices=["local", "cloud"],
                        help="local (mặc định) hoặc cloud (thuê máy Vast.ai). Cloud CẦN "
                             "--cloud-offer <id> hoặc --cloud-yes — không bao giờ hỏi lại")
    parser.add_argument("--cloud-offer", type=int, default=None, metavar="ID",
                        help="Thuê đúng offer này (lấy id từ --cloud-offers)")
    parser.add_argument("--cloud-yes", action="store_true",
                        help="Chấp nhận offer đề xuất, không cần chọn tay — BẮT BUỘC khi "
                             "dùng cùng --autopilot --render-location cloud, hoặc --cloud-flush")
    parser.add_argument("--cloud-offers", action="store_true",
                        help="In danh sách offer + chi phí ước tính (KHÔNG thuê gì) rồi thoát")
    parser.add_argument("--cloud-max-usd", type=float, default=None, metavar="F",
                        help="Trần chi cho rental này — chỉ có thể HẠ trần "
                             "config.max_total_usd_per_rental, không thể nâng")
    parser.add_argument("--cloud-queue", action="store_true",
                        help="Xếp job này vào batch queue cloud thay vì render ngay")
    parser.add_argument("--cloud-queue-status", action="store_true",
                        help="In trạng thái batch queue cloud + flush_check rồi thoát")
    parser.add_argument("--cloud-flush", action="store_true",
                        help="Buộc render ngay toàn bộ batch queue cloud trên 1 rental "
                             "chung — cần --cloud-yes")

    # --- R2 object storage --------------------------------------------------
    parser.add_argument("--r2-sync", action="store_true",
                        help="Buộc sync job này lên R2 ngay (bỏ qua auto_sync, "
                             "vẫn cần enabled:true trong config/r2-storage.json)")
    parser.add_argument("--r2-status", action="store_true",
                        help="In cấu hình R2 + biến môi trường nào đã đặt (KHÔNG gọi "
                             "network) rồi thoát")
    return parser


# ---------------------------------------------------------------------------
# Cloud render helpers -- kept here (not in lib.cloud_render) because every
# function below is CLI presentation/dispatch, not domain logic: the actual
# rental/queue mechanics live in tools/video/vast_cloud_render.py and
# tools/video/cloud_render_queue.py, which these functions call through.
# ---------------------------------------------------------------------------

def _validate_cloud_args(args: argparse.Namespace) -> str | None:
    """Every cloud-render CLI refusal in one place, so the consent rule is
    identical from every entry point (including the no-tty subprocess call
    from `server/queue_worker.py:226`). Returns an error message when the
    flags are unsafe/incomplete, else `None`. Pure -- zero SDK/network calls,
    same convention as `VastCloudRender.execute()`'s own refusals.
    """
    if args.cloud_flush and not args.cloud_yes:
        return ("--cloud-flush thuê máy thật cho cả batch -- cần --cloud-yes để xác nhận "
                "(CLI không có tty để hỏi lại)")

    # --cloud-offers/--cloud-queue/--cloud-queue-status never rent anything,
    # so the --render-location cloud consent rule below does not apply here.
    if args.cloud_offers or args.cloud_queue or args.cloud_queue_status:
        return None

    if args.render_location == "cloud":
        if args.autopilot and not args.cloud_yes:
            return ("--autopilot --render-location cloud cần --cloud-yes -- autopilot "
                    "không được thuê máy khi không có người xác nhận trên dòng lệnh")
        if not (args.cloud_offer or args.cloud_yes):
            return ("--render-location cloud cần --cloud-offer <id> hoặc --cloud-yes -- "
                    "chạy lại với --cloud-offer hoặc --cloud-yes")
    return None


def _clamp_cloud_max_usd(requested: float | None, ceiling: float) -> tuple[float, str | None]:
    """`--cloud-max-usd` may only LOWER the configured per-rental ceiling,
    never raise it -- a CLI flag must not be a backdoor around the config
    file's spend cap. Returns `(effective_ceiling, warning_or_None)`."""
    if requested is None:
        return ceiling, None
    if requested <= 0:
        return ceiling, (f"--cloud-max-usd {requested} không hợp lệ (phải > 0) -- "
                         f"dùng trần cấu hình ${ceiling}")
    if requested > ceiling:
        return ceiling, (f"--cloud-max-usd ${requested} vượt trần cấu hình ${ceiling} -- "
                         f"chỉ có thể HẠ trần, không thể nâng. Dùng ${ceiling}.")
    return requested, None


def _split_at_render(plan: list[str]) -> tuple[list[str], list[str]]:
    """(stages strictly before 'render', stages strictly after) — 'render'
    itself is excluded from both halves: the cloud path substitutes its own
    render call for the local `render.run` stage runner in between the two."""
    if "render" not in plan:
        return list(plan), []
    idx = plan.index("render")
    return list(plan[:idx]), list(plan[idx + 1:])


def _cloud_render_plan(args: argparse.Namespace,
                       stages: list[str] | None) -> tuple[list[str], list[str]] | None:
    """Whether — and how — this invocation should substitute a cloud render
    for the local 'render' stage. Returns `None` when it should not (local
    render, `--skip-render`, `--autopilot` (which routes through
    `_cloud_aware_runner` instead), or a custom `--stages`/`--only`/
    `--stage-from` plan that never actually includes 'render' — e.g.
    `--stages audit,resolve --render-location cloud` requests no render at
    all this invocation, so there is nothing to route through cloud).
    Otherwise returns `(pre_stages, post_stages)` from `_split_at_render`.
    """
    if args.render_location != "cloud" or args.skip_render or args.autopilot:
        return None
    plan = stages if stages is not None else [s for s in STAGES if s != "calibrate"]
    if "render" not in plan:
        return None
    return _split_at_render(plan)


def _print_cloud_offers(job_id: str | None) -> int:
    """`--cloud-offers`: the CLI equivalent of `dry_run()` -- prints the same
    shortlist a cloud render would announce and creates zero instances (the
    tool's `dry_run()` only calls `vast_client.search`, a read-only account
    call; no `create_instance` path is reachable from here)."""
    from lib.cloud_render import announce
    from tools.video.vast_cloud_render import VastCloudRender

    inputs: dict = {"mode": "render_now"}
    if job_id:
        inputs["job_id"] = job_id
    payload = VastCloudRender().dry_run(inputs)
    print(announce.format_announce(payload))
    return 0 if payload.get("would_execute") else 1


def _print_cloud_queue_status() -> int:
    """`--cloud-queue-status`: read-only, zero network -- the durable batch
    queue plus the pure `flush_check` threshold computation."""
    from tools.video.cloud_render_queue import CloudRenderQueue

    tool = CloudRenderQueue()
    listed = tool.execute({"operation": "list"})
    entries = listed.data.get("entries", []) if listed.success else []
    if not entries:
        print("Batch queue cloud: rỗng")
    else:
        print(f"Batch queue cloud: {len(entries)} job")
        for entry in entries:
            print(f"  {entry['job_id']:<44} v{entry['version_at_enqueue']}  "
                  f"{entry['status']:<10} ~{entry['estimated_render_seconds']:.0f}s  "
                  f"xếp lúc {entry['enqueued_at']}  {entry.get('note', '')}")

    checked = tool.execute({"operation": "flush_check"})
    if not checked.success:
        print(f"\nflush_check lỗi: {checked.error}", file=sys.stderr)
        return 0
    report = checked.data
    met = ", ".join(report["thresholds_met"]) or "chưa đạt ngưỡng nào"
    print(f"\nflush_check: {report['job_count']} job, "
          f"~{report['estimated_render_minutes']} phút render, "
          f"chờ lâu nhất {report['oldest_age_minutes']} phút -- ngưỡng đạt: {met}")
    print(f"  Ước tính chi phí: ~${report['estimated_cost_usd']} "
          f"(so với ~${report['estimated_cost_if_rendered_separately_usd']} nếu thuê riêng lẻ)")
    return 0


def _enqueue_cloud_job(job) -> int:
    """`--cloud-queue`: enqueue `job`'s current version into the durable
    batch queue instead of rendering now (locally or cloud). Free,
    local-only, zero network -- routed through `cloud_render_queue`'s
    registry surface so the tool's own input validation applies uniformly."""
    from lib.cloud_render import config as cloud_config
    from lib.cloud_render import cost_estimate
    from tools.video.cloud_render_queue import CloudRenderQueue

    state = job.load()
    version = int(state.get("current_version") or 0)
    duration_seconds = cost_estimate.job_duration_seconds(job.job_id)
    try:
        resolved = cloud_config.resolve()
        render_seconds_per_video_second = float(
            resolved.get("render_seconds_per_video_second", 1.9))
    except cloud_config.CloudRenderConfigError:
        render_seconds_per_video_second = 1.9
    estimated_render_seconds = duration_seconds * render_seconds_per_video_second

    result = CloudRenderQueue().execute({
        "operation": "enqueue", "job_id": job.job_id, "version": version,
        "estimated_render_seconds": estimated_render_seconds,
        "duration_seconds": duration_seconds,
        "project_id": state.get("project_id"),
    })
    if not result.success:
        print(f"Xếp queue lỗi: {result.error}", file=sys.stderr)
        return 1
    entry = result.data["entry"]
    print(f"Đã xếp job {job.job_id} v{version} vào batch queue cloud "
          f"(~{entry['estimated_render_seconds']:.0f}s render).")
    print("Xem trạng thái: --cloud-queue-status  |  buộc render ngay cả batch: --cloud-flush")
    return 0


def _cloud_render_now(job, args: argparse.Namespace) -> dict:
    """One cloud render for `job`'s current version, via the
    `VastCloudRender` tool: fresh `dry_run()` (offer search) -> print the
    announce block -> `execute()` (dry_run_ref-gated, ceiling-clamped).
    Raises `RuntimeError` on any refusal/failure so it surfaces through the
    same CLI error path as a local render failure (`run_stage` also just
    raises) -- see `main()`'s `except Exception` around the render call."""
    from lib.cloud_render import announce
    from lib.cloud_render import config as cloud_config
    from tools.video.vast_cloud_render import VastCloudRender

    tool = VastCloudRender()
    payload = tool.dry_run({"mode": "render_now", "job_id": job.job_id})
    print(announce.format_announce(payload))
    if not payload.get("would_execute"):
        raise RuntimeError(f"Cloud render bị chặn: {payload.get('error')}")

    offers_seen = {o["offer_id"] for o in payload.get("offers", [])}
    offer_id = args.cloud_offer or payload["recommended_offer_id"]
    if args.cloud_offer and args.cloud_offer not in offers_seen:
        raise RuntimeError(
            f"--cloud-offer {args.cloud_offer} không nằm trong shortlist vừa dry_run "
            f"({sorted(offers_seen)}) -- chạy lại --cloud-offers để thấy offer hiện tại")

    try:
        resolved = cloud_config.resolve()
    except cloud_config.CloudRenderConfigError as exc:
        raise RuntimeError(f"config/cloud-render.json không hợp lệ: {exc}") from exc
    max_total_usd, warning = _clamp_cloud_max_usd(
        args.cloud_max_usd, resolved["max_total_usd_per_rental"])
    if warning:
        print(warning, file=sys.stderr)

    result = tool.execute({
        "mode": "render_now", "job_id": job.job_id, "offer_id": offer_id,
        "max_total_usd": max_total_usd, "dry_run_ref": payload["dry_run_ref"],
    })
    if not result.success:
        raise RuntimeError(f"Cloud render lỗi: {result.error}")
    location = result.data.get("render_location", {})
    print(f"Cloud render xong: ${result.cost_usd} "
          f"({location.get('pricing_mode')}, offer #{location.get('offer_id')})")
    return result.data


def _cloud_aware_runner(args: argparse.Namespace):
    """`run_job`-compatible callable that reroutes the 'render' stage through
    cloud. This is `autopilot.run(..., runner=...)`'s injection point -- the
    one way `--autopilot --render-location cloud` reuses autopilot's
    existing retry loop without touching `lib/talking_head_edit/autopilot.py`
    (out of this phase's file ownership). Accepted risk (plan risk register):
    a retried render rents again, bounded by `MAX_RETRY = 1` in autopilot.py,
    the per-rental ceiling, and the reaper -- not a re-prompt per retry,
    exactly like the plan's own mitigation for this risk states."""
    def runner(job, options=None, stages=None, use_cache: bool = True) -> dict:
        plan = stages if stages is not None else STAGES
        if "render" not in plan:
            return run_job(job, options, stages=plan, use_cache=use_cache)
        pre, post = _split_at_render(plan)
        results: dict = run_job(job, options, stages=pre, use_cache=use_cache) if pre else {}
        results["render"] = {"cloud": True, **_cloud_render_now(job, args)}
        if post:
            results.update(run_job(job, options, stages=post, use_cache=use_cache))
        return results
    return runner


def _run_cloud_flush(args: argparse.Namespace) -> int:
    """`--cloud-flush`: force-render the whole batch queue on one rental,
    via the `VastCloudRender` tool (mode='flush') -- same dry_run_ref gate
    and ceiling clamp as the render-now path. Actually rents; only reached
    once `_validate_cloud_args` has confirmed `--cloud-yes` is present."""
    from lib.cloud_render import announce
    from lib.cloud_render import config as cloud_config
    from tools.video.cloud_render_queue import CloudRenderQueue
    from tools.video.vast_cloud_render import VastCloudRender

    listed = CloudRenderQueue().execute({"operation": "list"})
    entries = listed.data.get("entries", []) if listed.success else []
    if not entries:
        print("Batch queue cloud rỗng -- không có gì để flush.", file=sys.stderr)
        return 2
    job_ids = [entry["job_id"] for entry in entries]

    tool = VastCloudRender()
    payload = tool.dry_run({"mode": "flush", "job_ids": job_ids})
    print(announce.format_announce(payload))
    if not payload.get("would_execute"):
        return 1

    offers_seen = {o["offer_id"] for o in payload.get("offers", [])}
    offer_id = args.cloud_offer or payload["recommended_offer_id"]
    if args.cloud_offer and args.cloud_offer not in offers_seen:
        print(f"--cloud-offer {args.cloud_offer} không nằm trong shortlist vừa dry_run "
              "-- chạy lại --cloud-offers", file=sys.stderr)
        return 2

    try:
        resolved = cloud_config.resolve()
    except cloud_config.CloudRenderConfigError as exc:
        print(f"config/cloud-render.json không hợp lệ: {exc}", file=sys.stderr)
        return 2
    max_total_usd, warning = _clamp_cloud_max_usd(
        args.cloud_max_usd, resolved["max_total_usd_per_rental"])
    if warning:
        print(warning, file=sys.stderr)

    result = tool.execute({
        "mode": "flush", "job_ids": job_ids, "offer_id": offer_id,
        "max_total_usd": max_total_usd, "dry_run_ref": payload["dry_run_ref"],
    })
    if not result.success:
        print(f"Flush thất bại: {result.error}", file=sys.stderr)
        return 1
    data = result.data
    print(f"Xong: {len(data['rendered'])} render, {len(data['failed'])} lỗi, "
          f"{len(data['pending'])} còn treo, {len(data['blocked'])} bị khoá")
    print(f"  Chi phí thực: ${result.cost_usd}")
    for job_id, error in data["failed"].items():
        print(f"  LỖI [{job_id}] {error}", file=sys.stderr)
    return 0 if data["rendered"] else 1


def _print_r2_status() -> int:
    """`--r2-status`: config + which env vars are set, no network."""
    from lib.r2_storage.config import is_configured, resolve

    settings = resolve()
    configured, missing = is_configured()
    print(f"enabled={settings.enabled} auto_sync={settings.auto_sync} "
          f"bucket={settings.bucket!r} prefix={settings.prefix!r}")
    print(f"configured={configured} missing_env={missing}")
    return 0


def _run_r2_sync(job) -> int:
    """`--r2-sync`: force a sync of this job now. Ignores `auto_sync` but
    still honours `enabled` -- same gate the automatic job-end hook uses."""
    from lib.r2_storage.config import resolve
    from lib.r2_storage.sync import announce, apply_sync, plan_sync

    settings = resolve()
    if not settings.enabled:
        print("R2 storage tắt (enabled:false trong config/r2-storage.json) -- không sync.",
              file=sys.stderr)
        return 2
    prefix = f"{settings.prefix}/autoedit-jobs/{job.job_id}"
    plan = plan_sync(job.dir, prefix, settings)
    print(announce(plan, settings, first_sync=not (job.dir / ".r2sync.json").exists()))
    result = apply_sync(plan, settings)
    print(f"Xong: {result.uploaded} uploaded, {result.skipped} skipped, {result.bytes} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    cloud_error = _validate_cloud_args(args)
    if cloud_error:
        print(cloud_error, file=sys.stderr)
        return 2

    if args.cloud_offers:
        return _print_cloud_offers(args.job)
    if args.cloud_queue_status:
        return _print_cloud_queue_status()
    if args.cloud_flush:
        return _run_cloud_flush(args)
    if args.r2_status:
        return _print_r2_status()

    store = JobStore()

    if args.resources:
        data = inventory()
        print(json.dumps({
            "sfx": [e["name"] for e in data["sfx"]],
            "bgm": [e["name"] for e in data["bgm"]],
            "warnings": data["warnings"],
        }, indent=2, ensure_ascii=False))
        return 0

    if args.projects:
        from lib.talking_head_edit.project_store import ProjectStore

        for row in ProjectStore().list():
            print(f"{row['project_id']:<44} {row['source_count']} nguồn  "
                  f"{row['total_seconds']:.0f}s  {row['build_count']} bản dựng  "
                  f"{row['title']}")
        return 0

    if args.list:
        for state in list_all_jobs():
            where = state.get("project_id") or ("legacy" if state.get("is_legacy") else "-")
            print(f"{state['job_id']:<44} {state.get('status', '?'):<24} "
                  f"v{state.get('current_version', 0)}  ${state.get('cost_usd', 0)}  {where}")
        return 0

    options = {
        **DEFAULT_OPTIONS,
        "prompt": args.prompt,
        "topic": args.topic,
        "brand_pill": args.brand_pill,
        "card_plan": args.card_plan,
        "whisper_model": args.whisper_model,
        "keyterms": args.keyterm,
        "language": args.language or None,
        "tempo": args.tempo,
        "render_scale": args.scale,
        "bgm": not args.no_bgm,
        "cold_open": not args.no_cold_open,
    }
    if args.model:
        options["model"] = args.model
    if args.asr:
        options["asr_provider"] = args.asr
    if args.assembly_mode:
        options["assembly"] = {**(options.get("assembly") or {}),
                               "mode": args.assembly_mode}
    if args.frame:
        options["frame_preset"] = args.frame
    if args.audio:
        options["audio_preset"] = args.audio
    if args.no_calibrate:
        options["calibrate_grade"] = False
        options["calibrate_audio"] = False

    if args.job:
        # Resolved across both layouts, so a build inside a project is reachable
        # by the same id the UI shows.
        try:
            job = find_job(args.job)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        # keep stored options, override only what was explicitly passed
        stored = job.load().get("options", {})
        passed = {k: v for k, v in options.items()
                  if k in ("prompt", "topic", "brand_pill", "card_plan", "model",
                           "frame_preset", "audio_preset")
                  and v not in ("", None)}
        options = {**stored, **passed}
    elif args.project and not (args.input or args.input_dir):
        # Build from what the project already holds — nothing to upload or probe.
        from lib.talking_head_edit.project_store import ProjectError, ProjectStore

        try:
            project = ProjectStore().get(args.project)
            job = project.create_job(options, title=args.title)
        except (FileNotFoundError, ProjectError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        options = job.load()["options"]
        print(f"Job: {job.job_id} (project {args.project})")
    elif args.input or args.input_dir:
        inputs = [Path(p) for p in args.input]
        if args.input_dir:
            from lib.talking_head_edit.sources import SourceError, expand_dir
            try:
                inputs += expand_dir(args.input_dir)
            except SourceError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        missing = [p for p in inputs if not p.exists()]
        if missing:
            print("Không tìm thấy file: "
                  + ", ".join(str(p) for p in missing), file=sys.stderr)
            return 2
        job = store.create(inputs, options, title=args.title)
        print(f"Job: {job.job_id}"
              + (f" ({len(inputs)} nguồn)" if len(inputs) > 1 else ""))
    else:
        print("Cần --input <file> (lặp được), --input-dir <thư mục>, hoặc --job <job_id>",
              file=sys.stderr)
        return 2

    if args.r2_sync:
        return _run_r2_sync(job)

    if args.chat_history:
        from lib.talking_head_edit.chat_revise import read_history

        turns = read_history(job)
        if not turns:
            print("Chưa có lượt sửa nào.")
        for index, turn in enumerate(turns, start=1):
            mark = "✎" if turn.get("applied") else "○"
            print(f"{index:>3} {mark} «{turn['message']}»")
            print(f"      → {turn.get('result', '?')}"
                  + (f"  v{turn['to_version']}" if turn.get("to_version") else "")
                  + (f"  {turn.get('tokens', 0)} token" if turn.get("tokens") else ""))
        return 0

    if args.chat:
        from lib.talking_head_edit.chat_revise import ChatReviseError, chat

        try:
            result = chat(job, args.chat, options=options, dry_run=args.dry_run)
        except ChatReviseError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 — CLI boundary
            print(f"\nTHẤT BẠI: {exc}", file=sys.stderr)
            return 1

        print(("Thử (dry-run)" if args.dry_run else "Đã áp") + f": {result['report']}")
        if result.get("diff"):
            print(json.dumps(result["diff"].get("diff"), ensure_ascii=False, indent=2)[:2000])
        if not args.dry_run:
            print(f"Phiên bản mới: v{result['version']}")
            print("Cắt lại để xem: --stages resolve --no-cache")
        return 0

    if args.timeline_view:
        from lib.talking_head_edit.timeline_view import (
            TimelineViewError, output_time_mapper, timeline_view,
        )

        state = job.load()
        version = int(state.get("current_version", 0))
        # Prefer the finished render; fall back to the cut intermediate, which is
        # what exists before a render has been paid for.
        video = job.final_path if job.final_path.exists() else job.src_path
        if not video.exists():
            print("Chưa có final.mp4 hay src.mp4 — chạy resolve trước.", file=sys.stderr)
            return 2
        spine = (json.loads(job.spine_path.read_text(encoding="utf-8"))
                 if job.spine_path.exists() else {})
        report_path = job.dir / f"resolve_report_v{version}.json"
        report = (json.loads(report_path.read_text(encoding="utf-8"))
                  if report_path.exists() else {})
        from lib.talking_head_edit import spine_build

        words = spine_build.filter_words(
            spine.get("word_timestamps") or [],
            (state.get("selection") or {}).get("kept_word_ranges"))
        # Labels are placed on the OUTPUT timeline; a word's own timestamp is a
        # second in its source file, which on a cut edit is somewhere else.
        time_of = output_time_mapper(job, version)
        start, end = args.timeline_view
        try:
            path = timeline_view(
                video, start, end, words=words, time_of=time_of,
                marks=[float(s) for s in (report.get("seams") or [])],
                out=job.dir / "preview" / "timeline"
                / f"view_{start:07.2f}_{end:07.2f}.png")
        except (TimelineViewError, OSError, ImportError) as exc:
            print(f"Không dựng được ảnh: {exc}", file=sys.stderr)
            return 1
        print(f"Ảnh soi timeline: {path}")
        if report.get("seams"):
            inside = [s for s in report["seams"] if start <= float(s) <= end]
            print(f"  mối nối trong khoảng này: {inside or 'không có'}")
        return 0

    if (args.preview_grade or args.preview_still is not None
            or args.preview_clip is not None or args.preview_audio
            or args.judge_audio or args.check_hearing):
        from lib.talking_head_edit.preview import (
            composition_still, judge_audio, preview_audio, preview_clip, preview_grades,
        )

        state = job.load()
        version = int(state.get("current_version", 0))
        spec_grade: dict = {}
        if version and job.spec_path(version).exists():
            spec_grade = json.loads(job.spec_path(version).read_text(encoding="utf-8")).get("grade", {})

        if args.preview_grade:
            variants = {"hien_tai": spec_grade}
            if args.grade_json:
                variants["thu_nghiem"] = {**spec_grade, **json.loads(args.grade_json)}
            report = preview_grades(job, variants, at_seconds=args.at, options=options)
            for item in report["variants"]:
                stats = item["stats"]
                print(f"  {item['name']:<12} sáng {stats.get('luma', 0):6.1f} | "
                      f"ám ấm {stats.get('warm_bias', 0):+6.2f} | {item['image']}")
            print(f"\nẢnh so sánh: {job.dir / report['contact_sheet']}")

        if args.preview_audio:
            report = preview_audio(job, at_seconds=args.at if args.at is not None else 20.0)
            print("Mẫu audio để nghe thử:")
            for sample in report["samples"]:
                print(f"  {sample['preset']:<14} {sample['path']}")

        if args.check_hearing:
            from lib.talking_head_edit.audio_hearing_check import (
                PROMPT_VI, build_probe_set, score_hearing,
            )
            from lib.talking_head_edit.director_client import chat_with_audio

            at = args.at if args.at is not None else 20.0
            probes = build_probe_set(Path(state["input_path"]), job.dir / "hearing_check",
                                     at, strength=args.check_strength)
            verdict, _ = chat_with_audio(
                PROMPT_VI, [(label, path) for label, path, _ in probes],
                model=state.get("options", {}).get("model"), max_tokens=4000)
            result = score_hearing(verdict.get("ket_qua", []), probes)
            print(f"Tiêm lỗi ở giây {at} (độ mạnh {args.check_strength}):")
            for row in result["chi_tiet"]:
                print(f"  {row['loi']:<12} khi có {row['diem_khi_co']}, "
                      f"đối chứng {row['diem_doi_chung']}  ->  lệch {row['lift']}")
            print(f"\nNghe ra {result['so_loi_nghe_ra']}/{result['tong_so_loi']} lỗi, "
                  f"lệch trung bình {result['lift_trung_binh']}")
            print(f"Chấm lệch {result['sai_lech_hai_ban_giong_nhau']} điểm giữa HAI BẢN "
                  f"GIỐNG HỆT NHAU (càng gần 0 càng tốt)")
            print(f"=> {result['ket_luan']}")

        if args.judge_audio:
            report = judge_audio(job, at_seconds=args.at if args.at is not None else 20.0)
            verdict = report["verdict"]
            print(f"{'preset':<16}{'sach':>6}{'ro':>5}{'tu nhien':>10}{'shotgun':>9}")
            for row in verdict.get("danh_gia", []):
                print(f"{row['ban']:<16}{row['do_sach']:>6}{row['do_ro']:>5}"
                      f"{row['tu_nhien']:>10}{row['giong_shotgun']:>9}  {row['nhan_xet'][:60]}")
            print(f"\nNen dung: {verdict.get('nen_dung')}")
            print(f"Ly do: {verdict.get('ly_do')}")
            if verdict.get("canh_bao"):
                print(f"Canh bao: {verdict['canh_bao']}")

        if args.preview_still is not None:
            path = composition_still(job, args.preview_still)
            print(f"Ảnh bố cục: {path}")

        if args.preview_clip is not None:
            clip = preview_clip(job, start_seconds=args.clip_from, duration=args.preview_clip,
                                scale=args.clip_scale)
            print(f"Clip duyệt: {clip['path']}")
            print(f"  v{clip['version']} · từ {clip['start_seconds']}s · "
                  f"dài {clip['duration_seconds']}s · scale {clip['scale']} · crf {clip['crf']}")
            print("  Ưng thì render full: --stages render,verify --no-cache")
        return 0

    if args.revise:
        options["revise_instruction"] = args.revise
        stages = ["revise", "audit", "resolve"]
    elif args.stages:
        stages = [name.strip() for name in args.stages.split(",") if name.strip()]
    elif args.only:
        stages = [args.only]
    elif args.stage_from:
        stages = stages_from(args.stage_from)
    else:
        stages = None

    if args.skip_render:
        # Derive from STAGES so a new stage is never silently dropped here.
        base = stages or [s for s in STAGES if s != "calibrate"]
        stages = [s for s in base if s not in ("render", "verify")]

    # --cloud-queue enqueues instead of rendering (locally or cloud) at all —
    # everything up to (not including) 'render' still has to run locally so
    # the queue entry's duration/kit exist, but 'render' and 'verify' never
    # run here.
    if args.cloud_queue and not args.skip_render:
        plan = stages if stages is not None else [s for s in STAGES if s != "calibrate"]
        pre_stages, _ = _split_at_render(plan)
        try:
            if pre_stages:
                run_job(job, options, stages=pre_stages, use_cache=not args.no_cache)
        except Exception as exc:  # noqa: BLE001 — CLI boundary: report, don't traceback-dump
            print(f"\nTHẤT BẠI: {exc}", file=sys.stderr)
            print(f"Log chi tiết: {job.dir / 'logs'}", file=sys.stderr)
            return 1
        return _enqueue_cloud_job(job)

    # --render-location cloud substitutes a rented render for the local
    # 'render' stage; 'verify' (and anything else after 'render' in a custom
    # --stages list) still runs locally against the downloaded final.mp4.
    # --skip-render wins over --render-location cloud: stopping before any
    # render, local or cloud, is what --skip-render means. See
    # _cloud_render_plan()'s docstring for the full "when does this apply" list.
    cloud_plan = _cloud_render_plan(args, stages)
    cloud_pre, cloud_post = cloud_plan if cloud_plan is not None else (None, None)
    # Autopilot routes cloud through _cloud_aware_runner (its own retry loop
    # needs the injection point), so it needs a distinct "is cloud active at
    # all" check that does not depend on _cloud_render_plan's split (autopilot
    # always plans against the full STAGES list internally, never a custom
    # subset the CLI computed here).
    autopilot_cloud = args.render_location == "cloud" and not args.skip_render and args.autopilot

    try:
        if args.autopilot:
            from lib.talking_head_edit.autopilot import run as autopilot

            # Options are merged into the job first: autopilot re-enters run_job
            # several times and must not have to re-pass them each round.
            if options:
                job.update(options={**job.load().get("options", {}), **options})
            runner = _cloud_aware_runner(args) if autopilot_cloud else None
            report = autopilot(job, stages=stages,
                               time_budget=args.autopilot_budget * 60, runner=runner)
            print(f"\nAutopilot: {report['passes']} lượt, {report['retries']} lần sửa, "
                  f"{report['renders']} lần render, {report['seconds']:.0f}s")
            for attempt in report["attempts"]:
                fixed = ", ".join(attempt.get("remedies") or []) or "—"
                print(f"  lượt {attempt['pass']}: {', '.join(attempt['stages'])} "
                      f"| remedy: {fixed} | còn {len(attempt['issues'])} vấn đề")
            for finding in report["remaining_issues"]:
                print(f"  CÒN LỖI [{finding.get('code')}] {finding.get('message')}")
        elif cloud_pre is not None:
            if cloud_pre:
                run_job(job, options, stages=cloud_pre, use_cache=not args.no_cache)
            _cloud_render_now(job, args)
            if cloud_post:
                run_job(job, options, stages=cloud_post, use_cache=not args.no_cache)
        else:
            run_job(job, options, stages=stages, use_cache=not args.no_cache)
    except Exception as exc:  # noqa: BLE001 — CLI boundary: report, don't traceback-dump
        print(f"\nTHẤT BẠI: {exc}", file=sys.stderr)
        print(f"Log chi tiết: {job.dir / 'logs'}", file=sys.stderr)
        return 1

    state = job.load()
    print(f"\nXong. Trạng thái: {state['status']} | phiên bản v{state['current_version']} "
          f"| chi phí ${state.get('cost_usd', 0)}")
    print(f"Thư mục job: {job.dir}")
    if job.final_path.exists():
        print(f"Video: {job.final_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
