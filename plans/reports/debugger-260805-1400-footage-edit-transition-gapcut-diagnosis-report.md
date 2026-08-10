# Footage-Edit Transitions + Gap-Cutting Diagnosis Report

Scope: trace footage_edit_plan (Gemini) -> edit_decisions -> render for talking-head
raw footage. Two failure paths: (A) transitions look ineffective, (B) filler/dead-air
cutting doesn't tighten pacing. Static read of actual on-disk code (incl. uncommitted
WIP), cross-referenced with skills/tests/schemas.

## 0. Headline finding (applies to both paths)

**There is no code that turns `footage_edit_plan` (beats + removal_spans) into
`edit_decisions.cuts[]`.** This translation is 100% delegated to an LLM agent
following prose in `skills/pipelines/hybrid/edit-director.md` Step 6. Confirmed
explicitly by the plan author themselves:

`plans/260709-1218-learned-style-footage-edit/phase-04-hybrid-pipeline-integration.md`
(Key Insights): *"Directors are Markdown instructions (agent behavior), not code —
integration = editing these `.md` files to teach the agent to read `footage_edit_plan`
and translate it. No orchestration code."*

Everything below is either (a) a concrete code bug in the parts that ARE code
(schema, transitions renderer, span math), or (b) a documentation/design gap in the
prose translation step that makes correct output unlikely even from a careful agent.

---

## 1. Path A trace — transitions

1. **Gemini decides transition per beat**, constrained to inventory names.
   `tools/analysis/footage_edit_prompt.py:161` — prompt tells Gemini to pick from
   `[{transitions_list}]` (injected from `assets_library/resource_inventory.json`).
   Real inventory (`assets_library/resource_inventory.json:44-65`) = exactly
   `["none", "fade", "slide", "wipe"]` — matches the 4 Remotion presets 1:1, so no
   naming drift here (unlike the risk described in the task brief).

2. **Clamp/validate.** `tools/analysis/footage_edit_prompt.py:228-267`
   `validate_and_clamp_beats` — if `transition.type` isn't in `transitions[]`, clamps
   to `"none"` and records a QA violation. Fine, but note: `"none"` itself IS a member
   of the inventory list (`resource_inventory.json:46-49`), so this path rarely fires
   in practice for transitions specifically.

3. **Artifact assembly.** `tools/analysis/footage_edit_artifact.py:92-170` builds
   `footage_edit_plan.beats[]` with `transition.type`/`reason` untouched otherwise.
   No further processing of transitions here.

4. **Schema.** `schemas/artifacts/footage_edit_plan.schema.json:68-79` — beat
   `transition` is `{type, reason}`, both required at the *beat* level. Fine.
   `schemas/artifacts/edit_decisions.schema.json:84-96` — `cuts[].transition_in`,
   `transition_out`, `transition_duration` are all **optional**, not in the
   `cuts[]` `required` array (`edit_decisions.schema.json:14`, only
   `[id, source, in_seconds, out_seconds]`). Nothing enforces that a transition
   actually survives into `edit_decisions`.

5. **Prose translation (the only place beats become cuts).**
   `skills/pipelines/hybrid/edit-director.md:60` — table row: `beats[].transition.type`
   → `cuts[].transition_in` (+ `transition_duration`). This is agent-executed, not
   code. No validation step confirms it happened.

6. **Reference/test implementation of the mapping doesn't stress transitions either**
   — `tests/test_edit_decisions_hand_built.py:96-99` hardcodes
   `transition_duration = 0.3` for every cut with a non-"none" transition,
   regardless of beat length or footage_edit_plan's own duration guidance. Not wrong
   per se, but shows the "reference" implementation is a toy, not integration-tested
   against the renderer.

7. **Render-time wiring (Remotion `Explainer` composition).**
   `remotion-composer/src/transitions/preset-map.ts:9` — 4 presets, matches inventory.
   `remotion-composer/src/transitions/build-render-groups.ts:23-27`:
   ```ts
   function isContiguous(prev: CutTiming, cur: CutTiming, fps: number): boolean {
     const prevEndFrame = Math.round(prev.out_seconds * fps);
     const curStartFrame = Math.round(cur.in_seconds * fps);
     return prevEndFrame === curStartFrame;
   }
   ```
   and `build-render-groups.ts:38-64` `buildRenderGroups` — **a transition is only
   ever applied between two cuts that are frame-exact back-to-back on the OUTPUT
   timeline.** Any gap, overlap, or off-by-a-frame rounding drops the pair to the
   "flat" (hard-cut) path silently — this is documented as intentional ("Returning
   null is always safe... falls back to a hard cut") but it means a single rounding
   slip anywhere upstream (very likely exactly at the seams where removal_spans were
   trimmed — see Path B) silently and permanently kills the transition with zero
   error surfaced.

8. **Architecture mismatch — the composition actually named for this content type
   never uses any of this.** `remotion-composer/src/TalkingHead.tsx:307-353` (the
   `TalkingHead` composition, mapped from `renderer_family: "presenter"` in
   `tools/video/video_compose.py:681-690` `RENDERER_FAMILY_MAP`) is:
   ```tsx
   export const TalkingHead: React.FC<TalkingHeadProps> = ({ videoSrc, captions, overlays, ... }) => (
     <AbsoluteFill ...>
       <OffthreadVideo src={videoSrc} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
       {overlays?.map(...)}   {/* charts/stat cards/callouts only */}
       <CaptionOverlay .../>
     </AbsoluteFill>
   );
   ```
   It takes **one continuous `videoSrc` string**, not `cuts[]`. It never imports
   `buildRenderGroups`/`resolveCutTransition`, never reads `transition_in`. If a
   "raw talking-head footage" deliverable is rendered as `renderer_family: "presenter"`
   (the semantically obvious choice), the entire transitions system built in this
   commit (`build-render-groups.ts`, `preset-map.ts`) is simply never invoked — it
   only exists on the `Explainer` composition's cut-array path
   (`remotion-composer/src/Explainer.tsx:907-957`).

9. **B-roll-triggered transitions can't visibly land even on the `Explainer` path.**
   `styles/ugc-talking-head.yaml:90`: *"Default transition is none (hard cut);
   fade/slide/wipe are the exception, reserved for b-roll entry/exit."* — i.e. the
   transition is meant to be motivated by a b-roll insert appearing. But the b-roll
   insert is carried as `edit_decisions.overlays[]`
   (`edit_decisions.schema.json:102-126`: `{asset_id, start_seconds, end_seconds,
   position:{x,y,width,height}, animation, opacity}`), while `Explainer.tsx`'s own
   `overlays` prop is a **completely different, incompatible shape**:
   `Explainer.tsx:289-301`
   ```tsx
   interface Overlay {
     type: "section_title" | "stat_reveal" | "hero_title" | "provider_chip";
     in_seconds: number;
     out_seconds: number;
     ...
   }
   ```
   No `asset_id`, no image/video rendering support, and field names
   (`in_seconds`/`out_seconds`) don't even match the schema's
   (`start_seconds`/`end_seconds`). An `edit_decisions.overlays[]` entry built per
   schema will not match any `Overlay.type` in `OverlayRenderer`
   (`Explainer.tsx:827-865`, returns `null` for unrecognized type) and would compute
   `Math.round(overlay.in_seconds * fps)` as `NaN` since the field doesn't exist on a
   schema-shaped object. **Net effect: the b-roll insert that's supposed to motivate
   the transition never actually renders, so the anchor-cut's fade/slide/wipe plays
   with nothing to justify it on screen** — reads as a random, unmotivated,
   "ineffective" transition blip exactly matching the user's complaint.

### Path A root causes (ranked)

1. **Architecture mismatch: `TalkingHead.tsx` (the composition semantically matching
   "raw talking-head footage") never consumes `cuts[]`/transitions at all** —
   `TalkingHead.tsx:307-353` vs `Explainer.tsx:907-957`. If this is the render path
   actually used, 100% of the transition system is dead code for this feature.
2. **b-roll overlay schema mismatch breaks the visual motivation for transitions** —
   `edit_decisions.schema.json:102-126` (`asset_id`/`start_seconds`/`end_seconds`)
   vs `Explainer.tsx:289-301` (`type`/`in_seconds`/`out_seconds`, no asset
   rendering). Even on the `Explainer` path, the b-roll cutaway that's supposed to
   accompany the transition silently fails to render.
3. **Zero code enforcement that `transition_in` is ever set** — optional field
   (`edit_decisions.schema.json:14,84-96`) + zero-code manual LLM translation
   (`edit-director.md:60`) = no guarantee any given real run actually populates it.
4. **Frame-exact contiguity requirement silently no-ops transitions** at any seam
   with rounding/gap error — `build-render-groups.ts:23-27`. Directly compounds with
   Path B (removal-span trimming is exactly where such gaps appear).
5. Playbook design intent is itself "mostly none/hard cut"
   (`styles/ugc-talking-head.yaml:90`) — some of the "ineffective" perception may
   simply be by design (sparse transitions), amplified by #1/#2/#3 above making even
   the sparse ones fail to land correctly.

---

## 2. Path B trace — speech-gap / filler / dead-air cutting

1. **Detection.** `tools/analysis/speech_gap_spans.py`:
   - `compute_dead_air_spans:78-110` — inter-word gaps ≥ `min_dead_air_seconds`
     (default 0.6s, `speech_gap_detector.py:98`).
   - `compute_filler_spans:113-152` — lexicon match (`à, ừm, ờ, ừ, kiểu, kiểu như`),
     gated by `min_filler_confidence` (default 0.5).
   - `compute_pause_tighten_spans:48-75` — ffmpeg `silencedetect`-based, **compresses
     not removes**: keeps `pause_keep_seconds` (default `DEFAULT_PAD_SECONDS=0.15s`,
     `speech_gap_spans.py:19`) split at both edges. Max saved per pause ≈
     `pause_duration - 0.15s`. For pauses just above the 0.4s min
     (`min_pause_seconds`), that's ~0.25s saved *per pause* — real but small; needs
     many pauses to read as "tightened pacing."
   - `corroborate_dead_air:155-177` — **1-second-granular** guard: drops a dead-air
     candidate if *any* overlapping whole-second bucket from `audio_energy` is
     "active":
     ```python
     lo, hi = int(span["start_seconds"]), int(span["end_seconds"]) + 1
     if any(sec in active_seconds for sec in range(lo, hi)):
         continue
     ```
     Enabled by default (`corroborate_with_audio_energy: True`,
     `speech_gap_detector.py:101,161`). Because real dead-air gaps almost always sit
     adjacent to a word's start/end inside the same 1-second bucket, this is a very
     coarse filter that will drop a large share of genuine short (0.6-1.5s) dead-air
     spans whenever speech occurs anywhere else in that same second — plausible
     source of "not enough got cut."

2. **Gemini disfluency spans** (separate signal, same call as transitions).
   `tools/analysis/footage_edit_artifact.py:64-89` `disfluency_removal_spans` —
   filtered by `min_disfluency_confidence` default **0.75**
   (`tools/analysis/footage_edit_disfluency_prompt.py:26`), noticeably higher bar
   than the 0.5 filler-confidence default — likely intentional (LLM-judged, no ASR
   ground truth) but worth knowing it's the strictest gate in the whole pipeline.

3. **Merge.** `tools/analysis/speech_gap_spans.py:180-206` `merge_spans` — sorts,
   merges spans within `MERGE_GAP_EPSILON=0.2s` of each other. Straightforward, no
   issue found.

4. **`removal_spans[]` lands in `footage_edit_plan.json`** —
   `footage_edit_artifact.py:140-142`, schema
   `footage_edit_plan.schema.json:135-150`. This artifact is explicitly documented as
   **"NOT a canonical stage artifact"**
   (`footage_edit_plan.schema.json:5` description) — advisory only.

5. **Consumption — this is where it breaks down entirely.**
   - `tools/video/video_compose.py` has **zero references** to `removal_spans`,
     `speech_gap`, or `footage_edit` anywhere in the file (grep-verified, whole
     2679-line file). The uncommitted WIP diff in this file
     (`git diff -- tools/video/video_compose.py`) is entirely unrelated — it's
     visual-spotcheck luma detection and audio music-expected-flag improvements, not
     footage-edit wiring. **The task's premise that `video_compose.py` was modified
     to wire this feature is incorrect** — it wasn't.
   - The only place `removal_spans` consumption is even described is
     `edit-director.md:64`: *"For each removal span, split or shorten the affected
     anchor cut so its in_seconds/out_seconds excludes the removed range... Keep
     natural pads around the cut."* Pure prose, agent-executed, no code.
   - **The one test that claims to prove this mapping works ignores removal_spans
     completely.** `tests/test_edit_decisions_hand_built.py:42-148`
     `build_edit_decisions_from_fixture` iterates
     `footage_edit_plan["beats"]` and maps `start_seconds`/`end_seconds` straight
     onto `cuts[].in_seconds`/`out_seconds` — it **never reads
     `footage_edit_plan["removal_spans"]` at all.** Its own fixture
     (`tests/fixtures/footage_edit_plan_sample.json:771`) has
     `"removal_spans": []` — so this specific mapping row has **zero test
     coverage, and the codebase's only worked example of the translation
     doesn't attempt it.**

6. **Even if an agent does the trim correctly, a second, undocumented field is
   needed and is missing from the spec.** `remotion-composer/src/Explainer.tsx:208-210`:
   ```tsx
   // Video source trim — seek to this point in the source before playback.
   // Defaults to 0 (play from beginning). Use this instead of in_seconds for source trimming.
   source_in_seconds?: number;
   ```
   and `Explainer.tsx:801-811` `VideoScene startFrom={cut.source_in_seconds ?? 0}`.
   **`in_seconds`/`out_seconds` are the OUTPUT-timeline position; `source_in_seconds`
   is the separate SEEK offset into the source file.** Neither
   `edit-director.md`'s field-mapping table (`edit-director.md:58-64`) nor
   `phase-04-hybrid-pipeline-integration.md`'s architecture table nor the hand-built
   test ever mention `source_in_seconds`. The hand-built test literally sets only
   `in_seconds`/`out_seconds` = beat start/end
   (`test_edit_decisions_hand_built.py:65-75`), which — per `Explainer.tsx`'s own
   documented semantics — means every cut plays from **frame 0 of the source video**
   (`source_in_seconds` defaults to 0), not from that beat's actual footage
   position. Even without any removal_spans involved, following the documented
   mapping literally on the Remotion path produces cuts that show the wrong footage.
   This is a second, independent, and more severe way the translation as documented
   fails to survive contact with the actual renderer.

7. **Runtime-semantic split makes this worse.** `tools/video/video_compose.py:491-496`
   (`_compose`, FFmpeg path): `-ss str(in_s) -t str(duration) -i str(source)` — here
   `in_seconds`/`out_seconds` ARE source-seek points, and FFmpeg concat implicitly
   places segments back-to-back on the output with no separate timeline field needed.
   This is the **opposite semantic** of the Remotion `Explainer.tsx` path (#6 above)
   for the identical `cuts[].in_seconds`/`out_seconds` field names. The
   field-translation table (`edit-director.md`) gives no runtime-conditional
   guidance, so there is no way for an agent (or a future engineer) to know which
   semantics to produce without already knowing this renderer-internals detail.

8. **Architecture mismatch (same as Path A #1).** If the render composition is
   `TalkingHead.tsx` (`renderer_family: "presenter"`), there's no `cuts[]` array at
   all — just one `videoSrc`. Gap-cutting can only take effect if a *separate*,
   currently-nonexistent step pre-cuts the source video (FFmpeg) into a single
   trimmed file before it's ever handed to Remotion as `videoSrc`. No such step
   exists anywhere in `video_compose.py` (confirmed no `removal_spans` references).
   So on this composition, filler/dead-air removal has literally nowhere to land —
   the base video always plays uncut end to end.

### Path B root causes (ranked)

1. **No code performs the removal_spans → cuts[] trim at all; it's pure LLM prose
   (`edit-director.md:64`), and the one reference test/implementation
   (`test_edit_decisions_hand_built.py`) skips this exact step and uses a fixture
   with `removal_spans: []`.** This is the single biggest reason cutting isn't
   "effective" — there's no verified, working example of it happening anywhere in
   the codebase, let alone a guarantee it happens in production.
2. **`source_in_seconds` is completely absent from the translation spec** — the
   documented/tested mapping (beat start/end → `in_seconds`/`out_seconds` only)
   is wrong for the Remotion renderer regardless of removal_spans; every non-first
   beat cut would play from source frame 0 (`Explainer.tsx:208-210,805`).
3. **`corroborate_dead_air`'s 1-second-granularity active-audio guard likely drops a
   large fraction of genuine short dead-air spans** (`speech_gap_spans.py:165-177`),
   enabled by default — a real, measurable over-conservatism independent of the
   wiring gap.
4. **`video_compose.py` has zero involvement in this feature** (contrary to the
   task's premise) — its uncommitted diff is unrelated QA/spotcheck work. No
   ffmpeg-side or Remotion-side deterministic consumption path exists.
5. **`pause_tighten` only compresses (not removes) pauses to 0.15s total** — by
   design, but means the per-pause pacing win is small (`speech_gap_spans.py:48-75`);
   needs many qualifying pauses to be perceptible.
6. **Architecture mismatch with `TalkingHead.tsx`** (same as Path A #1) — if that's
   the actual render composition, gap-cutting has no mechanism to take effect at
   all, cut or no cut plan.
7. **Runtime-semantic split (FFmpeg vs Remotion) for the same `cuts[].in_seconds`/
   `out_seconds` fields** (`video_compose.py:491-496` vs `Explainer.tsx:208-210`)
   means a single field-mapping table cannot correctly serve both runtimes; whichever
   one wasn't consciously targeted when the cuts were authored will render wrong.

---

## 3. Optimization recommendations (ranked, impact vs effort)

**High impact / Medium effort**
1. Write an actual deterministic function (Python, unit-testable) that converts
   `footage_edit_plan.beats[] + removal_spans[]` into `edit_decisions.cuts[]`,
   replacing the prose-only step in `edit-director.md:64`. It must: (a) ripple-shift
   every subsequent cut's `in_seconds`/`out_seconds` to close gaps left by removed
   spans so cuts stay frame-contiguous on the OUTPUT timeline (required for
   `build-render-groups.ts` transitions to ever activate), and (b) always emit
   `source_in_seconds` = the real seek point into the source (i.e., the beat's
   original position, minus previously-removed duration accounting only affects the
   OUTPUT side). Add a unit test asserting `cuts[i].out_seconds == cuts[i+1].in_seconds`
   (frame-rounded) whenever no intentional gap is wanted.
2. Fix `tests/test_edit_decisions_hand_built.py` to actually exercise a
   `footage_edit_plan` fixture with non-empty `removal_spans[]`, and assert (a) the
   removed range is excluded from every resulting cut's `[in,out]`/`[source_in,
   source_in+duration]`, and (b) `source_in_seconds` is present and correct on every
   cut. Currently this "proof" test can't fail on this dimension because it never
   tries it.
3. Resolve the composition/architecture question explicitly: decide whether raw
   talking-head footage renders via `Explainer` (cut-array, supports transitions/
   gap-cutting) or `TalkingHead.tsx` (single continuous video, no cut/transition
   support at all). If `TalkingHead` is meant to be used, either (a) give it a
   `cuts[]`-aware base-video renderer using the same `build-render-groups.ts`
   machinery, or (b) explicitly document that footage-edit gap-cutting requires
   pre-cutting the source via FFmpeg into one trimmed file before Remotion ever sees
   it, and write that FFmpeg pre-cut step (none currently exists, verified no
   `removal_spans` references in `video_compose.py`).

**High impact / Low effort**
4. Fix the b-roll overlay schema mismatch: either extend `Explainer.tsx`'s `Overlay`
   interface (`Explainer.tsx:289-301`) to support an asset-image/video type matching
   `edit_decisions.overlays[]`'s actual shape (`asset_id`, `start_seconds`/
   `end_seconds`, `position{x,y,width,height}`), or change the schema/translation
   table to only ever emit overlay `type`s the renderer already understands
   (`section_title`/`stat_reveal`/`hero_title`/`provider_chip`). Currently any
   schema-compliant b-roll overlay silently fails to render (`OverlayRenderer`
   returns `null`, field name mismatch `in_seconds` vs `start_seconds`).
5. Disable `corroborate_with_audio_energy` by default, or make the guard
   sub-second (use the raw energy samples' actual timestamps rather than truncating
   to whole seconds) — `speech_gap_spans.py:165-177`. As written it drops most
   short dead-air candidates whenever any word boundary shares the same 1-second
   bucket, which is nearly always true for genuine sub-1s pauses.
6. Add a schema-level or post-build validation step (in whatever code implements
   recommendation #1) that fails loudly if `cuts[].transition_in` is unset/`"none"`
   for every cut in an artifact that had non-trivial `footage_edit_plan.beats[]`
   transition variety — surfaces silent-drop cases instead of shipping them.

**Medium impact / Low effort**
7. Increase `DEFAULT_TRANSITION_DURATION_FRAMES` (currently 14 frames ≈ 0.47s @
   30fps, `preset-map.ts:20`) modestly, or lower the `min_scene_hold_seconds`
   safety-margin assumption — not the primary cause, but combined with the
   `Math.min(requestedFrames, prevDur/2, nextDur/2)` clamp
   (`preset-map.ts:73-77`), any cut shortened by aggressive gap-trimming near the
   `min_scene_hold_seconds=3s` floor could clamp the transition to near-zero frames,
   compounding root cause A-4.
8. Increase `pause_keep_seconds` transparency: log/report total seconds actually
   saved by `pause_tighten` per render so it's measurable whether "tightened pacing"
   claims hold up on a given input, rather than inferred from theory.

**Low impact / verification only**
9. Re-verify `DEFAULT_MODEL = "gemini-3.1-flash-lite"` / `"gemini-3.5-flash"`
   (`footage_edit_prompt.py:20-21`) are real, currently-served Gemini model IDs —
   docs (`docs/PROVIDERS.md`) reference "Gemini 2.0 Flash" pricing instead, a naming
   mismatch that's out of this diagnosis's scope but worth a quick sanity check
   before assuming the analyzer step itself runs cleanly end-to-end.

---

## 5. Real production evidence (`projects/raw-test-1/`, confirmed post-report — resolves §4 unresolved questions)

A real job exists end to end: `output/raw_test_1_v2/footage_edit_plan.json` (real Gemini
output) → `projects/raw-test-1/artifacts/edit_decisions{,_v2,_v3,_v5}.json` →
`renders/final_v5.mp4`, plus `decision_log.json` recording what the agent found and did.
This **resolves §4 Q1/Q2** and meaningfully corrects §1/§2 above.

**Q1 resolved:** `render_report_v5.json` → `promise_preservation.renderer_family_used =
"explainer-teacher"`. The composition actually used is `Explainer` (cuts-aware), **not**
`TalkingHead.tsx`. **Root cause A-1 (TalkingHead dead code) is moot for this pipeline in
practice** — worth a doc note, not a fix priority.

**The real transition bug is worse than A-4/A-5 speculated, and it's already
self-documented in the decision log:**

- `footage_edit_plan.json` (real Gemini output): 18 beats, exactly 2 non-`"none"`
  transitions (`fade`, `slide`) — inventory-clamp and naming (§Path A steps 1-2) worked
  correctly, confirming those are NOT the problem.
- `edit_decisions.json` v1-v3 carried those same 2-3 transitions through untouched
  (`transition_in: "fade"/"slide"`, `transition_duration: 0.3`).
- `edit_decisions_v5.json` (current/final) has **zero** cuts with any transition set, and
  `metadata.visual_fix_note` states explicitly: *"no soft transitions (hard cuts only ->
  no TransitionSeries shrink -> narration matches video by construction)"*.
- **Root cause, confirmed by code:** `remotion-composer/src/transitions/build-render-groups.ts:70-75`
  — `<TransitionSeries>` overlaps two adjacent cuts by the transition's duration, so the
  run's on-screen video length is `sum(cut durations) - sum(transition frames)`, strictly
  shorter than `sum(out_seconds - in_seconds)` implies. `Root.tsx:139`
  (`computeContentDurationInFrames`) was added to fix the **composition-total** duration
  (avoids a dead black tail at the end) — confirmed wired in, uncommitted WIP as of this
  session. But nothing shifts the **narration/audio track** to match the shrink at the
  transition's position mid-video: audio is a fixed continuous track, so every cut *after*
  a mid-video transition plays `transition_duration` seconds early relative to narration,
  and that offset persists for the rest of the video. This is a **structural design gap
  between `<TransitionSeries>` and an unsplit narration track**, not a flaky edge case —
  it fires on every transition that isn't the very last cut. The team's own fix (v5) was
  to remove transitions entirely rather than fix the sync, i.e. **the bug is still live in
  the code**; only this one project's artifact was hand-patched around it.
- **Revises priority: this (audio/video desync on every non-terminal transition) is now
  the #1 confirmed root cause for "transitions ineffective," above A-2/A-3.**

**Q2 resolved, and Path B root cause #1 is confirmed WORSE than "no test coverage" —
it failed in the one real run that exists:**

- `footage_edit_plan.json.removal_spans` (Gemini's own output) = **`[]`, empty** — the
  LLM-driven detection produced nothing usable on real footage.
- `decision_log.json` `d-006` (`category: "fallback_decision"`, `subject: "removal_spans
  correction: pause-tighten ripple edit replacing the original 0-span result"`): an agent
  had to **manually redo gap detection** with a different, more reliable method (ffmpeg
  `silencedetect` cross-verified independently) and hand-compute a ripple-delete timeline
  compression (93.4s → 89.402s, i.e. 3.97s removed, 16 splice points) — this was NOT the
  productized `footage_edit_plan → edit_decisions` path working; it was a human/agent
  bypass of it.
- `d-007` (`category: "capability_extension"`) independently confirms, in the team's own
  words: *"edit_decisions.overlays[] shape mismatched with Explainer's Overlay interface,
  TalkingHead prop-shape not wired through video_compose"* — this is an **exact
  cross-confirmation of root cause A-2** from a real production run, found and
  consciously **left unpatched** ("found but NOT patched" per the decision's own reason
  text).
- The uncommitted WIP diff on `tools/analysis/speech_gap_detector.py` /
  `speech_gap_spans.py` (`compute_pause_tighten_spans`, `silence_ranges_via_ffmpeg`,
  new `tighten_pauses`/`min_pause_seconds`/`pause_keep_seconds` params) is exactly an
  in-progress attempt to **productize** the ffmpeg-silencedetect fallback that `d-006`
  had to do by hand — confirms the team already independently arrived at the same
  diagnosis and started fixing it, but it is not finished/wired end-to-end yet (still
  needs: (a) automatic ripple-shift of downstream cuts, matching what was hand-done in
  `d-006`, with no code found anywhere that generalizes it; (b) confirmation the
  `footage_edit_plan` → `edit_decisions` translation step actually consumes
  `pause_tighten` spans instead of requiring another manual decision-log bypass next
  time).
- Real numbers for calibrating recommendation priority: pause-tighten removed **3.97s /
  93.4s (4.2%)** across 16 splice points (avg ~0.25s each) — small per-cut, only
  perceptible in aggregate; consistent with report §3 rec #8 (make the saved-seconds
  number visible per render) already being worth doing.

**Revised ranking (supersedes §1/§2 rankings above):**
- Path A: mid-video `<TransitionSeries>`/narration desync (new, confirmed) > A-2 b-roll
  overlay mismatch (confirmed via `d-007`, independently) > A-3 optional field/no
  enforcement > A-4 frame-exact contiguity fragility > A-1 TalkingHead dead code (now
  low priority — moot for the actual render path).
- Path B: B-1 no deterministic removal_spans→cuts code (confirmed: real run's plan
  produced 0 spans, required a manual bypass) > B-2 `source_in_seconds` risk (actually
  NOT observed as broken in the real run — `edit_decisions_v5.json` has it correctly set
  on every cut, so treat as "was handled correctly by a careful agent this time," not a
  guaranteed-safe mechanism) > B-2(new) no reusable ripple-shift code, only a one-off
  hand fix > B-3 1-second corroboration guard (untested against this real run since the
  Gemini path returned 0 spans before that guard even mattered).

---

## 4. Unresolved questions (need a real render/run to confirm, not resolvable by static read)

1. **Which `renderer_family`/composition does the raw-talking-head hybrid pipeline
   actually select at proposal stage in real runs** — `Explainer` (cuts-aware) or
   `TalkingHead` (single continuous video)? This determines whether root causes A-1/
   B-6 (architecture mismatch) are the dominant issue or moot. Nothing in
   `pipeline_defs/hybrid.yaml` or the directors hardcodes this — it's a gated,
   per-run human/agent decision (`edit-director.md:70-73`).
2. **Was there ever a real Gemini API run of `footage_edit_analyzer` produced and
   fed through to a real render?** No `output/footage_edit_plan/`, `projects/*/`
   artifact, or `*_report*.json` with real applied transition/cut values was found
   in the repo to compare against theory. The `fixture_footage_edit_plan_sample.json`
   is real Gemini output (has real token counts) but its `removal_spans: []` means
   it never exercised the exact behavior in question, and there's no evidence it was
   ever carried through `edit-director.md`'s translation into an actual rendered
   video.
3. Whether `min_disfluency_confidence=0.75` and `min_dead_air_seconds=0.6` are
   themselves too conservative for this specific reference video can only be judged
   against the real transcript/audio — span math itself looks reasonable by
   inspection (QA test `tests/qa/test_10_speech_gap_detection.py` passes on
   synthetic + one real transcript with correct behavior).
4. Whether an agent, in practice (not per the buggy/incomplete written spec), has
   been manually setting `source_in_seconds` correctly out of general competence
   despite it not being documented — i.e., is root cause B-2 theoretical-only or has
   it actually produced wrong-footage renders. No render evidence in repo to check.
