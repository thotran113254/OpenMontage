# Phase 06 — Tests + docs

## Context links

- Test style to mirror: `tests/test_cloud_render_config.py:38,196` (config layering +
  "the real committed config must validate"), `tests/test_cloud_render_tool_registration.py:197`
  (asserts the shipped config is `enabled:false` — a locked decision guarded by a test)
- CI live-secret guard to copy: `tests/test_cloud_render_live_gate.py:1-25`
- Live suite convention: `tests/live/README.md`, `pytest.ini:7-9` (`markers = live`,
  `addopts = -m "not live"`)
- Docs to touch: `docs/cloud-render.md` is the model for a human-facing feature guide;
  `docs/PROVIDERS.md`; `AGENT_GUIDE.md`
- Blocked by: phases 02, 03, 04

## Overview

- **Priority:** P2
- **Status:** completed
- **Effort:** 3h
- Lock the behaviour that matters with tests that never touch real R2, and document the
  feature for humans and for the agent.

## Key insights

1. **moto + `endpoint_url` is unreliable.** `@mock_aws` patches botocore, but our client
   is constructed with `endpoint_url=https://<acct>.r2.cloudflarestorage.com`. Rather than
   gamble on interception, use **`moto.server.ThreadedMotoServer`** (`moto[server]`), which
   gives a real `http://127.0.0.1:PORT` endpoint. A fixture overrides
   `CLOUDFLARE_R2_ENDPOINT_URL` to point at it — this exercises the *actual* client code
   path including multipart, at the cost of one dev dependency.
2. **moto 5.x uses `@mock_aws`**, not the removed `@mock_s3`.
3. **The zero-API-call assertion needs a counting layer**, not moto. Wrap the client with
   a `botocore` event hook (`before-send`) that increments a counter, so the "no-change
   sync issues no requests" criterion from phase 03 is actually testable.
4. **CI must never hold real R2 credentials.** Copy the `test_cloud_render_live_gate.py`
   pattern: an *unmarked* test that runs in the default suite and asserts
   `CLOUDFLARE_R2_SECRET_ACCESS_KEY` is unset whenever `CI` is set.
5. **`.github/workflows/ci.yml` already runs with `-m "not live"`** via `pytest.ini` addopts
   — verify before assuming; do not add a second marker.

## Requirements

**Test matrix**

| Level | File | Covers | Network |
|---|---|---|---|
| unit | `tests/test_r2_storage_config.py` | layering, `_clean`, credential-key rejection, presign clamp, bucket resolution, `is_configured` | none |
| unit | `tests/test_r2_storage_client_config.py` | checksum mode == `when_required`, `region_name=="auto"`, `mask_endpoint`, botocore logger level | none |
| unit | `tests/test_r2_sync_planner.py` | `plan_sync` pure: exclude globs, POSIX keys, mtime-only change, content change, stale-manifest discard, size guard, `record_external_upload` → skip | none |
| unit | `tests/test_r2_delivery_url.py` | `delivery_url` public vs presigned fallback + warning; `presigned_url`/`presigned_put_url` stay signed when public base is set | none |
| integration | `tests/test_r2_sync_roundtrip.py` | push → re-push (0 uploads, 0 API calls) → pull → prune, multipart threshold crossed, `--prune` scoped to its prefix | ThreadedMotoServer (localhost) |
| contract | `tests/test_r2_tool_registration.py` | discovery, capability/provider, dep prefixes ∈ {cmd,env,python}, UNAVAILABLE without boto3, shipped config is `enabled:false` | none |
| contract | `tests/test_r2_hooks_never_raise.py` | hook swallows a raising sync → job still completes, `warning` event emitted; **exactly one** `maybe_sync_job` call site, in `run_job` | none (stubbed) |
| contract | `tests/test_cloud_render_r2_transfer.py` | presigned URL on **stdin not argv**; no credential env names in `lib/cloud_render/`; verification failure ⇒ no `CopyObject` to the archive key + staging deleted; `curl` exit 22 → "presign hết hạn" | none (fake ssh) |
| guard | `tests/test_r2_live_gate.py` | no real R2 secret in CI env | none |
| live (opt-in) | `tests/live/test_r2_live_smoke.py` | real bucket: put/list/presign/get/delete a 1 KB object | real, gated |

**Docs**
- `docs/r2-storage.md` — new human guide: what it is, **the two manual Cloudflare dashboard
  prerequisites** (create bucket `openmontage-assets`; enable public access / attach a
  custom domain and put it in `CLOUDFLARE_R2_PUBLIC_BASE_URL`), `.env` fill-in, enabling,
  CLI usage, cost model, key layout (`projects/**` durable vs `render-kits/**` transient),
  manual pruning, troubleshooting, security notes.
- `docs/cloud-render.md` — rewrite the transfer section: scp is gone; the rental pulls its
  kit and pushes its output via short-lived presigned URLs and never holds credentials.
- `docs/PROVIDERS.md` — add the Cloudflare R2 row.
- `AGENT_GUIDE.md` — one short subsection under capability families noting the
  `object_storage` family exists and is opt-in. Do **not** hardcode env var names there
  (AGENT_GUIDE's own rule) — point at the registry's `install_instructions`.
- `.agents/skills/vastai/SKILL.md` — owned by phase 05, verify here that no stale `scp_up`/
  `scp_down` rows survive.

## Related code files

**Create**
- `tests/test_r2_storage_config.py`
- `tests/test_r2_storage_client_config.py`
- `tests/test_r2_sync_planner.py`
- `tests/test_r2_delivery_url.py`
- `tests/test_r2_sync_roundtrip.py`
- `tests/test_r2_tool_registration.py`
- `tests/test_r2_hooks_never_raise.py`
- `tests/test_cloud_render_r2_transfer.py`
- `tests/test_r2_live_gate.py`
- `tests/live/test_r2_live_smoke.py`
- `docs/r2-storage.md`

**Modify**
- `docs/PROVIDERS.md`
- `docs/cloud-render.md`
- `AGENT_GUIDE.md`
- `tests/live/README.md` — add the R2 section + the `R2_LIVE_TEST` env gate

Note: `tests/test_cloud_render_remote.py` is **owned by phase 05** (it must be updated in
the same commit that removes scp). Do not edit it here.

**Delete** — none.

## Implementation steps

1. Add a shared fixture (in `tests/conftest.py` if the existing style allows, else a local
   fixture) starting `ThreadedMotoServer`, creating the bucket, and monkeypatching
   `CLOUDFLARE_R2_ENDPOINT_URL` / `CLOUDFLARE_R2_ACCESS_KEY_ID` /
   `CLOUDFLARE_R2_SECRET_ACCESS_KEY` to test values.
2. Add a `counting_client` helper registering a `before-send` botocore event handler to
   count requests — used by the "no-change sync = 0 calls" assertion.
3. Write the unit tests (config, client config, planner, delivery URL) — < 1 s total.
4. Write the roundtrip test. Force a multipart upload by setting
   `multipart_threshold_mb: 1` in the test settings and writing a 3 MB file — do not
   generate a 100 MB fixture.
5. Write the registration + hook tests, and `tests/test_cloud_render_r2_transfer.py` with
   a fake ssh/transport recording `(argv, stdin)` pairs — the stdin-not-argv assertion is
   the one that keeps presigned URLs out of the rental's process list.
6. Write `tests/test_r2_live_gate.py` mirroring `tests/test_cloud_render_live_gate.py:18-25`
   (unmarked, asserts `CI` ⇒ no `CLOUDFLARE_R2_SECRET_ACCESS_KEY`).
7. Write `tests/live/test_r2_live_smoke.py` with `@pytest.mark.live`, skipped unless
   `R2_LIVE_TEST=1`; it must clean up the object it creates in a `finally`.
8. `pytest -q` — full suite green. Then `pytest -q -k r2` for the focused run.
9. Write `docs/r2-storage.md`; update `docs/PROVIDERS.md` and `AGENT_GUIDE.md`.
10. Confirm CI: read `.github/workflows/ci.yml` and verify `moto[server]` is installed via
    `requirements-dev.txt` and that no R2 secret is referenced anywhere in the workflow.

## Todo list

- [x] 1. `ThreadedMotoServer` fixture + env monkeypatch
- [x] 2. `counting_client` request-counter helper
- [x] 3. Unit: config / client config / planner / delivery URL
- [x] 4. Integration: push → re-push → pull → prune (prefix-scoped), multipart path
- [x] 5. Contract: tool registration + hooks-never-raise + cloud-render R2 transfer
- [x] 6. CI live-secret guard test
- [x] 7. Opt-in live smoke test + `tests/live/README.md` section
- [x] 8. Full `pytest -q` green
- [x] 9. `docs/r2-storage.md`, `docs/PROVIDERS.md`, `AGENT_GUIDE.md`
- [x] 10. Verify `.github/workflows/ci.yml` installs dev deps and holds no R2 secret

## Success criteria

- `pytest -q` passes with zero network egress to `*.r2.cloudflarestorage.com` (verify by
  running with the R2 env vars unset — every non-live test must still pass).
- `pytest -q -k r2` runs in < 15 s.
- Re-push of an unchanged directory asserts **exactly 0** HTTP requests.
- A test fails if someone changes `config/r2-storage.json` to `enabled: true`
  (locked-decision guard, same as `tests/test_cloud_render_tool_registration.py:197`).
- A test fails if any `dependencies` entry on `CloudflareR2` uses an unrecognized prefix.
- `pytest -q` with `CI=1 CLOUDFLARE_R2_SECRET_ACCESS_KEY=x` fails the live-gate test.
- `docs/r2-storage.md` contains a "do not commit `.env`" section and **both** manual
  Cloudflare prerequisites (bucket creation; public access / custom domain).
- `grep -rn "scp_up\|scp_down" docs/ .agents/` → no matches (stale Layer 3 guidance would
  make future agents call functions that no longer exist).
- A test fails if `maybe_sync_job` gains a second call site in `runner.py` (Q2 guard).

## Risk assessment

| Risk | L×I | Mitigation |
|---|---|---|
| `moto[server]` flaky/slow in CI (port binding, thread teardown) | Med × Med | Session-scoped fixture, port 0 (auto-assign), explicit `.stop()`; if it proves flaky, fall back to `botocore.stub.Stubber` for the roundtrip and keep only the planner unit tests |
| Tests written against moto pass but real R2 rejects (checksum, multipart uniformity) | **High** × High | moto does not emulate R2's stricter rules. The opt-in live smoke test is the only real check — run it once manually after phase 01, before writing the rest |
| Someone adds an R2 secret to CI "to test the real thing" | Low × High | `tests/test_r2_live_gate.py` runs in the default suite |
| Docs drift from the registry (`install_instructions` duplicated in prose) | Med × Low | `AGENT_GUIDE.md` points at the registry rather than restating env var names |
| `boto3` adds ~15 s to CI install | High × Low | Accept |

**Rollback:** delete the test files and revert the three doc edits. No production code
depends on them.

## Security considerations

- No test may read the real `.env`. Fixtures monkeypatch env vars explicitly; the unit
  tests must pass with the real vars **unset** (that is a success criterion, and it also
  proves no test silently depends on the developer's credentials).
- `tests/live/test_r2_live_smoke.py` writes to a `livetest/` prefix and deletes in
  `finally` — never touches `projects/`.
- `docs/r2-storage.md` must state: `.env` is gitignored and stays that way; only
  `.env.example` with empty placeholders is committed; rotate the R2 API token if it was
  ever pasted into a chat, an issue, or a log.

## Next steps

After green: delegate to `code-reviewer`, then `docs-manager` for a docs consistency pass.
Final step of the plan — no phase remains open.
