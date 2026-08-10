# Phase 01 — R2 client + config layer

## Context links

- Config pattern to mirror: `lib/cloud_render/config.py:19-95` (`BUILTIN_DEFAULTS` /
  `_read_json` / `global_defaults` / `_clean` / `validate`)
- Committed config example: `config/cloud-render.json` (`enabled: false` on line 2)
- Env loader: `lib/env_loader.py:15-35` (`load_env`, `get_env`, `require_env`)
- `.env.example` R2 block already present: `.env.example:78-84`
- Registry auto-loads dotenv before discovery: `tools/tool_registry.py:120` (`self._load_dotenv()`)

## Overview

- **Priority:** P1 (blocks every other phase)
- **Status:** completed
- **Effort:** 2h
- Build `lib/r2_storage/` with a validated config layer and a single hardened boto3 S3
  client factory. No pipeline integration, no tool, no sync — just the foundation.

## Key insights

1. **boto3 >= 1.36 breaks R2 by default.** botocore now computes a CRC32 integrity
   checksum on every `PutObject`/`UploadPart`. R2 rejects it. Mitigation is
   `request_checksum_calculation="when_required"` + `response_checksum_validation="when_required"`.
   **[UNVERIFIED — verify at implementation time]** whether these are top-level
   `botocore.config.Config` kwargs or nested under `s3={...}`. Research returned the
   nested form; top-level is the documented botocore 1.36+ location. Step 1 below
   resolves this empirically before any other code is written.
2. **`region_name="auto"`.** Any AWS region code produces a 301 redirect from R2.
3. **Endpoint contains the account id**, so it is semi-sensitive — mask it in log output.
4. **botocore DEBUG logging prints `Authorization` headers.** Must be pinned to WARNING.
5. `check_dependencies()` (`tools/base_tool.py:209-231`) only understands `cmd:` / `env:` /
   `python:` prefixes — any other prefix silently falls through and the tool falsely
   reports AVAILABLE. Relevant to phase 02, decided here.

## Requirements

**Functional**
- Resolve settings from: built-in defaults → `config/r2-storage.json` → env vars (creds only).
- `validate()` fails loud on: malformed bucket, non-numeric size limits, unknown keys,
  and any JSON key whose name matches `secret|access_key|token|password`.
- `build_client(settings)` returns a configured `boto3` S3 client; import of `boto3` is
  lazy (module-level import is fine here — `lib/` is not walked by `registry.discover()`;
  only `tools/` is, see `tools/tool_registry.py:118-134`).
- `is_configured()` returns `(bool, list[str missing_env_names])` without touching network.

**Non-functional**
- No network call at import or config-load time.
- Zero secret values in any exception message, repr, or log line.

## Architecture

### Data flow

```
.env ─(load_env)─► os.environ ─┐
                               ├─► r2_storage.config.resolve() ─► R2Settings (frozen dataclass)
config/r2-storage.json ────────┤                                        │
BUILTIN_DEFAULTS ──────────────┘                                        ▼
                                                        r2_storage.client.build_client()
                                                                        │
                                                                        ▼
                                                          botocore S3 client (R2 endpoint)
```

`R2Settings` carries **non-secret** fields as attributes (bucket, prefix, endpoint,
thresholds) and exposes credentials only via a method `_credentials()` that reads
`os.environ` at call time — so a settings object logged or serialized cannot leak keys.

### `config/r2-storage.json` (committed, `enabled: false`)

```json
{
  "enabled": false,
  "bucket": "",
  "prefix": "projects",
  "auto_sync": false,
  "sync_after_stage": false,
  "exclude": ["*.log", "*.tmp", "render_public/**", "__pycache__/**", ".r2sync.json"],
  "multipart_threshold_mb": 64,
  "multipart_chunksize_mb": 64,
  "max_concurrency": 4,
  "presign_expiry_seconds": 604800,
  "max_upload_mb_per_sync": 5000
}
```

Resolution rules:
- `bucket`: JSON value if non-empty, else `CLOUDFLARE_R2_BUCKET`. Empty both → `validate()` fails.
- `endpoint_url`: `CLOUDFLARE_R2_ENDPOINT_URL`, else derived from `CLOUDFLARE_R2_ACCOUNT_ID`
  as `https://{account_id}.r2.cloudflarestorage.com`.
- `public_base_url`: from **env only** (`CLOUDFLARE_R2_PUBLIC_BASE_URL`), never from JSON —
  it is deployment-specific like the endpoint. Empty = feature off. Trailing slash
  stripped; must start with `https://`; `validate()` rejects anything else.
- Credentials: env only. Never read from JSON.
- `presign_expiry_seconds` clamped to `1..604800` (R2/SigV4 hard ceiling, 7 days).
- `max_upload_mb_per_sync`: guard so a mis-pointed sync cannot push 100 GB unattended.
- `addressing_style` deliberately **not set** — botocore's `auto` default works for
  dot-free bucket names like `openmontage-assets`. Documented in troubleshooting as the
  first knob to flip (`path`) if 403/TLS errors appear. (YAGNI: no config key until needed.)

### Client config (target shape, pending step 1)

```python
Config(
    region_name="auto",
    signature_version="s3v4",
    retries={"mode": "adaptive", "max_attempts": 5},
    request_checksum_calculation="when_required",   # position verified in step 1
    response_checksum_validation="when_required",
)
```

## Related code files

**Create**
- `lib/r2_storage/__init__.py` — public surface: `resolve`, `validate`, `build_client`, `is_configured`
- `lib/r2_storage/config.py` (~130 LOC)
- `lib/r2_storage/client.py` (~90 LOC)
- `config/r2-storage.json`

**Modify**
- `requirements.txt` — append a commented `boto3>=1.36` block in the existing style
  (see the `vastai>=1.0.4,<2` block at the file tail for the comment convention)
- `requirements-dev.txt` — append `moto[server]>=5.0` (needed by phase 06)
- `.env.example` — lines 78-84 already present; **add one line** in the same inline-comment
  style:
  ```
  CLOUDFLARE_R2_PUBLIC_BASE_URL=     # https://<custom-domain> hoặc Public Development URL của bucket; trống = chỉ dùng presigned URL
  ```

**Delete** — none.

## Implementation steps

1. **Resolve the checksum-kwarg question empirically before writing code.**
   ```bash
   python -c "import botocore,boto3;print(botocore.__version__,boto3.__version__)"
   python -c "from botocore.config import Config; print(Config(request_checksum_calculation='when_required'))"
   ```
   If the second command raises `TypeError`, fall back to nesting under `s3={...}`, and
   if that also fails, set env vars `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` /
   `AWS_RESPONSE_CHECKSUM_VALIDATION=when_required` inside `build_client()` before
   constructing the client. Record the winning form in a code comment stating *why*
   (R2 rejects CRC32 integrity headers) — not which plan phase decided it.
2. Add `boto3>=1.36` to `requirements.txt`; `pip install -r requirements.txt`.
3. Write `lib/r2_storage/config.py`: `BUILTIN_DEFAULTS`, `KEYS`, `R2ConfigError`,
   `_read_json`, `_clean`, `resolve(path=None)`, `validate(settings)`, `is_configured()`.
   Copy the layering/`_clean` semantics from `lib/cloud_render/config.py:76-95` — `None`
   is dropped, not treated as an override.
4. Add the credential-key rejection: `validate()` raises if any JSON key matches
   `(secret|access[_-]?key|token|password)` case-insensitively.
4b. Add `public_base_url` resolution + validation (env-only, `https://` required, trailing
   slash stripped) and a `public_url(key)` helper returning
   `f"{public_base_url}/{quote(key)}"` or `None` when unset. Q3 decision: public delivery
   is the default sharing path once the user configures a custom domain in the Cloudflare
   dashboard — **the plan never automates that dashboard step.**
5. Write `lib/r2_storage/client.py`:
   - `build_client(settings)` → boto3 client per the config above.
   - `transfer_config(settings)` → `boto3.s3.transfer.TransferConfig` from the mb keys.
   - `mask_endpoint(url)` → `https://a1b2c3d4….r2.cloudflarestorage.com` for log lines.
   - Module top: `logging.getLogger("botocore").setLevel(logging.WARNING)` guarded so it
     never *lowers* a level a user explicitly raised.
6. Write `config/r2-storage.json` exactly as above, `enabled: false`.
7. Add `moto[server]>=5.0` to `requirements-dev.txt`.
8. `python -c "from lib import r2_storage; print(r2_storage.resolve())"` — must print
   settings with no secrets and make no network call.

## Todo list

- [x] 1. Verify checksum kwarg position (`Config(...)` vs `s3={...}` vs env vars)
- [x] 2. `requirements.txt` += `boto3>=1.36`, install
- [x] 3. `lib/r2_storage/config.py` — layered resolve + validate
- [x] 4. Credential-key rejection in `validate()`
- [x] 4b. `public_base_url` (env-only) + `public_url(key)` helper
- [x] 5. `lib/r2_storage/client.py` — client factory, TransferConfig, endpoint masking, log pinning
- [x] 6. `config/r2-storage.json` with `enabled: false`
- [x] 7. `requirements-dev.txt` += `moto[server]>=5.0`
- [x] 8. Smoke-import check, no network

## Success criteria

- `python -c "from lib.r2_storage import resolve, validate; s=resolve(); validate(s); print(s)"`
  succeeds and prints **no** secret substring (grep the output for the first 6 chars of
  `CLOUDFLARE_R2_SECRET_ACCESS_KEY` — must be absent).
- `resolve()` returns `enabled=False` on a clean checkout.
- With `CLOUDFLARE_R2_SECRET_ACCESS_KEY` unset, `is_configured()` returns
  `(False, ["CLOUDFLARE_R2_SECRET_ACCESS_KEY"])` and does not raise.
- Putting `"secret_access_key": "x"` into `config/r2-storage.json` makes `validate()` raise.
- No module under `lib/r2_storage/` opens a socket at import time.

## Risk assessment

| Risk | L×I | Mitigation |
|---|---|---|
| Checksum kwarg form wrong → every upload 400s | High × High | Step 1 is an empirical check before any other code; phase 06 adds a regression test asserting the client's resolved checksum mode |
| `boto3` install breaks CI (transitive `botocore`/`urllib3` pins) | Low × Med | boto3 pins botocore exactly and only needs `urllib3`; run the full suite after install before proceeding to 02 |
| Endpoint/account id leaks into a shared log | Med × Low | `mask_endpoint()` used in every log/announce path |
| A future contributor adds creds to the JSON "for convenience" | Med × High | Mechanical rejection in `validate()`, not a docs note |
| Someone raises botocore log level to DEBUG while debugging and commits an `Authorization` header into a job log | Low × High | Log-level pin + explicit warning in `docs/r2-storage.md` (phase 06) |

**Rollback:** delete `lib/r2_storage/`, `config/r2-storage.json`, revert the two
requirements lines. Nothing else imports it yet — zero cascade.

## Security considerations

- Credentials read from `os.environ` at call time only; never stored on `R2Settings`,
  never in `__repr__`, never in exception text.
- `.env` stays gitignored (`.gitignore:42-44`). Only `.env.example` with placeholders is
  committed — already done, do not add real values.
- `CLOUDFLARE_R2_API_TOKEN` is **not** used by the boto3 S3 path (it is the Cloudflare
  REST API token for bucket admin). Do not wire it into the client; leave it documented
  as reserved.
- Log level for `botocore` pinned to WARNING so SigV4 `Authorization` headers never land
  in `projects/**/logs/*.log`.

## Next steps

Unblocks phase 02 and phase 03 (parallel).
