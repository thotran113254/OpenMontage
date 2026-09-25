# OpenMontage

**MANDATORY: Read [`AGENT_GUIDE.md`](AGENT_GUIDE.md) before responding to ANY user message.**

Do not act on the user's request until you have read AGENT_GUIDE.md.
It contains routing rules that determine your first action based on what the user asked.
Skipping it WILL cause you to take the wrong action.

## This machine (local render)

- **Render location:** local Remotion/FFmpeg, or Colab account 1's TPU v6e-1 (44 vCPU, fully automated: `render_location: "colab"` / `--render-location colab`, see `docs/colab-render.md`; never touch the `colab2` account). `config/cloud-render.json` stays `"enabled": false`. Do not rent Vast.ai unless the user explicitly asks.
- **CPU:** 10 cores, ~30 GB RAM, no NVIDIA GPU — and **shared** with other projects. Pipeline runs at `nice 10` + `ionice` and sizes ffmpeg threads / Remotion workers from `AUTOEDIT_CPU_BUDGET` (default 60% of cores = 6); `OPENMONTAGE_RENDER_MAX_CONCURRENCY=5` in `.env`. See `lib/talking_head_edit/cpu_budget.py`.
- **Storage:** Cloudflare R2 bucket `openmontage-assets` (presigned URLs; no public base URL). Auto-sync is off.
- **Public bind:** VPS IP `<VPS_HOST>`, `AUTOEDIT_BIND=0.0.0.0`. Set `AUTOEDIT_API_TOKEN` in `.env` so the public API requires a token: automation sends `Authorization: Bearer`, the browser UI asks for it once at login (HttpOnly cookie).

| Service | Command | Port | Local | Public |
|---------|---------|------|-------|--------|
| Autoedit API | `make autoedit-server` | 8861 | http://127.0.0.1:8861 | http://<VPS_HOST>:8861 |
| Autoedit UI | `make autoedit-ui` | 5617 | http://127.0.0.1:5617 | http://<VPS_HOST>:5617 |
| Both | `make autoedit-dev` | 8861 + 5617 | http://127.0.0.1:5617 | http://<VPS_HOST>:5617 |
| Remotion Studio | `cd remotion-composer && npm start` | 3000 | http://localhost:3000 | — |

**Start (dev):** `source .venv/bin/activate` then `make autoedit-dev`
**Test:** `make autoedit-test` (pipeline) · `make autoedit-ui-typecheck` (UI) · `make test` (all)
**R2 status:** `.venv/bin/python -m lib.r2_storage.cli --status`

Env that must stay in `.env` for this VPS: `AUTOEDIT_BIND`, `AUTOEDIT_PUBLIC_HOST=<VPS_HOST>`, `AUTOEDIT_PORT=8861`, `AUTOEDIT_UI_PORT=5617`, `AUTOEDIT_API_TOKEN`.
