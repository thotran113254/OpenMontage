# Phase 05 — Agent instruction layer: skills, schema, Decision Communication Contract

## Context

- [plan.md](plan.md)
- The contract this phase implements: `AGENT_GUIDE.md` — "Decision Communication Contract" at
  `:129`, "Announce Before Execution" at `:133-141`, "Ask Before Major Changes" at `:143-154`,
  "Present Both Composition Runtimes (HARD RULE)" at `:156-170` (the pattern to imitate),
  "Escalate Blockers Explicitly" at `:181-191`, "No Unilateral Substitutions" at `:202-212`,
  "Python = tools + persistence" at `:119`.
- Schemas: `schemas/artifacts/decision_log.schema.json` (closed `category` enum,
  `additionalProperties: false`), `schemas/artifacts/render_report.schema.json`
  (`additionalProperties: false`, free-form `metadata`),
  `schemas/artifacts/edit_decisions.schema.json` (`render_runtime` at `:248-252`).
- Layer-2 skill homes: `skills/core/` (`ffmpeg.md`, `remotion.md`, `hyperframes.md`).
  Layer-3 homes: `.agents/skills/<name>/SKILL.md`.
- Executive-producer skills already reference cost governance:
  `skills/pipelines/*/executive-producer.md` (10 files mention `cost_log`/budget).

## Overview

- **Priority:** P0 for correct behaviour — without this the tools exist but the agent has no rules.
- **Status:** completed (2026-08-06)
- **File-disjoint from phases 01-04** (touches only `skills/`, `.agents/skills/`, `schemas/`,
  `AGENT_GUIDE.md`), so it runs in parallel with 02/03 once phase 01 froze the tool/module names.

## Key insights

1. **`render_location` is a sibling of `render_runtime`, not a child of it.** `render_runtime`
   (`edit_decisions.schema.json:248-252`) answers *which engine*; location answers *which machine*.
   They are orthogonal — cloud Remotion and local Remotion are the same runtime. So: **do not touch
   `edit_decisions`.** Adding a field there would invalidate nothing today but would wrongly imply
   the edit stage owns a decision it does not make, and every existing artifact would need a value.
2. **`decision_log.category` is a closed enum with `additionalProperties: false`.** Logging a
   `render_location_selection` decision is impossible without a schema edit. This is the one
   required schema change.
3. **`render_report` needs the provenance, and `metadata` is not good enough.** The schema is
   `additionalProperties: false` but has a free-form `metadata: {type: object}`, so cloud facts
   *could* be stuffed there with zero churn. Recommend an explicit optional `render_location` object
   instead: "which machine rendered the deliverable, at what price, for how long" is an auditable
   fact a reviewer should be able to find by field name, not by digging in a blob. The zero-churn
   alternative is documented below so the user can choose.
4. **The runtime-presentation rule at `AGENT_GUIDE.md:156-170` is the template to copy, not to
   extend.** It is a HARD RULE with a required 3-part presentation and a mandated `decision_log`
   entry. Location deserves the same shape but is a *weaker* rule in one respect and *stronger* in
   another: weaker because local is a legitimate silent default (it costs nothing and is the status
   quo), stronger because the *cloud* choice spends real money and moves footage off-machine, so it
   can never be silent.
5. **Asymmetric default is the honest design.** Local → no announcement needed (nothing consequential
   happens). Cloud → full announce + explicit approval, every time for the first rental in a session
   and every time the *offer or ceiling changes*. This mirrors how `cost_tracker.py:134-141` gates
   only the first paid use of a tool.
6. **Batch-vs-render-now is a "changing from sample mode to batch mode" case.** `AGENT_GUIDE.md:153`
   already requires asking before that switch. So making batch a project default is explicitly a
   must-ask, not a nice-to-ask.

## Requirements

**Schema changes**
- `schemas/artifacts/decision_log.schema.json` — add `"render_location_selection"` to the
  `category` enum. No other change. Backwards compatible (enum widening; existing logs still valid).
- `schemas/artifacts/render_report.schema.json` — add optional `render_location`:
  ```jsonc
  "render_location": {
    "type": "object",
    "description": "Where this deliverable was actually rendered. Absent = local machine.",
    "required": ["location", "provider"],
    "properties": {
      "location":      { "type": "string", "enum": ["local", "cloud"] },
      "provider":      { "type": "string" },
      "instance_id":   { "type": "string" },
      "offer_id":      { "type": "string" },
      "pricing_mode":  { "type": "string", "enum": ["on-demand", "bid", "reserved"] },
      "dph_usd":       { "type": "number", "minimum": 0 },
      "rental_seconds":{ "type": "number", "minimum": 0 },
      "actual_cost_usd": { "type": "number", "minimum": 0 },
      "cpu_cores":     { "type": "integer" },
      "batch_size":    { "type": "integer", "minimum": 1 },
      "ledger_ref":    { "type": "string", "description": "Path to rentals.jsonl" }
    },
    "additionalProperties": false
  }
  ```
  Optional ⇒ every existing `render_report` stays valid; absent means local.
- `edit_decisions.schema.json` — **no change** (insight 1).

**Layer 2 skill — `skills/core/cloud-render.md`** must contain:
1. When cloud is worth it, in numbers, not adjectives: overhead ~6 min paid per rental; the crossover
   is roughly "estimated local render > ~2× the overhead, or ≥3 queued jobs". Local is the default.
2. The **announce block template** (mandatory, verbatim fields — all of these come straight out of
   `dry_run()`):
   ```
   RENDER LOCATION: cloud (Vast.ai)  — needs your approval before I rent anything
     Provider / mode : Vast.ai, on-demand   (interruptible available at $0.069/hr, can be
                       preempted mid-render — I do not recommend it for renders over ~2 min)
     Offer           : #40179084 — 32 vCPU i9-14900KF, MX, reliability 0.98
                       (the bundled RTX 4060 is unused; this render is CPU-bound)
     Price           : $0.0814/hr   |  ceiling in config: $0.15/hr
     Scope           : batch of 4 jobs, one rental        (sample|batch)
     Time            : ~6 min overhead + ~23 min renders = ~29 min
     Cost            : ~$0.048 total  |  per-rental ceiling: $0.50
     Separately      : ~$0.19 if each job rented its own instance
     Locally         : ~44 min, $0
     Data            : your footage (812 KB kit) is uploaded to a third-party machine and
                       deleted with the instance. Render locally if that is not acceptable.
     Safety          : instance is destroyed on completion, on error, and by a watchdog at
                       the 60-minute deadline even if this process dies.
   Approve? (yes / render locally / pick a different offer)
   ```
3. The 3-part presentation rule when the user has not chosen a location: one sentence on what cloud
   is best at *for this job*, one honest tradeoff, then the recommendation with reasoning — the same
   shape as `AGENT_GUIDE.md:160-165`.
4. Mandatory `decision_log` entry `category: "render_location_selection"` with **both** options
   present in `options_considered` (local and cloud), `user_approved: true` for cloud.
   A cloud render logged with only one option considered is a critical reviewer finding — same
   standard as `AGENT_GUIDE.md:166`.
5. **Never silently substitute location.** Cloud unreachable / `enabled:false` / offer gone / over
   ceiling → escalate with the 5-part blocker structure (`AGENT_GUIDE.md:183-189`), recommend local,
   wait. Falling back to local *without asking* is forbidden even though local is cheaper and safer:
   it changes render time and the user may be waiting on a deadline.
   Symmetrically: never fall *forward* to cloud because local is slow.
6. **Batch policy** (the thresholds are data in `config/cloud-render.json`; the decision is the
   agent's): call `cloud_render_queue.flush_check` → read `thresholds_met` → decide → announce →
   flush. Making batch a project default requires asking (`AGENT_GUIDE.md:153`).
7. **Cost governance checklist** (mirrors what `skills/pipelines/*/executive-producer.md` already
   does for other paid tools): `estimate` → `reserve` (expect `ApprovalRequiredError` on first use,
   `cost_tracker.py:134-141`) → user approval → `approve_tool("vast_cloud_render")` → `execute` →
   `reconcile(actual)` from `ToolResult.cost_usd`. On pre-rental failure: `refund`.
8. **Orphan hygiene:** run `cloud_render_queue` op `reap` at the start of any session that mentions
   cloud render, and report anything it destroyed. An orphan is a bug worth telling the user about.
9. **What cloud render does NOT support yet:** the `video_compose` props shape (phase 02) — quote the
   refusal reason so the agent recognizes it rather than retrying.

**Layer 3 skill — `.agents/skills/vastai/SKILL.md`** (raw provider knowledge, referenced from
`agent_skills`): SDK method map (`search_offers(query, type, order, limit)`,
`create_instance(id, image, disk, label, onstart_cmd, price, runtype, env)`, `show_instance`,
`show_instances`, `ssh_url`, `destroy_instance`, `stop_instance`, `label_instance`) and the five
verified gotchas:
1. Team API key cannot create SSH keys — inject the pubkey via `onstart`.
2. Default images force tmux and drop non-interactive commands — `touch ~/.no_auto_tmux` in
   `onstart`.
3. `create_instance` has **no** `ssh=`/`direct=` kwargs (no `**kwargs` at all);
   `--ssh --direct` ⇒ `runtype="ssh_direc ssh_proxy"`.
4. `VastAI.copy()` / `vastai copy` silently mis-parse Windows drive letters
   (`parse_vast_url("D:/x") → ("D", "/x")`) and go through a server-side rsync that never reads
   local bytes — use `scp`.
5. CLI `destroy instance` prompts without `-y`; the SDK method does not prompt.
Plus the measured baselines (boot ~60 s, `npm ci` 9.2 s, on-demand $0.0814/hr vs bid $0.069/hr,
reserved offered no discount at this scale/duration) so the agent quotes numbers instead of guessing.

**`AGENT_GUIDE.md`** — new subsection "Render Location — Local vs Cloud" inside the Decision
Communication Contract, after "Present Both Composition Runtimes". Short: states the asymmetric
default, points to `skills/core/cloud-render.md`, names the `render_location_selection` decision
category, and states that renting is a paid consequential action requiring explicit approval.

## Related code files

**Create**
- `skills/core/cloud-render.md`
- `.agents/skills/vastai/SKILL.md`

**Modify**
- `schemas/artifacts/decision_log.schema.json` — one enum entry
- `schemas/artifacts/render_report.schema.json` — one optional object
- `AGENT_GUIDE.md` — one subsection (~15 lines) in the Decision Communication Contract
- `docs/PROVIDERS.md` — add Vast.ai row (cloud render), since it is a new paid provider

**Do not modify**
- `schemas/artifacts/edit_decisions.schema.json` (insight 1)
- `pipeline_defs/*.yaml` — cloud render is a tool/location, not a pipeline stage; no manifest churn

## Implementation steps

1. Schema edits first (they are the contract everything else references), then validate every
   existing artifact in `tests/fixtures/` and any `projects/*/artifacts/` still on disk against the
   updated schemas — an enum widening plus an optional field should validate 100%; prove it.
2. Write `skills/core/cloud-render.md` with the announce template as a literal fenced block so the
   agent copies it rather than paraphrasing (paraphrase is how required fields go missing).
3. Write `.agents/skills/vastai/SKILL.md` with the five gotchas and the SDK map. Every gotcha gets
   the symptom first, then the fix — the agent will meet the symptom before it knows the cause.
4. `AGENT_GUIDE.md` subsection. Keep it short and point at the skill; the guide is already long and
   its job is routing.
5. `docs/PROVIDERS.md` row: provider, what it unlocks, cost shape, key location.
6. Add a contract test asserting `"render_location_selection"` is in the decision_log enum and that
   a `render_report` with a full `render_location` block validates (pattern:
   `tests/test_footage_edit_plan_schema.py`).

## Todo

- [x] `decision_log.schema.json` — add `render_location_selection`
- [x] `render_report.schema.json` — add optional `render_location`
- [x] Re-validate all existing artifacts/fixtures against both updated schemas
- [x] `skills/core/cloud-render.md` (crossover numbers, announce template, decision log, blockers, batch policy, cost checklist, reap hygiene, unsupported shapes)
- [x] `.agents/skills/vastai/SKILL.md` (SDK map + 5 verified gotchas + measured baselines)
- [x] `AGENT_GUIDE.md` "Render Location — Local vs Cloud" subsection
- [x] `docs/PROVIDERS.md` Vast.ai row
- [x] Schema contract test

## Success criteria

- Every artifact under `tests/fixtures/` and every `render_report`/`decision_log` on disk validates
  against the updated schemas (no migration needed — proven, not assumed).
- A `decision_log` entry with `category: "render_location_selection"` and both local+cloud in
  `options_considered` validates.
- A `render_report` **without** `render_location` still validates (local renders unchanged).
- `skills/core/cloud-render.md` announce template contains every field `dry_run()` returns —
  asserted by a test that greps the skill for each key name in the `dry_run` payload, so the two
  cannot drift.
- `AGENT_GUIDE.md` names `render_location_selection` and links the skill.

## Risks

| Risk | Mitigation |
|---|---|
| Announce template drifts from `dry_run()` payload → user sees fewer facts than exist | The grep test above ties them together mechanically |
| Agent reads the skill and still defaults to cloud because it is faster | The tool refuses without `offer_id` + `dry_run_ref` (phase 04), so the announce is structurally unavoidable |
| Widening the enum invites future categories with no rationale | Only one entry added, with a description; the enum stays a closed set |
| `render_location` in `render_report` duplicates `rentals.jsonl` | Intentional: the report is the per-deliverable audit record, the ledger is the cross-deliverable money record. `ledger_ref` links them |
| The "never fall back silently" rule frustrates users during an outage | The blocker message recommends local and needs one word to accept — friction is one keystroke, and it prevents a silent 40-minute local render nobody asked for |

## Security

- The skill must state plainly, at the point of decision, that footage is uploaded to third-party
  hardware and that anyone with confidentiality constraints should render locally. This belongs in
  the announce block, not only in docs — nobody reads docs at decision time.
- The Layer 3 skill must not contain any real API key, instance id, or ssh host from the manual run
  beyond the already-destroyed offer id used as an illustrative example.

## Next

Phase 06 tests the whole surface; phase 07 exposes it to humans.

## Unresolved questions

1. Explicit `render_location` field in `render_report` vs zero-churn `metadata.render_location`?
   Recommend the explicit field (auditable by name). This is a schema-shape call — confirm.
2. Should cloud render, once approved, be re-announced for **every** rental in a session, or only
   when the offer/ceiling/scope changes? Recommend re-announce on change only, matching how
   `cost_tracker` gates only the first paid use per tool. Confirm.
3. `skills/core/` vs a new `skills/meta/` home for `cloud-render.md`? `skills/core/` holds engine
   how-tos (`ffmpeg`, `remotion`, `hyperframes`), which fits; `skills/meta/` holds cross-cutting
   protocol. Location choice is arguably protocol. Recommend `skills/core/` — confirm.
