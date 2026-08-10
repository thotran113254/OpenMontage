# Phase 04 — Hybrid Pipeline Integration

## Context Links
- Manifest: `pipeline_defs/hybrid.yaml`
- Directors: `skills/pipelines/hybrid/{script,scene,asset,edit}-director.md`
- Loader (custom_tools question): `lib/pipeline_loader.py`
- Artifacts: `schemas/artifacts/{edit_decisions,scene_plan,asset_manifest}.schema.json`
- Scene realization options: `remotion-composer/SCENE_TYPES.md`
- Composition rules: `AGENT_GUIDE.md:117-140`

## Overview
- **Priority:** P1
- **Status:** complete
- **Description:** Wire the P02 `footage_edit_analyzer` (and P03 detector) into the hybrid pipeline so its `footage_edit_plan` grounds the script → scene_plan → asset → edit chain, and document how b-roll inserts and removal spans flow to `edit_decisions`. Runtime & scene-realization decisions are flagged, NOT chosen.

## Key Insights
- Hybrid stages (`hybrid.yaml:46-227`): `script` has `tools_available:[transcriber,scene_detect,audio_enhance]`; `scene_plan` has `[frame_sampler,scene_detect]`; `edit` has `[]`. To expose the analyzer, add it to the `script` stage `tools_available` (it needs the raw video + produces the beat/transcript grounding the script depends on).
- **BLOCKER TO VERIFY FIRST:** `hybrid.yaml:24` `extensions.custom_tools: false`. Must confirm (read `lib/pipeline_loader.py`) this refers to per-project injected custom tools, not registry tools listed in `tools_available`. If it blocks, expose the tool via a `source_media_review`-style pre-idea analysis step instead.
- `edit_decisions.schema.json` already has every field needed to realize the plan: `cuts[].layer` (primary anchor vs overlay), `overlays[]` (b-roll windows), `audio.sfx[]` (sfx cues), `subtitles` (caption style), plus `render_runtime`/`composition_mode` locked at proposal. No schema change needed for edit.
- Directors are Markdown instructions (agent behavior), not code — integration = editing these `.md` files to teach the agent to read `footage_edit_plan` and translate it. No orchestration code.

## Requirements
**Functional**
- `script-director.md`: read `footage_edit_plan.beats[]` + `transcript[]`; produce a script whose sections mirror the beats and explicitly flag each `b_roll.needed` beat as a support-led narrative gap (per `hybrid.yaml` review_focus "source-led vs support-led beats separated").
- `scene-director.md`: for each b-roll window, create a support scene entry (source stays primary layer) describing the required insert (`suggested_visual`) and its on-screen window.
- `asset-director.md`: generate the insert asset per support scene (chat-UI / product-demo mock). When a b-roll beat needs a generated IMAGE and no real footage exists, route through `image_selector` → the P06 `nine_router_image` tool (or other available image provider); announce provider before the paid call. Add a step pointing to the scene-realization decision (below). Keep source-vs-generated map (`asset-director.md:44-52`).
- `edit-director.md`: add explicit translation rules — beat→`cuts[]` (anchor `layer:primary`, zoom via `transform.animation`/`scale`), b-roll window→`overlays[]`, `sound_effect`→`audio.sfx[]` (asset_id resolves to a real P01b `assets_library/sfx/` file), `transition.type`→`cuts[].transition_in` (a real P01b preset name), `removal_spans`→omitted source ranges in the anchor `cuts[]` in/out. Preserve `render_runtime`/`composition_mode` unchanged from proposal.
- `hybrid.yaml` script stage: add `footage_edit_analyzer` (and `speech_gap_detector`) to `optional_tools` + `tools_available` (pending BLOCKER verify).

**Non-functional**
- Director edits additive; do not remove existing guidance. Markdown, no 200-line rule (config/doc exempt per CLAUDE.md).

## Architecture — where each field lands
| footage_edit_plan | Stage | edit_decisions target |
|-------------------|-------|-----------------------|
| `transcript[]`, `beats[].spoken_text` | script | script sections + `subtitles.source` |
| `beats[].b_roll` | scene_plan / assets | support scene + generated asset (via P06 `nine_router_image`) → `overlays[]` |
| `beats[].zoom` | edit | `cuts[].transform.scale`/`animation` |
| `beats[].transition` | edit | `cuts[].transition_in` + `transition_duration` (P01b preset name) |
| `beats[].sound_effect` | edit | `audio.sfx[]` (asset_id = real P01b `assets_library/sfx/` file) |
| `removal_spans[]` | edit | dropped ranges → anchor `cuts[]` in/out boundaries |

## Related Code Files
- **Edit:** `pipeline_defs/hybrid.yaml` (script stage `tools_available`/`optional_tools`; assets stage already has `image_selector` which auto-discovers P06)
- **Edit:** `skills/pipelines/hybrid/script-director.md`, `scene-director.md`, `asset-director.md`, `edit-director.md`
- **Read (no edit):** `lib/pipeline_loader.py` (verify custom_tools), `schemas/artifacts/*.schema.json`, `remotion-composer/SCENE_TYPES.md`, `assets_library/resource_inventory.json` (P01b)

## Dependency note
Blocked by P02 (footage_edit_plan), P03 (removal_spans), and P06 (`nine_router_image` — asset-director's b-roll image path). `image_selector` auto-discovers P06 with no manifest edit (assets stage `tools_available` already lists `image_selector`, `hybrid.yaml:131-139`).

## Approval Gates (surface to user, DO NOT decide here)
1. **Composition runtime (HARD RULE `AGENT_GUIDE.md:117-131`):** present Remotion / HyperFrames / FFmpeg with tradeoffs for THIS brief; log `render_runtime_selection` with full shortlist. Plan text must instruct the agent to run this gate at proposal — not pre-pick "remotion" even though `hybrid.yaml:203` hints it.
2. **Authoring mode (`AGENT_GUIDE.md:133-140`):** templated vs atelier, logged `composition_mode`.
3. **B-roll insert realization:** options to present —
   - `screenshot_scene` (`SCENE_TYPES.md:27`) — drop a screenshot, animate scripted overlays. Likely sufficient for static chat-UI/product shots. Lowest effort.
   - New `ChatTranscript` Remotion component (`SCENE_TYPES.md:61` names it as a likely-next candidate) — animated chat bubbles; higher fidelity, requires new component work.
   - HyperFrames HTML/GSAP scene — if runtime=hyperframes.
   Flag as a build decision gated on gate #1; do not choose.

## Implementation Steps
1. Read `lib/pipeline_loader.py`; resolve the `custom_tools:false` question. Record answer in plan open-questions.
2. If registry tools allowed in `tools_available`: add `footage_edit_analyzer`, `speech_gap_detector` to script stage. Else: define a pre-idea analysis step producing `footage_edit_plan` as an `optional_artifacts_in` to `idea`/`script`.
3. Edit `script-director.md`: add "Consume footage_edit_plan" section (beats→sections, b_roll flags).
4. Edit `scene-director.md`: add "B-roll insert windows → support scenes" section.
5. Edit `asset-director.md`: add "Generate learned-style inserts" step + pointer to scene-realization gate.
6. Edit `edit-director.md`: add the field-translation table + `removal_spans` handling + runtime-carry-forward reminder.
7. Add a short note in each edited director referencing the `ugc-talking-head` playbook for caption/sfx consistency (Phase 01).

## Todo List
- [x] Verify `custom_tools:false` scope in pipeline_loader — CONFIRMED: gates only `check_extension_permitted()` calls (per-project injected custom scripts/playbooks/skills/tools), which nothing in the manifest tool-listing path (`get_required_tools()`, stage `tools_available`) invokes. No caller of `check_extension_permitted` exists anywhere in the codebase (grep-verified). Registry tools listed in a stage's `tools_available`/`optional_tools` are plain string arrays per `schemas/pipelines/pipeline_manifest.schema.json` (no enum, no extension-gate cross-check). Hypothesis confirmed — proceeded with direct manifest wiring, no fallback needed.
- [x] Wire analyzer + detector into script stage (or pre-idea step) — added `footage_edit_analyzer`, `speech_gap_detector` to `script` stage `optional_tools` + `tools_available` in `pipeline_defs/hybrid.yaml`; validated via `lib.pipeline_loader.load_pipeline("hybrid")` (schema-valid, no errors).
- [x] Update script-director — added Prerequisites row + Process #6 "Consume footage_edit_plan When Available"
- [x] Update scene-director — added Prerequisites row + Process #6 "B-Roll Insert Windows → Support Scenes"
- [x] Update asset-director (+ scene-realization gate pointer) — added Process #6 "Generate Learned-Style Inserts" with explicit realization-pointer note (gate #3 not decided)
- [x] Update edit-director (field map + removal_spans + runtime carry-forward) — added Process #6 (full field-translation table) + Process #7 (runtime/composition_mode carry-forward + escalation instructions, gates #1/#2 not decided)
- [x] Cross-link playbook in directors — all four directors reference `styles/ugc-talking-head.yaml` (quality_rules / asset_generation / pacing_rules) for caption/sfx consistency

## Success Criteria
- Manifest loads valid (`lib/pipeline_loader.py` parses; schema-valid per `schemas/pipelines/pipeline_manifest.schema.json`).
- Each director names `footage_edit_plan` and its exact consumption.
- edit-director documents the full field→edit_decisions mapping incl. removal_spans.
- Both approval gates explicitly instructed at proposal; no runtime/scene pre-pick in any edited file.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `custom_tools:false` blocks tools_available add | Med | High | Verify FIRST; fallback = pre-idea analysis-artifact path (no manifest tool add) |
| Agent silently defaults render_runtime (governance CRITICAL) | Med | High | edit-director + plan reinforce HARD RULE; reviewer `review_focus` already flags silent swap (`hybrid.yaml:202`) |
| Over-inserting b-roll (every beat) vs reference's sparse 3 | Med | Med | Playbook `quality_rules` cap; scene-director instructs "insert only on concrete-example beats" |
| Removal spans make anchor cut choppy | Med | Med | edit-director keeps natural pads (P03), reviews anchor-cut-coherence gate (`edit-director.md:44`) |

## Security Considerations
- No secrets in manifests/skills. Generated inserts must not embed real customer chat data — use mock content (note in asset-director).

## Next Steps
Enables Phase 05 end-to-end validation.
