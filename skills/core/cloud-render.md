# Cloud Render (Vast.ai) — Layer 2 skill

This is the **OpenMontage-specific** guide to renting a short-lived Vast.ai box to render a
composition instead of rendering it on this machine. It teaches WHEN cloud is worth it, the
announce/approval contract, the decision-log shape, and the batch/hygiene policy. Raw Vast.ai SDK
knowledge (method map, gotchas) lives in the Layer 3 skill: `.agents/skills/vastai/SKILL.md`.

`render_location` (local vs cloud) is a sibling of `render_runtime` (Remotion vs HyperFrames vs
FFmpeg), not a child of it — they answer different questions ("which machine" vs "which engine").
Cloud Remotion and local Remotion are the same runtime. This skill never touches
`edit_decisions.render_runtime`.

## No autopilot — every rental needs a human, every time

**There is no standing pre-approval path for cloud render in this system, and there never has
been one.** Every rental — a render-now request AND every single batch flush — requires an
explicit announce (see template below) followed by explicit human approval, before any
`create_instance` call. This is not a one-time gate that gets satisfied and then skipped for later
rentals in the same session: `approve_tool("vast_cloud_render")` only satisfies the mechanical
`cost_tracker` new-paid-tool gate (see Cost governance checklist), it does NOT stand in for the
per-rental announce+approval this skill requires. There is no unattended or overnight auto-flush
mode. If a batch's `flush_check` reports `thresholds_met: true` while the user is not available to
approve, the agent queues the report and waits — it does not rent on the user's behalf.

## When cloud is worth it (numbers, not adjectives)

- **Overhead**: ~6 minutes of paid time per rental before the first frame renders (boot ~60s +
  `npm ci` ~9.2s for the composer kit + offer search/ssh-ready wait + teardown). This is money
  spent before any render output exists.
- **Crossover rule**: cloud is worth proposing when **estimated local render time > ~2× the
  overhead** (i.e. > ~12 minutes), **or** the queue already has **≥ 3 jobs** waiting.
- **Local is the default.** Below the crossover, renting is pure overhead with no benefit — do not
  propose cloud for a single short job "just because it's available."

## The announce block template (mandatory, verbatim)

Copy this block, filling in the bracketed values from the tool's `dry_run()` payload. Do not
paraphrase — paraphrasing is how required fields quietly go missing. `pricing_mode: bid` is the
**default** ceiling-checked price in this system (see "Bid is the default, not on-demand" below);
show it first.

```
RENDER LOCATION: cloud (Vast.ai) — needs your approval before I rent anything
  Offer           : #[offer_id] — [cpu_cores] vCPU [gpu_name if present, else "no GPU used"],
                    [geolocation], reliability [reliability]
                    ([gpu_name] is unused if this render is CPU-bound — say so explicitly)
  Pricing mode    : bid (interruptible) — DEFAULT — $[dph_usd]/hr
                    Vast.ai can preempt this instance mid-render. Preemption is the NORMAL case to
                    design around here, not a rare edge case: on preemption the job is requeued,
                    the render is retried on a fresh rental, and I will re-announce before that
                    retry rents anything.
                    On-demand (non-interruptible, costlier, safer) is available at
                    $[on_demand_alternative_dph_usd]/hr if you'd rather not risk preemption for
                    this render — say "use on-demand" to switch.
  Scope           : [sample of 1 job | batch of N jobs, one rental]
  Time            : ~[estimated_overhead_minutes] min overhead + ~[estimated_render_minutes] min
                    render(s) = ~[sum] min
  Cost            : ~$[estimated_cost_usd] total  |  per-rental ceiling: $[max_total_usd_per_rental]
                    |  $/hr ceiling: $[max_dph_usd]
  Separately      : ~$[cost_if_rendered_separately_usd] if each job rented its own instance
  Locally         : ~[estimated_local_render_minutes] min, $0
  Data            : your footage/render kit ([kit_size_bytes] bytes) is uploaded to a third-party
                    machine and deleted with the instance. Render locally if that is not
                    acceptable for this project.
  Safety          : instance is destroyed on completion, on error, and by a watchdog at the
                    [max_runtime_minutes]-minute deadline even if this process dies.
Approve? (yes / use on-demand instead / render locally / pick a different offer)
```

If `warnings` from `dry_run()` is non-empty (e.g. "GPU in the offer is unused"), fold each one into
the relevant line above rather than appending a separate list — a warning about the GPU belongs
next to the Offer line, not buried at the bottom.

### Field reference (ties this template to `dry_run()`)

Every field the tool's `dry_run()` returns must be represented in the announce block above. This
table is the mechanical link a test checks — if `dry_run()`'s payload shape changes, this table
(and the block above) must change with it:

| `dry_run()` field | Where it appears above |
|---|---|
| `tool`, `provider`, `would_execute` | Implicit in the block header ("cloud (Vast.ai)... needs your approval") — `would_execute: true` means nothing is rented yet |
| `offers[].offer_id` | `Offer` line, `#[offer_id]` |
| `offers[].dph_usd` | `Pricing mode` line, bid `$[dph_usd]/hr` |
| `offers[].cpu_cores` | `Offer` line |
| `offers[].geolocation` | `Offer` line |
| `offers[].reliability` | `Offer` line |
| `offers[].gpu_name` | `Offer` line, with the unused-GPU caveat |
| `recommended_offer_id` | The offer shown IS the recommended one unless the agent explains otherwise |
| `pricing_mode` | `Pricing mode` line — default value is `"bid"` in this system (see below) |
| `on_demand_alternative_dph_usd` | `Pricing mode` line, on-demand price — **renamed in phase 04** from `interruptible_alternative_dph_usd`: that old key name was written when on-demand was the assumed default and bid was "the alternative." Now that bid is the default (see below), `on_demand_alternative_dph_usd` correctly names the *non-default* on-demand price instead of implying bid is the exception. |
| `kit_size_bytes` | `Data` line |
| `jobs` | `Scope` line |
| `estimated_render_minutes` | `Time` line |
| `estimated_overhead_minutes` | `Time` line |
| `estimated_cost_usd` | `Cost` line |
| `cost_if_rendered_separately_usd` | `Separately` line |
| `estimated_local_render_minutes` | `Locally` line |
| `ceilings.max_dph_usd` | `Cost` line |
| `ceilings.max_total_usd_per_rental` | `Cost` line |
| `ceilings.max_runtime_minutes` | `Safety` line |
| `warnings` | Folded into the relevant line (see above) |
| `dry_run_ref` | Not shown to the user directly — passed back to `execute()` as `dry_run_ref` so the rented offer is provably one the user actually saw (see "Never silently substitute location") |

## Bid is the default, not on-demand — preemption is the normal case

`config/cloud-render.json` ships `"pricing_mode": "bid"` as the default (confirmed:
`BUILTIN_DEFAULTS` in `lib/cloud_render/config.py`). Bid (interruptible) pricing is ~15% cheaper
than on-demand at the measured baseline (bid ~$0.069/hr vs on-demand ~$0.0814/hr on the same
32-vCPU offer, verified 2026-08-06) — cheaper-plus-risk is the accepted tradeoff by default, not
safer-plus-costlier.

Because bid is the default, **design for preemption as the normal case a batch will hit
sometimes, not as a rare failure**:

- On a preemption signal (instance disappears / SSH drops mid-render with no explicit destroy from
  this process), the job goes back into the queue, not into a failure report.
- Before the retry rents a new instance, re-run `dry_run()` and re-announce — the offer that gets
  rented on retry may differ from the one that was preempted, and the user must see the new offer,
  not assume the old approval still applies.
- On-demand remains available as the safer-but-costlier opt-in for renders where a mid-render
  restart would be expensive to redo (very long single renders, e.g. near the
  `max_runtime_minutes` ceiling). Offer it explicitly in the announce block's approval line; do not
  make the user dig for it.

## Config ships disabled

`config/cloud-render.json` ships with `"enabled": false`. Cloud render does not run at all until a
human explicitly flips this — there is no "first successful rental turns it on for next time"
behavior. If `enabled` is `false` when a job would otherwise be a good cloud candidate, say so
plainly ("cloud render exists but is disabled in this project's config — enable it in
`config/cloud-render.json` if you want to use it") and proceed locally without nagging further.

## 3-part presentation rule (when location hasn't been chosen yet)

Same shape as `AGENT_GUIDE.md`'s "Present Both Composition Runtimes" rule:

1. One sentence on what cloud is best at for *this* job (e.g. "this batch of 4 jobs and ~23 min of
   render time clears the crossover — cloud turns that into ~29 min of paid wall-clock instead of
   ~44 min blocking this machine").
2. One honest tradeoff (e.g. "bid pricing means it can be preempted and retried mid-batch, and
   your footage briefly leaves this machine").
3. The recommendation with reasoning, then wait for explicit approval before advancing.

## Mandatory `decision_log` entry

Every cloud rental — sample or batch — gets one `decision_log` entry with
`category: "render_location_selection"`. Both `"local"` and `"cloud"` **must** appear in
`options_considered`, even when the recommendation is obviously cloud; a cloud rental logged with
only `"cloud"` considered is a critical reviewer finding, the same standard as the
`render_runtime_selection` rule in `AGENT_GUIDE.md`. Set `user_approved: true` only once the human
has actually approved the announce block above — never optimistically.

```jsonc
{
  "decision_id": "d-0NN",
  "stage": "compose",
  "category": "render_location_selection",
  "subject": "Render location for <job/batch description>",
  "options_considered": [
    {"option_id": "local", "label": "Local machine", "score": 0.4,
     "reason": "Free, no data leaves the machine.",
     "rejected_because": "Estimated <N> min exceeds the ~12 min cloud crossover for this queue."},
    {"option_id": "cloud", "label": "Vast.ai — offer #<offer_id>, bid $<dph_usd>/hr", "score": 0.9,
     "reason": "Clears the crossover; ceilings ($<max_dph_usd>/hr, $<max_total_usd_per_rental>/rental) hold."}
  ],
  "selected": "cloud",
  "reason": "User approved the announce block; offer #<offer_id> within ceiling.",
  "user_visible": true,
  "user_approved": true
}
```

## Never silently substitute location

If cloud is unreachable, `config.enabled` is `false`, the announced offer disappears, or the price
now exceeds a ceiling — **escalate**, using the 5-part blocker structure from
`AGENT_GUIDE.md` → "Escalate Blockers Explicitly" (what was attempted, what failed, why, what
options exist, which one is recommended). Recommend local, then wait. **Do not fall back to local
without asking**, even though local is cheaper and safer — it changes render time and the user may
be working against a deadline they didn't get a say in extending.

Symmetrically: **never fall forward to cloud** because local looks slow in the moment. The
render-now vs cloud choice, and batch-vs-render-now, are both decisions that need the announce +
approval above — a slow local render is not by itself consent to spend money.

The tool's `execute()` mechanically enforces the "no substitution" half of this: it refuses any
`offer_id` that is not present in the `offer_ids` list stored under the `dry_run_ref` the user was
shown. An offer the user never saw in an announce block cannot be rented, full stop — this is a
structural guarantee, not merely a policy one.

## Batch policy

Cloud render batches queued jobs rather than renting per-job. This is `cloud_render_queue`'s job,
not `vast_cloud_render`'s:

1. Call `cloud_render_queue` with `operation="flush_check"` → read the returned
   `ThresholdReport.thresholds_met`.
2. If met, decide whether to flush now (the agent's judgment: queue depth, how long jobs have
   waited relative to `config["batch"]["max_wait_minutes"]`, whether the user is present to
   approve).
3. Announce (per the template above, with `Scope: batch of N jobs, one rental`) and wait for
   approval before calling `vast_cloud_render` with `mode="flush"`.

**Making batch the project default (i.e. "always batch instead of render-now from now on") is a
mode change** — `AGENT_GUIDE.md` §153 already requires asking before switching from sample mode to
batch mode. Treat it as a must-ask, not a nice-to-ask, exactly like any other creative-mode switch.

## Cost governance checklist

Mirrors the pattern every `skills/pipelines/*/executive-producer.md` already uses for other paid
tools — no new mechanism, just correct sequencing:

1. `dry_run(inputs)` → no cost entry yet; this is free and reversible.
2. `entry = tracker.estimate("vast_cloud_render", "rental", estimated_cost_usd)` then
   `tracker.reserve(entry)`. Expect `ApprovalRequiredError` on the first-ever use of this tool
   (`tools/cost_tracker.py`'s new-paid-tool gate) — this is separate from, and does not replace,
   the per-rental announce above.
3. Once the human approves (both the tool-level gate AND the per-rental announce), call
   `tracker.approve_tool("vast_cloud_render")`, then `execute()`.
4. On success: `tracker.reconcile(entry, result.cost_usd, success=True)` — `cost_usd` is the
   *actual* `dph × elapsed / 3600` from the ledger, never the estimate.
5. On any failure before a rental actually starts: `tracker.refund(entry)`.

## Orphan hygiene — reap at session start

At the start of any session that will touch cloud render (mentions it, resumes a project that used
it, or the user asks about cost), run `cloud_render_queue` with `operation="reap"` (wraps
`lib.cloud_render.reap.reap()` / `lib/cloud_render/ledger.py::reap`) before anything else cloud
related. **An orphaned rental is a bug worth telling the user about, not a silent cleanup** —
report anything it destroyed, including the `intent_id`/`instance_id` and why (past-deadline,
no local record, etc.).

## What cloud render does NOT support yet

The `video_compose` atelier/props shape from phase 02 is not supported by the render kit this tool
uploads. If a job's props don't match the supported shape, the tool refuses with a specific
reason — quote that refusal reason back to the user verbatim rather than retrying with a modified
shape or silently falling back to local. Retrying blindly against an unsupported shape wastes the
~6 minute overhead for a rental that was going to fail anyway.
