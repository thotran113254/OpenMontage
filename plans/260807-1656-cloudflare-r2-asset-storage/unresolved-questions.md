# Decision log — Cloudflare R2 asset storage

All five open questions were answered by the user on 2026-08-07. **Nothing is blocking.**
This file is now the decision record; do not silently reverse anything below.

## Q1 — Vast.ai transfer: RESOLVED → **use R2, drop `scp`**

User: *"Khi chạy render thì cần đưa lên vast render và lưu về R2 chứ, khâu nào ở vast thì
lưu ở vast và lưu trữ lâu dài là R2, upload video lên chuẩn bị rồi chạy render mới lên
vast - trước đó là R2."*

R2 is the durable store and the source/sink for cloud-render transfer. Flow: local → R2 →
(rent) → box pulls kit → render → box pushes output → R2 → local verifies. `scp_up`/
`scp_down` are deleted; SSH remains for running commands only.

Security constraint retained: **no long-lived R2 credentials on the rented box** (it is
destroyed and re-leased). Presigned URLs only — no `boto3`, no `awscli`, no `.env` there.

Implemented in [phase 05](phase-05-vastai-transfer-via-r2.md), which changed from a
decision gate into a real implementation phase (4h).

## Q2 — Sync timing: RESOLVED → **job end only**

No per-stage sync call site. `sync_after_stage` is cut entirely (YAGNI); a grep success
criterion in [phase 04](phase-04-pipeline-sync-hooks.md) guards against a second call site
creeping in. `auto_sync` still ships `false` per the repo's opt-in-cloud philosophy.

## Q3 — Delivery access: RESOLVED → **public bucket / custom domain**

Not presigned-only. `delivery_url` returns `https://<CLOUDFLARE_R2_PUBLIC_BASE_URL>/<key>`
when that env var is set, else falls back to a presigned URL **with a warning**.
`presigned_url` / `presigned_put_url` remain strictly signed — the Vast.ai transfer path
must never accidentally receive a public URL.

**Consequence to be aware of, not a blocker:** a public bucket makes every object readable
by anyone holding the key, including raw footage inside kit tarballs under
`render-kits/**`. Keys are unguessable in practice (job ids, content hashes). If this
becomes unacceptable, the fix is a second bucket for kits — which reopens Q5 and should be
raised with the user, not decided silently.

## Q4 — Retention: RESOLVED → **no automatic lifecycle policy**

Manual `--prune`. Phase 05 deletes its own `render-kits/jobs/<id>/` staging after each
render; content-addressed composer kits are few and are intentionally kept for reuse.

## Q5 — Bucket count: RESOLVED → **one bucket, prefixes**

`openmontage-assets` holds both `projects/**` (durable) and `render-kits/**` (transient).
Confirmed still correct after Q1: transient kits and long-term archive differ only by
prefix.

---

## Manual prerequisites (user, Cloudflare dashboard — code cannot do these)

1. **Create bucket `openmontage-assets`.** Still not created. Phases 01 and 02 build and
   unit-test without it; phase 03's manual verification needs it.
2. **Enable public access or attach a custom domain**, then set
   `CLOUDFLARE_R2_PUBLIC_BASE_URL` in `.env`. Only needed for Q3 public delivery —
   everything else (sync, cloud-render transfer) works without it, and `delivery_url`
   degrades to a presigned URL plus a warning until it is set.

## Assumptions worth re-checking during implementation

- The exact `botocore.config.Config` kwarg position for the checksum settings is tagged
  `[UNVERIFIED]` in [phase 01](phase-01-r2-client-and-config.md); step 1 of that phase
  resolves it empirically before any other code is written.
- `moto` does not emulate R2's stricter multipart rules or its signature quirks. The
  opt-in live smoke test is the only real validation — run it once after phase 01.
