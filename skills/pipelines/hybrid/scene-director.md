# Scene Director - Hybrid Pipeline

## When To Use

You are translating the hybrid structure into a visual system that keeps the source visible and the support layers under control.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/scene_plan.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["script"]["script"]`, `state.artifacts["idea"]["brief"]` | Hybrid structure and source truth |
| Tools | `frame_sampler`, `scene_detect` | Optional source inspection |
| Optional artifact | `state.artifacts["script"]["footage_edit_plan"]` (if present, pass-through from script stage) | b-roll windows to realize as support scenes |
| Playbook | Active style playbook | Layout consistency |

## Process

### 1. Keep The Anchor Medium Visible

If the piece is source-led, the source must remain visually primary in the scene plan. Do not hide the anchor behind constant overlays.

### 2. Reserve Support For Clear Jobs

Use support scenes for:

- chapter transitions,
- clarifying diagrams,
- stat emphasis,
- CTA or summary moments,
- gap-filling inserts.

### 3. Plan Variant Safety

If the project needs multiple aspect ratios, define where:

- subtitles live,
- speaker labels live,
- chart or code safe zones live,
- crop-sensitive source media becomes unsafe.

### 4. Use Metadata For Balance Rules

Recommended metadata keys:

- `anchor_rules`
- `support_rules`
- `safe_zones`
- `variant_rules`
- `overlay_density_limits`

### 5. Quality Gate

- the anchor medium stays primary where intended,
- support layers are limited and purposeful,
- aspect-ratio planning is explicit,
- no scene relies on invisible future magic.

### 6. B-Roll Insert Windows → Support Scenes

If a `footage_edit_plan` is available (via the script stage, per its `beats[].b_roll`), translate each beat with `b_roll.needed=true` into a support scene entry, one per window:

- Keep the source (anchor) as the primary layer for the whole beat; the support scene is an insert/overlay on top of it, not a replacement.
- Window: the support scene's on-screen time range is the beat's `start_seconds`/`end_seconds` (or a sub-range within it, if the insert should not span the full beat).
- Describe what the insert needs to show using `b_roll.suggested_visual` and `b_roll.reason` — this becomes the brief for `asset-director` to generate or source the actual asset.
- **Do not choose the Remotion realization here** (`screenshot_scene` vs a new `ChatTranscript`-style component vs a HyperFrames scene). That choice is a build decision gated on the composition-runtime approval gate (see `AGENT_GUIDE.md:117-131`) and must be flagged for the user at proposal time, not pre-picked in the scene plan. Note the requirement (e.g. "chat-UI insert, 3.2s window") and defer the realization choice to `asset-director.md` (which points to the actual decision).
- Insert sparingly: follow the active style playbook's `quality_rules` (e.g. `styles/ugc-talking-head.yaml`: "insert a UI/demo overlay when the script names a concrete example... never every beat") to avoid over-inserting relative to a sparse, source-led reference edit.

## Common Pitfalls

- Turning source-led scenes into overlay soup.
- Forgetting variant-safe zones until compose.
- Using generated inserts for every transition.
- Pre-selecting a Remotion scene-type realization for a b-roll insert instead of flagging it as a pending build decision.
