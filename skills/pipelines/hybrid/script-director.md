# Script Director - Hybrid Pipeline

## When To Use

This stage maps the story across source-led beats and support-led beats. You are deciding where the source carries the message and where support assets clarify it.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/script.schema.json` | Artifact validation |
| Prior artifact | `state.artifacts["idea"]["brief"]` | Anchor medium and deliverable mix |
| Tools | `transcriber`, `scene_detect`, `audio_enhance` | Optional source analysis |
| Tools (learned-style grounding) | `footage_edit_analyzer`, `speech_gap_detector` | Optional — produce `footage_edit_plan` (beats, transcript, b-roll flags) grounding this stage when a raw source take is available |

## Process

### 1. Mark Source-Led Versus Support-Led Beats

For each section, state whether it is:

- carried by source dialogue or footage,
- carried by narration,
- carried by diagrams or overlays,
- carried by text only.

### 2. Use Source Speech When It Is Better Than Rewriting

If the supplied footage already contains strong lines, use `transcriber` and keep the authenticity. Do not replace good source material with unnecessary narration.

### 3. Use Support Only To Clarify

Support-led beats should answer:

- what is not visible,
- what needs summarizing,
- what needs emphasis,
- what changes for a different platform.

### 4. Use Metadata For Structure

Recommended metadata keys:

- `anchor_sections`
- `support_sections`
- `narration_sections`
- `required_support_assets`

### 5. Quality Gate

- source-led beats are clearly marked,
- support-led beats are justified,
- the script does not depend on fake or unavailable assets without saying so,
- the structure can produce the intended deliverables.

### Mid-Production Fact Verification

If you encounter uncertainty during script writing:
- Use `web_search` to verify factual claims before committing them to the script
- Use `web_search` to find reference images for visual accuracy
- Log verification in the decision log: `category="visual_accuracy_check"`

Every factual claim in the script should be traceable to the `research_brief`.
If you make a claim that isn't in the research, do additional research and
add the source. Do not invent statistics, dates, or attributions.

### 6. Consume `footage_edit_plan` When Available (Learned-Style Grounding)

If a raw source take was analyzed with `footage_edit_analyzer` (optionally merged with `speech_gap_detector` removal spans), read the resulting `footage_edit_plan` artifact and ground the script in it instead of re-deriving structure from scratch:

- Map each `beats[]` entry to a script section, in beat order. Use `beats[].spoken_text` as the section's source dialogue — do not rewrite it (see Process #2).
- For every beat where `b_roll.needed=true`, explicitly flag that section as a **support-led narrative gap** in `required_support_assets` / `support_sections` metadata, using `b_roll.suggested_visual` and `b_roll.reason` as the justification. This directly satisfies `hybrid.yaml` `script` stage review_focus: "source-led vs support-led beats separated."
- Carry `transcript[]` forward for the subtitle source (used later by `edit-director` → `subtitles.source`).
- Do not invent transitions or sound effects here — those are edit-stage translations (see `edit-director.md`); this stage only needs the beat structure and b-roll flags.
- If no `footage_edit_plan` exists (no raw source take, or analyzer not run), proceed with the standard source-led/support-led marking in Process #1 — this grounding is optional, not required.

Cross-reference: when the active style playbook is `styles/ugc-talking-head.yaml`, follow its `quality_rules` (e.g. "insert a UI/demo overlay when the script names a concrete example") for consistency between the script's b-roll flags and the eventual caption/sfx choices made downstream.

## Common Pitfalls

- Rewriting strong source dialogue into weaker narration.
- Adding diagrams or cards where the footage already explains the point.
- Hiding unsupported requirements until asset generation.
- Ignoring an available `footage_edit_plan` and re-deriving beat structure by hand, causing drift from the edit stage's translation of the same plan.
