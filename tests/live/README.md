# Live cloud-render suite — costs real money

`test_cloud_render_live_smoke.py` rents a real Vast.ai instance. Every other
test in this repo runs against a fake `vastai` module injected into
`sys.modules` and never touches the network — this is the one exception,
on purpose, to prove the fake's assumptions still match the real API.

**Never runs automatically.** `pytest.ini` deselects everything marked
`@pytest.mark.live` by default (`addopts = -m "not live"`), and
`tests/test_cloud_render_live_gate.py` (which *does* run in the default
suite) fails the build if `VAST_LIVE_TEST` is ever set while `CI` is set.

## Expected spend

| Test | What it does | Rough cost |
|---|---|---|
| `TestLiveSmokeHappyPath` | rent cheapest offer under ceiling → render a 2s synthetic clip → pull → destroy | < $0.01 |
| `TestLiveSmokeCrashPath` | rent → hard-kill the render process mid-flight → prove a **fresh process** (`python -m lib.cloud_render.reap`) still destroys it | < $0.01 |
| `TestLiveSmokeFidelity` | render the same props locally and on the instance, diff via PSNR — slowest, priciest of the three | < $0.03 |

**Total across all three: well under $0.05.** Each test independently
computes its own pre-flight cost estimate and refuses to rent if that
estimate is not below `VAST_LIVE_MAX_USD` — the ceiling is not just an
opt-in switch, it is re-checked.

None of the three tests upload real footage. `_build_synthetic_smoke_job()`
generates a 2-second solid-color clip with `ffmpeg` on the fly — nothing
from `projects/` ever leaves this machine via this suite.

## How to run

Requires:

- A working Vast.ai API key already set up (`vastai set api-key`, or
  `VAST_API_KEY` in `.env` — same as any other use of this feature).
- The dedicated cloud-render SSH keypair created once:
  `python -m lib.cloud_render.setup_key` (defaults to
  `~/.ssh/openmontage_cloud_render`, matching `config/cloud-render.json`).
- `ffmpeg`/`ffprobe` on `PATH` (used both to synthesize the tiny smoke clip
  and to verify/compare the rendered output).

```
VAST_LIVE_TEST=1 VAST_LIVE_MAX_USD=0.05 make cloud-render-live-test
```

Or directly:

```
VAST_LIVE_TEST=1 VAST_LIVE_MAX_USD=0.05 python -m pytest tests/live/ -v -m live
```

Both env vars are required; either missing and every test in this module
skips with a message pointing back here.

## After it runs

Check `plans/260806-1404-vastai-cloud-render/reports/live-smoke-measurements.jsonl`
for the real boot/`npm ci`/render timings and actual `$` spent each test
recorded — that data is what `render_seconds_per_video_second` (used by
every cost estimate in the default suite) should eventually be recalibrated
from, per the phase's own requirement.

Then double-check the account directly (`vastai show instances` or the
dashboard) that nothing `openmontage-*`-labelled is still running. Every
test here asserts this itself in a `finally`, but this is real money — a
second manual look costs nothing and catches the case where the test
process itself got killed before its own assertion ran.

## Recommended cadence

Do **not** put this on a schedule (weekly cron, nightly CI, etc.) — that is
recurring spend for a check that only matters when something relevant
changes. Run it manually:

- Whenever `vastai` (the PyPI package) is upgraded — see the version pin in
  `requirements.txt`.
- Whenever `lib/cloud_render/remote.py`, `vast_client.py`, or
  `lib/talking_head_edit/stages/render.py::build_remotion_command` change in
  a way the fake-remote fidelity test (`tests/test_cloud_render_fidelity.py`)
  cannot fully cover (e.g. anything about real Vast.ai boot/API behavior).
- Before relying on it for a real production render for the first time.

## R2 live smoke (`test_r2_live_smoke.py`)

Separate opt-in gate, separate cost model — R2 has no per-request fee that
matters at this scale (a handful of Class A/B ops), but this is still the
only test in the repo that touches the real bucket instead of `moto[server]`.

```
R2_LIVE_TEST=1 python -m pytest tests/live/test_r2_live_smoke.py -v -m live
```

Requires `CLOUDFLARE_R2_*` already set in `.env` (see `docs/r2-storage.md`)
and the bucket already created. Writes a single ~20-byte object under
`livetest/<random>.txt`, exercises put/list/presign/get, and deletes it in a
`finally` — never touches `projects/` or `render-kits/`. Run it:

- After rotating R2 credentials or changing the bucket/endpoint in `.env`.
- Whenever `lib/r2_storage/client.py`'s checksum/region config changes —
  moto does not emulate R2's stricter multipart/signature rules, so this is
  the only test that would actually catch a real rejection.
