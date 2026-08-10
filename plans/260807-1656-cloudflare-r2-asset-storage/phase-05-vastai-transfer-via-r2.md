# Phase 05 — Vast.ai transfer via R2 presigned URLs (replaces scp)

## Context links

- Transport to replace: `lib/cloud_render/transfer.py:62-82` (`scp_up`, `scp_down`) and
  `transfer.ssh:57-61` (**kept** — SSH still runs the render command)
- Call sites: `lib/cloud_render/remote.py:207` (per-item kit up), `:242` (result down),
  `:298` (shared composer kit up)
- Verification that must keep working: `remote.py:250-267` — `MIN_OUTPUT_BYTES`,
  `probe_duration`, `DURATION_TOLERANCE_RATIO` drift check
- Content addressing already exists: `lib/cloud_render/kit.py:82-88` (`_hash_kit`),
  `:117` (`kit_hash=`); `JobKitManifest` fields at `:57-68`
- Onstart script builder: `lib/cloud_render/onstart.py:15,45,54` (`apt-get install -y {packages}`)
- apt package list: `config/cloud-render.json` → `apt_packages` (**no `curl` today**)
- Layer 3 skill documenting scp: `.agents/skills/vastai/SKILL.md:24-25,80`
- Blocked by: phases [01](phase-01-r2-client-and-config.md),
  [02](phase-02-r2-storage-tool.md), [03](phase-03-project-sync-engine.md)

## Overview

- **Priority:** P2
- **Status:** completed (post-review fix: see plan.md's "Post-review fixes" — `record_external_upload` timing bug found and fixed; composer-kit push-before-rent cost claim tracked as an open follow-up decision, not fixed)
- **Effort:** 4h
- Replace binary `scp` transfer between the local machine and the rented Vast.ai box with
  R2 as the intermediary. The rental pulls its kit from R2 and pushes its output to R2,
  both via short-lived presigned URLs. SSH remains, but only to run commands.

## Locked decision (user, 2026-08-07)

> "Khi chạy render thì cần đưa lên vast render và lưu về R2 chứ, khâu nào ở vast thì lưu ở
> vast và lưu trữ lâu dài là R2, upload video lên chuẩn bị rồi chạy render mới lên vast -
> trước đó là R2."

R2 is the durable store and the source/sink for cloud-render transfer. The Vast.ai box is
ephemeral compute, never storage. **`scp` is removed from this path.**

Security constraint retained: the rented box gets **no long-lived R2 credentials** — it is
destroyed and re-leased to strangers. Presigned URLs only; no `boto3`, no `awscli`, no
`.env` on the box.

## Why this is a net win

The old `scp_up` ran **while the rental meter was ticking**: home-uplink upload time was
billed at the rental's $/hr. Now:

- kit upload local→R2 happens **before** renting (unbilled),
- R2→rental runs at datacenter speed with **zero egress fee**,
- the shared composer kit is content-addressed by the existing `kit_hash`, so a repeat
  rental of an unchanged kit re-downloads from R2 instead of re-uploading from home,
- `curl -C -` makes transfers resumable; `scp` had no resume.

## Key insights

1. **Presigned URLs are per-object; a kit is a directory.** Tar the kit to a single
   `.tar.gz`, upload one object, hand the box one presigned GET. Remote unpacks with
   `tar -xzf`. `tar` is in the Debian base; **`curl` is not guaranteed** — add it to
   `apt_packages` (idempotent, installed during onstart before the ready marker).
2. **A presigned URL in an SSH command lands in the remote's `ps` output and possibly in
   Vast.ai's own instance logs.** Pass it on **stdin**, never in argv. `transfer.ssh()`
   currently has no stdin plumbing (`transfer.py:47-54` `_run` calls `subprocess.run`
   without `input=`) — add an `input` parameter.
3. **TTL should be scoped per operation, not per rental.** The kit GET is needed once,
   right after readiness; the result PUT is needed at the end of *one* item. Presign the
   PUT immediately before that item's render starts, with TTL = `item.timeout_s` + margin.
   A batch's last item does not need a 90-minute-old URL.
4. **The output must not land at the durable archive key before it is verified.**
   `remote.py:250-267` rejects renders that are too small or whose duration drifts. If the
   box PUT straight to `projects/autoedit-jobs/<id>/final.mp4`, a failed render would
   overwrite the archive with garbage. Flow: PUT to a **staging** key → local downloads and
   verifies → **server-side `CopyObject`** to the archive key (no bytes through local) →
   delete staging.
5. **After the server-side copy, the archive object already exists** — phase 04's job-end
   sync must not re-upload `final.mp4` from home. `manifest.record_external_upload()`
   (phase 03) writes the manifest entry so the next `plan_sync` skips it. This is where
   the real bandwidth saving is realised.
6. **`curl` exit codes are the new error surface.** `22` = HTTP >= 400 (a 403 here almost
   always means the presign expired); `28` = timeout; `56` = receive failure. Map these to
   readable Vietnamese errors — a bare "exit 22" inside a paid rental is hostile.
7. **`scp` has exactly 3 call sites and 2 doc references** — no hidden consumers
   (`grep -rn "scp_up\|scp_down"` outside `.venv` returns `remote.py:207,242,298` and
   `.agents/skills/vastai/SKILL.md:24-25,80`).

## Requirements

**Functional**
- `transfer.push_kit(kit_dir, key) -> str` — tar.gz + upload to R2, return the key. Skip
  the upload when an object with the same content hash already exists (`head_object`).
- `transfer.fetch_to_remote(host, port, key_path, key, remote_dir)` — presign GET, ssh
  `curl | tar -xz` with the URL on stdin.
- `transfer.push_from_remote(host, port, key_path, remote_path, key, ttl)` — presign PUT,
  ssh `curl -X PUT --upload-file` with the URL on stdin.
- `transfer.ssh(..., input=None)` — new stdin parameter.
- `remote.py` call sites rewritten; the render command itself (`_stream_command`) unchanged.
- `scp_up` / `scp_down` **deleted**.
- Verification chain at `remote.py:250-267` preserved byte for byte in behaviour.

**Non-functional**
- No R2 credential, access key, or `.env` content ever reaches the rented box.
- A rental must fail **loudly and fast** on a transfer error, never idle while billing.
- Adding `curl` must not slow readiness materially (it is one small apt package).

## Architecture

### Key layout (single bucket, prefixes — Q5 unchanged)

```
render-kits/composer/<kit_hash>/kit.tar.gz     # shared, content-addressed, reused across rentals
render-kits/jobs/<job_id>/kit.tar.gz           # per-item, ephemeral
render-kits/jobs/<job_id>/out/final.mp4        # STAGING — unverified render output
projects/autoedit-jobs/<job_id>/final.mp4      # ARCHIVE — only after local verification
```

`render-kits/**` is transient (pruned manually per Q4); `projects/**` is the durable archive.

### Flow

```
LOCAL                                  R2                                 VAST.AI RENTAL
  │                                                                             │
  ├─ tar.gz composer kit ──────────► render-kits/composer/<hash>/kit.tar.gz     │
  │   (skipped if hash already exists)                                          │
  ├─ rent instance (money starts) ──────────────────────────────────────────────►
  ├─ _wait_ready (ssh)                                                          │
  ├─ presign GET ──┐                                                            │
  ├─ ssh + stdin ──┴──────────────────────────────────────────────► curl | tar -xz
  ├─ ssh npm ci ────────────────────────────────────────────────────────────────►
  │                                                                             │
  │  per item:                                                                  │
  ├─ tar.gz job kit ──────────────► render-kits/jobs/<id>/kit.tar.gz            │
  ├─ presign GET ──► ssh + stdin ──────────────────────────────────► curl | tar -xz
  ├─ ssh render (streaming progress, unchanged) ────────────────────────────────►
  ├─ presign PUT (ttl = item.timeout_s + margin)                                │
  ├─ ssh + stdin ──────────────────────────────────────────────────► curl -X PUT ──►
  │                                        render-kits/jobs/<id>/out/final.mp4  │
  ├─ download (free egress) ◄──────────────┘                                    │
  ├─ verify: size > MIN_OUTPUT_BYTES, probe_duration, drift ratio               │
  ├─ CopyObject staging ──► projects/autoedit-jobs/<id>/final.mp4  (server-side)│
  ├─ manifest.record_external_upload(...)   # phase-04 sync will skip it        │
  └─ delete staging key                                                         │
```

### Presign TTL policy

| Object | TTL | Rationale |
|---|---|---|
| composer kit GET | 30 min | used once, right after readiness |
| job kit GET | 30 min | used immediately before that item's render |
| result PUT | `item.timeout_s + 600s`, clamped to ≤ 6h | must survive exactly one render |

All well under the 7-day SigV4 ceiling clamped in phase 01.

## Related code files

**Create** — none in `lib/r2_storage/` beyond what phases 01-03 built, except:
- `lib/r2_storage/presign.py` (~60 LOC) — `get_url(key, ttl)`, `put_url(key, ttl)`,
  `copy_object(src_key, dst_key)`, `object_exists(key)`. Kept separate so
  `lib/cloud_render/` imports one small module rather than the sync engine.

**Modify**
- `lib/cloud_render/transfer.py` — delete `scp_up`/`scp_down`; add `push_kit`,
  `fetch_to_remote`, `push_from_remote`; add `input=` to `ssh()`/`_run()`; rewrite the
  module docstring (keep the vastai-SDK `copy()`/`execute()` warning — still valid for
  `ssh()` — drop the scp-specific paragraphs)
- `lib/cloud_render/remote.py` — rewrite `:207`, `:242`, `:298`; add staging→verify→copy→
  delete around the existing verification block
- `lib/cloud_render/onstart.py` — no logic change; `curl` arrives via `apt_packages`
- `config/cloud-render.json` — add `"curl"` to `apt_packages`
- `.agents/skills/vastai/SKILL.md:24-25,80` — replace the scp rows with the R2 presigned
  functions; this is the Layer 3 skill agents read, stale rows here mislead future agents
- `tests/test_cloud_render_remote.py` — update the fake transport
- `docs/cloud-render.md` — transfer section

**Delete**
- `transfer.scp_up`, `transfer.scp_down` (functions, not files)

## Implementation steps

1. `lib/r2_storage/presign.py` — `get_url`, `put_url`, `copy_object`, `object_exists`.
   `put_url` uses `generate_presigned_url("put_object", ...)`; verify the remote `curl -X
   PUT --upload-file` matches the signed method and that **no extra headers** are signed
   (a signed `Content-Type` that curl does not send produces a 403 — omit it).
2. `transfer.py`: add `input: str | None = None` to `_run()` and `ssh()`, passing through
   to `subprocess.run(..., input=input)`.
3. `transfer.push_kit(kit_dir, key)`: `tarfile` gzip into a temp file, `object_exists(key)`
   → skip if present, else `upload_file`. Return `(key, bytes, skipped)`.
4. `transfer.fetch_to_remote(...)`: presign GET, then
   ```
   ssh(..., "mkdir -p DIR && read -r U && curl -fsSL --retry 3 -C - \"$U\" -o /tmp/k.tgz "
            "&& tar -xzf /tmp/k.tgz -C DIR && rm -f /tmp/k.tgz",
       input=url + "\n")
   ```
   Assert the URL is **not** in the argv list (unit-testable).
5. `transfer.push_from_remote(...)`: presign PUT, ssh
   `read -r U && curl -fsS --retry 3 -X PUT --upload-file REMOTE "$U"`, URL on stdin.
6. `_curl_error(returncode, stderr) -> str` — map 22/28/56 to Vietnamese messages naming
   the likely cause (presign hết hạn / timeout / mất kết nối).
7. Rewrite `remote.py:298` (shared composer kit): `push_kit(composer.kit_dir,
   f"render-kits/composer/{composer.kit_hash}/kit.tar.gz")` **before** `_wait_ready`
   ideally, but at minimum before `npm ci`; then `fetch_to_remote(... REMOTE_KIT_DIR)`.
   Failure here stays a shared-setup `CloudRenderError` (raises, kills the batch) —
   unchanged semantics.
8. Rewrite `remote.py:207` (per-item kit) the same way into `remote_job_dir`.
9. Rewrite `remote.py:242` (result): `push_from_remote(remote_out, staging_key, ttl)` →
   `r2_storage` download to `local_tmp` → **existing** size/duration/drift checks →
   `copy_object(staging, archive_key)` → `record_external_upload` → delete staging.
   A verification failure must **not** copy to the archive, and should still delete
   staging (in a `finally`) so failed renders do not accumulate.
10. Add `"curl"` to `apt_packages` in `config/cloud-render.json`.
11. Update `tests/test_cloud_render_remote.py`, `.agents/skills/vastai/SKILL.md`,
    `docs/cloud-render.md`.
12. Live validation: one real rental with a short job. Confirm identical output vs a
    pre-change render, and confirm rental wall-clock dropped.

## Todo list

- [x] 1. `lib/r2_storage/presign.py` (get/put/copy/exists)
- [x] 2. `ssh()`/`_run()` accept `input=`
- [x] 3. `push_kit` — tar.gz + content-addressed skip
- [x] 4. `fetch_to_remote` — presign GET, URL on stdin, curl|tar
- [x] 5. `push_from_remote` — presign PUT, URL on stdin
- [x] 6. `_curl_error` mapping
- [x] 7. Rewrite `remote.py:298` shared composer kit
- [x] 8. Rewrite `remote.py:207` per-item kit
- [x] 9. Rewrite `remote.py:242` result: stage → verify → server-side copy → prune staging
- [x] 10. `curl` into `apt_packages`
- [x] 11. Update tests, `.agents/skills/vastai/SKILL.md`, `docs/cloud-render.md`
- [x] 12. Live rental validation

## Success criteria

- `grep -rn "scp_up\|scp_down" lib/ tools/ tests/ .agents/` → **no matches**.
- `grep -rn "SECRET_ACCESS_KEY\|ACCESS_KEY_ID" lib/cloud_render/` → **no matches**
  (no credential ever reaches the box).
- Unit test asserts the presigned URL appears in the ssh **stdin**, never in `argv`.
- A render with a deliberately expired presign fails with a message naming "presign hết
  hạn", and the rental is destroyed rather than left idling.
- A render whose duration drifts past `DURATION_TOLERANCE_RATIO` leaves
  `projects/autoedit-jobs/<id>/final.mp4` **absent** in R2 and the staging key deleted.
- After a successful cloud render, phase 04's job-end sync reports `final.mp4` as
  **skipped** (already uploaded), not re-uploaded.
- Re-renting with an unchanged composer kit reports `skipped: true` from `push_kit` —
  zero home-uplink bytes for the shared kit.
- Byte-identical `final.mp4` vs the same job rendered before this phase.

## Risk assessment

| Risk | L×I | Mitigation |
|---|---|---|
| Transfer breaks **inside a paid rental**, box idles while billing | Med × **High** | Every transfer failure raises `CloudRenderError` immediately; the existing reaper (`lib/cloud_render/reap.py`) and `deadline_epoch` still destroy the box; `_curl_error` makes the cause obvious |
| Presign expires mid-batch | Med × High | Per-operation TTL presigned **immediately before** use, not once per rental |
| Presigned URL leaks via `ps` / Vast.ai instance logs | Med × High | URL passed on stdin, never argv; unit test asserts it |
| Signed headers mismatch → 403 on PUT | Med × Med | Step 1 signs no extra headers; live validation in step 12 is the real check (moto will not catch an R2 signature quirk) |
| Corrupt render overwrites the durable archive | Low × **High (data loss)** | Staging key + server-side copy only after the existing verification passes |
| Removing `scp` breaks a consumer we did not find | Low × High | `grep` verified: 3 call sites + 2 doc lines, no other consumers |
| `curl` missing on the image → every rental fails at fetch | Low × High | Added to `apt_packages`, installed before the ready marker; step 12 validates on a real box |
| `render-kits/**` accumulates and bills forever | Med × Low | Job staging deleted in `finally`; composer kits are content-addressed and few; manual prune per Q4 |
| Output crosses R2 twice (up from box, down to local) | — | Accepted: R2 egress is free, and it removes a home-uplink upload of the same file |

**Rollback:** this phase is a replacement, not additive, so rollback is
`git revert` of the `lib/cloud_render/` commit. Cloud render is `enabled: false` by
default (`config/cloud-render.json:2`), so a regression here cannot affect a user who has
not opted into cloud rendering at all. Recommend landing it as its own commit for exactly
that reason.

## Backwards compatibility

- No artifact/schema change; `RemoteRenderResult` keeps its fields.
- In-flight rentals from a previous version are unaffected (nothing persists across
  rentals except the ledger).
- Any user with `enabled: true` must re-run onstart on a **new** rental to get `curl` —
  existing long-lived rentals are not patched. Acceptable: rentals are ephemeral by design.
- `.agents/skills/vastai/SKILL.md` must be updated in the same commit, otherwise agents
  reading Layer 3 will call functions that no longer exist.

## Security considerations

- **No long-lived credentials on the rented box** — the whole reason for presigned URLs.
  Enforced mechanically by the `grep` success criterion.
- Presigned PUT is scoped to exactly one key, never a prefix, and expires with the item.
- Presigned URLs never enter `events.jsonl`, `job.json`, a log line, or an argv.
- The kit tarball contains the user's footage and props. It sits in R2 under
  `render-kits/**` until manually pruned — documented in `docs/cloud-render.md`.
- SSH keypair policy unchanged (`config/cloud-render.json:ssh_key_path`, dedicated key).

## Next steps

Feeds phase 06 (tests + `docs/cloud-render.md`). After landing, update the planner memory
note `.claude/agent-memory/planner/vastai-sdk-verified-gotchas.md:20`, which still says
"Use raw `scp`".
