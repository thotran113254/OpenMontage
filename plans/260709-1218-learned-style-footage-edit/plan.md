---
title: "Learned-Style Auto-Edit for Raw Talking-Head Footage"
description: "Encode a reference UGC edit style into a playbook + Gemini analysis tool that auto-cuts raw talking-head footage through the hybrid pipeline."
status: completed
priority: P2
effort: 34h
branch: main
tags: [hybrid-pipeline, analysis-tool, style-playbook, gemini, footage-led]
created: 2026-07-09
completed: 2026-07-09
---

# Learned-Style Auto-Edit for Raw Talking-Head Footage

## Goal
Turn the learned edit style of a finished reference UGC sales vlog into a **reusable, automated capability**: given NEW raw talking-head footage (single continuous take, no b-roll), the `hybrid` pipeline auto-produces beats, zoom rhythm, transitions, filler/dead-air cuts, sound cues, and **newly generated** support/overlay assets (chat-UI / product-demo inserts) matching the reference style.

## Why hybrid (not reference-driven generation)
Future input is real footage that must stay the anchor layer with generated support inserts composited on top. Per `AGENT_GUIDE.md:243` + `pipeline_defs/hybrid.yaml:1-9`, that is exactly `hybrid` (stability: production). Reference video was used only to *learn* the style, not to be re-generated.

## Phases
| # | Phase | Effort | Blockers |
|---|-------|--------|----------|
| 01 | [Style playbook](phase-01-style-playbook.md) — encode learned style as `styles/*.yaml` | 3h | 01b (inventory names) |
| 01b | [Resource library bootstrap](phase-01b-resource-library-bootstrap.md) — real SFX files + wired Remotion transition presets + inventory manifest | 6h | none |
| 02 | [Footage edit-analysis tool](phase-02-footage-edit-analysis-tool.md) — Gemini tool + artifact schema (inventory-constrained prompt) | 8h | 01, 01b |
| 03 | [Filler-word / dead-air detection](phase-03-filler-deadair-detection.md) — word-gap + lexicon cut points | 4h | none |
| 04 | [Hybrid pipeline integration](phase-04-hybrid-pipeline-integration.md) — wire tool + director skills | 5h | 02, 03, 06 |
| 05 | [Validation & tests](phase-05-validation-and-tests.md) — schema + tool + e2e | 3h | 01-04, 06 |
| 06 | [B-roll image gateway tool](phase-06-broll-image-gateway-tool.md) — NineRouter OpenAI-images-compatible provider | 5h | none |

## Data flow (end to end)
```
[P01b] resource inventory (real sfx files + wired transition presets)
     => available_sound_effects[] + available_transitions[]  (injected as prompt constraint)

raw talking-head mp4
  -> [P02] footage_edit_analyzer (Gemini gemini-3.1-flash-lite/HIGH + P01 playbook + P01b inventory)
       + [P03] speech_gap_detector (transcriber word-timestamps + filler lexicon)
     => footage_edit_plan artifact (transcript, beats[zoom/transition/broll/sfx], removal_spans)
        — transition/sfx choices constrained to the real P01b inventory, varied across beats
  -> [P04] script-director   consumes beats+transcript  -> script (b-roll narrative gaps flagged)
  -> [P04] scene-director    consumes broll_needs       -> scene_plan (support scene per insert)
  -> [P04] asset-director    b-roll image via [P06] nine_router_image (when no real footage) -> asset_manifest
  -> [P04] edit-director     beats->cuts[], broll windows->overlays[], sfx->audio.sfx[] (P01b files),
                             transitions->cuts[].transition_in (P01b presets),
                             removal_spans->dropped source ranges -> edit_decisions
  -> compose (existing)      -> final.mp4
```

## Key architectural facts (verified this session)
- Tools auto-register: `registry.discover()` walks the `tools` package (`tools/tool_registry.py:118-134`). A new `BaseTool` subclass file in `tools/analysis/` registers with NO manifest edit.
- No existing tool wraps Gemini video (`video_analyzer.py` is local-only). Google-genai pattern already used in `tools/graphics/google_imagen.py` via `tools/google_credentials.py`. `GEMINI_API_KEY` in project `.env`.
- `styles/` playbooks validated by `schemas/styles/playbook.schema.json`; hybrid `compatible_playbooks.custom_allowed: true` (`hybrid.yaml:44`).
- `edit_decisions.schema.json` already supports `cuts[]` (layer primary/overlay), `overlays[]`, `audio.sfx[]`, `subtitles`, `render_runtime`, `composition_mode` — the P02 artifact maps cleanly onto it.
- `transcriber` emits word-level `start/end/probability` (`transcriber.py:170-179`) — enough for dead-air gaps + filler lexicon. `audio_energy` is 1s-granular, music-tuned (`audio_energy.py:184-200`) — too coarse for precise cut points; used only as corroboration.
- Existing prototype `scripts/footage_edit_pipeline/` (ffmpeg cut/crossfade/zoom/sfx/subtitle/music) is a mechanics reference for the compose path, NOT the registered tool.
- **Resource reality (verified):** `assets_library/sfx/` has exactly ONE file (`whoosh_light.wav`); `assets_library/music/` does not exist. Gemini must NOT be allowed to name sfx that don't exist → P01b builds a real curated set.
- **Transitions reality (verified):** `@remotion/transitions` is declared (`remotion-composer/package.json:16`) but has ZERO usages in `remotion-composer/src` (grepped, none) — no transition preset is wired into any scene/component. P01b is new engineering, not documentation.
- Image-gen tool shape reference: `tools/graphics/google_imagen.py` (`capability="image_generation"`, `runtime=API`, env read dynamically) — the model for P06's `nine_router_image`.

## Approval gates — DO NOT decide in this plan (surface at proposal time)
1. **Composition runtime (HARD RULE, `AGENT_GUIDE.md:117-131`)** — Remotion vs HyperFrames vs FFmpeg must both/all be presented to the user; log `render_runtime_selection`. Plan stays runtime-neutral.
2. **Composition authoring mode (`AGENT_GUIDE.md:133-140`)** — templated vs atelier is a separate user decision logged as `composition_mode`.
3. **B-roll scene realization** — chat-UI / product-demo insert: existing `screenshot_scene` (`SCENE_TYPES.md:27`) may suffice, OR a new `ChatTranscript` Remotion component (`SCENE_TYPES.md:61`), OR HyperFrames HTML/GSAP. This is a downstream build decision gated on #1 — flagged, not chosen.

## Architectural Decisions — Deferred to Pipeline Execution

These three decisions intentionally remain open per `AGENT_GUIDE.md:243` governance rules. They are proposal-time gates, not implementation gaps. Resolved when the pipeline is actually run against real user video.

1. **Composition runtime** (`AGENT_GUIDE.md:117-131`): Remotion vs HyperFrames vs FFmpeg must be presented to user; log `render_runtime_selection`. Plan stays runtime-neutral; P04 directors surface this as a hard gate at proposal.
2. **Composition authoring mode** (`AGENT_GUIDE.md:133-140`): templated vs atelier is user decision logged as `composition_mode`. P04 edit-director flags this at proposal.
3. **B-roll scene realization** (P04 gate #3): chat-UI / product-demo insert via `screenshot_scene` (existing, simple) vs new `ChatTranscript` Remotion component (higher fidelity) vs HyperFrames HTML/GSAP. Downstream build decision gated on runtime choice (#1).

## Resolved Questions

- **custom_tools:false scope (P04):** Gates only per-project injected custom extensions; registry tools in `tools_available` are unblocked. Confirmed verified by source (`lib/pipeline_loader.py:169-196`).
- **Vietnamese filler lexicon adequacy (P03):** Implemented both lexicon + Gemini corroboration; lexicon conservative (curated subset), confidence-gated, user-overridable.
- **Model default (P02):** `gemini-3.1-flash-lite` with `thinking_level=HIGH` confirmed; flash-lite 20s vs 3.5-flash minutes tradeoff accepted by user. Architectural fix (inventory-constrained + vary-across-beats instruction) mitigates earlier repetitiveness A/B finding.
- **P06 NineRouter SSE schema:** Verified empirically (one live call). Terminal event = `done`, final image = `data.data[0].b64_json` (base64 PNG). Implementation confirmed unit-tested against observed schema.
