# Cloud Render (Vast.ai) — Code Review

Score: **7/10** — architecture, label-safety, dry_run_ref gating, and destroy-guarantee are solid
and well tested (262/262 cloud-render tests green + 15/15 contract tests). One real money-safety
gap (finding 1) and one design-deviation (finding 2) keep this from an 8+.

Scope reviewed: `lib/cloud_render/` (all 14 files), `tools/video/vast_cloud_render.py`,
`tools/video/cloud_render_queue.py`, `lib/talking_head_edit/stages/render.py` refactor,
`lib/talking_head_edit/cli.py` cloud flags, `schemas/artifacts/{decision_log,render_report}.schema.json`,
`skills/core/cloud-render.md`, `.agents/skills/vastai/SKILL.md`, `AGENT_GUIDE.md`, `config/cloud-render.json`,
`docs/cloud-render.md`, `docs/PROVIDERS.md`, Makefile/CI additions, and all `tests/test_cloud_render_*.py`
+ `tests/live/*` + `tests/fixtures/fake_vastai.py` + `tests/conftest.py`. `py_compile` clean on every
new/modified `.py` file; `pytest tests/test_cloud_render_*.py tests/contracts/test_cloud_render_governance_contract.py tests/test_talking_head_render.py -q` → 262 passed.

Note on verification limits: `lib/talking_head_edit/` and `lib/cloud_render/` are entirely
**untracked** in git (`git log` returns nothing for `render.py`) — this whole feature area has never
been committed. I could not diff against a real pre-refactor `render.py`; see finding 4.

---

## Critical findings

### 1. `max_total_usd` (the per-rental spend ceiling) is validated once, then never enforced

`VastCloudRender.execute()` requires `max_total_usd` as an input and checks it against the static
config ceiling:

```python
# tools/video/vast_cloud_render.py:250
if float(max_total_usd) > float(resolved["max_total_usd_per_rental"]):
    return ToolResult(success=False, error=...)
```

That is the **only** place `max_total_usd` (the caller's value) is used. It is never passed to
`lib.cloud_render.remote.render_now()` or `lib.cloud_render.queue.flush()` — neither function
accepts a `max_total_usd` parameter (`remote.py:330`, `queue.py:361`) — so the actual rental's
lifetime is bounded only by `config["max_runtime_minutes"]` / `max_batch_runtime_minutes` × the
already-checked `max_dph_usd`, with **no runtime check that `dph × elapsed` (or even
`dph × max_runtime_minutes`) stays under `max_total_usd_per_rental`**.

This currently "works" only by arithmetic coincidence of the shipped defaults:
`max_runtime_minutes=60 × max_dph_usd=$0.15/hr = $0.15` and
`max_batch_runtime_minutes=90 × $0.15/hr = $0.225`, both under the `$0.50` ceiling. But:

- `max_runtime_minutes` **is** overridable per-call via the tool's own `input_schema`
  (`vast_cloud_render.py:96-97`, wired through `_resolve_config` at `:184-195`), and
  `config.validate()` accepts any value in `[5, 240]` (`lib/cloud_render/config.py:111-115`) with
  **no cross-check against `max_total_usd_per_rental`**. A caller passing
  `max_runtime_minutes: 240` with the default `max_dph_usd=0.15` creates a rental that can legally
  run up to `240/60 × 0.15 = $0.60` — **exceeding the $0.50 per-rental ceiling** — and nothing in
  `rent()` (`vast_client.py:172-227`), `render_now()`, or `flush()` catches it.
- The CLI's `--cloud-max-usd` (`cli.py:186-198`) and the tool's `max_total_usd` input are
  effectively decorative once past that one preflight comparison: a user asking to cap *this
  specific* rental tighter than the global config ceiling (e.g. "cap this one at $0.05") gets no
  actual protection — the real deadline/ceiling used by `render_now`/`flush` is always
  `config["max_runtime_minutes"]`/`max_batch_runtime_minutes` × `max_dph_usd`, never the caller's
  `max_total_usd`.

This is precisely the class of check the review brief asked to re-verify ("ceilings re-verified
immediately before spending, not just at dry_run time"). `max_dph_usd` **is** correctly re-checked
synchronously inside `rent()` right before `create_instance` (`vast_client.py:207-210`) — that half
is solid. `max_total_usd_per_rental`/`max_total_usd` is not independently re-checked anywhere near
the real spend, only compared once against a static config value that has no relationship to the
runtime/rate combination actually used.

**Fix suggestion**: either (a) thread `max_total_usd` through to `render_now`/`flush` and clamp
`deadline_epoch` so `dph × (deadline - now) / 3600 <= max_total_usd` before calling `rent()`, or (b)
add a `config.validate()` cross-check that `max_dph_usd × max_runtime_minutes / 60 <=
max_total_usd_per_rental` (and the batch equivalent) so an inconsistent config/override combination
fails loud instead of silently exceeding the stated ceiling.

**Test gap confirming this was never caught**: `tests/test_cloud_render_tool_registration.py`
exercises `max_total_usd` only against the static-ceiling refusal path (lines 217-278); no test
asserts a rental is actually bounded by the caller's `max_total_usd` value, because no code path
does that.

---

## High-priority findings

### 2. Missing server-startup reap — deviates from the documented 4-layer design

Phase 01's safety design explicitly states the reaper "runs at every entry point (both tools'
`execute` *and* `dry_run`, CLI, server startup)", mirroring `server/queue_worker.py`'s
`_reconcile_orphans`-at-construction pattern. `grep -rn "cloud_render\|ledger.reap" server/` returns
nothing — `server/app.py`/`server/queue_worker.py` never call `lib.cloud_render.ledger.reap()`.

In practice today `reap()` still runs on every `dry_run()` (via `announce.build()`,
`announce.py:26`) and transitively on every `execute()` (via `render_now`/`flush`, both call
`ledger.reap()` as their first line), so the gap only matters for a **long-running server process
that never again touches cloud-render tooling** after a rental goes stale — it has no automatic
sweep beyond the on-instance deadline `shutdown -h` watchdog (whose own billing behavior is an open
question in the plan). Not a "will leak money" bug given the other three layers, but a real
deviation from the stated design and a one-line fix (`ledger.reap()` at server startup, same
pattern as the local job queue's own orphan reconciliation).

### 3. `render_now`/`flush` are not self-defending against `enabled: false` or the `dry_run_ref` gate

`config["enabled"]` and the `dry_run_ref` check both live exclusively in
`VastCloudRender.execute()` (`vast_cloud_render.py:245-259`) — `lib.cloud_render.remote.render_now()`
and `lib.cloud_render.queue.flush()` themselves have no internal `enabled` check and no offer
provenance check. Today there is exactly one call site for each (confirmed by grep — only
`vast_cloud_render.py` and the test suite call them), so this is not an active bypass. But it means
the safety gates are enforced by *convention* at the tool layer, not by the library functions
themselves — any future direct import of `lib.cloud_render.render_now`/`flush` (a script, a
notebook, a different tool) would skip `enabled` and the "no unilateral substitution" guarantee
entirely. Recommend a defensive `if not config.get("enabled"): raise CloudRenderError(...)` at the
top of both functions so the guarantee is structural, not just a today-there's-one-caller fact.

---

## Verified safety-critical guarantees (matching the review brief's checklist)

- **try/finally destroy**: `render_now` (`remote.py:415-427`) and `flush` (`queue.py:496-507`) both
  wrap the rental lifetime in `try/finally`; `destroy()` failure is caught, logged, and the ledger
  is left `active` for the reaper rather than swallowed or re-raised past the `finally`. Confirmed
  by a real parametrized test over 8 failure points including `KeyboardInterrupt`/`SystemExit`
  (`tests/test_cloud_render_remote.py:607-702`), all green.
- **Pending-record recovery**: if `create_instance` succeeds but the local process dies/raises
  before `ledger.promote()` runs, `reap()`'s decision table (`ledger.py:206-309`) still finds the
  labelled instance via `show_instances()` and destroys it once past deadline, or treats a stale
  unpromoted `pending` record (>15 min old, no matching remote instance) as safely closeable —
  verified in code, matches the "never silently lost" requirement.
- **`max_dph_usd` re-checked at spend time**: `vast_client.rent()` re-checks
  `offer_dph > ceiling_dph` synchronously right before `create_instance`, independent of the
  earlier `dry_run`/`eligible` filter (`vast_client.py:207-210`).
- **`dry_run_ref` cannot be bypassed**: `execute()` calls `dry_run_store.resolve(dry_run_ref,
  offer_id)` (`vast_cloud_render.py:256-259`), which raises unless the offer_id is in a
  non-expired, previously-created ref (`dry_run_store.py:75-102`). No code path in `execute()`
  skips this call.
- **No autopilot leak**: grepped every call site of `render_now`/`flush`/the two tools across the
  repo — the only production entry points are `VastCloudRender.execute()` (offer_id +
  dry_run_ref-gated) and the CLI, which itself refuses `--render-location cloud` without
  `--cloud-offer`/`--cloud-yes` and refuses `--autopilot --render-location cloud` without
  `--cloud-yes` (`cli.py:160-183`, tested in `tests/test_cloud_render_cli_args.py`, 37 passed).
  `server/` has no cloud-render call at all.
- **Label-prefix reaper safety**: every reaper/vast_client read filters on
  `f"{LABEL_PREFIX}-"` (`ledger.py:35`, `vast_client.py:155-169`); `parse_label()` returns `None`
  for anything that doesn't match `openmontage-{intent_id}-until-{deadline}` exactly, so a human's
  unrelated instance is structurally unreachable.
- **No secrets leak**: checked `ledger.py` record shape, `queue.py`'s `batch-queue.json` shape,
  `dry_run_store.py`'s ref shape, and `announce.py`'s payload/formatter — none carry the API key,
  the private key contents, or `ssh_host`/`ssh_port`. `setup_key.py` generates a dedicated keypair
  and only ever reads/returns the `.pub` half via `public_key_text()`.
- **`config/cloud-render.json` ships `enabled: false`**, matched by `BUILTIN_DEFAULTS["enabled"] =
  False` in `config.py`, and `execute()` refuses before any SDK call when it resolves to `False`.

## Backward-compatibility / schema check

- `decision_log.schema.json`: enum widening only (`render_location_selection` appended) —
  additive.
- `render_report.schema.json`: new optional `render_location` object, `additionalProperties: false`
  internally but not added to the top-level `required` array (confirmed:
  `required: ["version", "outputs"]` unchanged) — additive, existing reports still validate.
- `edit_decisions.schema.json` **is** modified in the working tree (+53/-2), but the diff is scene-type/
  caption/chart fields for an unrelated in-flight "footage edit" feature (no `render_location`/`cloud`/`vast`
  string anywhere in it) — confirmed this plan did not touch it, matching its explicit "no change to
  edit_decisions" decision. Flagging only so it isn't confused with this plan's own diff during review.
- Contract test `tests/contracts/test_cloud_render_governance_contract.py` (15 tests) passes,
  covering both schema changes plus fixture re-validation.

## Registry / pattern compliance

- `registry.discover()` confirmed to import both new tool modules without importing `vastai`
  (`'vastai' not in sys.modules` after `discover()` in a live check) — lazy-import pattern matches
  `talking_head_autoedit.py`.
- Both tools use `dependencies = ["python:vastai", "cmd:ssh", "cmd:scp"]`, correctly avoiding the
  `binary:` prefix bug already present (and explicitly *not* fixed, per phase 04's own scoping
  note) in `talking_head_autoedit.py:41`.
- `lib/cloud_render/config.py` mirrors `assembly_config.py`'s `resolve`/`sources_of`/`_clean` shape
  faithfully, including the "`None` override is dropped, not applied" behavior for the same
  fail-loud-on-ceiling-removal reason.
- `tools/cost_tracker.py` and `config.yaml` are untouched, per the plan's explicit "existing gates
  suffice" decision — confirmed via `git status`.

## Minor / low-priority

### 4. Doc/code mismatch: claimed core-count rescaling doesn't exist

`kit.py:163-165`'s docstring says `render_now`/`render_batch` scale `estimated_render_seconds` by
`(reference_cores / offer_cores)` once an offer is chosen. No such scaling exists in `remote.py` or
`queue.py` — `queue.py:480` and the per-item timeout both use `manifest.estimated_render_seconds`
raw. Low impact today because `offer_query` already constrains offers to
`cpu_cores_effective>=32`, in the same range as the `render_seconds_per_video_second` calibration
reference (44-core), but the comment should be fixed or the scaling implemented before that
constant drifts from reality.

### 5. Argv-parity test is a hand-copied golden snapshot, not a real git diff

`tests/test_talking_head_render.py`'s `_pre_refactor_build_command` is manually re-derived from
what the pre-refactor closure looked like (per its own docstring), not diffed against a real
previous commit — `lib/talking_head_edit/stages/render.py` has no git history at all (the whole
directory is untracked). The test is **not vacuous** — it asserts real list equality between two
independently-written argv builders across two scale branches, and a third test proves `run()`'s
internal `build_command` closure now delegates to the module-level builder. But I could not
independently verify "byte-identical to today's pre-refactor argv" against real history the way the
review brief asked, because that history doesn't exist in this repo. Not a defect; flagging as a
verification limit.

## Positive observations

- The `try/finally` + ledger + label-based stateless sweep + on-instance deadline shutdown 4-layer
  design is implemented faithfully and is well covered by tests, including real
  `KeyboardInterrupt`/`SystemExit` paths rather than mocked exceptions.
- `transfer.py`'s "never use `VastAI.copy()`" guardrail is documented with the exact SDK
  line/behavior that makes it dangerous (silent Windows drive-letter mis-parse), and no code path
  anywhere uses it — `grep -rn "VastAI().copy\|vastai copy" lib/ tools/` returns nothing.
  `scp_up`/`scp_down` pass local paths as single argv elements (no shell), correctly handling the
  `D:\CODE WITH AI\...` space-containing repo path.
- The announce block/`dry_run()`/skill template are mechanically tied together via
  `format_announce()` — the same formatter is used by `skills/core/cloud-render.md`'s template, the
  CLI's `--cloud-offers`, and render-now/flush, so they cannot drift from each other by construction.
- Single shared `build_remotion_command` for local and cloud is enforced by a lint-style test
  (`test_remote_module_source_has_no_second_hardcoded_render_argv`,
  `tests/test_cloud_render_remote.py:52`) that greps for a second hardcoded render argv — a good,
  cheap guard against exactly the fidelity-drift risk the plan calls out.
- Batch isolation (one bad job's kit/render failure doesn't cost the rest of the batch their
  rental) and incremental download (dequeue happens per-item, immediately) are both implemented and
  tested, including the deadline-mid-batch "stop cleanly, leave the rest pending" case.

## Recommended actions (priority order)

1. **Critical** — Wire `max_total_usd` (or add a `config.validate()` cross-check between
   `max_dph_usd`/`max_runtime_minutes`/`max_total_usd_per_rental`) so the per-rental spend ceiling
   is a real, enforced invariant rather than an artifact of today's default numbers happening to be
   consistent.
2. **High** — Add `ledger.reap()` to server startup to match the documented 4-entry-point design.
3. **High** — Add an internal `enabled`/gate check inside `render_now`/`flush` themselves so the
   safety guarantee doesn't depend on every future caller going through `VastCloudRender.execute()`.
4. **Low** — Fix or implement the `kit.py` core-count-rescaling docstring claim.
5. **Low** — No action required, but worth noting in the plan's own tracking: the argv-parity test
   cannot be checked against real git history because `lib/talking_head_edit/` has never been
   committed.

## Unresolved questions

1. Was the caller-supplied `max_total_usd` intended to be a hard per-call override of the deadline
   (i.e. should `render_now`/`flush` derive `max_runtime_minutes` FROM it), or purely a
   double-confirmation of the static config ceiling? The `input_schema` description ("Ceiling tong
   chi cho rental nay") reads like the former; the implementation does the latter (and only weakly).
   Needs a decision before finding 1 is fixed one way or the other.
2. Is committing `lib/talking_head_edit/` and `lib/cloud_render/` to git planned before or alongside
   merging this feature? Both are fully untracked today, which is why the argv-parity verification
   (finding 4/5) could not be done against real history.
