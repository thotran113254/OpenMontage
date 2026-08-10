"""Directory <-> R2 prefix diff-sync: `plan_sync` (pure) + `apply_sync` (upload)
+ `pull` (reverse) + `prune` (delete orphaned remote keys).

One bucket, two top-level prefixes (Q5): this engine mirrors *a directory* to
*a prefix* -- shape-agnostic. Mapping a directory to its prefix is the
caller's job (pipeline hooks, the CLI, or the Vast.ai transfer phase).
Deletion is never mirrored automatically: a file removed locally stays in R2
(it is an archive) unless `prune()` is called explicitly with `yes=True`.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lib.r2_storage import manifest as manifest_mod

CLASS_A_USD_PER_MILLION = 4.50
STORAGE_USD_PER_GB_MONTH = 0.015


@dataclass
class PlanItem:
    relpath: str
    key: str
    size: int
    mtime_ns: int
    md5: str | None
    reason: str  # "new" | "changed" | "unchanged" | "touched"


@dataclass
class SyncPlan:
    local_dir: Path
    prefix: str
    upload: list[PlanItem] = field(default_factory=list)
    skip: list[PlanItem] = field(default_factory=list)
    total_bytes: int = 0
    object_count: int = 0


@dataclass
class SyncResult:
    uploaded: int
    skipped: int
    bytes: int
    prefix: str


def _scan(local_dir: Path, exclude: tuple[str, ...]) -> list[tuple[str, int, int]]:
    """Walk local_dir; skip symlinks, dirs, the manifest itself, excluded globs."""
    results = []
    for path in local_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        relpath = path.relative_to(local_dir).as_posix()
        if relpath == manifest_mod.MANIFEST_NAME:
            continue
        if any(fnmatch.fnmatch(relpath, pattern) for pattern in exclude):
            continue
        stat = path.stat()
        results.append((relpath, stat.st_size, stat.st_mtime_ns))
    return results


def plan_sync(local_dir: Path, prefix: str, settings: Any) -> SyncPlan:
    """Pure: no network, no upload. size+mtime_ns cheap gate; md5 only on mismatch."""
    local_dir = Path(local_dir)
    manifest = manifest_mod.load(local_dir, settings.bucket, prefix)
    files = manifest["files"]
    plan = SyncPlan(local_dir=local_dir, prefix=prefix)

    for relpath, size, mtime_ns in _scan(local_dir, settings.exclude):
        key = f"{prefix}/{relpath}"
        entry = files.get(relpath)
        if manifest_mod.entry_matches(entry, size, mtime_ns):
            plan.skip.append(PlanItem(relpath, key, size, mtime_ns, entry.get("md5"), "unchanged"))
            continue
        md5 = manifest_mod.file_md5(local_dir / relpath)
        if entry is not None and entry.get("md5") == md5:
            # Only mtime changed (e.g. a checkout re-touch) -- no upload, but the
            # manifest entry is refreshed in apply_sync so this doesn't re-hash forever.
            plan.skip.append(PlanItem(relpath, key, size, mtime_ns, md5, "touched"))
            continue
        reason = "changed" if entry is not None else "new"
        plan.upload.append(PlanItem(relpath, key, size, mtime_ns, md5, reason))
        plan.total_bytes += size
        plan.object_count += 1

    return plan


def estimate_storage_usd_per_month(total_bytes: int) -> float:
    return round((total_bytes / 1e9) * STORAGE_USD_PER_GB_MONTH, 6)


def estimate_class_a_usd(op_count: int) -> float:
    return round(op_count * CLASS_A_USD_PER_MILLION / 1_000_000, 8)


def announce(plan: SyncPlan, settings: Any, first_sync: bool) -> str:
    from lib.r2_storage.client import mask_endpoint

    total_mb = plan.total_bytes / (1024 * 1024)
    if not first_sync:
        return (f"R2 sync {plan.prefix}: {len(plan.upload)} objects để upload, "
                f"{len(plan.skip)} skip, {total_mb:.1f} MB")
    storage_usd = estimate_storage_usd_per_month(plan.total_bytes)
    ops_usd = estimate_class_a_usd(len(plan.upload))
    return (
        f"R2 sync (lần đầu) -- bucket={settings.bucket} "
        f"endpoint={mask_endpoint(settings.endpoint_url)}\n"
        f"  {plan.object_count} objects, {total_mb:.1f} MB\n"
        f"  ~${ops_usd:.6f} chi phí upload ops, ~${storage_usd:.4f}/tháng lưu trữ\n"
        f"  Egress miễn phí; storage tính phí hàng tháng cho tới khi bị xoá."
    )


def apply_sync(plan: SyncPlan, settings: Any,
                on_progress: Callable[[str], None] | None = None) -> SyncResult:
    """Upload plan.upload; write the manifest after each success (crash-safe:
    an interrupted sync never claims a file uploaded that never finished)."""
    from lib.r2_storage.client import build_client, transfer_config
    from lib.r2_storage.config import R2ConfigError

    total_upload_mb = sum(item.size for item in plan.upload) / (1024 * 1024)
    if total_upload_mb > settings.max_upload_mb_per_sync:
        raise R2ConfigError(
            f"Kế hoạch sync {total_upload_mb:.1f} MB vượt max_upload_mb_per_sync="
            f"{settings.max_upload_mb_per_sync} MB -- huỷ trước khi upload byte nào.")

    manifest = manifest_mod.load(plan.local_dir, settings.bucket, plan.prefix)
    client = build_client(settings)
    cfg = transfer_config(settings)
    uploaded_bytes = 0

    for item in plan.upload:
        local_path = plan.local_dir / item.relpath
        client.upload_file(str(local_path), settings.bucket, item.key, Config=cfg,
                            ExtraArgs={"Metadata": {"local-md5": item.md5}})
        manifest["files"][item.relpath] = {
            "size": item.size, "mtime_ns": item.mtime_ns, "md5": item.md5,
            "key": item.key, "uploaded_at": time.time(),
        }
        manifest_mod.save(plan.local_dir, manifest)  # after each file, not at the end
        uploaded_bytes += item.size
        if on_progress:
            on_progress(item.relpath)

    refreshed = False
    for item in plan.skip:
        if item.reason == "touched":
            prior = manifest["files"].get(item.relpath, {})
            manifest["files"][item.relpath] = {
                "size": item.size, "mtime_ns": item.mtime_ns, "md5": item.md5,
                "key": item.key, "uploaded_at": prior.get("uploaded_at", time.time()),
            }
            refreshed = True
    if refreshed:
        manifest_mod.save(plan.local_dir, manifest)

    return SyncResult(uploaded=len(plan.upload), skipped=len(plan.skip),
                       bytes=uploaded_bytes, prefix=plan.prefix)


def pull(prefix: str, local_dir: Path, settings: Any, force: bool = False) -> dict[str, Any]:
    """Reverse sync: download everything under `prefix` into `local_dir`.

    Overwrite policy: skip a local file that already matches by size; never
    silently clobber a larger/different local file without `force=True`.
    """
    from lib.r2_storage.client import build_client, transfer_config

    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    client = build_client(settings)
    cfg = transfer_config(settings)

    downloaded, skipped = 0, 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relpath = key[len(prefix):].lstrip("/")
            if not relpath:
                continue
            dest = local_dir / relpath
            if dest.exists() and not force and dest.stat().st_size == obj["Size"]:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(settings.bucket, key, str(dest), Config=cfg)
            downloaded += 1
    return {"downloaded": downloaded, "skipped": skipped}


def prune(prefix: str, local_dir: Path, settings: Any, yes: bool = False) -> dict[str, Any]:
    """Delete remote keys under `prefix` that have no matching local file.

    Scoped strictly to `prefix` -- never lists or deletes anything outside it
    (e.g. a `projects/x` prune must never touch `render-kits/**`).
    """
    from lib.r2_storage.client import build_client

    local_dir = Path(local_dir)
    client = build_client(settings)
    local_relpaths = ({relpath for relpath, _, _ in _scan(local_dir, settings.exclude)}
                       if local_dir.exists() else set())

    orphaned: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relpath = key[len(prefix):].lstrip("/")
            if relpath and relpath not in local_relpaths:
                orphaned.append(key)

    if yes:
        for key in orphaned:
            client.delete_object(Bucket=settings.bucket, Key=key)
    return {"orphaned": orphaned, "deleted": yes}
