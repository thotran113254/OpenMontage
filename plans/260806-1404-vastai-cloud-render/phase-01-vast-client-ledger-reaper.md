# Phase 01 — Vast client, onstart script, transfer, rental ledger + reaper

## Context

- [plan.md](plan.md)
- Orphan-reconciliation prior art to mirror: `server/queue_worker.py:117` (reaper at
  construction), `:125-139` (`_reconcile_orphans` — reads on-disk pid, checks liveness),
  `:250-253` (why the pid is written to disk, not kept in memory).
- 3-layer config prior art: `lib/talking_head_edit/assembly_config.py:85-90` (`resolve`) and
  `:117-129` (`sources_of` — which layer a value came from; the UI needs this).
- Installed SDK: `C:\Users\PC\AppData\Roaming\Python\Python312\site-packages\vastai\`
  (v1.0.4, 147 methods on `VastAI`).

## Overview

- **Priority:** P0 — blocks 02, 03, 04.
- **Status:** completed (2026-08-06)
- Everything that touches the Vast.ai account, the SSH wire, and the money ledger. No render
  logic here. This phase is where the "never leak a rental" guarantee lives.

## Key insights

1. **`create_instance` has no `**kwargs`.** `vastai/api/instances.py:74-79` lists keywords
   explicitly. `ssh=True, direct=True` (as used in the manual CLI run) will raise `TypeError`
   through the SDK. The CLI's `--ssh --direct` is `runtype="ssh_direc ssh_proxy"`
   (`vastai/cli/commands/instances.py:94-110`, `elif args.ssh: runtype = 'ssh_direc ssh_proxy'
   if args.direct else 'ssh_proxy'`). Accepted keywords that matter here: `label`, `onstart_cmd`,
   `price`, `env`, `disk`, `image`, `runtype`, `force`, `cancel_unavail`.
2. **`VastAI.copy()` is unusable and *dangerously* so.** `vastai/utils.py:59 parse_vast_url`
   splits on the first `:`, so `"D:/CODE/x.txt"` → `("D", "/CODE/x.txt")` **without raising** —
   the drive letter becomes an instance id. Downstream `vastai/api/storage.py::copy` PUTs
   `/commands/rsync/` (server-side rsync) and never reads local bytes. Raw `scp` is the only
   correct local↔remote transfer. Encode this as a module-level comment so nobody "simplifies"
   `transfer.py` back onto the SDK.
3. **`VastAI.execute(id, cmd)` is an API command endpoint, not ssh** — it PUTs
   `/instances/command/{id}/` and polls a `result_url`. Raw `ssh` gives real exit codes and honours
   the `~/.no_auto_tmux` fix; use it.
4. **Team API key cannot register SSH keys** (`vastai create ssh-key` → "Team SSH keys are not
   supported"). The public key must be injected by the onstart script. This is not a workaround to
   revisit later — with a team key it is the only path.
5. **Default images hijack every SSH session into tmux** and drop the non-interactive command.
   `touch ~/.no_auto_tmux` must be in the onstart script, not a manual follow-up step, or the very
   first `ssh host 'npm ci'` silently does nothing.
6. **`destroy_instance` via the SDK needs no `-y`** — the interactive prompt is a CLI-only
   behaviour (`vastai destroy instance <id>` hangs without `-y`). Another reason to prefer the SDK
   for lifecycle and ssh/scp for I/O.
7. **The label is the durable state.** Local files can be deleted; the account cannot. Putting
   `intent_id` + deadline epoch in the label makes both the orphan sweep and the double-rent guard
   work with zero local state.

## Requirements

**Functional**
- `search(query, *, order, mode, limit) -> list[Offer]` where `mode ∈ {on-demand, bid, reserved}`
  maps to `VastAI.search_offers(type=...)`. Returns normalized `{id, dph, cpu_cores_effective,
  ram_gb, disk_avail_gb, reliability, geolocation, gpu_name}` — never raw provider dicts leaking
  upward.
- `rent(offer_id, *, intent_id, deadline_epoch, ceiling_dph, image, disk_gb, onstart, bid_price)`
  → adopt-or-create:
  1. `show_instances()`; if any label contains `intent_id`, **adopt** that instance, do not create.
  2. Re-check `offer.dph <= ceiling_dph`; refuse otherwise (`CeilingExceeded`).
  3. Write ledger `pending` record **before** the API call.
  4. `create_instance(..., label=f"openmontage-{intent_id}-until-{deadline_epoch}",
     runtype="ssh_direc ssh_proxy", onstart_cmd=..., price=bid_price or None)`.
  5. Promote ledger record to `active` with the returned instance id.
- `wait_running(instance_id, timeout_s)` — poll `show_instance()` until
  `actual_status == "running"`; surface `ssh_host`/`ssh_port` from the same payload.
- `destroy(instance_id)` — idempotent; "already gone" is success, not an error.
- `ssh(instance_id_or_host, command, timeout)` / `scp_up(local, remote)` / `scp_down(remote, local)`
  in `transfer.py`, all via `subprocess` with `-i <key> -P <port> -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null -o BatchMode=yes`.
- `onstart.build(pubkey, apt_packages, deadline_epoch) -> str` producing, in order:
  `mkdir -p /root/.ssh` → append pubkey → `chmod 700/600` → `touch /root/.no_auto_tmux` →
  `apt-get update && apt-get install -y <packages>` → `nohup sh -c "sleep <secs>; shutdown -h now"
  &`. Every step `|| true`-guarded except the apt install, whose failure must be visible.
- `ledger.reap(dry_run=False) -> ReapReport` — the 4-layer sweep of plan.md, callable standalone
  (`python -m lib.cloud_render.reap`).
- `config.resolve(project=None, job=None) -> dict` — global → project → job, plus
  `sources_of()` for the UI, copying `assembly_config.py:85-129`.

**Non-functional**
- `import lib.cloud_render` must not import `vastai`. `vastai` is imported inside the functions
  that call it (`registry.discover()` imports every module under `tools/`,
  `tools/tool_registry.py:118-134`; a missing dependency must degrade to `status=unavailable`,
  never break preflight for everyone).
- No secret ever written to the ledger, an event log, or a job file. Only the *path* to the key.
- Ledger writes are atomic (write temp + `os.replace`) — a crash mid-write must not lose the
  instance id of a live rental.

## Architecture

`config/cloud-render.json` (global layer, committed):

```jsonc
{
  "enabled": false,                       // opt-in: nothing rents until a human flips this
  "provider": "vastai",
  "image": "node:22-bookworm",
  "disk_gb": 12,
  "pricing_mode": "on-demand",            // on-demand | bid   (bid = preemptible, ~15% cheaper)
  "max_dph_usd": 0.15,                    // hard $/hr ceiling; measured cheapest today 0.0814
  "max_total_usd_per_rental": 0.50,       // hard per-rental spend ceiling
  "max_runtime_minutes": 60,              // watchdog deadline -> instance label + shutdown
  "offer_query": "reliability>0.95 rentable=True cpu_cores_effective>=32",
  "apt_packages": ["ffmpeg", "ca-certificates", "fonts-liberation", "libnss3",
                   "libatk-bridge2.0-0", "libatk1.0-0", "libcups2", "libdrm2", "libgbm1",
                   "libasound2", "libpangocairo-1.0-0", "libxss1", "libxtst6", "libx11-xcb1",
                   "libxcomposite1", "libxdamage1", "libxrandr2", "libgtk-3-0", "xdg-utils"],
  "ssh_key_path": "~/.ssh/vast_new",
  "batch": { "min_jobs": 3, "min_total_render_minutes": 20, "max_wait_minutes": 240 },
  "render_seconds_per_video_second": 1.9  // calibration; colab 44-core measured ~1.84
}
```

`projects/cloud-render/active.json` (one entry per live rental):

```jsonc
{ "rentals": [{
  "intent_id": "a3f9c1", "instance_id": 40179084, "label": "openmontage-a3f9c1-until-1786...",
  "created_at": "2026-08-06T14:04:11Z", "deadline_epoch": 1786000000,
  "dph_usd": 0.0814, "max_total_usd": 0.50, "mode": "on-demand",
  "purpose": "render_now", "job_ids": ["job_x"], "status": "active"
}]}
```

`projects/cloud-render/rentals.jsonl` — append-only: one line per state transition
(`pending`, `active`, `closed`, `reaped`, `failed`) with `actual_usd` and `duration_seconds` on
close. This file is the input for recalibrating `render_seconds_per_video_second` and for the
cost reconciliation in phase 04.

**Reaper decision table** (pure mechanics — no policy):

| Local `active.json` | Labelled instance on account | Past deadline | Action |
|---|---|---|---|
| yes | yes | no | leave alone |
| yes | yes | **yes** | destroy, ledger `reaped`, loud warning |
| yes | **no** | – | ledger `closed` (already gone) |
| **no** | yes | no | destroy **only** if `--adopt-unknown` not set; default = destroy + warn (an OpenMontage-labelled instance with no local record is by definition an orphan) |
| **no** | yes | yes | destroy, ledger `reaped` |

## Related code files

**Create**
- `lib/cloud_render/__init__.py`, `config.py`, `vast_client.py`, `onstart.py`, `transfer.py`,
  `ledger.py`, `reap.py` (`__main__`-style entry)
- `config/cloud-render.json`
- `tests/test_cloud_render_ledger_reaper.py`, `tests/test_cloud_render_config.py`,
  `tests/test_cloud_render_onstart.py`

**Modify**
- `requirements.txt` — `vastai>=1.0.4` (comment: needed only for the cloud render location)
- `.env.example` — cloud-render block: `CLOUD_RENDER_SSH_KEY`, `VAST_API_KEY` (note the SDK also
  auto-reads `~/.config/vastai/vast_api_key`), `CLOUD_RENDER_MAX_DPH_USD` override
- `.gitignore` — confirm `projects/` at `:29` already covers `projects/cloud-render/` (it does;
  no edit needed unless the state root moves)

## Implementation steps

1. `config.py`: load `config/cloud-render.json`, merge project → job per key (copy the
   `_clean`/`resolve`/`sources_of` trio from `assembly_config.py`), then `validate()`:
   `max_dph_usd > 0`, `max_runtime_minutes` in `[5, 240]`, `pricing_mode ∈ {on-demand, bid}`,
   `disk_gb >= 10`. Raise `CloudRenderConfigError` on violation — a bad ceiling must fail loud,
   not default silently.
2. `vast_client.py`: `_client()` does `from vastai import VastAI` inside the function. Wrap each
   call so provider dicts never escape: `Offer`, `Instance` dataclasses. Map
   `pricing_mode="bid"` → `search_offers(type="bid")` + `create_instance(price=bid_price)`.
3. `onstart.py`: pure string builder, no I/O, so it is trivially testable. Assert in a test that
   the output contains `no_auto_tmux` *before* any `apt-get`, and that the pubkey is
   single-quoted (a key with a comment containing spaces must not split the shell word).
4. `transfer.py`: module docstring records insight 2 verbatim with the `parse_vast_url` citation.
   `scp_up` takes a directory and uses `-r`; on Windows use forward-slash-normalized local paths
   but pass them as one argv element (no shell), so spaces in `D:\CODE WITH AI\...` survive.
5. `ledger.py`: `append(record)`, `active()`, `open_pending(intent_id, ...)`, `promote(...)`,
   `close(intent_id, actual_usd, duration_s)`, `reap(...)`. Atomic writes via temp + `os.replace`.
6. `rent()` adopt-or-create ordering exactly as in Requirements — pending record written *before*
   the API call, because the failure we are guarding against is "create succeeded, response lost".
7. `reap.py`: CLI printing a table and exiting non-zero if it had to destroy anything (so a
   Makefile target / CI hook can surface it).
8. Tests: a fake `vastai` module injected via `sys.modules` (never the real SDK). Cover:
   adopt-on-retry, ceiling refusal, deadline reap, ledger-lost sweep, unknown-label orphan,
   `destroy` idempotency, atomic-write survival (kill between temp-write and replace).

## Todo

- [x] `config/cloud-render.json` + `lib/cloud_render/config.py` + validate
- [x] `vast_client.py` with lazy `vastai` import and normalized dataclasses
- [x] `onstart.py` builder (no_auto_tmux before apt; deadline shutdown)
- [x] `transfer.py` raw scp/ssh + the "never use vastai copy" docstring
- [x] `ledger.py` atomic ledger + `active.json` + `rentals.jsonl`
- [x] `rent()` adopt-or-create with `intent_id` label
- [x] `reap()` 4-layer sweep + `python -m lib.cloud_render.reap`
- [x] `requirements.txt`, `.env.example`
- [x] Tests with a fully faked `vastai` module (zero network, zero spend)

## Success criteria

- `python -c "import lib.cloud_render"` works with `vastai` **uninstalled**.
- `python -m lib.cloud_render.reap` on a clean machine prints "0 rentals, 0 destroyed", exit 0.
- Unit test proves: calling `rent()` twice with the same `intent_id` results in **one**
  `create_instance` call and two identical instance ids returned.
- Unit test proves: with `active.json` deleted and a labelled past-deadline instance present,
  `reap()` destroys it.
- Unit test proves: `max_dph_usd` lowered below the offer price → `rent()` raises before any
  `create_instance` call (assert the fake SDK recorded zero create calls).
- `grep -rn "VastAI().copy\|vastai copy" lib/ tools/` returns nothing.

## Risks

| Risk | Mitigation |
|---|---|
| `runtype` string wrong → instance without direct SSH, unreachable | Live smoke (phase 06) asserts `ssh_host`/`ssh_port` present and a `ssh true` round-trip succeeds. Value copied verbatim from `vastai/cli/commands/instances.py:108` |
| Reaper destroys an instance a *human* rented manually | Only ever touches labels starting `openmontage-`; documented that this prefix is reserved |
| Deadline `shutdown -h` billing assumption wrong (stopped instance still bills) | Flagged as an unresolved question; layer 4 is a backstop, layers 1-3 are the real guarantee |
| `apt-get install` failing leaves a rented box with no Chrome deps → render fails after paying for boot | onstart writes `/root/.openmontage-ready` only on success; `remote.py` (phase 02) waits for that file and aborts+destroys if absent within timeout |
| Two OpenMontage processes reap each other's live rentals | Reap only destroys past-deadline or unknown-label instances; a live in-deadline rental present in `active.json` is never touched |

## Security

- API key: read by the SDK from `~/.config/vastai/vast_api_key` or `VAST_API_KEY`. Never logged,
  never written to `rentals.jsonl`, never passed to the rented instance.
- The rented instance is **someone else's hardware**. Nothing secret is uploaded: no `.env`, no
  API keys, no `~/.config`. The kit (phase 02) is an explicit allowlist, not an exclude list.
- Private SSH key stays local; only the public half is injected via onstart.
- `StrictHostKeyChecking=no` is accepted here (host is ephemeral and its key is unknowable in
  advance) but combined with `UserKnownHostsFile=/dev/null` so the local `known_hosts` is never
  polluted with dozens of dead hosts.

## Next

Phase 02 consumes `rent`/`wait_running`/`ssh`/`scp_*`/`destroy` and adds the render kit.

## Unresolved questions

1. Does a *stopped* (`shutdown -h`) Vast instance keep billing disk, and at what rate for 12 GB?
   Decides whether safety layer 4 is "caps the bleed at pennies" or "does nothing".
2. `~/.ssh/vast_new` has comment `vast-ai-20260428` — should the plan generate a fresh dedicated
   keypair instead of reusing an existing one, so revocation is scoped to cloud render?
