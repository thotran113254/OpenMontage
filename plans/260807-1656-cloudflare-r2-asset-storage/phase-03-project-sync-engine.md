# Phase 03 — Diff-sync engine + CLI

## Context links

- Job layout: `lib/talking_head_edit/job_store.py:26-31` — `JOBS_ROOT = projects/autoedit-jobs`,
  `PROJECTS_ROOT = projects/autoedit`; per-job paths at `:167-213`
- Project layout: `lib/talking_head_edit/project_store.py:46,65-77` — `sources/`, `jobs/`,
  `project.json`
- Classic pipeline layout: `AGENT_GUIDE.md` → Project Directory Convention
  (`projects/<name>/{artifacts,assets,renders}`)
- `projects/` is gitignored: `.gitignore:29`
- Blocked by: [phase 01](phase-01-r2-client-and-config.md)

## Overview

- **Priority:** P2
- **Status:** completed
- **Effort:** 3h
- A directory→R2-prefix mirror that uploads only what changed, plus a standalone CLI so
  the whole feature is usable before any pipeline hook exists.

## Key insights

1. **ETag cannot be used to detect "already uploaded".** For a multipart upload R2's ETag
   is `md5(md5(part1)+…+md5(partN))-N`, not the file's md5. Any diff logic keyed on ETag
   will re-upload every large video forever.
2. **`head_object`-per-file costs a Class B op per file per sync.** With a job dir of
   ~40 files synced after every stage, that is ~280 ops/job — still pennies, but it also
   costs a network round-trip each, which is the real cost (latency on a residential link).
   A **local manifest** makes the no-op sync free and instant.
3. **`size + mtime_ns` is the cheap gate; hash only on mismatch.** Hashing a 4 GB source
   file on every sync would dominate runtime.
4. **The manifest must not upload itself** — `.r2sync.json` is in the default `exclude`
   list from phase 01.
5. **Three different "project" shapes exist** in this repo (`projects/<name>/` classic
   pipeline, `projects/autoedit/<id>/` talking-head project, `projects/autoedit-jobs/<id>/`
   job). The sync engine must be shape-agnostic: it mirrors *a directory* to *a prefix*.
   Mapping directory→prefix is the caller's job (phase 04).

## Requirements

**Functional**
- `plan_sync(local_dir, prefix, settings) -> SyncPlan` — pure, no network, no upload.
  Returns `upload[]`, `skip[]`, `total_bytes`, `object_count`.
- `apply_sync(plan, settings, on_progress=None) -> SyncResult` — performs uploads, updates
  the manifest incrementally (crash-safe: write manifest after each file, not at the end).
- `pull(prefix, local_dir, settings)` — reverse direction, for restoring a project onto a
  fresh machine. Overwrite policy: skip if local file exists with matching size+md5,
  else download; never silently clobber a larger local file without `--force`.
- Deletion is **not** mirrored by default. A file removed locally stays in R2 (it is an
  archive). `--prune` opt-in flag deletes orphaned remote keys.
- `manifest.record_external_upload(local_dir, relpath, key)` — marks a file as already
  present in R2 **without** uploading it, by stat-ing the local copy and writing the
  manifest entry. Required by phase 05: after a cloud render, `final.mp4` reaches R2 from
  the Vast.ai box, so the job-end sync must skip re-uploading the same bytes from the
  user's home uplink. This is the single largest bandwidth saving in the whole plan —
  without it the R2 transfer rework is partly pointless.
- Announce: first ever sync (manifest absent) prints a full block — bucket, masked
  endpoint, object count, total MB, estimated `$/month` storage, "egress is free, storage
  is billed monthly until you delete it". Subsequent syncs print one line.

**Non-functional**
- A no-change sync of a 40-file job dir completes in < 1 s with **zero** network calls.
- `max_upload_mb_per_sync` guard aborts before the first byte if the plan exceeds it,
  printing what would have been uploaded.
- Windows path handling: keys always POSIX (`as_posix()`), never backslashes.

## Architecture

### Data flow

```
local_dir ──scan(exclude globs)──► [(relpath, size, mtime_ns)]
                                            │
              .r2sync.json ─────────────────┤ compare size+mtime_ns
                                            ▼
                                    changed? ──yes──► md5(file) ──► differs? ──► SyncPlan.upload
                                        │ no                            │ no
                                        └──────────► SyncPlan.skip ◄────┘
                                            │
                                            ▼
                 apply_sync ──► client.upload_file(ExtraArgs={"Metadata":{"local-md5":…}})
                                            │
                                            ▼
                          .r2sync.json += {relpath: {size, mtime_ns, md5, key, uploaded_at}}
```

### Manifest — `<local_dir>/.r2sync.json`

```json
{
  "version": 1,
  "bucket": "openmontage-assets",
  "prefix": "projects/autoedit-jobs/j_abc123",
  "files": {
    "final.mp4": {"size": 214748364, "mtime_ns": 1754500000000000000,
                  "md5": "9f8e…", "uploaded_at": 1754500123.4}
  }
}
```

If `bucket`/`prefix` in the manifest differ from the resolved settings, the manifest is
treated as stale and **discarded** (full re-plan) — prevents a bucket switch from
producing a silently empty sync.

### Key layout

```
projects/autoedit-jobs/j_20260807_abc/final.mp4     # durable archive (this phase)
projects/autoedit/p_shoot1/sources/s0_take-1.mp4    # durable archive (this phase)
projects/hidden-math-of-nature/renders/final.mp4    # durable archive (this phase)
render-kits/composer/<kit_hash>/kit.tar.gz          # transient, phase 05
render-kits/jobs/<job_id>/...                       # transient, phase 05
```

One bucket, two top-level prefixes (Q5): `projects/**` is the durable archive this phase
owns; `render-kits/**` is transient cloud-render staging owned by phase 05. `prefix`
(default `projects`) comes from config; the caller passes the rest. The sync engine never
touches `render-kits/**` — `--prune` must therefore be scoped to the prefix it was given,
never the bucket root.

### CLI — `python -m lib.r2_storage.cli`

```
--push <dir> --prefix <key-prefix>   # dry-run by default
--yes                                # actually upload
--pull <key-prefix> --dest <dir>
--list <key-prefix>
--prune                              # delete orphaned remote keys (with --yes)
--presign <key> [--expires 86400]
--status                             # config + credential state, no network
```
Dry-run-by-default mirrors the cloud-render philosophy: the first thing a human sees is
what *would* happen and what it costs.

## Related code files

**Create**
- `lib/r2_storage/manifest.py` (~110 LOC) — read/write/compare, md5 helper
- `lib/r2_storage/sync.py` (~180 LOC) — `plan_sync`, `apply_sync`, `pull`, `prune`, announce
- `lib/r2_storage/cli.py` (~150 LOC) — argparse frontend

**Modify** — none (phase 01 owns `config.py`/`client.py`; do not touch them here).

## Implementation steps

1. `manifest.py`: `load(dir)`, `save(dir, data)` (atomic — write `.tmp` + `os.replace`),
   `file_md5(path, chunk=8MiB)`, `entry_matches(entry, stat)`,
   `record_external_upload(dir, relpath, key)`.
2. `sync.py::_scan(local_dir, exclude)` — walk with `pathlib`, apply `fnmatch` globs
   against the POSIX relpath, skip symlinks, skip `.r2sync.json`.
3. `plan_sync()` — build `SyncPlan` dataclass. Pure; unit-testable with no client.
4. Cost/announce helper: `estimate_storage_usd_per_month(bytes)` = `bytes/1e9 * 0.015`;
   `estimate_class_a_usd(n)` = `n * 4.50/1e6`. Print both in the announce block.
5. `apply_sync()` — guard against `max_upload_mb_per_sync` **before** uploading; upload
   each file; after each success write the manifest entry immediately.
6. `pull()` and `prune()`.
7. `cli.py` with the flags above; `--push` without `--yes` prints the plan and exits 0.
8. Manual verification against a real bucket (user must create `openmontage-assets`
   first): push a small throwaway dir, re-run to confirm 0 uploads, `--presign` and open
   the URL, then `--prune` to clean up.

## Todo list

- [x] 1. `manifest.py` — atomic read/write + md5 + `record_external_upload`
- [x] 2. `_scan` with exclude globs, POSIX keys, symlink skip
- [x] 3. `plan_sync()` pure planner + `SyncPlan`
- [x] 4. Cost estimator + announce block (first-sync vs one-liner)
- [x] 5. `apply_sync()` with size guard + incremental manifest writes
- [x] 6. `pull()` + `prune()`
- [x] 7. `lib/r2_storage/cli.py`, dry-run default
- [x] 8. Manual push/re-push/presign/prune against the real bucket

## Success criteria

- `python -m lib.r2_storage.cli --status` prints config + which env vars are set (never a
  value), makes no network call, exits 0 on a clean checkout with `enabled:false`.
- `--push <dir> --prefix test/x` with no `--yes` uploads nothing and prints
  `N objects, X MB, ~$Y/month`.
- Second `--push --yes` of an unchanged dir reports `0 uploaded, N skipped` and issues
  **zero** S3 API calls (assert in phase 06 with a call-counting stub).
- Touching one file's content (not just mtime) makes exactly that one file re-upload.
- Touching only mtime (`Path.touch()`) triggers an md5 read but **not** an upload.
- Keys in R2 use `/` on Windows — verify with `--list`.
- A plan exceeding `max_upload_mb_per_sync` aborts with a message naming the config key,
  before any upload.
- After `record_external_upload(dir, "final.mp4", key)`, the next `plan_sync` puts
  `final.mp4` in `skip[]`, not `upload[]` — with no network call.
- `--prune` on prefix `projects/x` never lists or deletes anything under `render-kits/`.

## Risk assessment

| Risk | L×I | Mitigation |
|---|---|---|
| Diff logic re-uploads everything each run → slow + Class A cost | Med × Med | size+mtime gate; success criterion asserts 0 API calls on no-change |
| Interrupted sync leaves manifest claiming files that never uploaded | Med × High | Manifest written **after** each successful upload, never before; atomic replace |
| Bucket/prefix change silently skips everything (stale manifest) | Low × High | Manifest records bucket+prefix; mismatch discards it |
| Accidental `--prune` deletes an archived render not present locally | Low × **High (data loss)** | `--prune` requires `--yes`, prints every key to be deleted first, and is off by default; deletion is never part of a normal sync |
| Huge accidental upload (wrong `--push` dir) | Med × Med | `max_upload_mb_per_sync` hard stop + dry-run default |
| Windows backslash keys create unusable object names | Med × Med | `as_posix()` everywhere; success criterion checks `--list` output |
| md5 of a 10 GB file blocks the CLI for minutes | Low × Low | Only hashed when size **or** mtime changed; chunked read |

**Rollback:** delete the three new modules and any `.r2sync.json` files. Remote objects
remain but are inert (nothing reads them yet). No local pipeline behaviour changes.

## Security considerations

- Presigned URLs printed by `--presign` go to stdout only. Warn in the CLI output that
  the URL is a bearer token and should not be pasted into a shared log/issue.
- `--status` prints env var **names** and set/unset, never values.
- Uploaded objects inherit the bucket's access setting. Q3 locked **public delivery via a
  custom domain / Public Development URL** as the sharing path — enabling that is a manual
  Cloudflare dashboard action by the user, which this plan never automates. Consequence to
  document: once the bucket is public, every object is readable by anyone holding the key,
  including raw footage under `render-kits/**`. Keys are unguessable in practice, but this
  is a genuine exposure change, not a formality.
- The manifest contains file paths and md5s — no secrets. Still lives under gitignored
  `projects/`.

## Next steps

Unblocks phase 04 (hooks) and phase 05 (Vast.ai transfer spike).
