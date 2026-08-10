# Vast.ai (Layer 3)

Raw provider knowledge for renting a short-lived Vast.ai CPU box to render a composition. This is
mechanics — *how* the SDK actually behaves, verified against the installed package. For *when* to
rent, the announce/approval contract, and the decision-log shape, read the Layer 2 skill first:
`skills/core/cloud-render.md`. This skill is referenced from `vast_cloud_render`'s `agent_skills`
field — read it before writing any code that touches the `vastai` SDK or an `onstart` script.

## SDK method map

Real module surface implemented under `lib/cloud_render/` (phase 01) — read these functions
instead of re-deriving Vast.ai usage from scratch:

| Function | Module | What it does |
|---|---|---|
| `search(query, *, mode, order, limit, api_key)` | `lib/cloud_render/vast_client.py` | Wraps `VastAI.search_offers(query=..., type=mode, order=..., limit=...)`; returns normalized `Offer` dataclasses |
| `rent(offer_id, offer_dph, *, intent_id, deadline_epoch, ceiling_dph, image, disk_gb, onstart, bid_price=None, api_key)` | `lib/cloud_render/vast_client.py` | Adopt-or-create: looks for an already-labelled instance for `intent_id` first, else re-checks the ceiling and calls `VastAI.create_instance(...)`. Writes the ledger `pending` record *before* the API call. |
| `wait_running(instance_id, timeout_s, *, poll_interval_s, api_key)` | `lib/cloud_render/vast_client.py` | Polls `VastAI.show_instance(id=...)` until `actual_status == "running"` |
| `destroy(instance_id, *, api_key)` | `lib/cloud_render/vast_client.py` | Wraps `VastAI.destroy_instance(id=...)`; idempotent — an already-gone instance is treated as success |
| `list_labelled_instances(*, api_key)` | `lib/cloud_render/vast_client.py` | Wraps `VastAI.show_instances()`, filtered to labels with the `openmontage-` prefix; returns raw dicts (not normalized) so the label string survives |
| `parse_label(label)` | `lib/cloud_render/vast_client.py` | Parses `"openmontage-{intent_id}-until-{deadline_epoch}"`; returns `None` for anything not ours |
| `onstart.build(pubkey, apt_packages, deadline_epoch, now_epoch=None)` | `lib/cloud_render/onstart.py` | Pure string builder for the `onstart_cmd` script (no I/O, no SDK) |
| `transfer.ssh(host, port, key_path, command, *, timeout, input=None)` | `lib/cloud_render/transfer.py` | One non-interactive command via raw `ssh` subprocess — real exit code. `input` is piped to the remote command's stdin (never argv) |
| `transfer.push_kit(kit_dir, key, *, settings=None)` | `lib/cloud_render/transfer.py` | tar.gz `kit_dir` and upload to R2 under `key`; skips the upload (content-addressed) if the key already exists |
| `transfer.fetch_to_remote(host, port, key_path, key, remote_dir, *, timeout, ttl, settings=None)` | `lib/cloud_render/transfer.py` | Presigns a GET for `key`, hands the URL to the rental on stdin, remote `curl \| tar -xz` into `remote_dir` |
| `transfer.push_from_remote(host, port, key_path, remote_path, key, *, ttl, timeout, settings=None)` | `lib/cloud_render/transfer.py` | Presigns a PUT for `key`, hands the URL to the rental on stdin, remote `curl -X PUT --upload-file` |
| `ledger.reap(dry_run=False, *, adopt_unknown=False, api_key=None, now_epoch=None)` | `lib/cloud_render/ledger.py` | 4-layer orphan sweep (see decision table in the module docstring); only ever touches `openmontage-`-labelled instances |
| `setup_key.ensure_keypair(key_path=DEFAULT_KEY_PATH)` / `setup_key.public_key_text(key_path)` | `lib/cloud_render/setup_key.py` | Generates/reads the dedicated cloud-render ed25519 keypair |
| `config.resolve(project, job, global_path=None)` / `config.validate(config)` | `lib/cloud_render/config.py` | 3-layer (global → project → job) config merge with fail-loud validation |

`lib/cloud_render/__init__.py` is deliberately empty of submodule imports — `import lib.cloud_render`
must never require the `vastai` package to be installed. Import the submodule you need directly
(`from lib.cloud_render import vast_client`), and only inside a function body if that submodule
itself needs the SDK (`vast_client._client()` does the lazy `from vastai import VastAI`).

## Five verified gotchas

Each one: the symptom you will meet first, then the fix — reading the fix without meeting the
symptom first won't stick.

### 1. Team API key cannot create SSH keys

**Symptom**: `vastai create ssh-key` (CLI) or the equivalent SDK call returns "Team SSH keys are
not supported," and you have no way to register a public key with the account before renting.

**Fix**: Don't register a key with Vast.ai at all. Inject the public key directly via the
`onstart` script instead (`onstart.build()` writes it to `/root/.ssh/authorized_keys`). This is the
only path available to a team key — not a workaround to "fix properly" later.

### 2. Default images force tmux and silently drop non-interactive commands

**Symptom**: `ssh root@host -p PORT 'npm ci'` returns immediately with no output and no error —
the command never ran, and there's no obvious reason why.

**Fix**: The default Vast.ai images hijack every SSH session into `tmux`, which swallows a
non-interactive command. Run `touch /root/.no_auto_tmux` as one of the very first lines of the
`onstart` script, before anything else that could hang.

### 3. `create_instance` has no `ssh=`/`direct=` kwargs

**Symptom**: Calling `VastAI.create_instance(id=..., ssh=True, direct=True)` (the pattern from the
`vastai` CLI's `--ssh --direct` flags) raises `TypeError: create_instance() got an unexpected
keyword argument 'ssh'`.

**Fix**: `instances.create_instance`'s real signature has no `**kwargs` catch-all at all — it lists
keywords explicitly (`env`, `price`, `label`, `onstart_cmd`, `force`, `cancel_unavail`, `runtype`,
...). The CLI's `--ssh --direct` combination maps onto `runtype="ssh_direc ssh_proxy"`. Pass that
string, not two booleans.

### 4. Never use `VastAI.copy()` / `vastai copy` — Windows paths silently mis-parse

**Symptom**: A "copy" call to move a local render kit to the instance appears to succeed (no
exception) but no local bytes ever reach the box, or a Windows path like `D:/CODE/kit.zip` produces
a confusing "instance not found" error where the instance id somehow became `"D"`.

**Fix**: `vastai/utils.py::parse_vast_url` splits its argument on the *first* `:`, so
`"D:/CODE/kit.zip"` parses as `("D", "/CODE/kit.zip")` — the drive letter becomes the instance id
candidate, silently, with no exception raised. Downstream, `vastai/api/storage.py::copy` PUTs
`/commands/rsync/`, which is a *server-side* rsync between two rented instances — it never reads
local bytes at all, so there is no local↔remote path through the SDK's `copy()` regardless of
platform. Local↔remote transfer now goes through Cloudflare R2 presigned URLs instead of raw `scp`
(`transfer.push_kit` / `transfer.fetch_to_remote` / `transfer.push_from_remote`) — the rented box is
ephemeral compute, never storage, and never holds a long-lived R2 credential; it only ever sees a
short-lived presigned URL, handed to it on stdin (never argv) so it cannot leak via `ps` or Vast.ai's
own instance logs. Likewise, `VastAI.execute(id, cmd)` PUTs an async job-queue endpoint polled via a
`result_url` — it is not a shell and does not give a real exit code; use raw `ssh` (`transfer.ssh`)
for commands.

### 5. CLI `destroy instance` prompts without `-y`; the SDK method does not

**Symptom**: A shell script that calls `vastai destroy instance <id>` hangs waiting for a `y/n`
confirmation that never comes in a non-interactive context (CI, background job).

**Fix**: The interactive confirm prompt is CLI-only behavior, not part of the underlying API call.
`VastAI.destroy_instance(id=...)` (used by `vast_client.destroy`) does not prompt at all — call the
SDK method directly rather than shelling out to the CLI. `destroy()` is also idempotent: destroying
an instance that's already gone is treated as success (404 / "not found" caught and swallowed), not
an error.

## Measured baselines (2026-08-06, 32-vCPU offer)

Quote these numbers instead of guessing when writing an announce block or estimating cost/time:

| Metric | Value |
|---|---|
| On-demand price | ~$0.0814/hr |
| Bid (interruptible) price | ~$0.069/hr — ~15% cheaper, the **default** pricing mode in this system |
| Boot time | ~60 seconds |
| `npm ci` for the composer kit | ~9.2 seconds |
| Reserved pricing | Offered no discount at this scale/duration on the offers tested — not worth querying by default |
| SSH keypair | Dedicated ed25519 pair at `~/.ssh/openmontage_cloud_render`, generated by
`python -m lib.cloud_render.setup_key` — **not** a shared/reused key (a compromise or rotation then
only affects cloud-render rentals) |

No real API key, instance id, or ssh host from any manual verification run is reproduced in this
skill. The offer id `#40179084` that appears as an illustrative example elsewhere in this
documentation set was for an instance that has already been destroyed.
