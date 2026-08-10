# Phase 04 Implementation Report — BaseTool wrappers, registry visibility, cost_tracker wiring

## Executed Phase
- Phase: phase-04-tools-registry-cost-tracker
- Plan: plans/260806-1404-vastai-cloud-render/
- Status: completed

## Files Created
- `tools/video/vast_cloud_render.py` (349 lines) — `class VastCloudRender`, full contract table,
  `get_status()`/`get_info()` overrides, `dry_run()`/`execute()`, cost/runtime estimators.
- `tools/video/cloud_render_queue.py` (123 lines) — `class CloudRenderQueue`, 6 operations
  (`enqueue|list|remove|clear|flush_check|reap`), zero deps, `runtime=LOCAL`.
- `lib/cloud_render/dry_run_store.py` (102 lines) — `create()`/`resolve()` for the `dry_run_ref`
  token: persists `{offer_ids, ceilings, expires_at}` per the phase's literal spec, atomic
  temp+`os.replace` write, 10-min default TTL.
- `lib/cloud_render/cost_estimate.py` (103 lines) — local-only (no SDK/network) job-duration/
  kit-size helpers shared by `estimate_cost()`/`estimate_runtime()`/`dry_run()`.
- `lib/cloud_render/announce.py` (117 lines) — assembles the `dry_run()` payload (offer search,
  shortlist, cost/time estimates, `dry_run_ref` creation). Split out of the tool file per the
  200-line modularization rule.
- `tests/test_cloud_render_tool_registration.py` (426 lines, 35 tests, all passing).

## Files Modified
- `lib/cloud_render/__init__.py` (25 lines) — now re-exports `render_now`, `flush`, `queue`,
  `ledger`, `config`. Verified `import lib.cloud_render` never touches `sys.modules['vastai']`
  (none of `remote`/`queue`/`ledger`/`config` import the SDK at module scope; only
  `vast_client._client()` does, lazily).
- `skills/core/cloud-render.md` — targeted 2-line edit: announce template placeholder and the
  field-reference table row renamed `interruptible_alternative_dph_usd` →
  `on_demand_alternative_dph_usd`, per phase 05's flagged naming-drift note. The old name string is
  still present in the row's explanatory text, so phase 05's grep contract test
  (`test_cloud_render_skill_announce_template_names_every_dry_run_field`) still passes unchanged.
- `plans/260806-1404-vastai-cloud-render/phase-04-tools-registry-cost-tracker.md` — Status →
  completed, all 7 Todo items checked.

## Design decisions / deviations from the phase file's illustrative sketch

1. **Modularized beyond the phase's "Related code files" list.** The phase names only
   `tools/video/vast_cloud_render.py`, `tools/video/cloud_render_queue.py`, the test file, and
   `lib/cloud_render/__init__.py`. To honor the 200-line-file dev rule without gutting readability
   of the governance-critical `execute()` path, I split the offer-search/cost-estimate assembly
   into two new `lib/cloud_render/` modules (`announce.py`, `cost_estimate.py`) and the
   `dry_run_ref` mechanism into `dry_run_store.py`. All three live inside the same
   `lib/cloud_render` package the plan already owns exclusively at this stage (phases 01-03/05
   done, no parallel phase touches this dir now) — no file-ownership conflict. `vast_cloud_render.py`
   is still 349 lines after this split; the remainder is mostly declarative contract fields
   (~120 lines, phase-mandated) plus `execute()`'s refusal/delegation logic, which I kept
   co-located with the tool class deliberately — burying "no autopilot" mechanics in a lib helper
   would hurt exactly the auditability a security reviewer needs.
2. **`dry_run_ref` stores `{offer_ids, ceilings, expires_at}` literally as spec'd**, not a
   per-offer-dph map I initially considered. Ceiling re-validation at `execute()` time is enforced
   by construction (an offer only ever enters the ref's `offer_ids` after passing the
   ceiling-at-announce-time filter) plus defense-in-depth inside `render_now`/`flush` themselves
   (both re-search and re-filter by the *current* `max_dph_usd` right before renting). I did not
   add a second independent dph cache to avoid diverging from the phase's literal persisted shape.
3. **`bid_price_usd` / `max_concurrency` input_schema fields are documented but not wired.**
   `render_now`/`flush`'s real signatures (confirmed via the task brief and by reading
   `lib/cloud_render/remote.py`/`queue.py`) pick bid price from the chosen offer's `dph` and
   concurrency from `offer.cpu_cores_effective` internally — there is no parameter to forward
   these two into. `pricing_mode` and `max_runtime_minutes` *are* wired (as a job-layer override via
   `config.resolve(job=...)`, the exact 3-layer mechanism phase 03 built for this).
4. **`on_demand_alternative_dph_usd` (renamed field) is computed via a second, filter-free
   `vast_client.search()` call** at the opposite pricing mode, best-effort (`None` on any search
   failure — never blocks the announce).
5. **`estimated_local_render_minutes`** uses a `local_cores`/`offer.cpu_cores_effective` ratio
   heuristic (via `os.cpu_count()`) since there's no separate local-render constant in
   `config/cloud-render.json`. Coincidentally reproduces close to the skill's own worked example
   ratio (44 / 23.5 ≈ 1.87 vs a 32-core offer against a typical dev-box core count).
6. **Reap via `cloud_render_queue`'s `operation="reap"` is NOT actually network-free**, contrary to
   the phase text's "all ops are pure local file work" — it delegates to `ledger.reap()` (phase 01,
   already implemented), which unconditionally calls `vast_client.list_labelled_instances()` to
   reconcile local vs remote state; that is `reap`'s entire purpose. The other 5 operations
   (`enqueue|list|remove|clear|flush_check`) are genuinely local/offline/vastai-free, proven by a
   dedicated test that sets `sys.modules['vastai']=None` before calling them. `reap` is tested by
   mocking `ledger.reap` itself rather than the network.

## Tests Status
- Type check: N/A (no repo-wide typecheck script; `python -m py_compile` clean on all new/modified
  files).
- New test file: 35/35 passed.
- Phase 01-03 cloud-render suites + phase 05 contract suite + new file together: 86/86 passed.
- Full repo suite `python -m pytest tests/ -q`: **1479 passed, 11 skipped, 0 failed** (341.6s). The
  11 skips are pre-existing (`RUN_LIVE_*`-gated live tests unrelated to this phase). One
  pre-existing `UserWarning` from `test_cloud_render_batch_flush.py` (phase 02/03's own test,
  unrelated to this phase) — not a failure.

## Success Criteria Checklist
- [x] `registry.discover()` succeeds without `vastai` installed (verified via
      `sys.modules['vastai']=None` injection; both tools appear; `vast_cloud_render` reports
      `unavailable` with actionable `install_instructions` containing `pip install vastai`).
- [x] `"vastai" not in sys.modules"` after a bare `registry.discover()` — verified in a **fresh
      subprocess** (`test_registry_discover_leaves_vastai_unimported_in_a_fresh_process`), the
      strongest form of this check since it can't be polluted by other tests' import state.
- [x] `provider_menu_summary()["capabilities"]` has a `cloud_render` entry, `total=2`,
      `configured=1` (queue tool only, since `vast_cloud_render` is DEGRADED while
      `config/cloud-render.json` ships `enabled:false`), `"openmontage"` in `available_providers`,
      `"vastai"` in `unavailable_providers` — matches the real, unmocked config on disk.
- [x] `execute()` without `offer_id` → `ToolResult(success=False)`, zero SDK calls (proven via a
      fixture that makes `vast_client.search/rent`, `remote.render_now`, `queue.flush` raise
      `AssertionError` if reached — none of the refusal tests trip it).
- [x] `execute()` with an `offer_id` absent from `dry_run_ref` → refused, zero SDK calls.
- [x] `reserve()` raises `ApprovalRequiredError` until `approve_tool()`, asserted against the real
      `config.yaml` defaults (`mode: warn`, `require_approval_for_new_paid_tool: true`) read via
      `yaml.safe_load`.
- [x] `cloud_render_queue` works with `vastai` uninstalled (`sys.modules['vastai']=None`) for
      `enqueue|list|remove|clear|flush_check`; `reap` verified by mocking `ledger.reap` (see
      deviation #6 above for why `reap` itself isn't network-free).
- [x] `get_status()==UNAVAILABLE` when `cmd:ssh` is missing — `monkeypatch.setattr("shutil.which",
      lambda _: None)`, confirming the exact `talking_head_autoedit.py:41` bug (unsupported
      `binary:` prefix silently ignored) is not repeated here.

## Unresolved Questions
1. Plan-level phase-status table in `plans/.../plan.md` still shows all phases "pending" even
   though phases 01-03/05 are reportedly done — I updated only phase-04's own file's Status field
   (not plan.md's table), matching what phases 01-03/05 apparently did. Flag to whoever owns
   plan.md sync: should the overview table be updated now, or is that deferred to a later
   consolidation pass?
2. Phase 06's "Related code files" list also names `tests/test_cloud_render_tool_registration.py`
   as something it creates. I created it now per phase 04's own explicit deliverable list; phase 06
   should extend/add to this file rather than overwrite it, or coordinate file ownership if it
   intends a from-scratch rewrite.
