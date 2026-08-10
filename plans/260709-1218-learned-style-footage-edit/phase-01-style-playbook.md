# Phase 01 — Style Playbook (Learned UGC Talking-Head Style)

## Context Links
- Schema: `schemas/styles/playbook.schema.json`
- Reference shape: `styles/flat-motion-graphics.yaml`
- Learned facts: reference video analysis (this session), `edit_workflow_prompt.txt` scratchpad
- Consumed by: Phase 02 (tool params), Phase 04 (script/scene/edit directors)

## Overview
- **Priority:** P1 (blocks P02 parameterization)
- **Status:** completed
- **Blocked by:** Phase 01b — the playbook's `motion.transitions[]` and `audio.sfx_style` must name ONLY presets/effects that actually exist in the P01b inventory. Draft can proceed in parallel, but finalize transition/sfx names against P01b's `resource_inventory.json`.
- **Description:** Encode the reference's reusable edit grammar as a schema-valid `styles/*.yaml` playbook. This is the single source of truth for transition vocabulary, zoom rhythm, caption style, sfx vocabulary, and b-roll trigger heuristics that P02 reads.

## Key Insights
- Reference = single continuous talking-head anchor + exactly 3 UI/PiP demo overlays (~14-19s, ~30-39s, ~46-49s), each tied to a concrete product-demo line.
- Beats avg ~6s aligned to sentence boundaries; 5 distinct transition types used non-repetitively (cut, zoom_transition, whip_pan, crossfade, j_cut).
- Captions: bold animated keyword-highlight (TikTok style), Vietnamese, uses `BeVietnamPro-Bold.ttf` (already in `assets_library/fonts/`).
- Zoom motivated by emphasis words, NOT mechanical per-beat.
- **Playbook schema has no native "b-roll heuristic" or "beat pacing" field.** Encode those in `quality_rules[]` (free-form strings, schema `quality_rules` min 1) and `motion.pacing_rules`. Do NOT invent new top-level keys — schema is `additionalProperties:false` on most objects.

## Requirements
**Functional**
- New file `styles/ugc-talking-head.yaml` validating against `schemas/styles/playbook.schema.json`.
- `identity.category` must be one of enum — use `custom` (UGC talking-head is not motion-graphics/cinematic).
- `motion.transitions[]` = the 5 learned types. `motion.pacing_rules` = min 3s / max 8s scene hold, ~0.3s transition.
- `audio.sfx_style` = punchy pop/whoosh/impact vocabulary tied to cuts + keywords.
- `quality_rules[]` MUST encode, as explicit strings, the reusable heuristics P02/P04 read:
  - "Insert a UI/demo overlay when the script names a concrete example, product, or screenshot moment."
  - "Zoom only on emphasis words or new-subject reveals, never every beat."
  - "Beat boundaries align to sentence/clause ends; target 3-8s; never cut mid-word."
  - "Transitions must not repeat the same type more than ~2 beats running (avoid flash-lite monotony)."
  - "Captions: bold animated keyword highlight, one highlighted word per beat."

**Non-functional**
- Keep YAML < 120 lines, comments explaining each learned choice.

## Architecture
Playbook is passive config. P02's `footage_edit_analyzer` loads it and injects `transitions`, `pacing_rules`, `sfx_style`, and the b-roll/zoom `quality_rules` into the Gemini prompt (parameterizing the proven `edit_workflow_prompt.txt`). P04 directors read the same file for consistency (captions, sfx). No code depends on new schema fields — only on documented existing ones.

## Related Code Files
- **Create:** `styles/ugc-talking-head.yaml`
- **Read (no edit):** `schemas/styles/playbook.schema.json`, `styles/flat-motion-graphics.yaml`
- **No edits** to schema (reuse existing fields only).

## Implementation Steps
1. Copy the structure of `styles/flat-motion-graphics.yaml`; keep all 7 required top-level keys (`identity, visual_language, typography, motion, audio, asset_generation, quality_rules`).
2. Set `identity`: name "UGC Talking-Head Sales", category `custom`, mood "authentic, energetic, direct", pace `fast`, best_for talking-head UGC sales / demo vlogs.
3. `typography.headings.font` = "Be Vietnam Pro" (matches bundled font); weight 700-800; caption highlight style.
4. `motion.transitions` = the subset actually wired in P01b (e.g. `[none, fade, slide, wipe, clockWipe, flip]` — final list = whatever P01b implements). Do NOT list conceptual names like `whip_pan`/`j_cut` unless P01b maps them to a real preset. `pacing_rules.min_scene_hold_seconds: 3`, `max_scene_hold_seconds: 8`, `transition_duration_seconds: 0.3`.
5. `audio.sfx_style` = describe usage of the REAL P01b sfx set (e.g. "pop on keyword highlight, whoosh on slide/wipe, impact on reveals") — every named effect must exist as a file in `assets_library/sfx/`; set `music_volume` low (~0.1).
6. `asset_generation.image_prompt_prefix` for the chat-UI/demo insert look (clean mobile chat UI, product screenshot mock); `consistency_anchors[]` for insert framing (rounded phone frame, brand accent).
7. Fill `quality_rules[]` with the 5 heuristic strings above (verbatim intent).
8. Validate: `python -c "import json,yaml,jsonschema; jsonschema.validate(yaml.safe_load(open('styles/ugc-talking-head.yaml')), json.load(open('schemas/styles/playbook.schema.json')))"`.

## Todo List
- [x] Draft `styles/ugc-talking-head.yaml`
- [x] Encode transitions + pacing_rules
- [x] Encode sfx + caption style
- [x] Encode b-roll/zoom/beat quality_rules strings
- [x] Schema-validate; fix any `additionalProperties` violations

## Success Criteria
- File validates against `playbook.schema.json` (jsonschema passes).
- All 5 learned heuristics present as readable `quality_rules` strings.
- No invented top-level keys; every field maps to a schema property.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Encoding heuristics in `quality_rules` strings is stringly-typed; P02 must parse intent | Med | Med | P02 injects rules as prompt text, not parsed logic — no brittle parsing |
| `category` enum lacks "ugc" — using `custom` may reduce downstream routing hints | Low | Low | `custom` is the schema-intended escape hatch; `best_for` carries routing signal |

## Security Considerations
None — static config file, no secrets, no execution.

## Next Steps
Unblocks Phase 02 (parameterization) and Phase 04 (director consistency).
