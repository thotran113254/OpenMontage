# Edit Director - Hybrid Pipeline

## When To Use

This stage creates the layered edit logic for a source-led video with support elements. The order matters: anchor cut first, support layers second.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/edit_decisions.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["assets"]["asset_manifest"]`, `state.artifacts["scene_plan"]["scene_plan"]`, `state.artifacts["script"]["script"]` | Source/support assets and timeline intent |
| Optional artifact | `state.artifacts["script"]["footage_edit_plan"]` (produced by `footage_edit_analyzer`/`speech_gap_detector` in the script stage, if run) | Beat-level cut/overlay/sfx grounding — see field-translation table below |
| Playbook | Active style playbook | Typography and motion consistency |

## Process

### 1. Lock The Anchor Cut First

The viewer should understand the story before support overlays are added. If the anchor cut is weak, support layers will not save it.

### 2. Add Support In Priority Order

Typical order:

1. subtitles,
2. speaker or context labels,
3. diagrams or stat cards,
4. optional inserts,
5. CTA elements.

### 3. Protect Readability

Never stack too many support layers in one moment. If subtitles, labels, charts, and overlays collide, simplify.

### 4. Use Metadata For Layering Logic

Recommended metadata keys:

- `anchor_cut_notes`
- `layer_order`
- `overlay_windows`
- `variant_edit_rules`

### 5. Quality Gate

- the anchor cut works on its own,
- support layers clarify instead of distract,
- mobile readability survives,
- variants remain consistent.

### 6. Translate `footage_edit_plan` Into `edit_decisions` (When Available)

If `state.artifacts["script"]["footage_edit_plan"]` exists, use it as the primary grounding for the anchor cut and its support layers.

**The anchor cut's timing, `source_in_seconds`, and `transition_in`/`transition_duration` are NOT hand-translated anymore.** Call
`tools.analysis.footage_edit_cuts_builder.build_cuts_from_plan(beats, removal_spans, source, fps)`
and use its return value directly as the `cuts[]` primary layer. This exists because the
hand-executed version of exactly this mapping produced zero usable output on the one real
production run that tried it — the removal-span ripple-shift and the source-vs-output timeline
distinction (`source_in_seconds` != `in_seconds` once anything upstream was removed) are easy to
get wrong by hand and hard to notice wrong until a render visibly desyncs. Do not re-derive this
mapping in prose; if the builder's output looks wrong for a given plan, that is a bug in the
builder to fix, not a cue to hand-patch its output.

**The builder strips `transition_in` from every cut except the LAST one by default.** Narration in
this composition is one continuous audio track (video cuts render muted); `<TransitionSeries>`
shrinks the on-screen video from a transition point onward without correspondingly shrinking that
audio track, so a mid-video transition desyncs everything after it from narration, permanently —
this is exactly why a real prior render used mid-video transitions, then removed all of them (see
`projects/raw-test-1/artifacts/edit_decisions_v5.json` `metadata.visual_fix_note`). It's the last
cut, not the first, because the renderer only ever activates a transition at run-local `idx > 0`
(`build-render-groups.ts`'s `hasActiveTransition`, `Explainer.tsx`'s `if (idx > 0)` guard) — the
very first cut can never render a transition at all, no matter what value it holds. The last cut
is the one position that is both renderable (`idx > 0`, since all cuts here form one contiguous
run) and narration-safe (nothing plays after it to desync — the composition's total duration just
shrinks by `transition_duration`, trimming a bounded sliver of trailing narration instead). Do not
pass `enforce_narration_safe_transitions=False` to get more transitions
without first splitting narration into per-run segments (a larger, separate fix — flag it to the
user rather than silently disabling the guard).

Everything else still maps as before:

| `footage_edit_plan` field | `edit_decisions` target | Notes |
|---|---|---|
| `beats[]` + `removal_spans[]` | `cuts[]` primary layer (`in_seconds`/`out_seconds`/`source_in_seconds`/`transition_in`/`transition_duration`) | **Call `build_cuts_from_plan()` — see above. Do not hand-translate.** |
| `beats[].zoom.action`/`recommended_scale_range` | `cuts[].transform.scale` and `cuts[].transform.animation` | `zoom_in`/`zoom_out` → an animation value (e.g. `ken-burns-slow-zoom`) with the recommended scale; `static` → no zoom animation. `recommended_scale_range` is free text from Gemini, not a structured value — read it yourself, do not expect a parser |
| `beats[].b_roll` (when `needed=true`) | See the b-roll sourcing/matching/rendering process below — **not** a direct field mapping | `edit_decisions.overlays[]`'s asset shape does not render through the current composition; see the b-roll section |
| `beats[].sound_effect.type` (when not `"none"`) | `audio.sfx[]` entry, `asset_id` = the real sfx file name | Resolve the name via `inventory_used.sound_effects` against `assets_library/resource_inventory.json` (e.g. `whoosh_light`, `telop-pop-low`) — never a made-up sfx name |
| `transcript[]` | `subtitles.source` | Use as the caption source when `subtitles.enabled=true` |

Any beat field whose value is not present in the plan (e.g. `sound_effect.type: "none"`) simply means no `audio.sfx[]` entry is added for that beat — do not fabricate one.

### 6b. B-roll: Only Real, Only Matched, Only If It Renders

A `b_roll.needed=true` beat is a *need signal*, not a sourcing signal — `suggested_visual` is
Gemini imagining what would help from watching the main footage alone, never checked against
anything real. Do not fabricate a visual just because a beat asks for one:

1. If the project declared real b-roll source clips for this run, and one has a content match
   above the confidence threshold for this beat's `suggested_visual`, use it.
2. Otherwise, leave the beat as a plain anchor cut (no insert) — this is the correct, honest
   outcome, not a fallback to apologize for. Only fabricate a generated asset if the user has
   explicitly approved AI-generated inserts for this run (see `asset-director.md` Process #6).
3. Whichever insert (real-matched or approved-generated) you place, realize it through
   `cuts[].type` + `backgroundVideo`/`backgroundVideoStart` (a scene-type cut with the anchor
   footage dimmed behind it) — **not** `edit_decisions.overlays[]`. `overlays[]`'s schema shape
   (`asset_id`/`start_seconds`/`end_seconds`) does not match what the `Explainer` composition's
   `Overlay` interface can render (4 fixed text-overlay types only); an asset-shaped entry there
   silently renders nothing. `overlays[]` stays reserved for `section_title`/`stat_reveal`/
   `hero_title`/`provider_chip`.

### 7. Runtime and Authoring-Mode Carry-Forward (DO NOT Decide Here)

`render_runtime` and `composition_mode` are **locked at the proposal stage**, before this stage runs (per `AGENT_GUIDE.md:117-140`, HARD RULE). This director's job is to **carry them forward unchanged** from `proposal_packet` into `edit_decisions.render_runtime` / `edit_decisions.composition_mode` — never to pick or default one here, even though `hybrid.yaml`'s `compose` stage `review_focus` notes hybrid "typically uses render_runtime='remotion'". That note is a hint for the proposal-time conversation with the user, not a default this stage may silently apply.

- If `render_runtime`/`composition_mode` are missing from the proposal (e.g. an older proposal_packet, or this stage is reached without the gate having run), **stop and escalate to the user** for the composition-runtime gate (`AGENT_GUIDE.md:117-131` — present Remotion vs HyperFrames vs ffmpeg with tradeoffs, log `render_runtime_selection` with the full shortlist in `decision_log`) and the authoring-mode gate (`AGENT_GUIDE.md:133-140` — templated vs atelier, logged as `composition_mode` in `decision_log`) before producing `edit_decisions`. Do not guess.
- A silent runtime swap or an un-gated default pick here is a CRITICAL governance violation (already flagged by `hybrid.yaml` `compose` stage review_focus: "render_runtime in edit_decisions matches proposal_packet — silent swap is a CRITICAL governance violation").

## Common Pitfalls

- Trying to fix a weak cut with extra graphics.
- Letting support layers compete with the source.
- Building each platform variant as a separate editorial philosophy.
- Silently defaulting or swapping `render_runtime`/`composition_mode` instead of carrying forward the proposal-stage decision or escalating the gate.
- Inventing a transition or sound-effect name instead of resolving it from the real P01b `assets_library/resource_inventory.json` inventory.
- Hand-translating `beats[]`/`removal_spans[]` into `cuts[]` instead of calling `build_cuts_from_plan()` — this is exactly the mapping that silently produced zero usable output the one time it was done by hand.
- Placing a b-roll insert into `edit_decisions.overlays[]` instead of `cuts[].backgroundVideo`, or fabricating a b-roll visual for a beat with no real matched clip and no user approval for generation.
