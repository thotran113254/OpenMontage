"""`python -m lib.r2_storage.cli` -- standalone CLI for the R2 sync engine.

Dry-run by default (mirrors the cloud-render philosophy): the first thing a
human sees is what *would* happen and what it costs, never a surprise upload.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.r2_storage.config import R2ConfigError, is_configured, resolve
from lib.r2_storage.sync import announce, apply_sync, plan_sync, prune, pull


def _cmd_status(_args: argparse.Namespace) -> int:
    settings = resolve()
    configured, missing = is_configured()
    print(f"enabled={settings.enabled} bucket={settings.bucket!r} prefix={settings.prefix!r}")
    print(f"configured={configured} missing_env={missing}")
    return 0


def _cmd_push(args: argparse.Namespace) -> int:
    settings = resolve()
    local_dir = Path(args.push)
    plan = plan_sync(local_dir, args.prefix, settings)
    first_sync = not (local_dir / ".r2sync.json").exists()
    print(announce(plan, settings, first_sync))
    if not args.yes:
        print("(dry-run -- thêm --yes để upload thật)")
        return 0
    result = apply_sync(plan, settings, on_progress=lambda relpath: print(f"  uploaded {relpath}"))
    print(f"Xong: {result.uploaded} uploaded, {result.skipped} skipped, {result.bytes} bytes")
    return 0


def _cmd_pull(args: argparse.Namespace) -> int:
    settings = resolve()
    result = pull(args.pull, Path(args.dest), settings, force=args.force)
    print(f"Đã pull {args.pull} -> {args.dest}: {result['downloaded']} downloaded, "
          f"{result['skipped']} skipped")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from lib.r2_storage.client import build_client

    settings = resolve()
    client = build_client(settings)
    paginator = client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=settings.bucket, Prefix=args.list):
        for obj in page.get("Contents", []):
            print(f"{obj['Key']}\t{obj['Size']}")
            count += 1
    print(f"({count} objects)")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    settings = resolve()
    result = prune(args.prune, Path(args.push or "."), settings, yes=args.yes)
    for key in result["orphaned"]:
        print(f"  {'deleted' if args.yes else 'would delete'}: {key}")
    if not args.yes:
        print(f"(dry-run -- {len(result['orphaned'])} orphaned keys; thêm --yes để xoá thật)")
    return 0


def _cmd_presign(args: argparse.Namespace) -> int:
    from lib.r2_storage.client import build_client

    settings = resolve()
    client = build_client(settings)
    url = client.generate_presigned_url(
        "get_object", Params={"Bucket": settings.bucket, "Key": args.presign},
        ExpiresIn=args.expires)
    print(url)
    print("(URL này là bearer token -- không paste vào log/issue công khai)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lib.r2_storage.cli")
    parser.add_argument("--push", metavar="DIR", help="Local directory to sync up")
    parser.add_argument("--prefix", metavar="KEY_PREFIX", help="Remote key prefix for --push/--prune")
    parser.add_argument("--yes", action="store_true", help="Actually upload/delete (default: dry-run)")
    parser.add_argument("--pull", metavar="KEY_PREFIX", help="Remote key prefix to sync down")
    parser.add_argument("--dest", metavar="DIR", help="Local destination directory for --pull")
    parser.add_argument("--force", action="store_true", help="Overwrite mismatched local files on --pull")
    parser.add_argument("--list", metavar="KEY_PREFIX", help="List objects under a prefix")
    parser.add_argument("--prune", metavar="KEY_PREFIX",
                         help="Delete orphaned remote keys under a prefix (needs --yes)")
    parser.add_argument("--presign", metavar="KEY", help="Print a presigned GET URL for a key")
    parser.add_argument("--expires", type=int, default=86400, help="Presign TTL in seconds")
    parser.add_argument("--status", action="store_true", help="Print config + credential state, no network")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.status:
            return _cmd_status(args)
        if args.presign:
            return _cmd_presign(args)
        if args.list:
            return _cmd_list(args)
        if args.prune:
            return _cmd_prune(args)
        if args.pull:
            if not args.dest:
                parser_error = "--pull cần --dest <dir>"
                print(parser_error, file=sys.stderr)
                return 2
            return _cmd_pull(args)
        if args.push:
            if not args.prefix:
                print("--push cần --prefix <key-prefix>", file=sys.stderr)
                return 2
            return _cmd_push(args)
    except R2ConfigError as exc:
        print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
        return 1
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
