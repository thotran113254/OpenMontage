# Phase 01b — Resource Library Bootstrap (Real SFX + Wired Transition Presets + Inventory Manifest)

## Context Links
- Current assets: `assets_library/` (verified: `sfx/whoosh_light.wav` only; no `music/`)
- Transition package: `remotion-composer/package.json:16` (`@remotion/transitions` declared, ZERO usages in `src/`)
- Remotion scene dispatch: `remotion-composer/src/Explainer.tsx`, `src/components/index.ts`
- SFX generation: `.agents/skills/sound-effects` / `elevenlabs` (Layer 3; verify exact skill via registry)
- Consumed by: Phase 02 (prompt inventory injection), Phase 01 (playbook names), Phase 04 (edit-director sfx/transition realization)

## Overview
- **Priority:** P1 (hard blocker for P02's inventory-constrained prompt)
- **Status:** completed
- **Description:** Build the small, curated, REAL resource inventory the system can actually render, and publish it as a machine-readable manifest. Two concrete deliverables that do not exist today: (1) a curated SFX file set, (2) working Remotion transition presets wired via `@remotion/transitions`. Then emit `resource_inventory.json` enumerating what exists so P02 can constrain Gemini to real options only.

## Key Insights
- Verified gap: only ONE sfx file exists; `@remotion/transitions` is a dependency with no code using it. Letting Gemini free-choose transition/sfx names (current `edit_workflow_prompt.txt` uses free enums like `whip_pan`, `j_cut`) produces choices that map to NOTHING renderable. Root cause of the earlier "repetitive cut" A/B finding is lack of a real, varied menu — an architectural gap, not a model-intelligence ceiling.
- Keep it SMALL and curated (YAGNI): ~5 sfx, ~5-6 transition presets. Enough for variety, not an exhaustive library.
- `@remotion/transitions` provides `TransitionSeries` + presets (`fade`, `slide`, `wipe`, `clockWipe`, `flip`) + timings (`linearTiming`, `springTiming`). Wire the subset that is realistically implementable into the compose path.

## Requirements
**Functional — SFX set**
- **DONE (2026-07-09):** 8 real sfx files now in `assets_library/sfx/`, sourced from the user's own CapCut cache (cross-referenced via `draft_content.json` materials.audios[] to recover real names), converted to 44.1kHz stereo `.wav`: `whoosh_light.wav` (pre-existing), `whoosh-swish-large.wav`, `swish-short.wav`, `telop-pop-low.wav`, `typing-single-key.wav`, `page-turn.wav`, `slap.wav`, `ping.wav`. Exceeds the ≥5 target; no ElevenLabs generation needed.
- Remaining work for this sub-item: none — just fold these 8 names into `resource_inventory.json` (see below) with simple tags.

**Functional — Transition presets**
- **RESOLVED (user decision, 2026-07-09):** exactly 4 presets — `none` (hard cut), `fade`, `slide`, `wipe`. No `clockWipe`/`flip`/custom flash. Matches the user's actual CapCut usage history (mostly hard cuts, occasional slide-left; confirmed simple/basic is the explicit goal for their short-video flow) and keeps the set minimal per YAGNI.
- Wire `@remotion/transitions` into the Remotion compose path so `edit_decisions.cuts[].transition_in` values actually render. Implement a named preset map for these 4 names using `TransitionSeries` + `linearTiming`/`springTiming`.
- Extend the cut/scene dispatch (`Explainer.tsx` + `components/index.ts`) so a transition name on a cut is applied between adjacent cuts. Document the new names in `remotion-composer/SCENE_TYPES.md` (per its "Adding a new scene type" convention).
- Provide a runtime-neutral note: if the locked runtime is HyperFrames/FFmpeg (decided at proposal, not here), the transition realization differs — P04/compose handle routing. This phase implements the Remotion path (the hybrid manifest's typical runtime) but the inventory manifest stays runtime-labeled.

**Functional — Inventory manifest**
- Emit `assets_library/resource_inventory.json`: `{ "sound_effects": [{name, file, tags}], "transitions": [{name, runtime, preset}], "generated_at" }`.
- This is the single source P02 reads to build `available_sound_effects[]` / `available_transitions[]`.

**Non-functional**
- Any new JS/TS wiring file kept focused; document in SCENE_TYPES.md. Manifest is data, not code.

## Architecture
```
assets_library/
  sfx/{whoosh,pop,click,impact,riser}.wav      (real files)
  resource_inventory.json                       (enumerated inventory)
remotion-composer/src/
  transitions/preset-map.ts (new)  -> maps name -> @remotion/transitions preset+timing
  Explainer.tsx (edit)             -> apply cut.transition_in between cuts via TransitionSeries
  components/index.ts (edit)       -> export preset map
SCENE_TYPES.md (edit)              -> document available transition names
```
Data out: `resource_inventory.json` → P02 prompt. Presets/files out → P04 edit-director + compose render real output.

## Related Code Files
- **Create:** sfx files in `assets_library/sfx/`, `assets_library/resource_inventory.json`, `remotion-composer/src/transitions/preset-map.ts`
- **Edit:** `remotion-composer/src/Explainer.tsx`, `remotion-composer/src/components/index.ts`, `remotion-composer/SCENE_TYPES.md`
- **Read (no edit):** `remotion-composer/package.json`, `styles/ugc-talking-head.yaml` (P01 vocabulary alignment)

## Implementation Steps
1. Confirm the available sfx-generation skill/provider via registry (`registry.get_by_capability("music_generation")` / `.agents/skills/sound-effects`). Read its Layer 3 skill before generating (Rule Zero).
2. Generate/source the 4 missing sfx; normalize; place in `assets_library/sfx/`.
3. Read `@remotion/transitions` API (version `^4.0.484`); build `preset-map.ts` mapping names → `{presentation, timing}`.
4. Wire `TransitionSeries` into `Explainer.tsx` so adjacent cuts with a `transition_in` name render the preset; guard `none` = hard cut.
5. Render a 2-cut smoke composition per preset; visually confirm each transition actually plays (verification, not assumption).
6. Document transition names in `SCENE_TYPES.md`.
7. Write `resource_inventory.json` enumerating both sets (transitions tagged `runtime: remotion`).
8. Cross-check P01 playbook names == inventory names; reconcile.

## Todo List
- [x] Verify sfx provider + read Layer 3 skill — NOT NEEDED (8 real files sourced from user's CapCut cache instead)
- [x] Source/generate curated sfx set (5) — DONE 2026-07-09, 8 files, see Requirements above
- [x] Build `preset-map.ts` from `@remotion/transitions` — `src/transitions/preset-map.ts`
- [x] Wire TransitionSeries into Explainer.tsx dispatch — via `src/transitions/build-render-groups.ts` (contiguous-run detection) + `Explainer.tsx` render loop
- [x] Smoke-render each preset; confirm it plays — `smoke-test/transitions-fixture.json` + `npx remotion still`, verified fade/slide/wipe visually distinct, `none`/absent renders identical to pre-existing flat path
- [x] Document names in SCENE_TYPES.md — "Transitions (`cut.transition_in`)" section added
- [x] Emit resource_inventory.json — DONE 2026-07-09, `assets_library/resource_inventory.json`: 8 sound_effects (tags derived from P01 playbook's `audio.sfx_style` usage-context mapping) + 4 transitions (`none`/`fade`/`slide`/`wipe`, `runtime: "remotion"`, matching `preset-map.ts`)
- [x] Reconcile with P01 playbook vocabulary — DONE 2026-07-09, verified `styles/ugc-talking-head.yaml` `motion.transitions` (`[none, fade, slide, wipe]`) and `audio.sfx_style` names (`telop-pop-low`, `whoosh_light`, `swish-short`, `whoosh-swish-large`, `typing-single-key`, `page-turn`, `slap`, `ping`) are an exact 1:1 match with the inventory — no drift, no reconciliation edits needed

## Success Criteria
- `assets_library/sfx/` holds ≥5 distinct, normalized sfx; no missing playbook effect.
- Each transition name in the inventory renders a visibly different transition in a smoke composition (not a silent no-op).
- `resource_inventory.json` validates as well-formed and lists every real sfx + transition; P02 can load it.
- No inventory name is conceptual-only — every entry maps to a real file or wired preset.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `@remotion/transitions` wiring conflicts with existing scene dispatch | Med | Med | Additive TransitionSeries wrapper; smoke-test per preset; keep `none` as passthrough |
| sfx provider unavailable / quota | Med | Med | Fall back to royalty-free drop-in; inventory only lists what exists |
| Transition realization differs under non-Remotion runtime (gate #1) | Med | Low | Tag inventory `runtime`; P04/compose route; this phase owns Remotion path only |
| Over-building the library (scope creep) | Med | Low | Cap at ~5 sfx / ~6 transitions (YAGNI); curated not exhaustive |

## Security Considerations
- Use royalty-free / licensed sfx only; record provenance in the manifest. No secrets involved.

## Next Steps
Unblocks P02 (inventory injection) and finalizes P01 vocabulary; P04 edit-director realizes these into `edit_decisions`.
