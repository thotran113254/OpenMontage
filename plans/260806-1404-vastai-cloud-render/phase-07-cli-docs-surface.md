# Phase 07 — CLI / Makefile / docs surface

## Context

- [plan.md](plan.md), [phase-04](phase-04-tools-registry-cost-tracker.md),
  [phase-05](phase-05-skills-schema-decision-contract.md)
- Autoedit CLI arg surface: `lib/talking_head_edit/cli.py:30-122` (existing flags to sit beside:
  `--scale`, `--skip-render`, `--stages`, `--autopilot`).
- Makefile autoedit targets: `Makefile:138` (`autoedit-server`), `:142` (`autoedit-ui`),
  `:147` (`autoedit-dev`).
- Docs conventions: `docs/talking-head-autoedit.md` (one build end to end),
  `docs/talking-head-autoedit-platform.md` (many-sources layer), `docs/PROVIDERS.md`,
  `docs/ARCHITECTURE.md`.
- The manual predecessor this supersedes: `remotion-composer/colab-render-pipeline.ipynb`.

## Overview

- **Priority:** P2 — nothing above depends on it; it is what makes the capability usable by a human
  without going through the agent.
- **Status:** completed (2026-08-06)

## Key insights

1. **The CLI must not be able to rent without an explicit flag.** `--render-location cloud` is not
   enough on its own — the CLI is also how an unattended `--autopilot` run works
   (`cli.py:75-79`), and autopilot renting money without a human present is the failure mode to
   avoid. So: cloud from the CLI requires `--render-location cloud` **and** either
   `--cloud-offer <id>` (explicit) or `--cloud-yes` (accept the recommended offer), and
   `--autopilot` refuses cloud unless `--cloud-yes` is also present. Same asymmetry as phase 05:
   local is silent, cloud is never silent.
2. **A `--cloud-offers` listing subcommand is the CLI equivalent of `dry_run()`.** It prints the same
   fields as the announce block so a human sees exactly what the agent would see. Reuse
   `dry_run()`; do not write a second formatter.
3. **The reaper needs a human-runnable entry point that is easy to remember.** `make cloud-render-reap`
   plus `python -m lib.cloud_render.reap`. Put the same command in the docs' troubleshooting section
   and in the error message whenever a destroy fails — the moment someone needs it, they are worried
   about money and will not go looking.
4. **The colab notebook should be marked superseded, not deleted.** It documents a working free path
   (Colab high-CPU) that costs nothing, and the benchmark in its header (44-core EPYC, 93 s 1080p in
   ~2m51s) is the calibration source for `render_seconds_per_video_second`. Add a header pointing at
   the automated path; keep the notebook.
5. **The web UI can show cloud renders with no UI change** because cloud progress reuses
   `job.emit("progress", "render", ...)` (phase 02, key insight 4). A UI toggle is a separate,
   optional piece of work — call it out but leave it out (YAGNI). What the UI *does* need eventually
   is a visible "rental active / cost so far" indicator; not in this plan.

## Requirements

**CLI (`lib/talking_head_edit/cli.py`)**
- `--render-location {local,cloud}` (default `local`).
- `--cloud-offer ID` — rent this exact offer.
- `--cloud-yes` — accept the recommended offer non-interactively (required with `--autopilot`).
- `--cloud-offers` — print the offer shortlist + estimate (no rental) and exit.
- `--cloud-max-usd F` — per-run override, clamped by `config.max_total_usd_per_rental`
  (an override may only lower the ceiling, never raise it above config).
- `--cloud-queue` — enqueue this job for batch instead of rendering; print queue state and exit.
- `--cloud-queue-status` / `--cloud-flush [--cloud-offer ID]` — inspect / force-flush.
- Without `--cloud-offer` or `--cloud-yes`, `--render-location cloud` prints the announce block and
  exits non-zero with "re-run with --cloud-offer or --cloud-yes" — never an interactive prompt (the
  CLI is called from `server/queue_worker.py:226` as a subprocess with no tty).

**Makefile**
- `cloud-render-offers` — the dry-run listing.
- `cloud-render-reap` — the orphan sweep; non-zero exit if it destroyed anything.
- `cloud-render-queue` — print queue + `flush_check`.
- `cloud-render-live-test` — the phase 06 live suite, with a printed cost warning and required env
  vars.

**Docs**
- `docs/cloud-render.md` (new, the single human entry point):
  1. What it is, in one paragraph, with the measured economics (overhead ~6 min; $0.0814/hr for
     32 vCPU; a 93 s 1080p render ≈ 3 min on a 44-core box; total for the manual smoke run <$0.01).
  2. Setup: `pip install vastai`, key at `~/.config/vastai/vast_api_key`, ssh keypair,
     `enabled: true` in `config/cloud-render.json`, and every ceiling explained with its default.
  3. When to use it, when not to — including "your footage is uploaded to a third-party machine".
  4. Render-now walkthrough and batch walkthrough, both as real command transcripts.
  5. The four safety layers, in plain language, and **exactly** what to run if something goes wrong:
     `make cloud-render-reap`, then check the Vast.ai console.
  6. Cost accounting: where `rentals.jsonl` lives, how it feeds `cost_log.json`, how to recalibrate
     `render_seconds_per_video_second` from it.
  7. Known limits: autoedit props shape only (not `video_compose`); bid mode caveats; team-key SSH
     constraint.
  8. Troubleshooting table keyed by symptom (tmux banner, VRL/drive letter, `runtype` TypeError,
     destroy prompt hang, apt failure) → cause → fix, each citing the Layer 3 skill.
- `docs/ARCHITECTURE.md` — add `lib/cloud_render/` and the two tools to the module map.
- `AGENT_GUIDE.md` — one line in the routing area pointing at `docs/cloud-render.md` (the Decision
  Contract subsection itself is phase 05).
- `remotion-composer/colab-render-pipeline.ipynb` — header cell: superseded by the automated path,
  link to `docs/cloud-render.md`, keep as the free/manual alternative.
- `docs/talking-head-autoedit.md` — one paragraph in the render section on `--render-location cloud`.

## Related code files

**Create**
- `docs/cloud-render.md`

**Modify**
- `lib/talking_head_edit/cli.py` — the new flags + the "no tty, so no prompt" refusal path
- `Makefile` — four targets (add to `.PHONY` at `:9`)
- `docs/ARCHITECTURE.md`, `docs/talking-head-autoedit.md`, `AGENT_GUIDE.md`
- `remotion-composer/colab-render-pipeline.ipynb` — header cell only
- `README.md` — one line in the capability list, if it enumerates capabilities

## Implementation steps

1. CLI flags with the argument-validation matrix as a single `_validate_cloud_args(args)` function so
   every refusal message is in one place and testable.
2. `--cloud-offers` formats `dry_run()` output using the **same** formatter the announce block uses;
   extract it to `lib/cloud_render/announce.py::format_announce(payload) -> str` so the skill template
   (phase 05), the CLI, and any future UI all render identical text. This also makes phase 05's
   "template contains every dry_run key" test cover the CLI for free.
3. Makefile targets, mirroring the style of `Makefile:138-147`.
4. `docs/cloud-render.md` with real transcripts captured while running the phase 06 live suite —
   invented transcripts drift from reality immediately.
5. Notebook header + doc cross-links last, once the commands are final.

## Todo

- [x] `--render-location`, `--cloud-offer`, `--cloud-yes`, `--cloud-offers`, `--cloud-max-usd`
- [x] `--cloud-queue`, `--cloud-queue-status`, `--cloud-flush`
- [x] `_validate_cloud_args` incl. `--autopilot` requires `--cloud-yes`, and no-tty refusal
- [x] `lib/cloud_render/announce.py::format_announce` shared by skill/CLI
- [x] Makefile: `cloud-render-offers|reap|queue|live-test` (+ `.PHONY`)
- [x] `docs/cloud-render.md` with real transcripts + troubleshooting table
- [x] `docs/ARCHITECTURE.md`, `docs/talking-head-autoedit.md`, `AGENT_GUIDE.md` pointer
- [x] Notebook header marking it superseded (keep the file)
- [x] CLI arg-validation tests

## Success criteria

- `python -m lib.talking_head_edit.cli --cloud-offers` prints the shortlist and creates **zero**
  instances (asserted with the fake SDK).
- `--render-location cloud` without `--cloud-offer`/`--cloud-yes` exits non-zero with an actionable
  message and never prompts.
- `--autopilot --render-location cloud` without `--cloud-yes` is refused.
- `--cloud-max-usd 999` is clamped to `config.max_total_usd_per_rental`, with a warning.
- `make cloud-render-reap` runs on a clean machine, prints a zero-row table, exits 0.
- Every command shown in `docs/cloud-render.md` was actually executed while writing it.
- The troubleshooting table covers all five verified gotchas from phase 05's Layer 3 skill.

## Risks

| Risk | Mitigation |
|---|---|
| `--autopilot` + cloud rents unattended in a loop | Requires `--cloud-yes`; per-rental ceiling still applies; the reaper's deadline bounds any single rental |
| Docs drift from behaviour | Transcripts captured from real runs; the shared `format_announce` keeps the CLI and the skill in sync mechanically |
| Ceiling override used to raise the ceiling | `--cloud-max-usd` may only lower it; clamped with a warning, asserted by test |
| Deleting the colab notebook loses the free path and the benchmark source | Notebook kept, header marks it superseded |
| A UI user sees a cloud render with no cost indicator and no way to stop it | Out of scope, but flagged as an explicit follow-up so it is a known gap, not a surprise |

## Security

- Docs must state, at setup time and again at the "when not to use" section, that footage leaves the
  machine and that the rented box is third-party hardware.
- Docs must not include a real API key, ssh host, or instance id from any run.
- The setup section points at `~/.config/vastai/vast_api_key` / `.env`; it must not suggest putting
  the key on a command line (shell history).

## Next

Plan complete. Follow-ups worth their own plans: cloud render for the `video_compose` props shape;
a UI rental/cost indicator; fixing the `binary:` dependency-prefix bug.

## Unresolved questions (plan-level)

1. **Is cloud render meant to be usable unattended at all** (`--autopilot`, or the server queue
   auto-flushing a batch overnight)? The plan currently requires an explicit human `--cloud-yes` for
   every unattended path. A true "offload the batch overnight" workflow would need a standing
   pre-approval with a spend cap — a business decision, not a technical one. **Needs your call.**
2. **Default `pricing_mode`.** Plan defaults to `on-demand` ($0.0814/hr) and treats `bid`
   ($0.069/hr, ~15% cheaper, preemptible) as explicit opt-in. Is a 15% saving worth the preemption
   risk for long batches, given incremental download limits the loss to one job?
3. **Ceiling defaults.** `max_dph_usd: 0.15`, `max_total_usd_per_rental: 0.50`,
   `max_runtime_minutes: 60` are proposals derived from today's $0.0814/hr measurement, not your
   numbers. Confirm or replace.
4. **Batch thresholds.** `min_jobs: 3`, `min_total_render_minutes: 20`, `max_wait_minutes: 240` are
   likewise proposals. `max_wait_minutes: 240` in particular encodes "a queued job may sit for 4
   hours" — is that acceptable?
5. **`config/cloud-render.json` `enabled: false` by default** — so nothing can rent until a human
   flips it. Confirm this is the wanted friction.
6. **Does a stopped Vast instance keep billing disk?** (from phase 01) Decides whether safety
   layer 4 is meaningful.
7. **Reuse `~/.ssh/vast_new` or mint a dedicated cloud-render keypair?** (from phase 01)
8. **Queue file concurrency: lock file or last-write-wins?** (from phase 03) Recommend lock file.
9. **Explicit `render_report.render_location` field vs `metadata` blob?** (from phase 05)
   Recommend explicit field.
10. **Is the `video_compose` props shape needed soon?** It is the pipeline path (as opposed to the
    autoedit path) and needs asset collection + `file://` rewriting — roughly one more phase.
