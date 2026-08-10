# Asset Director - Hybrid Pipeline

## When To Use

This stage prepares the support kit around the anchor edit: subtitles, diagrams, generated inserts, narration, music, and reusable overlay systems.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/asset_manifest.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["scene_plan"]["scene_plan"]`, `state.artifacts["script"]["script"]`, `state.artifacts["idea"]["brief"]` | Support needs and variant plan |
| Tools | `subtitle_gen`, `tts_selector`, `image_selector`, `video_selector`, `diagram_gen`, `code_snippet`, `music_gen`, `audio_enhance` — selectors auto-discover all available providers from the registry | Optional support asset production |
| Playbook | Active style playbook | Consistency rules |

## Process

### 1. Build Shared Support Assets First

Start with reusable systems:

- subtitle treatment,
- lower-third or label system,
- stat-card system,
- CTA container,
- diagram style.

### 1b. Sample Preview (Prevents Wasted Spend)

Before batch-generating support assets, produce one sample of each expensive generated type and show the user:

1. **TTS sample** (if narration is needed): Generate one section. Confirm voice and tone before batching.
2. **Image/video sample** (if generating inserts): Generate one representative visual. Confirm style fits the source footage before batching.

If rejected, adjust parameters and retry (max 3 iterations). Do not batch until approved.

### 2. Generate Only The Support Assets You Need

Support assets should fill identified needs from the script and scene plan, not speculative possibilities.

### 3. Preserve Anchor Truth

Keep the metadata clear about which assets are:

- source-derived,
- provided,
- recorded,
- generated.

### 4. Use Metadata For The Support Map

Recommended metadata keys:

- `shared_support_assets`
- `scene_asset_index`
- `source_vs_generated_map`
- `variant_assets`

### 5. Quality Gate

- support assets map to real narrative needs,
- reusable kits are present,
- source and generated assets are clearly separated,
- every referenced file exists.

### Mid-Production Fact Verification

If you encounter uncertainty during asset generation:
- Use `web_search` to verify visual accuracy of subjects (e.g. what does this building actually look like?)
- Use `web_search` to find reference images before generating illustrations
- Log verification in the decision log: `category="visual_accuracy_check"`

Visual accuracy matters. If the script mentions a specific place, person, or object,
verify what it actually looks like before generating images. Don't rely on
the AI model's training data — it may be wrong or outdated.

### 6. Realize B-roll Inserts — Real Clips First, Content-Matched, Never Fabricated By Default

When `footage_edit_plan.beats[].b_roll.needed=true`, that is a *need signal* (this beat would benefit from a cutaway) — it is NOT proof a suitable visual exists or should be invented. Resolve each needed beat in this order:

1. **Real clips first.** If `footage_edit_plan.broll_candidates[]` is non-empty (populated when the project declared `broll_sources` at analysis time — each candidate already carries a Gemini-produced `content_description`), call `tools.analysis.footage_edit_broll_matcher.match_broll_to_beats(beats, broll_candidates, threshold)` and use its result. A beat present in the returned mapping got a real, content-matched clip — use it, no generation needed, no approval needed (it's real footage, not a paid generation call).
2. **No match, no candidates.** If the beat isn't in the match result (empty candidates, or nothing scored above threshold), the correct default is **no insert** — leave the beat as a plain anchor cut. This is the honest, expected outcome for a project with no real b-roll footage, not a degraded fallback to apologize for.
3. **Generated insert, only if the user has separately approved it.** Only if the user has explicitly approved AI-generated inserts for this run does step 2's "no match" case become "generate an image/video via `image_selector`" (auto-discovers all registered `image_generation` providers including `nine_router_image`). Announce the selected provider (and that the call is paid) to the user **before** making the call — do not silently spend budget, and do not treat "no real match" as implicit permission to generate.
- Use mock/placeholder content only for any chat-UI or app-mockup insert — never embed a real customer's actual chat data or personal information in a generated or composited asset.
- Keep the `source_vs_generated_map` metadata (Process #4) accurate for every insert asset produced this way — including which beats got a real-matched clip (with its match score), which got a generated asset, and which got neither and why, so the choice is auditable.
- **Realization:** place the resolved insert (real-matched or approved-generated) as a `cuts[].type` + `backgroundVideo`/`backgroundVideoStart` entry (anchor footage dimmed behind the scene component) — **not** `edit_decisions.overlays[]`, whose schema shape does not render through the current composition (see `edit-director.md` §6b). Which Remotion scene type renders the foreground component (`screenshot_scene` per `remotion-composer/SCENE_TYPES.md:27`, a new `ChatTranscript`-style component per `SCENE_TYPES.md:61`, or a HyperFrames HTML/GSAP scene) is still NOT decided here — that is approval-gate #3 ("b-roll insert realization"), gated on the composition-runtime gate (`AGENT_GUIDE.md:117-131`) and must be surfaced to the user as a live build-decision question at proposal time.
- Cross-reference `styles/ugc-talking-head.yaml` (`asset_generation` block) for the prompt prefix/negative-prompt and consistency anchors when generating IS approved and the active playbook is the learned UGC talking-head style.

## Common Pitfalls

- Overbuilding support assets before the anchor cut is proven.
- Losing track of which assets are generated versus supplied.
- Creating inconsistent overlay systems across one project.
- Choosing the Remotion realization component for a b-roll insert instead of leaving it as a pending, user-gated build decision.
- Embedding real customer chat/personal data in a generated chat-UI mockup instead of mock content.
- Generating a b-roll visual for a `needed=true` beat without first checking `broll_candidates[]` for a real match, or treating "no real match" as automatic permission to generate without the user's separate approval.
- Placing a resolved b-roll insert into `edit_decisions.overlays[]` instead of `cuts[].backgroundVideo`.


## When You Do Not Know How

If you encounter a generation technique, provider behavior, or prompting pattern you are unsure about:

1. **Search the web** for current best practices — models and APIs change frequently, and the agent's training data may be stale
2. **Check `.agents/skills/`** for existing Layer 3 knowledge (provider-specific prompting guides, API patterns)
3. **If neither helps**, write a project-scoped skill at `projects/<project-name>/skills/<name>.md` documenting what you learned
4. **Reference source URLs** in the skill so the knowledge is traceable
5. **Log it** in the decision log: `category: "capability_extension"`, `subject: "learned technique: <name>"`

This is especially important for:
- **Video generation prompting** — models respond to specific vocabularies that change with each version
- **Image model parameters** — optimal settings for FLUX, GPT Image, Imagen differ and evolve
- **Audio provider quirks** — voice cloning, music generation, and TTS each have model-specific best practices
- **Remotion component patterns** — new composition techniques emerge as the framework evolves

Do not rely on stale knowledge. When in doubt, search first.
