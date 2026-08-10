---
title: "Cloud render on rented Vast.ai instances (render-now + batch)"
description: "Render OpenMontage Remotion/ffmpeg compositions on a rented Vast.ai CPU instance and destroy it immediately after, with a render-now mode and a durable batch queue that amortizes boot overhead across several jobs on one rental."
status: completed
priority: P2
effort: 38h
branch: main
tags: [cloud-render, vastai, remotion, cost-governance, batch-queue, safety]
created: 2026-08-06
---

# Cloud render on rented Vast.ai instances

## Goal

Add a **cloud render location** next to local rendering: rent a cheap CPU instance on Vast.ai,
run the exact same Remotion command there, pull the deliverable back, destroy the instance.
Two user-facing modes:

1. **Render now** — one job, rent immediately, destroy when done.
2. **Batch** — queue jobs durably; rent once and render N jobs on that single rental when a
   threshold is hit or the user forces a flush, so the ~5-7 min fixed overhead is paid once.

Ground truth for every Vast.ai claim in this plan: the manual end-to-end run on 2026-08-06 —
offer 40179084, i9-14900KF 32 vCPU, $0.0814/hr, `node:22-bookworm`, disk 12 GB, kit 631 KB,
`npm ci` 9.2 s, 60-frame Remotion smoke render 14.6 s, total lifetime ~7-8 min, cost <$0.01 —
cross-checked this session against the installed SDK
(`site-packages/vastai/`, v1.0.4) and against this repo's own render code. Three claims from that
run turned out to be wrong or imprecise; see "Corrections" below.

## Non-goals (YAGNI)

- No multi-cloud abstraction / `cloud_render_selector`. One provider (Vast.ai). A second
  provider auto-appears in the same `capability="cloud_render"` family if ever added.
- No GPU rendering. Remotion/ffmpeg here are CPU-bound; the bundled GPU is ignored.
- No `@remotion/lambda` path (declared in `remotion-composer/package.json:16`, used nowhere —
  see phase 05 "Alternatives rejected").
- No change to `edit_decisions` — render *location* is an execution decision, not an editorial one.
- No replacement of `server/queue_worker.py` (in-memory, local-only, single-job). Cloud batch
  needs durability across restarts, so it gets its own on-disk queue.

## Phases

| # | Phase | Status | Blocked by | Effort |
|---|---|---|---|---|
| 01 | [Vast client, onstart, transfer, rental ledger + reaper](phase-01-vast-client-ledger-reaper.md) | completed | — | 6h |
| 02 | [Render kit packager + single-job remote render](phase-02-render-kit-and-remote-render.md) | completed | 01 | 8h |
| 03 | [Durable batch queue + one-rental flush](phase-03-batch-queue-and-flush.md) | completed | 02 | 6h |
| 04 | [BaseTool wrappers + registry + cost_tracker wiring](phase-04-tools-registry-cost-tracker.md) | completed | 03 | 5h |
| 05 | [Agent instruction layer: skills, schema, AGENT_GUIDE](phase-05-skills-schema-decision-contract.md) | completed | 01 (naming only) | 4h |
| 06 | [Tests: mocked-SDK unit + opt-in live smoke](phase-06-tests-mocked-and-live-gate.md) | completed — live suite implemented, deliberately never executed | 04 | 6h |
| 07 | [CLI / Makefile / docs surface](phase-07-cli-docs-surface.md) | completed | 04 | 3h |

Phase 05 touches only `skills/`, `schemas/`, `AGENT_GUIDE.md`, `docs/` — **file-disjoint from
01-04**, so it can run in parallel with 02/03 once phase 01 has frozen module/tool names.

## Architecture

```
lib/cloud_render/                       Python = mechanics + persistence only
├── config.py        3-layer resolve: config/cloud-render.json -> project -> job
│                    (same shape as lib/talking_head_edit/assembly_config.py:85)
├── vast_client.py   thin wrap: search_offers / create_instance / show_instance /
│                    show_instances / destroy_instance. Lazy `import vastai`.
├── onstart.py       builds the --onstart script (pubkey inject, ~/.no_auto_tmux,
│                    apt deps, deadline shutdown)
├── transfer.py      raw scp/ssh subprocess. NEVER vastai copy (see phase 01)
├── ledger.py        durable rental ledger + orphan reaper (3 layers)
├── kit.py           packs {props.json, public staging, composer src} -> tar
├── remote.py        one rental: wait-running -> upload -> npm ci -> render(s) -> pull
└── queue.py         durable batch queue file + threshold report

tools/video/vast_cloud_render.py     class VastCloudRender  (capability="cloud_render",
                                     provider="vastai", runtime=API) — costs money
tools/video/cloud_render_queue.py    class CloudRenderQueue (capability="cloud_render",
                                     provider="openmontage", runtime=LOCAL) — free

config/cloud-render.json             global layer (ceilings, thresholds, image, apt deps)
projects/cloud-render/               runtime state (gitignored via .gitignore:29)
├── rentals.jsonl                    append-only audit of every rental
├── active.json                      live rentals the reaper must watch
└── batch-queue.json                 durable batch queue

skills/core/cloud-render.md           Layer 2 — when/how, decision contract, announce template
.agents/skills/vastai/SKILL.md        Layer 3 — raw Vast.ai gotchas + SDK method map
```

Data flow, render-now: `dry_run(offers)` → announce → user approval → `cost_tracker.reserve`
→ create (labelled, deadline in label) → ledger `active` → poll `actual_status=="running"`
→ scp kit → ssh `npm ci` → ssh `npx remotion render` → scp back → verify bytes exist →
`destroy_instance` in `finally` → ledger `closed` → `cost_tracker.reconcile(actual)`.

Batch flush: same, but upload N kits, `npm ci` **once**, render **sequentially** (Remotion wants
every core — same reasoning as `server/queue_worker.py:3-5`), and **pull each output as it
finishes** so a mid-flush failure loses one job, not all N.

## Verified facts this plan depends on

| Fact | Verified at |
|---|---|
| `registry.discover()` imports **every** module under `tools/` | `tools/tool_registry.py:118-134` → `vastai` must be imported lazily inside `execute()`, mirroring `tools/video/talking_head_autoedit.py:3-6` |
| `check_dependencies` only understands `cmd:` / `env:` / `python:` | `tools/base_tool.py:212-231`. `talking_head_autoedit.py:41` uses `binary:ffmpeg`, which is **silently ignored** — the new tools must use `cmd:` |
| A new capability string auto-appears in the preflight menu | `tools/tool_registry.py:357-377` iterates whatever `provider_menu()` returns; no whitelist |
| `require_approval_for_new_paid_tool: true` already blocks first paid use | `config.yaml:14` + `tools/cost_tracker.py:134-141` — no new approval mechanism needed |
| `decision_log.category` is a **closed enum** + `additionalProperties:false` | `schemas/artifacts/decision_log.schema.json` → adding `render_location_selection` requires a schema edit |
| `render_report` allows free-form `metadata` but is otherwise `additionalProperties:false` | `schemas/artifacts/render_report.schema.json` |
| Autoedit render is already a self-contained bundle (`--props` + `--public-dir`) | `lib/talking_head_edit/stages/render.py:150-159` — the clean cloud target |
| Pipeline render embeds absolute `file:///D:/...` URIs in props | `tools/video/video_compose.py:1697-1704` — needs path rewrite + asset collection (phase 02) |
| Local render caps concurrency at 8 | `lib/talking_head_edit/stages/render.py:31,47` — a 32-vCPU box would be throttled unless parameterized |
| `remotion-composer/public/` is 349 MB; kit inputs (src+package.json+lock+tsconfig) are ~623 KB | measured `du -sh` this session |

## Corrections to the manual ground truth (verified in the installed SDK)

1. **`create_instance(..., ssh=True, direct=True)` will raise `TypeError`.**
   `vastai/api/instances.py:74-79` has an explicit keyword list and **no `**kwargs`**. The CLI's
   `--ssh --direct` maps to `runtype="ssh_direc ssh_proxy"`
   (`vastai/cli/commands/instances.py:94-110`). Use `runtype=`, not `ssh=`/`direct=`.
   The signature does accept `label=`, `onstart_cmd=`, `price=` (bid), `env=`, `force=`,
   `cancel_unavail=` — all four are load-bearing in this plan.
2. **`VastAI.copy()` on Windows is worse than "throws VRLException" — it silently mis-parses.**
   `vastai/utils.py:59` `parse_vast_url("D:/CODE/x.txt")` returns `("D", "/CODE/x.txt")` —
   drive letter becomes an *instance id*, no exception. `vastai/api/storage.py::copy` then PUTs
   `/commands/rsync/` server-side, which never reads local bytes anyway. **Never use it**; raw
   `scp` only. (Confirms and sharpens the manual finding.)
3. **`VastAI.execute(id, cmd)` is not an ssh exec** — it PUTs `/instances/command/{id}/` and polls
   a `result_url` (`vastai/api/instances.py`). Use raw `ssh` for remote commands so the
   `~/.no_auto_tmux` fix and exit codes behave predictably.

## Safety design (non-negotiable — detail in phase 01)

Four independent layers, because the worst failure mode is a forgotten rental bleeding money:

1. **`try/finally` destroy** in-process.
2. **Local ledger reaper** — `active.json` holds `{instance_id, created_at, deadline_epoch}`;
   reaper runs at every entry point (both tools' `execute` *and* `dry_run`, CLI, server startup),
   mirroring `JobQueue._reconcile_orphans` (`server/queue_worker.py:117,125-139`).
3. **Stateless remote sweep** — the instance label is `openmontage-{intent_id}-until-{epoch}`, set
   at create time. `show_instances()` + label parse destroys anything past its deadline **even if
   all local state is lost**. Also the idempotency check: a retry finds its own label and adopts
   instead of double-renting.
4. **On-instance deadline shutdown** — the onstart script schedules `shutdown -h` at the deadline
   and wraps the render in `timeout`. Worst case compute stops without any local process alive.
   (Caveat to verify against a real bill: a *stopped* Vast instance still bills disk.)

Plus: hard `$/hr` ceiling and hard `max_total_usd` per rental, both in `config/cloud-render.json`,
both re-checked immediately before `create_instance`, both required in the announce message.

## Risk register (plan-level)

| Risk | L×I | Mitigation | Phase |
|---|---|---|---|
| Orphaned rental bleeding money | M×**Critical** | 4-layer reaper above; live test asserts zero instances after run | 01, 06 |
| Cloud output differs from local (softer/desynced) | M×High | Cloud must reuse the **same flag set** as `render.py:150-159`; byte/PSNR gate vs a local render of the same props | 02, 06 |
| Real spend in CI | L×**Critical** | Unit tests mock the SDK module entirely; live test gated on `VAST_LIVE_TEST=1` **and** a ceiling env var | 06 |
| Double-rent on retry | M×High | client-side `intent_id` in label + `show_instances()` adopt-before-create | 01 |
| Interruptible (bid) preemption mid-render | M×Med | **bid is the default** (user decision, 2026-08-06 — see "Decisions locked"); mitigate via requeue-and-reannounce retry, not by avoiding bid | 02, 05 |
| Agent silently defaults to cloud | M×High | `execute()` refuses without an `offer_id` obtained from `dry_run` + a `render_location_selection` decision entry | 04, 05 |
| Upload cost/time for media-heavy jobs | H×Med | kit excludes `public/` (349 MB); per-job staging only; report kit size in the announce | 02 |
| 32-vCPU box throttled to 8 workers | H×Med | parameterize `MAX_CONCURRENCY` (`render.py:31`) instead of hardcoding | 02 |

## Decisions locked by user (2026-08-06)

These override any conflicting proposal elsewhere in this plan/phase files — phase authors must
reconcile against these, not the other way round (see risk register row below, now stale).

1. **No autopilot, ever.** Every rental — render-now AND each batch flush — requires an announce +
   explicit approval. No standing pre-approval, no unattended overnight batch in this scope.
2. **Default `pricing_mode` is `interruptible` (bid), not on-demand.** ~15% cheaper
   (~$0.069/hr vs ~$0.081/hr at today's rate). This reverses the risk-register mitigation
   "on-demand is the default" — phase 02/05 must instead design for **preemption as the normal
   case**, not the rare one: detect a preempted mid-render job (poll `actual_status`, watch for
   ssh/scp failure mid-transfer), requeue that job (don't lose it), retry with a fresh `dry_run` →
   announce → approval (a retry is a new rental, so still gated by decision #1 above), and only
   fall back to on-demand pricing after user approval of that specific fallback (never silently).
3. **Ceilings accepted as proposed**: `$0.15/hr` rate ceiling, `$0.50/rental` total ceiling,
   `60 min` max runtime — all three re-checked immediately before `create_instance` and stated in
   every announce message.
4. **Feature ships disabled by default** (`config/cloud-render.json` → `enabled: false`). Opt-in
   per project/job only; never auto-selected over local rendering without the user turning it on
   first.

Phase 02/05 authors: add explicit preemption-retry handling for the batch flush path (queued jobs
must survive one preempted rental and resume on the next approved rental, not be dropped).

## Post-implementation: code review + fix (2026-08-06)

Independent `code-reviewer` pass (report:
`reports/code-reviewer-260806-1726-cloud-render-review-report.md`) scored 7/10 and found one
**critical** gap: `max_total_usd` (the tool's required per-rental spend ceiling input) was
validated once against `config.max_total_usd_per_rental` in `VastCloudRender.execute()` and then
discarded -- never forwarded to `render_now()`/`flush()`. Real enforcement was only
`max_dph_usd x max_runtime_minutes`, and since `max_runtime_minutes` is overridable up to 240 min
per-call with no cross-check, a caller could legally construct a rental costing more than the
ceiling with nothing catching it.

Fixed same-day, before finalize: `remote.render_now()` and `queue.flush()` now both accept
`max_total_usd` and clamp the rental's own deadline via a new `remote._clamp_deadline_minutes()`
helper (`effective_minutes = min(requested_minutes, max_total_usd / offer.dph * 60)`, refusing
below a 5-minute floor rather than renting a box that cannot finish booting).
`VastCloudRender.execute()` now forwards the validated `max_total_usd` through to both calls. Also
added, as defense-in-depth per the review's high-priority findings: `config["enabled"]` is now
re-checked inside `render_now`/`flush` themselves (not only at the tool layer), and
`server/app.py`'s startup `lifespan` now runs a best-effort `ledger.reap()` (phase 01's design
required a reap at every entry point including server startup; `server/` had zero cloud-render
references before this fix). 7 new tests added
(`TestRenderNowCeilingEnforcement`/`TestFlushCeilingEnforcement` in
`tests/test_cloud_render_remote.py`/`tests/test_cloud_render_batch_flush.py`) proving the clamp,
the refusal floor, and the `enabled` precondition. Full suite re-verified green: 1549 passed, 0
failed.

Not fixed, flagged instead (review's other high-priority note, lower financial risk): the
`offer_id` a human approves via `dry_run_ref` is not the exact offer `render_now`/`flush` end up
renting -- both do their own fresh `search()` + pick-cheapest-eligible internally, independent of
the approved `offer_id`. The picked offer is always <= `max_dph_usd`, so this is a transparency gap
("did the user see exactly this machine") rather than a money-safety gap, but it means "no
unilateral substitution" is only partially mechanical today. Worth a follow-up if this feature
sees real use.

## Unresolved questions

Collected at the end of each phase; the plan-level ones are in
[phase-07](phase-07-cli-docs-surface.md#unresolved-questions). Items already resolved above
(autopilot, pricing_mode default, ceilings, enabled-default) are closed — do not re-ask.
Still open: dedicated automation keypair vs reuse `~/.ssh/vast_new`, queue-file lock vs
last-write-wins (recommend lock), explicit `render_report.render_location` field vs `metadata`
blob (recommend explicit), whether a `video_compose` pipeline-shape integration is needed now or
later (recommend later — out of initial scope), whether a stopped Vast instance still bills disk
(verify against a real invoice before relying on safety layer 4).
