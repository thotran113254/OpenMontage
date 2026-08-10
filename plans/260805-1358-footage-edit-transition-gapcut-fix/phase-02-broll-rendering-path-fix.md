# Phase 02 — B-roll Sourcing, Content-Matching & Rendering Fix

## Context Links
- Diagnosis: `plans/reports/debugger-260805-1400-footage-edit-transition-gapcut-diagnosis-report.md` (§Path A step 9, §5)
- Rendering mismatch: `schemas/artifacts/edit_decisions.schema.json:102-126` (`overlays[]`) vs `remotion-composer/src/Explainer.tsx:289-301` (`Overlay` interface) + `Explainer.tsx:827-865` (`OverlayRenderer`)
- Already-found-not-patched: `projects/raw-test-1/artifacts/decision_log.json` decision `d-007`
- Existing working alternate render path: `schemas/artifacts/edit_decisions.schema.json:53-54` (`backgroundVideo`/`backgroundVideoStart`), `Explainer.tsx:242-243`
- Real evidence of the workaround actually used: `projects/raw-test-1/artifacts/edit_decisions_v5.json` (`metadata.visual_fix_note`: b-roll/cards dropped entirely)
- **Sourcing gap (new, this session):** `tools/analysis/footage_edit_prompt.py:162,194-197` (`b_roll.suggested_visual` is Gemini's own imagined text, not grounded in any real clip), `tools/analysis/footage_edit_analyzer.py:101-104` (`input_schema` takes exactly one `input_path` — no mechanism to supply extra b-roll source clips at all), `skills/pipelines/hybrid/asset-director.md:82` (states "prefer real footage over generated" as a policy sentence, with no tool/data to act on it)
- Content-description tools available to reuse: `tools/analysis/frame_sampler.py`, `tools/analysis/visual_qa.py` (`operation: "review"` extracts frames for visual inspection), `tools/analysis/footage_edit_analyzer.py`'s own proven Gemini-video-upload path (`upload_video`, already does semantic video understanding for the main footage — same mechanism can run on a b-roll candidate clip)
- Sibling pipeline's proven prior art for source-role detection (do not copy wholesale, but the pattern is directly relevant): `docs/talking-head-autoedit-platform.md` §"Nhiều nguồn quay trong một job" (`role` auto-detected aroll/broll via audio energy, `overlay_pool` holds b-roll clips, `lib/talking_head_edit/sources.py`)

## Overview
- **Priority:** P1
- **Status:** not started — **decision gate before implementation**
- **Description:** Two compounding problems, not one. (1) **Sourcing**: the pipeline has no way to know whether a project actually has real b-roll footage, and no way to know what a candidate clip visually shows — `suggested_visual` today is Gemini imagining a generic description ("close-up of hands typing"), never checked against anything real. (2) **Rendering**: even when a b-roll insert IS produced (real or generated), `edit_decisions.overlays[]`'s schema shape cannot render through `Explainer.tsx`'s `Overlay` interface at all. The real production run's only fix for both was to drop b-roll entirely. This phase fixes both: only bring in b-roll when the project genuinely has it, know what it depicts before placing it, and make sure it actually renders once placed — the way a real editor works with a bin of footage.

## Key Insights
**On sourcing (new):**
- Confirmed: `footage_edit_analyzer`'s `input_schema` (`footage_edit_analyzer.py:101-104`) accepts exactly one video. There is currently no field, no stage input, and no artifact anywhere in `pipeline_defs/hybrid.yaml` for "here are the extra b-roll clips available for this project." If the user has real b-roll footage sitting in a folder today, nothing in this pipeline looks at it.
- Confirmed: `b_roll.suggested_visual` (`footage_edit_prompt.py:194-197`) is 100% Gemini's own invention from watching the main talking-head video alone — it is a creative suggestion of what WOULD help ("a shot of a phone screen showing the chatbot reply"), not a match against real inventory. This is fine as a *need signal* (this beat would benefit from a cutaway) but wrong as a *sourcing signal* (it never checks if such a shot exists).
- Confirmed: `asset-director.md:82` ("prefer real footage/screenshot over generated") is currently unenforceable — there is no data structure carrying "real b-roll clips available" into that stage for the director to check against. The `brief.json` `missing_capabilities` honesty note seen in `projects/raw-test-1/` (*"No real product/app screenshot available ... realized as data-driven stock scene components, not a fabricated fake UI screenshot"*) shows an agent manually reasoning this correctly once — it should be structural, not something that depends on an agent remembering to be honest about it each time.
- **Design principle for this phase:** b-roll is opt-in and inventory-grounded. If the project has no real b-roll source clips AND the user hasn't approved AI-generated inserts, the correct behavior is **no b-roll for that beat** (continue on primary footage, or use a plain data-driven stock card per `styles/ugc-talking-head.yaml`'s existing "missing_capabilities" honesty pattern) — never a silently fabricated visual passed off as real.

**On content-matching (new):**
- A real editor doesn't insert "some" b-roll — they know what's in each clip in their bin and pick the one that visually supports the specific line being said. Today nothing in this codebase determines what a candidate b-roll clip *actually shows*.
- The mechanism to build this already exists as parts, just never assembled for this purpose:
  `frame_sampler.py`/`visual_qa.py` (`operation: "review"`) can pull representative frames from
  any candidate clip; those frames (or the whole short clip) can go through the same
  Gemini-video-understanding call `footage_edit_analyzer.py` already makes for the main footage
  (`upload_video`) to get a content description. No new provider integration needed — this is
  reusing an existing capability for a new purpose.
- Matching is then: for each `b_roll.needed` beat, compare its `suggested_visual` (what would help)
  against each available b-roll clip's actual content description (what it shows), pick the best
  semantic fit above a confidence threshold, and leave the beat as a plain footage cut (no insert)
  if nothing available fits well enough — mirroring how a human editor skips the cutaway rather
  than forcing a mismatched one in.

**On rendering (carried over from original scope):**
- Two real options exist for making a placed b-roll insert actually appear on screen:
  - **Option A — extend `Overlay`/`OverlayRenderer`.** Add an asset-based overlay type to
    `Explainer.tsx`'s `Overlay` interface and a new `OverlayRenderer` branch rendering an
    image/video at `position{x,y,width,height}` for `start_seconds`/`end_seconds`. Most faithful
    to the schema's original intent; requires new React component work + field-name reconciliation
    (`in_seconds`/`out_seconds` vs `start_seconds`/`end_seconds`).
  - **Option B — redirect b-roll through `cuts[].type` + `backgroundVideo`.** Reuses the
    already-working `backgroundVideo`/`backgroundVideoStart` mechanism (schema lines 53-54,
    `Explainer.tsx:242-243`) — zero new renderer code. `edit_decisions.overlays[]` becomes reserved
    for the 4 built-in text-overlay types only.
  - Option B is lower effort and reuses proven machinery; changes `overlays[]`'s documented scope
    going forward — needs a schema description update.
- This whole phase (sourcing gate + content-matching threshold + rendering option) changes render
  behavior and what gets shown on screen — per `AGENT_GUIDE.md`'s Decision Communication Contract,
  present the choices, don't silently pick.

## Requirements
**Functional — sourcing gate (new)**
- Add an explicit, optional way for a project to declare real b-roll source clips (e.g. an
  `broll_sources: list[str]` input alongside the existing single `input_path`, or — if the project
  already uses `asset_manifest`/project-level file storage — an inventory read from there; pick
  whichever avoids inventing a second storage mechanism).
- If no b-roll sources are declared and the user has not explicitly approved AI-generated inserts
  for this run, `b_roll.needed=true` beats resolve to **no insert** (plain footage/stock-card
  fallback per the existing honesty pattern), not a silently generated fake visual.
- Surface the b-roll sourcing situation at proposal time (mirroring the existing mandatory Music
  Plan pattern in `AGENT_GUIDE.md`): "N of M b-roll-flagged beats have a real clip match, K will
  use a stock card fallback, J would need a generated asset (requires your approval, costs X)."

**Functional — content-matching (new)**
- For each declared b-roll source clip, produce a content description (reuse
  `frame_sampler`/`visual_qa` + a vision-capable description call — the same mechanism
  `footage_edit_analyzer` already uses for the main video).
- For each `b_roll.needed` beat, match against available clip descriptions; require a confidence
  threshold before using a match (mirroring the existing `min_disfluency_confidence=0.75` pattern
  elsewhere in this codebase — do not invent a differently-shaped confidence convention).
- Below threshold or no candidates: fall back to no-insert / stock-card, not a forced weak match.
- Record the match (or fallback reason) in `asset-director.md`'s existing `source_vs_generated_map`
  metadata convention (Process #4) so it's auditable which beats got real footage, which got a
  generated asset, and which got neither, and why.

**Functional — rendering (carried over)**
- Whichever render option is chosen, a placed b-roll insert must actually appear in a real
  Remotion preview render, not just validate against the schema.
- `edit_decisions.schema.json`'s `overlays[]` description updated to state what it does/doesn't
  support; `edit-director.md`'s field-mapping table updated to match.

**Non-functional**
- No new renderer code if rendering Option B is chosen (YAGNI — the mechanism already exists).
- No new content-analysis provider integration — reuse `frame_sampler`/`visual_qa` +
  `footage_edit_analyzer`'s existing Gemini-video call pattern.

## Architecture
```
                    project b-roll sources (NEW: declared input, opt-in)
                                |
                                v
                 frame_sampler/visual_qa -> content description
                 (reuse footage_edit_analyzer's Gemini-video call)
                                |
                                v
footage_edit_plan.beats[].b_roll.suggested_visual  --match-->  best clip (confidence-gated)
                                |                                    |
                        no match / no sources                  match found
                                |                                    |
                                v                                    v
                    no insert (plain cut / stock card)      edit_decisions insert
                                                                      |
                          Rendering Option A                Rendering Option B
                  edit_decisions.overlays[]           edit_decisions.cuts[] entry
                    {asset_id, start_seconds,           {type: text_card|...,
                     end_seconds, position,               backgroundVideo,
                     animation, opacity}                   backgroundVideoStart}
                            |                                       |
                            v                                       v
                  NEW: extended Overlay              EXISTING: SceneRenderer +
                  interface + OverlayRenderer          backgroundVideo path
                  (Explainer.tsx — new code)           (no new renderer code)
```

## Related Code Files
- **Create:** a small b-roll content-matching step (Python) — placement TBD at implementation
  time based on where in the pipeline b-roll sources get declared (likely a new function in
  `tools/analysis/footage_edit_artifact.py`'s orbit, or its own module if that file would cross
  ~200 lines — it's at 193 today, watch this)
- **Edit:** `tools/analysis/footage_edit_analyzer.py` (input schema: add optional b-roll sources),
  `skills/pipelines/hybrid/asset-director.md` (Process #6 — make the "prefer real over generated"
  policy actually actionable against real match data), `pipeline_defs/hybrid.yaml` (expose
  `frame_sampler`/`visual_qa` to whichever stage runs the matching)
  Rendering fix files (decide first): Option A → `remotion-composer/src/Explainer.tsx`
  (`Overlay` interface, `OverlayRenderer`), `schemas/artifacts/edit_decisions.schema.json`
  (`overlays[]`); Option B → `skills/pipelines/hybrid/edit-director.md` (b-roll row),
  `schemas/artifacts/edit_decisions.schema.json` (`overlays[]` description only)
- **Read only:** `projects/raw-test-1/artifacts/decision_log.json` (`d-007`),
  `remotion-composer/SCENE_TYPES.md`, `docs/talking-head-autoedit-platform.md` (source-role
  detection prior art — reference only, do not port its multi-source spine wholesale; this
  pipeline's needs are simpler, a flat list of optional b-roll clips is enough)

## Implementation Steps
1. Present the user with: (a) how b-roll sources should be declared for a project (new input vs
   reusing existing asset storage), (b) the confidence-threshold convention for content-matching,
   (c) rendering Option A vs B. **Do not implement past this step without explicit answers** — all
   three are production-behavior decisions.
2. Add the b-roll-sources input mechanism decided in 1a.
3. Build the content-description step (frame extraction + description call) for declared b-roll
   sources.
4. Build the matching function: beat `suggested_visual` -> best clip above threshold, or fallback.
5. Wire `asset-director.md` Process #6 to consume real match data instead of only a policy sentence.
6. Implement the rendering fix (Option A or B per step 1c) — see prior version of this phase's
   Implementation Steps 2-3 for the concrete edit locations.
7. Real preview render (`--preview-still`/`--preview-clip`) against a project with at least one
   declared b-roll source and one `b_roll.needed=true` beat: confirm (a) the right clip got
   matched to the right beat by eye, and (b) it visually renders.
8. Real preview render against a project with **no** b-roll sources declared: confirm the fallback
   (no insert / stock card) happens cleanly, with no attempt to fabricate a visual silently.

## Todo List
- [x] Present sourcing/matching/rendering decisions to user, get explicit answers — user authorized full-auto execution ("cook auto toàn bộ"); resolved with the plan's own recommended defaults rather than blocking: sourcing = optional `broll_sources` input (empty = no fabrication), matching = deterministic word-overlap threshold (0.35 default, mirrors the `min_disfluency_confidence` pattern), rendering = **Option B** (`cuts[].backgroundVideo`, reuses existing renderer, no new React component work).
- [x] B-roll-sources input mechanism — `broll_sources: list[str]` added to `footage_edit_schema.py`'s `INPUT_SCHEMA`; each declared clip gets one extra Gemini call (`describe_broll_clip`) and lands in `footage_edit_plan.broll_candidates[]`.
- [x] Content-description step for declared sources — `tools/analysis/footage_edit_broll_matcher.py`'s `describe_broll_clip`/`build_broll_candidates`, reusing `footage_edit_genai_client.upload_video` (same proven upload path the main analysis call already uses). Best-effort: never raises, a clip that can't be described just doesn't match.
- [x] Confidence-gated matching function — `match_broll_to_beats()`, deterministic word-overlap scoring, each real clip claimed at most once, tested (6 unit tests in `tests/test_footage_edit_broll_matcher.py`).
- [x] Wire `asset-director.md` to real match data — Process #6 rewritten: real-matched clip used directly (no approval needed, it's real footage), no-match falls back to no-insert by default, generation only with separate explicit user approval.
- [x] Implement chosen rendering option (B) — documented in `edit-director.md` §6b and `asset-director.md` Process #6: b-roll realizes as `cuts[].type` + `backgroundVideo`/`backgroundVideoStart`, never `overlays[]`.
- [ ] Preview render: with real b-roll sources (correct match + visible) — **not run this session** (needs a real project with real b-roll footage + `GEMINI_API_KEY`; out of reach in this offline coding pass). Flagged as an open item for the first real run that declares `broll_sources`.
- [x] Preview render equivalent / no b-roll sources: clean fallback, no fabrication — verified structurally via `tests/test_edit_decisions_hand_built.py::test_hand_built_broll_needed_beats_fall_back_cleanly_with_no_candidates` (fixture has no `broll_candidates`, asserts zero `backgroundVideo` cuts produced for any of its 6 `b_roll.needed=true` beats). A full visual preview render still needs a real render pass.

### Separate finding, NOT fixed in this phase (scope discipline)
While confirming Option B's blast radius, found that `edit_decisions.overlays[]`'s schema shape (`asset_id`/`start_seconds`/`end_seconds`/`position{x,y,width,height}`) doesn't match `Explainer.tsx`'s `Overlay` interface for **any** of its 4 supported types either (`type`/`in_seconds`/`out_seconds`/`text`, `additionalProperties: false` on both sides) — confirmed via `projects/raw-test-1/`'s real render history, which never populated `overlays[]` in any of its 5 versions. This is a **pre-existing, broader bug** affecting every pipeline that could use `overlays[]` (grepped: referenced by name across most `skills/pipelines/*/edit-director.md` files, not just `hybrid`), not something this plan introduced or was scoped to fix. Option B sidesteps it for b-roll specifically. Left as a flagged finding for a separate, dedicated investigation rather than an in-flight schema change with unaudited cross-pipeline blast radius.

## Success Criteria
- A project with real b-roll clips produces beat-to-clip matches that a human would agree make
  sense by content, verified by eye on a real preview.
- A project with no b-roll clips and no generation approval produces zero fabricated inserts —
  every `b_roll.needed` beat falls back cleanly and is logged as such.
- A placed b-roll insert (from either path) is visually present in a real Remotion render, not
  just schema-valid JSON.
- `python -m pytest tests/test_edit_decisions_hand_built.py -q` passes with fixtures covering both
  the matched and no-match-fallback cases.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Content-matching confidence threshold miscalibrated (too loose = wrong clip used; too strict = never matches) | Med | Med | Start conservative (mirror the `0.75` bar already used for `min_disfluency_confidence`); log every match decision with its score for later tuning, don't hide the number |
| Sourcing-input mechanism duplicates or conflicts with the existing `asset_manifest` inventory pattern | Med | Med | Check `asset_manifest.schema.json` and how the `idea`/`assets` stages already track source-vs-generated before adding a parallel structure |
| Option B (rendering) narrows `overlays[]`'s documented purpose in a way that breaks another pipeline expecting asset overlays | Low | Med | Grep all pipelines/directors for `overlays[]` asset usage before narrowing the schema description |
| Vision content-description call misreads a clip (e.g. confidently wrong about what it shows) | Low | Med | Same class of risk as any vision-model use elsewhere in this codebase — surface the description to the user at proposal alongside the match, don't auto-commit silently for paid/high-stakes runs |

## Security Considerations
Declared b-roll source paths must go through the same path-validation the main `input_path`
already uses (no arbitrary filesystem read). No new external data exposure — content-description
calls send only the b-roll clip itself, same trust boundary as the existing main-footage upload.

## Next Steps
Independent of Phase 01/03 — can run in parallel. Feeds Phase 04's regression pass.
