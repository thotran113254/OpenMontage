# Cloudflare R2 object storage — human entry point

R2 is the durable off-machine store for project assets (`projects/**`) **and** the
transfer medium for cloud rendering (`render-kits/**`) — see `docs/cloud-render.md`
for the cloud-render side. This page covers setup, the CLI, cost, and troubleshooting
for R2 storage itself.

**Ships disabled.** Nothing syncs, nothing uploads, until a human flips
`"enabled": true` in `config/r2-storage.json` — see §2.

## 1. What it is

- **Durable archive.** `projects/autoedit-jobs/<job_id>/**` and
  `projects/autoedit/<project_id>/**` mirror to R2 at job end (opt-in, §3).
  Local stays the working store; R2 is the archive.
- **Diff-sync, not a mirror tool.** A local `.r2sync.json` manifest tracks
  `size + mtime_ns` (hashing only on mismatch), so a no-change sync costs
  **zero** network calls and completes in under a second.
- **Delivery.** `delivery_url` returns a public link once you configure a
  custom domain (§2 step 2); until then it falls back to a presigned URL with
  a warning.
- **Cloud-render transfer.** The Vast.ai transfer path (`docs/cloud-render.md`)
  uses R2 as the hop between your machine and a rented box — the box never
  holds a credential, only short-lived presigned URLs.

## 2. Setup (two manual Cloudflare-dashboard steps)

1. **Create the bucket** — `openmontage-assets`, in the Cloudflare dashboard
   (R2 → Create bucket). Everything below needs it to exist.
2. **(Optional) Enable public delivery** — either enable the bucket's Public
   Development URL or attach a custom domain (R2 → bucket → Settings →
   Public access), then put that URL in `.env`'s
   `CLOUDFLARE_R2_PUBLIC_BASE_URL`. Skip this and `delivery_url` still works —
   it falls back to a presigned URL with a warning.

Then, in this repo:

3. `R2 → Manage API Tokens → Create API token` (Object Read & Write scoped to
   the bucket). Fill in `.env` (copy from `.env.example`):
   ```
   CLOUDFLARE_R2_ACCOUNT_ID=          # subdomain of the R2 endpoint URL
   CLOUDFLARE_R2_ACCESS_KEY_ID=       # from the token above
   CLOUDFLARE_R2_SECRET_ACCESS_KEY=   # shown once at creation -- save it now
   CLOUDFLARE_R2_ENDPOINT_URL=        # https://<account_id>.r2.cloudflarestorage.com
   CLOUDFLARE_R2_BUCKET=openmontage-assets
   CLOUDFLARE_R2_PUBLIC_BASE_URL=     # only if you did step 2
   ```
   **`.env` is gitignored and stays that way.** Only `.env.example` (empty
   placeholders) is committed. If a real key was ever pasted into a chat, an
   issue, or a log, rotate it in the Cloudflare dashboard.
4. `pip install -r requirements.txt` (pulls in `boto3>=1.36`).
5. Flip `"enabled": true` in `config/r2-storage.json` to turn on the
   `cloudflare_r2` tool and (if also set) `auto_sync`. Cloud-render's own
   transfer path does **not** need this flag — see `docs/cloud-render.md`.
6. Verify: `python -m lib.r2_storage.cli --status` (no network, prints
   config + which env vars are set).

## 3. Enabling automatic sync

`config/r2-storage.json`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Master switch. Off = the tool refuses every action (except `force:true` on a single object) and the sync hook is a true no-op. |
| `auto_sync` | `false` | With `enabled:true`, syncs a job to R2 automatically at **job end only** — never per-stage (avoids 7 round-trips inside the interactive prompt-iterate loop; every intermediate artifact is regenerable anyway). |
| `prefix` | `"projects"` | Top-level key prefix for the durable archive. |
| `exclude` | logs/tmp/caches/the manifest itself | Globs never uploaded. |
| `multipart_threshold_mb` / `multipart_chunksize_mb` | `64` / `64` | s3transfer sizing. |
| `presign_expiry_seconds` | `604800` (7 days, the SigV4 ceiling) | Default TTL for `presigned_url`/`delivery_url`'s fallback. |
| `max_upload_mb_per_sync` | `5000` | Hard stop before the first byte if a sync's plan exceeds this — catches a mis-pointed `--push`. |

Enabling `auto_sync` moves personal footage off-machine automatically — the
first-ever sync of a directory prints a full announce block (object count,
MB, estimated $/month) before uploading anything.

## 4. CLI

```
python -m lib.r2_storage.cli --push <dir> --prefix <key-prefix>   # dry-run by default
python -m lib.r2_storage.cli --push <dir> --prefix <key-prefix> --yes
python -m lib.r2_storage.cli --pull <key-prefix> --dest <dir>
python -m lib.r2_storage.cli --list <key-prefix>
python -m lib.r2_storage.cli --prune <key-prefix> --push <dir> --yes   # delete orphaned remote keys
python -m lib.r2_storage.cli --presign <key> --expires 86400
python -m lib.r2_storage.cli --status                                  # config + creds, no network
```

Talking-head auto-edit CLI equivalents: `--r2-sync` (force a sync of the
current job now, ignoring `auto_sync` but still honouring `enabled`) and
`--r2-status`.

## 5. Cost model

Cloudflare R2 pricing (no egress fee, unlike S3):

| Item | Price |
|---|---|
| Storage | $0.015 / GB-month |
| Class A ops (PUT, LIST, ...) | $4.50 / million |
| Class B ops (GET, HEAD, ...) | $0.36 / million |
| Egress | **Free** |

The CLI's announce block and the `cloudflare_r2` tool's `estimate_cost()`
compute these live. A 2 GB upload is ≈ `$0.0000045` in ops plus
`$0.03/month` in storage, not `$0.00`.

## 6. Key layout

```
projects/autoedit-jobs/<job_id>/…        durable archive
projects/autoedit/<project_id>/…         durable archive
render-kits/composer/<kit_hash>/…        transient, content-addressed, cloud-render only
render-kits/jobs/<job_id>/…               transient staging, cloud-render only
```

`projects/**` is durable and only ever grown, never auto-deleted.
`render-kits/**` is transient cloud-render staging (see `docs/cloud-render.md`)
— per-job staging is deleted right after each render; composer kits are
content-addressed and few, kept for reuse across rentals.

## 7. Manual pruning (no automatic lifecycle policy)

There is no TTL/lifecycle rule on the bucket by design (locked decision) —
storage is cheap enough that automatic deletion risks losing something a
human wanted kept. Clean up explicitly:

```
python -m lib.r2_storage.cli --prune projects/autoedit-jobs/<old-job-id> --push <local-dir>
```

`--prune` always dry-runs first (prints every key it *would* delete) and is
scoped strictly to the prefix you give it — pruning `projects/x` never lists
or deletes anything under `render-kits/`.

## 8. Security notes

- **Credentials only in `.env`.** `config/r2-storage.json` mechanically
  rejects any key whose name matches `secret|access_key|token|password` —
  not a lint suggestion, an actual `R2ConfigError`.
- **Public delivery is an exposure change, not a formality.** Once
  `CLOUDFLARE_R2_PUBLIC_BASE_URL` is set, every object under the bucket is
  readable by anyone holding the key — including raw footage inside kit
  tarballs under `render-kits/**`. Keys are unguessable in practice (job ids,
  content hashes), but this is real, not cosmetic. If it matters for a
  project, do not enable public delivery for that project's assets.
- **`presigned_url`/`presigned_put_url` are bearer tokens.** The CLI's
  `--presign` output warns about this; never paste a signed URL into a
  shared log or issue.
- **`CLOUDFLARE_R2_API_TOKEN`** (Cloudflare's own REST API token, for bucket
  admin) is reserved and unused by the boto3 S3 client path — do not wire it
  into anything that touches objects.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403`/TLS errors on a dot-free bucket name | `addressing_style` defaults to `auto` in botocore, which usually works | Flip to `path` addressing style if this ever surfaces (not currently a config key — YAGNI until needed) |
| Every upload fails with a checksum-related 400 | boto3 ≥1.36 computes a CRC32 integrity header R2 rejects by default | Already handled: `client.py` sets `request_checksum_calculation`/`response_checksum_validation` to `"when_required"` — if this regresses, `python -c "from lib.r2_storage.client import build_client; ..."` and inspect `client.meta.config` |
| A 301 redirect on any request | `region_name` set to a real AWS region instead of `"auto"` | Never override `region_name`; R2 requires `"auto"` |
| `resolve()` raises even though `enabled` is `false` | `bucket` empty AND `enabled:true` was set somewhere (project/job layer) | `validate()` only requires a bucket when `enabled` is `true` — set the bucket or leave `enabled` off |
| A future contributor puts a real key in `config/r2-storage.json` "for convenience" | — | Mechanically rejected by `validate()`'s credential-key-name check; fix is to move it to `.env` |
