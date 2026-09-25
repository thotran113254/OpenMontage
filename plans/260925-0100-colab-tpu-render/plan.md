# Render on Colab TPU v6e-1 (fully automated)

Status: done · Decision (user, 2026-09-25): option A, TPU v6e-1 (44 vCPU EPYC 9B14, ~4.08 CU/h)

## Outcome
`render_location: "colab"` renders a job's MP4 on Colab account 1 without any manual step, and
the VPS only builds the kit, drives the browser and verifies the result.

## Design
- Kit and transfer: reuse `lib/cloud_render/kit.py` + R2 (composer kit content-addressed, job kit
  per run). Presigned GET for kits and a generated `run.sh`; presigned PUT for `status.json`
  (overwritten every ~15s), `render.log` and `out/final.mp4`. Colab never holds a credential.
- Browser: `lib/cloud_render/colab_browser.py` drives agent-browser session `colab-cdp` on CDP
  `9222` (account 1). It never touches `colab2`. Steps: logged-in check → dedicated notebook →
  runtime `v6e-1 TPU` → connect → one cell `!curl -fsSL <run.sh> | bash` → `runtime.unassign()`.
- Orchestrator: `lib/cloud_render/colab.py` — single-flight lock, polls `status.json` through R2,
  emits progress, enforces a deadline, verifies with the same code as Vast.ai
  (`remote.finalize_output`), always unassigns the runtime and deletes temp keys.
- Entry points: render stage branches on `options.render_location == "colab"`, so the UI's render
  button, `/api/runs`, the queue and autopilot all work unchanged. Config: `config/colab-render.json`.

## Acceptance
- A real job renders on v6e-1, final.mp4 passes the duration check, runtime is unassigned.
- Unit tests for script building, status parsing, lock and failure cleanup.
- UI shows a "Render trên Colab (TPU v6e)" action; the API accepts `render_location`.

## Result (2026-09-25)
- Real 240 s job rendered on v6e-1: final.mp4 187 MB, H.264 1080×1920, duration check passed;
  ~14 min end to end (~40 s connect, ~55 s setup, ~11 min render), ~0.9 CU; runtime released,
  no active sessions, no leftover R2 keys. VPS render of the same job: ~20 min of local CPU.
- `veryfast` x264 gave no speedup (frame capture is the bottleneck) → default preset kept.
- Fixes found live: `/notebook#create=true` path; toolbar Connect ignores synthetic clicks → menu item.
- 897 autoedit tests green; UI typecheck clean; docs/colab-render.md.
