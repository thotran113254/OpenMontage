# Phase 04 — BaseTool wrappers, registry visibility, cost_tracker wiring

## Context

- [plan.md](plan.md), [phase-02](phase-02-render-kit-and-remote-render.md),
  [phase-03](phase-03-batch-queue-and-flush.md)
- Tool contract: `tools/base_tool.py` — field list at `:142-197`, `check_dependencies` at
  `:209-231`, `get_info` at `:233-276`, `estimate_cost` at `:280`, `idempotency_key` at `:290-294`,
  `dry_run` at `:303-311`.
- Thin-wrapper prior art (lazy import so `discover()` stays cheap):
  `tools/video/talking_head_autoedit.py:1-6`, class at `:31`.
- Registry: `discover()` imports every module under `tools/` at `tools/tool_registry.py:118-134`;
  `register_module` instantiates every concrete `BaseTool` subclass at `:73-84`;
  capability rollup for the preflight menu at `:357-377`.
- Cost governance: `tools/cost_tracker.py` — `estimate` `:101`, `reserve` `:117`, single-action
  threshold check `:126-132`, **new-paid-tool approval gate `:134-141`**, `reconcile` `:159`,
  `refund` `:168`. Defaults come from `config.yaml:9-14`.

## Overview

- **Priority:** P0 — this is the surface the agent actually calls.
- **Status:** completed (2026-08-06)
- Two tools: one that spends money and one that does not. Splitting them is deliberate — the
  registry's `runtime` and `side_effects` fields then tell the truth without a mode flag, and the
  free one can be called freely during planning.

## Key insights

1. **`registry.discover()` imports every module under `tools/`** (`tool_registry.py:118-134`) and
   *instantiates* every concrete subclass (`:73-84`). A top-level `import vastai` in the new tool
   module would make preflight crash for every user who has not installed it. Lazy-import inside
   `execute()`/`dry_run()`, exactly as `talking_head_autoedit.py:1-6` explains.
2. **`check_dependencies` understands only `cmd:` / `env:` / `python:`** (`base_tool.py:212-231`).
   `talking_head_autoedit.py:41` declares `"binary:ffmpeg"`, which falls through every branch and is
   **silently ignored** — the tool reports AVAILABLE with no ffmpeg. Do not copy that. Use
   `["python:vastai", "cmd:ssh", "cmd:scp"]`. (Fixing `talking_head_autoedit.py` is out of scope
   here; note it for a follow-up so the finding is not lost.)
3. **The new-paid-tool gate already exists and already does the right thing.**
   `config.yaml:14` sets `require_approval_for_new_paid_tool: true`, and
   `cost_tracker.py:134-141` raises `ApprovalRequiredError` on the first paid use of a tool name
   unless `approve_tool()` was called. So "the agent must not rent without asking" has a mechanical
   backstop already — no new mechanism, just correct usage. DRY win; do not invent a second gate.
4. **`single_action_approval_usd: 0.50` (`config.yaml:13`) will usually *not* fire** — a 30-minute
   rental at $0.0814/hr is ~$0.04. That is fine: the gate that matters here is the new-paid-tool one
   plus the explicit `offer_id` requirement. Do not lower the global threshold to force a prompt;
   that would change behaviour for every other paid tool.
5. **`dry_run()` is the announce payload.** `base_tool.py:303-311` already defines `dry_run` as
   "preflight check without side effects. Override for paid/publishing tools." Overriding it to
   return the offer shortlist + $/hr + estimated total + kit size is exactly the data
   AGENT_GUIDE's "Announce Before Execution" (`AGENT_GUIDE.md:135-141`) demands. No new concept.
6. **`execute()` must refuse without an `offer_id`.** That is a *mechanical* precondition, not
   policy: it structurally forces the agent through `dry_run` (and therefore through the announce)
   before any rental. Same for a required `max_total_usd` input. Policy (which offer, whether to
   rent at all) stays in the skill.

## Requirements

**`tools/video/vast_cloud_render.py` → `class VastCloudRender`**

| Field | Value |
|---|---|
| `name` | `vast_cloud_render` |
| `capability` | `cloud_render` (new family; auto-appears in the preflight menu per `tool_registry.py:357-377`) |
| `provider` | `vastai` |
| `tier` | `ToolTier.CORE` |
| `runtime` | `ToolRuntime.API` (costs money, needs network) |
| `stability` | `ToolStability.EXPERIMENTAL` |
| `execution_mode` | `ExecutionMode.SYNC` |
| `determinism` | `Determinism.DETERMINISTIC` (same props + same Remotion version ⇒ same frames) |
| `dependencies` | `["python:vastai", "cmd:ssh", "cmd:scp"]` |
| `install_instructions` | `pip install vastai` + how the key is found (`~/.config/vastai/vast_api_key` or `VAST_API_KEY`) + `enabled: true` in `config/cloud-render.json` + an ssh keypair at `ssh_key_path` |
| `agent_skills` | `["vastai", "remotion-best-practices"]` |
| `side_effects` | `["rents_paid_cloud_instance", "uploads_footage_offmachine", "destroys_cloud_instance", "spends_money"]` |
| `resource_profile` | `ResourceProfile(cpu_cores=1, ram_mb=512, disk_mb=2000, network_required=True)` — local footprint is just transfer |
| `retry_policy` | `RetryPolicy(max_retries=1, backoff_seconds=10, retryable_errors=["offer_unavailable", "ssh_timeout", "npm_ci_failed"])` |
| `resume_support` | `ResumeSupport.FROM_CHECKPOINT` (batch: already-downloaded outputs are skipped) |
| `idempotency_key_fields` | `["kit_hash", "composition_id", "offer_id"]` |
| `not_good_for` | short jobs where overhead dominates; anything confidential; the `video_compose` props shape (phase 02 refusal) |

`input_schema` (required marked **bold**): **`mode`** ∈ `render_now|flush`, **`offer_id`**,
**`max_total_usd`**, `job_id` (render_now), `job_ids` (flush), `pricing_mode`, `bid_price_usd`,
`max_runtime_minutes`, `max_concurrency`, `dry_run_ref` (the `dry_run` token, see step 4).

`dry_run(inputs)` returns, **without renting**:
```jsonc
{ "tool": "vast_cloud_render", "provider": "vastai", "would_execute": true,
  "offers": [{"offer_id": 40179084, "dph_usd": 0.0814, "cpu_cores": 32,
              "geolocation": "MX", "reliability": 0.98, "gpu_name": "RTX 4060 (unused)"}],
  "recommended_offer_id": 40179084,
  "pricing_mode": "on-demand", "interruptible_alternative_dph_usd": 0.069,
  "kit_size_bytes": 812345, "jobs": 4,
  "estimated_render_minutes": 23.5, "estimated_overhead_minutes": 6,
  "estimated_cost_usd": 0.048, "cost_if_rendered_separately_usd": 0.19,
  "estimated_local_render_minutes": 44,
  "ceilings": {"max_dph_usd": 0.15, "max_total_usd_per_rental": 0.50,
               "max_runtime_minutes": 60},
  "warnings": ["footage leaves this machine", "GPU in the offer is unused — render is CPU-bound"],
  "dry_run_ref": "dr_9f2a1c" }
```

`estimate_cost(inputs)` = `dph × (overhead_minutes + Σ render_minutes) / 60`, from the ledger-
calibrated constant. `estimate_runtime(inputs)` = the same in seconds.

**`tools/video/cloud_render_queue.py` → `class CloudRenderQueue`**

`name="cloud_render_queue"`, `capability="cloud_render"`, `provider="openmontage"`,
`runtime=ToolRuntime.LOCAL`, `dependencies=[]`, `side_effects=["writes_local_queue_file"]`,
`estimate_cost` → `0.0`. Operations: `enqueue | list | remove | clear | flush_check | reap`.
Free, offline, safe to call during planning — which is why it is a separate tool.

**Cost governance flow the tool enforces mechanically** (policy lives in phase 05's skill):
1. `dry_run` → no cost entry (nothing reserved for a search).
2. Caller does `entry = tracker.estimate("vast_cloud_render", "rental", est)`;
   `tracker.reserve(entry)` → raises `ApprovalRequiredError` on first use (`cost_tracker.py:134-141`)
   until the user approves and the agent calls `approve_tool("vast_cloud_render")`.
3. `execute()` refuses if `offer_id` missing, if `max_total_usd` missing, if `dph > max_dph_usd`, if
   `config.enabled is False`, or if `max_total_usd > config.max_total_usd_per_rental`.
4. `ToolResult.cost_usd` = actual `dph × elapsed / 3600` (also written to `rentals.jsonl`), so
   `tracker.reconcile(entry, result.cost_usd, success)` records truth, not the estimate.
5. On failure before any rental: `tracker.refund(entry)` (`cost_tracker.py:168`).

## Related code files

**Create**
- `tools/video/vast_cloud_render.py`
- `tools/video/cloud_render_queue.py`
- `tests/test_cloud_render_tool_registration.py` (pattern: `tests/test_footage_edit_analyzer_registration.py`)

**Modify**
- `lib/cloud_render/__init__.py` — export `render_now`, `flush`, `queue`, `ledger`, `config` so the
  tools import one surface

**Do not modify**
- `tools/cost_tracker.py`, `config.yaml` — the existing gates suffice (insight 3/4)

## Implementation steps

1. `VastCloudRender` skeleton with the full contract table above; `get_status()` inherited (the
   `python:vastai` dep makes it UNAVAILABLE without the package, which is correct) **plus** an
   override that reports DEGRADED when `vastai` is installed but `config.enabled is False`, with the
   reason in `get_info()["cloud_render_config"]` so preflight can show it.
2. `dry_run()`: `ledger.reap()` first (free, and it is the most likely moment to catch an orphan),
   then `search`, then `flush_check` when `mode=="flush"`, then assemble the payload above. Persist
   `{dry_run_ref: {offer_ids, ceilings, expires_at: now+10min}}` under
   `projects/cloud-render/dry_runs.json`.
3. `execute()`: validate → resolve `dry_run_ref` (reject if expired or if `offer_id` is not in that
   ref's offer list) → delegate to `lib.cloud_render.render_now` / `flush` → map to `ToolResult`
   with `cost_usd`, `duration_seconds`, `artifacts=[local output paths]`, and
   `data.render_location` (see phase 05's `render_report.render_location` block).
4. The `dry_run_ref` check is what makes "no unilateral substitution" mechanical: an offer the user
   never saw cannot be rented, because it is not in the ref.
5. `CloudRenderQueue.execute()`: dispatch on `operation`; all ops are pure local file work.
   `flush_check` returns the phase 03 `ThresholdReport` untouched.
6. Registration test: `registry.discover()` finds both names; `capability_catalog()` has a
   `cloud_render` family; `provider_menu_summary()` includes it; neither tool imports `vastai` at
   module scope (assert via `sys.modules` before/after import).

## Todo

- [x] `VastCloudRender` full contract + lazy import + DEGRADED-when-disabled status
- [x] `dry_run()` announce payload + `dry_run_ref` persistence and expiry
- [x] `execute()` preconditions (offer_id, max_total_usd, ceilings, enabled, ref match)
- [x] `estimate_cost` / `estimate_runtime` from the ledger-calibrated constant
- [x] `CloudRenderQueue` with 6 operations, cost 0, no network
- [x] Registration + no-eager-import tests
- [x] `lib/cloud_render/__init__.py` public surface

## Success criteria

- `registry.discover()` succeeds on a machine **without** `vastai` installed; both tools appear,
  `vast_cloud_render` with `status: "unavailable"` and an actionable `install_instructions`.
- `python -c "...registry.discover()..."; "vastai" not in sys.modules` → True.
- `provider_menu_summary()["capabilities"]` contains a `cloud_render` entry with correct
  `configured/total`.
- `execute()` without `offer_id` → `ToolResult(success=False)` and **zero** SDK calls.
- `execute()` with an `offer_id` absent from the `dry_run_ref` → refused, zero SDK calls.
- With `require_approval_for_new_paid_tool: true`, `reserve()` raises until `approve_tool` is
  called — asserted in a test that reads the real `config.yaml` defaults.
- `cloud_render_queue` works with `vastai` uninstalled and network unplugged.

## Risks

| Risk | Mitigation |
|---|---|
| Copying `binary:` prefix from `talking_head_autoedit.py:41` → tool falsely reports available | Explicit `cmd:` deps + a test asserting `get_status()==UNAVAILABLE` when `ssh` is absent from PATH |
| Eager `import vastai` breaks preflight repo-wide | `sys.modules` assertion test (success criterion above) |
| `dry_run_ref` expiry too short → agent has to re-search after a slow user conversation | 10 min default, configurable; expiry produces a clear "re-run dry_run" message, never a silent re-search |
| Agent treats `estimate_cost` as truth and never reconciles | Skill (phase 05) makes reconcile a checklist step; `rentals.jsonl` keeps the actual regardless, so the audit trail is right even if the tracker call is skipped |
| New capability family confuses the preflight menu presentation | Menu is generated from the registry, so it self-describes; phase 07 docs show the expected line |

## Security

- `install_instructions` must not suggest pasting the API key anywhere but
  `~/.config/vastai/vast_api_key` or `.env`. Never echo the key in `get_info()`.
- `get_info()` may expose ceilings and the image name (useful) but never the ssh key path contents
  or the API key.
- `side_effects` lists `spends_money` and `uploads_footage_offmachine` explicitly — reviewers and
  the agent both read this field, and both facts are things a user must be able to discover from
  the registry alone.

## Next

Phase 06 tests this surface; phase 07 exposes it on the CLI. Phase 05 supplies the instructions the
agent needs to use it correctly.

## Unresolved questions

1. Should `vast_cloud_render` also be reachable through a future `render_selector` (mirroring
   `tts_selector`/`video_selector`)? Recommend not yet — one provider, and location choice must stay
   an explicit user decision, not selector routing.
2. `capability="cloud_render"` vs folding into the existing `video_post` family. New family makes it
   visible at preflight as its own line; `video_post` hides it among ffmpeg tools. Recommend new
   family — confirm.
