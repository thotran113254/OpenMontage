# Phase 02 — `cloudflare_r2` BaseTool

## Context links

- Tool contract: `tools/base_tool.py:139-276` (attrs), `:209-231` (`check_dependencies`),
  `:299` (`execute`)
- Lazy-import precedent + the `binary:` prefix trap: `tools/video/vast_cloud_render.py:1-21`
- Compact API-tool example: `tools/analysis/elevenlabs_scribe.py:47-80`
- Registry walks and **instantiates** every class under `tools/`: `tools/tool_registry.py:118-134`
- Blocked by: [phase 01](phase-01-r2-client-and-config.md)

## Overview

- **Priority:** P2
- **Status:** completed
- **Effort:** 1.5h
- Registry-visible wrapper exposing R2 as a first-class OpenMontage capability so the
  preflight provider menu can report it. Thin — all logic lives in `lib/r2_storage/`.

## Key insights

1. **`registry.discover()` imports and instantiates every module under `tools/`.** A
   top-level `import boto3` would crash preflight for anyone who has not installed it.
   Import boto3 only inside `execute()` / `get_status()` paths, exactly as
   `tools/video/vast_cloud_render.py:1-21` documents.
2. **`dependencies` prefix must be `python:` / `env:` / `cmd:`.** `check_dependencies`
   (`tools/base_tool.py:212-231`) silently ignores anything else, so a typo'd prefix makes
   the tool falsely report AVAILABLE. Use
   `["python:boto3", "env:CLOUDFLARE_R2_ACCESS_KEY_ID", "env:CLOUDFLARE_R2_SECRET_ACCESS_KEY", "env:CLOUDFLARE_R2_ENDPOINT_URL"]`.
   `CLOUDFLARE_R2_BUCKET` is deliberately **not** a dependency — it can come from
   `config/r2-storage.json`; bucket resolution is validated inside `execute()` instead.
3. **`capability="object_storage"` is a new capability family.** It has no selector and no
   pipeline manifest references it — that is intentional. It will surface in
   `provider_menu_summary()` as a new `0/1` or `1/1` row. Confirm this does not break any
   consumer that enumerates capabilities (see step 6).
4. **Presigned URLs are credentials in a query string.** `presigned_url` must return the
   URL in `ToolResult.data` but the tool must never write it into `artifacts` (which are
   file paths persisted by callers) or emit it to a log.
5. **Three URL actions, deliberately not one.** Q3 locked public/custom-domain delivery as
   the sharing path, but the phase-05 Vast.ai transfer needs *private* presigned URLs.
   Collapsing these into one action risks handing a rented box a public URL, or pasting a
   signed URL into a client-facing link. So:
   - `presigned_url` / `presigned_put_url` — **always** signed, never public. Internal use
     (Vast.ai transfer). Safe to persist? No — still bearer tokens.
   - `delivery_url` — public `https://<base>/<key>` when `CLOUDFLARE_R2_PUBLIC_BASE_URL`
     is set; otherwise falls back to a presigned GET **plus** a `warning` telling the user
     to configure a custom domain. This is the human-sharing path.
6. **Public delivery has a manual prerequisite the code cannot do.** Enabling public
   access or attaching a custom domain to `openmontage-assets` happens in the Cloudflare
   dashboard. The tool only *consumes* `CLOUDFLARE_R2_PUBLIC_BASE_URL`; it never calls the
   Cloudflare REST API to flip it. `CLOUDFLARE_R2_API_TOKEN` stays unused (phase 01).

## Requirements

**Functional** — one tool, `action`-dispatched:

| action | inputs | returns in `.data` |
|---|---|---|
| `upload` | `local_path`, `remote_key`, `content_type?` | `key`, `bytes`, `etag`, `multipart` |
| `download` | `remote_key`, `local_path` | `local_path`, `bytes` |
| `list` | `prefix`, `max_keys?` | `objects[] {key,size,last_modified,etag}`, `truncated` |
| `delete` | `remote_key` | `key`, `deleted` |
| `presigned_url` | `remote_key`, `expires_in?` | `url`, `expires_at`, `key` |
| `presigned_put_url` | `remote_key`, `expires_in?` | `url`, `expires_at`, `key` |
| `delivery_url` | `remote_key`, `expires_in?` | `url`, `kind` (`public`\|`presigned`), `warning?` |

**Non-functional**
- `get_status()` must be network-free and fast (preflight calls it for every tool).
- `estimate_cost()` returns a real number: Class A ops $4.50/M, Class B $0.36/M,
  storage $0.015/GB-month — so upload of a 2 GB file reports ≈ `$0.0000045` op +
  `$0.03/month` storage, not `0.0`.
- File under 200 LOC (repo modularization rule). Split helpers into `lib/r2_storage/` if it grows.

## Architecture

```
agent / pipeline
      │  .execute({"action": "upload", ...})
      ▼
tools/storage/cloudflare_r2.py  (CloudflareR2, BaseTool)
      │  lazy: from lib.r2_storage import resolve, validate, build_client, transfer_config
      ▼
lib/r2_storage/client.py ──► botocore S3 client ──► https://<acct>.r2.cloudflarestorage.com
```

Class name `CloudflareR2` — PascalCase, no `Tool` suffix (`AGENT_GUIDE.md` → Tool Class
Naming Convention). Module path `tools/storage/cloudflare_r2.py` (Python snake_case).

Tool attrs:
```
name = "cloudflare_r2"; version = "0.1.0"; tier = ToolTier.CORE
capability = "object_storage"; provider = "cloudflare_r2"
stability = ToolStability.EXPERIMENTAL; execution_mode = ExecutionMode.SYNC
determinism = Determinism.DETERMINISTIC; runtime = ToolRuntime.API
side_effects = ["network", "remote_write"]
resource_profile = ResourceProfile(network_required=True)
retry_policy = RetryPolicy(max_retries=0)   # botocore adaptive retries own this
resume_support = ResumeSupport.NONE          # s3transfer restarts a failed multipart
```

`install_instructions` (registry reads this; do **not** duplicate it into skill/prompt text):
```
1. pip install boto3
2. Tạo bucket trong Cloudflare dashboard (R2 > Create bucket)
3. R2 > Manage API Tokens > Create API token (Object Read & Write)
4. Điền CLOUDFLARE_R2_* vào .env (xem .env.example)
5. Bật enabled: true trong config/r2-storage.json (mặc định tắt)
```

## Related code files

**Create**
- `tools/storage/__init__.py` (empty, package marker — required for `pkgutil.walk_packages`)
- `tools/storage/cloudflare_r2.py` (~180 LOC)

**Modify** — none. (Do not edit `lib/r2_storage/*` here; phase 03 owns the remaining
files in that package and phase 01 owns `config.py`/`client.py`.)

## Implementation steps

1. `mkdir tools/storage` + empty `__init__.py`.
2. Write the class header block (attrs above), `best_for` / `not_good_for`,
   `input_schema` / `output_schema` describing the `action` union.
3. `execute(inputs)`: validate `action`, lazy-import `lib.r2_storage`, call
   `resolve()`+`validate()`, refuse with a clear error when `enabled` is false
   *unless* `inputs.get("force")` is true (so an agent that explicitly wants one object
   can act without flipping the global flag — and it is still never silent).
4. Implement the 7 actions against the client. `delivery_url` reads `public_url(key)` from
   phase 01 and falls back to `presigned_url` + a `warning` string naming
   `CLOUDFLARE_R2_PUBLIC_BASE_URL`. `presigned_put_url` signs **no extra headers** — a
   signed `Content-Type` the uploader does not send yields a 403 (see phase 05).
   `upload` uses `client.upload_file(...,
   Config=transfer_config(settings), ExtraArgs={"Metadata": {"local-md5": ...}})` — the
   md5 metadata is what phase 03's cross-machine verify reads.
5. Wrap `botocore.exceptions.ClientError` into a `ToolResult(success=False, error=...)`
   whose message includes the S3 error code and the **masked** endpoint, never the key.
6. Registration check — must list the tool and must not crash:
   ```bash
   python -c "from tools.tool_registry import registry; registry.discover(); import json; print(json.dumps(registry.provider_menu_summary(), indent=2))"
   ```
   Confirm an `object_storage` row appears and nothing else regressed.
7. Uninstall-simulation check: temporarily rename the boto3 dist-info (or run in a venv
   without boto3) and re-run step 6 — discovery must still succeed with the tool reported
   `UNAVAILABLE`, not raise `ImportError`.

## Todo list

- [x] 1. `tools/storage/__init__.py`
- [x] 2. Class header + schemas + `install_instructions`
- [x] 3. `execute()` dispatch + `enabled` guard
- [x] 4. upload / download / list / delete / presigned_url / presigned_put_url / delivery_url
- [x] 5. `ClientError` → `ToolResult` with masked endpoint
- [x] 6. `provider_menu_summary()` shows `object_storage`
- [x] 7. Discovery survives boto3 being absent

## Success criteria

- `registry.discover()` includes `cloudflare_r2`; `registry.get_by_capability("object_storage")`
  returns exactly `[CloudflareR2]`.
- With boto3 absent → `get_status() == UNAVAILABLE`, discovery does not raise.
- With boto3 present but `CLOUDFLARE_R2_SECRET_ACCESS_KEY` unset → `UNAVAILABLE` with the
  missing var named in the `DependencyError` text.
- `execute({"action":"upload",...})` with `enabled:false` and no `force` returns
  `success=False` and an error that names `config/r2-storage.json`.
- `delivery_url` with `CLOUDFLARE_R2_PUBLIC_BASE_URL` set returns `kind="public"` and a URL
  containing **no** `X-Amz-Signature`; with it unset returns `kind="presigned"` and a
  non-empty `warning`.
- `presigned_url` / `presigned_put_url` return a signed URL **even when** the public base
  is configured (they must never silently become public).
- `grep -rn "AKIA\|CLOUDFLARE_R2_SECRET" tools/storage/` → no literal secret handling.
- File ≤ 200 LOC.

## Risk assessment

| Risk | L×I | Mitigation |
|---|---|---|
| Top-level `import boto3` breaks preflight for every user | Med × **Critical** | Lazy import; step 7 explicitly tests the boto3-absent path |
| Wrong `dependencies` prefix → tool falsely AVAILABLE | Med × High | Only `python:`/`env:` used; phase 06 test asserts every prefix is one of the three understood by `check_dependencies` |
| Presigned URL written into `events.jsonl` by a caller | Med × High | Tool returns it in `.data` only, never in `artifacts`; documented in the output schema description; phase 04 hook never persists it |
| New `object_storage` capability confuses a selector | Low × Low | No selector routes on it; step 6 verifies the menu still renders |
| `list` on a large bucket returns unbounded results | Low × Med | `max_keys` default 1000, `truncated` flag returned, no auto-pagination |

**Rollback:** delete `tools/storage/`. Registry auto-discovers, so removal is complete —
no registration list to unwind.

## Security considerations

- Never log or return the secret key, the raw `Authorization` header, or an unmasked
  endpoint.
- `presigned_url` grants bearer access to an object for up to 7 days. Default
  `expires_in` comes from config (`presign_expiry_seconds`), clamped in phase 01; the
  tool must re-clamp defensively rather than trust the caller.
- `delete` is destructive and irreversible (no versioning configured on the bucket).
  Require an exact `remote_key` — reject any input containing `*` or ending in `/`.
- **Public delivery makes every object under the bucket world-readable by key.** Q3 accepts
  this for output sharing, but it means `render-kits/**` (which contains the user's raw
  footage inside the kit tarball) is also reachable by anyone who can guess or obtain the
  key. Keys are unguessable in practice (job ids / content hashes), but this is a real
  exposure change and must be stated in `docs/r2-storage.md`. If it matters, the correct
  fix is a second bucket for kits — reopening Q5, not something to decide silently here.
- `presigned_put_url` grants write to exactly one key. Never presign a prefix.

## Next steps

Independent of phase 03/04. Feeds phase 06 (registration test mirroring
`tests/test_cloud_render_tool_registration.py`).
