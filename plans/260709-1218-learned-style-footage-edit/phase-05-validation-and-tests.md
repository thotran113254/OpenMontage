# Phase 05 — Validation & Tests

## Context Links
- All prior phases (01-04)
- Existing tests location: `tests/` (mirror project convention)
- Schemas: `schemas/styles/playbook.schema.json`, `schemas/artifacts/footage_edit_plan.schema.json`

## Overview
- **Priority:** P2
- **Status:** completed
- **Description:** Validate each new artifact/tool in isolation, then a dry end-to-end trace through the hybrid pipeline. No fake data / no mocks that hide real behavior (per development-rules).

## Requirements
**Unit**
- Playbook validates against schema (Phase 01 success criterion, as a test).
- `footage_edit_plan.schema.json` validates a known-good sample (fixture built from the reference video's proven output).
- `SpeechGapDetector` span math: dead-air gap detection, filler lexicon match, merge/dedupe, edge cases (leading/trailing silence, adjacent fillers) — pure-python, no API.

**Integration**
- `FootageEditAnalyzer` registers and reports `available` when `GEMINI_API_KEY` set; `unavailable` (clean error) when unset.
- Analyzer on the reference mp4 (live, opt-in / not in CI by default — costs money): output schema-valid, EVERY beat transition/sfx ∈ P01b inventory (zero invented values), transitions not all-identical (variety), b-roll windows overlap the 3 known overlay ranges. Runs with `gemini-3.1-flash-lite`/HIGH default.
- Detector output shape merges into analyzer `removal_spans[]`.
- **P01b:** `resource_inventory.json` well-formed; each transition preset smoke-renders a visibly distinct result (manual/opt-in Remotion render); every playbook sfx/transition name has a matching inventory entry.
- **P06:** `NineRouterImage` registers, appears in `registry.get_by_capability("image_generation")`; `available` iff `NINE_ROUTER_API_KEY`+`NINE_ROUTER_BASE_URL` set. Live SSE call (gated `RUN_LIVE_NINEROUTER=1`) produces a valid PNG.

**End-to-end (dry trace)**
- Feed a sample `footage_edit_plan` through the four edited directors manually; confirm each field lands in a schema-valid `edit_decisions` (validate the hand-built result).

## Related Code Files
- **Create:** `tests/test_footage_edit_plan_schema.py`, `tests/test_speech_gap_detector.py`, `tests/test_footage_edit_analyzer_registration.py`, `tests/test_resource_inventory.py`, `tests/test_nine_router_image_registration.py`
- **Create:** `tests/fixtures/footage_edit_plan_sample.json`, `tests/fixtures/resource_inventory_sample.json`
- **Read:** all new tools/schemas + `assets_library/resource_inventory.json`

## Implementation Steps
1. Build a fixture `footage_edit_plan_sample.json` from the reference's proven Gemini output.
2. Write schema-validation tests (playbook + footage_edit_plan) using jsonschema.
3. Write `SpeechGapDetector` unit tests with synthetic word-timestamp lists (no whisper run) — inject transcript via `transcript_json` param.
4. Write registration + availability tests for `FootageEditAnalyzer` and `NineRouterImage` (skip live calls unless `RUN_LIVE_GEMINI=1` / `RUN_LIVE_NINEROUTER=1`). Add an inventory-membership unit test: given a fixture plan + inventory, assert the validator rejects out-of-set transition/sfx values.
5. Write `test_resource_inventory.py`: inventory well-formed, every playbook transition/sfx name present, no conceptual-only entries.
6. Hand-build an `edit_decisions` from the fixture per the P04 field map (sfx asset_id → real P01b file, transition_in → real preset); validate against `edit_decisions.schema.json`.
7. Run: `.claude\skills\.venv\Scripts\python.exe -m pytest tests/ -k "footage or speech_gap or inventory or nine_router"` (or project test runner). Fix failures — do not skip.

## Todo List
- [x] Fixture from proven output
- [x] Playbook + footage_edit_plan schema tests
- [x] SpeechGapDetector unit tests (synthetic transcript)
- [x] Analyzer registration/availability test (live gated)
- [x] Hand-built edit_decisions validation
- [x] All pass; tests complete

## Success Criteria
- All non-live tests pass in CI.
- Live analyzer test passes on demand (documented flag), meets transition-variety + b-roll-overlap bars.
- Hand-built `edit_decisions` from the field map is schema-valid — proves P04 mapping is realizable.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Live Gemini test flaky/costly in CI | High | Low | Gate behind `RUN_LIVE_GEMINI`; default-skip |
| Fixture drifts from real output shape | Med | Med | Derive fixture from an actual analyzer run, refresh when model pinned |

## Security Considerations
- Tests must not commit any API key; live test reads env only. No customer data in fixtures (mock chat content).

## Next Steps
On green: `code-reviewer` pass, then `docs-manager` to log the new capability in `docs/` + changelog.
