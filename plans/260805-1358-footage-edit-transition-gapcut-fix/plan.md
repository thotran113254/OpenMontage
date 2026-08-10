---
title: "Fix footage-edit transitions (A/V desync) and gap-cutting (no deterministic path)"
description: "Two confirmed production bugs in the learned-style footage-edit feature (hybrid pipeline): TransitionSeries desyncs narration from video on every non-terminal transition, and removal_spans->cuts[] translation has zero code/test coverage and failed outright on the one real run."
status: in-progress
priority: P1
effort: 26h
branch: main
tags: [hybrid-pipeline, footage-edit, remotion, transitions, speech-gap, edit_decisions]
created: 2026-08-05
---

# Fix footage-edit transitions (A/V desync) and gap-cutting (no deterministic path)

## Goal
Fix the two confirmed root causes behind "transitions look ineffective" and "dead-air/hook
cutting doesn't tighten pacing" in the `hybrid` pipeline's learned-style footage-edit
feature, using real production evidence (not speculation) from `projects/raw-test-1/`.

## Why this plan exists
User-reported: current transitions and gap-cutting aren't effective. A diagnostic pass this
session (`plans/reports/debugger-260805-1400-footage-edit-transition-gapcut-diagnosis-report.md`)
confirmed two structural bugs by reading real, already-rendered artifacts from
`projects/raw-test-1/` (a real footage-edit job, 5 render iterations, `raw-test-1/renders/final_v5.mp4`)
plus its `decision_log.json` — not just static code reading. The team's own prior session already
independently found both bugs and **worked around them by removing the features** rather than
fixing them (v5's `metadata.visual_fix_note`: *"no soft transitions ... narration matches
video by construction"*; b-roll/overlay cards were also dropped entirely, all 26 cuts in v5
have `type`/`backgroundVideo`/`overlays` all empty). This plan fixes the underlying bugs so
transitions and b-roll/gap-cutting can be used again without giving up sync correctness.

## Phases
| # | Phase | Effort | Blockers |
|---|-------|--------|----------|
| 01 | [Deterministic cuts translator](phase-01-deterministic-cuts-translator.md) — replace prose-only `footage_edit_plan -> edit_decisions.cuts[]` mapping with tested code; finish in-flight `pause_tighten` wiring | 7h | none |
| 02 | [B-roll sourcing, content-matching & rendering fix](phase-02-broll-rendering-path-fix.md) — only use b-roll when the project has real clips (no fabricated inserts), match clips to beats by actual visual content, and fix how the placed insert renders (`overlays[]` mismatch vs redirect through `cuts[].backgroundVideo`) | 10h | none (parallel to 01) |
| 03 | [Transition/narration sync fix](phase-03-transition-narration-sync-fix.md) — decision-gated: pick and implement the real fix for `<TransitionSeries>` shrinking video without shrinking narration | 6h | 01 |
| 04 | [Test coverage & regression validation](phase-04-test-coverage-and-validation.md) — close the `removal_spans: []` fixture gap, add ripple-shift/sync assertions, full-suite regression run | 3h | 01, 02, 03 |

## Key architectural facts (verified this session, real evidence not speculation)
- Real render path used in production: `renderer_family_used: "explainer-teacher"` ->
  `Explainer` composition (`tools/video/video_compose.py:681-690` `RENDERER_FAMILY_MAP`).
  `TalkingHead.tsx` (single continuous `videoSrc`, no `cuts[]`/transitions at all) is **not**
  the render path in use — confirmed via `projects/raw-test-1/artifacts/render_report_v5.json`.
  Not in scope for this plan; note only.
- **Transition bug is structural, not an edge case.** `remotion-composer/src/transitions/build-render-groups.ts:70-75`
  documents it directly: `<TransitionSeries>` overlaps two adjacent cuts by
  `transition_duration`, so a run's on-screen length is
  `sum(cut durations) - sum(transition frames)`. `Root.tsx:139` (`computeContentDurationInFrames`,
  already wired, uncommitted WIP) fixes only the **composition-total** duration (avoids a
  black tail at the end) — it does **not** re-time the narration/caption/word-timestamp
  tracks, which stay on the original, unshrunk timeline. Every cut after a mid-video
  transition therefore plays out of sync with narration from that point on, permanently.
  Real evidence: `projects/raw-test-1/` used 2-3 transitions (`fade`, `slide`) in
  `edit_decisions.json`/`_v2`/`_v3`; `edit_decisions_v5.json` has **zero** transitions and an
  explicit note that this was to fix the desync.
- **Gap-cutting has no working deterministic path.** `output/raw_test_1_v2/footage_edit_plan.json`
  (real Gemini output) has `removal_spans: []` — the LLM-driven detection returned nothing
  usable. `projects/raw-test-1/artifacts/decision_log.json` decision `d-006`
  (`category: "fallback_decision"`) shows an agent had to manually rerun gap detection via
  ffmpeg `silencedetect` and hand-compute a ripple-delete timeline compression (93.4s ->
  89.402s, 16 splice points) — the productized `footage_edit_plan -> edit_decisions`
  translation was bypassed, not exercised. The translation itself is 100% prose
  (`skills/pipelines/hybrid/edit-director.md:60-64`), agent-executed every time, with zero
  test coverage of the `removal_spans` path (`tests/test_edit_decisions_hand_built.py`'s
  fixture has `removal_spans: []`).
- **`source_in_seconds` is already in the schema and the renderer** (`schemas/artifacts/edit_decisions.schema.json:60`,
  `remotion-composer/src/Explainer.tsx:208-210`) — it was set correctly by hand in the one real
  run (`edit_decisions_v5.json`), so this is a "make it deterministic/tested," not a "field is
  missing," fix.
- **B-roll overlays never render either way.** `edit_decisions.schema.json:102-126` overlays
  shape (`asset_id`/`start_seconds`/`end_seconds`/`position{x,y,width,height}`) does not match
  `Explainer.tsx:289-301`'s `Overlay` interface (`type: section_title|stat_reveal|hero_title|provider_chip`,
  `in_seconds`/`out_seconds`, no asset rendering — `OverlayRenderer`, `Explainer.tsx:827-865`,
  returns `null` for any other type). Decision `d-007` in the same log independently found this
  exact mismatch and explicitly did **not** patch it. Checked in `edit_decisions_v5.json`: the
  team's actual workaround was to drop b-roll/cards entirely (every cut's `type`/`backgroundVideo`
  is empty, `overlays: []`) rather than fix or reroute it — the schema already has a working
  alternate path for this (`Cut.backgroundVideo`/`backgroundVideoStart`, schema lines 53-54,
  `Explainer.tsx:242-243`) that nothing currently uses for footage-edit b-roll.
- **B-roll is never grounded in real footage today, on top of the rendering bug.**
  `tools/analysis/footage_edit_analyzer.py:101-104`'s `input_schema` accepts exactly one video —
  no mechanism anywhere in `pipeline_defs/hybrid.yaml` lets a project declare extra b-roll source
  clips. `b_roll.suggested_visual` (`footage_edit_prompt.py:194-197`) is Gemini imagining a
  generic cutaway from watching the main footage alone, never checked against real inventory or
  its content. `asset-director.md:82`'s "prefer real footage over generated" policy is currently
  unenforceable — no data exists for a director to check against. Phase 02 now also covers: an
  opt-in way to declare real b-roll clips, a content-description step so the system knows what
  each clip depicts, confidence-gated matching of beats to clips, and a clean no-insert fallback
  (not fabrication) when nothing qualifies.
- Uncommitted WIP already exists on `tools/analysis/speech_gap_detector.py` /
  `speech_gap_spans.py` (`compute_pause_tighten_spans`, `silence_ranges_via_ffmpeg`,
  `tighten_pauses` param) — an in-flight attempt to productize the `d-006` manual fallback.
  Phase 01 finishes this, it does not restart it. Run `git diff -- tools/analysis/speech_gap_detector.py tools/analysis/speech_gap_spans.py`
  before touching either file.

## Approval gates — DO NOT decide in this plan (surface at proposal/implementation time)
1. **Transition/narration sync fix approach (Phase 03).** Three real options exist with real
   tradeoffs (split-narration-per-run vs re-time-everything-post-shrink vs
   restrict-transitions-to-narration-free-segments). Per `AGENT_GUIDE.md`'s Decision
   Communication Contract, this changes render behavior and must be presented to the user
   before implementation, not silently picked. See phase-03 for the full option matrix.
2. **B-roll sourcing, matching threshold, and rendering path (Phase 02).** Three separate calls:
   (a) how a project declares real b-roll clips (new input vs reusing `asset_manifest`), (b) the
   confidence threshold for matching a beat's `suggested_visual` to a real clip's content before
   using it, (c) extend `Explainer`'s `Overlay`/`OverlayRenderer` to support asset-based overlays
   vs redirect footage-edit b-roll through `cuts[].type` + `backgroundVideo` (reuses
   already-working renderer code, no new React component work, but changes what
   `edit_decisions.overlays[]` is for going forward). See phase-02 for the full option matrix —
   pick one path per decision, don't drift into building both.

## Implementation status (2026-08-06)

All 4 phases implemented and code-reviewed this session (user authorized full-auto execution,
"cook auto toàn bộ" — decision gates resolved with the plan's own recommended defaults, logged
per-phase in each Todo list). Repo-wide test suite: **1280 passed, 9 skipped, 0 failed**
(`pytest tests/ --ignore=tests/qa`) + **33/33** manual QA script checks, independently confirmed
by a `tester` subagent. Mandatory `code-reviewer` subagent found one **critical** bug in phase-03's
first attempt (transition kept on the wrong cut — the one position the renderer can never
activate); fixed same session, re-reviewed, **8.5/10**, critical resolved, no new criticals.

Two items remain genuinely open across phases 02/03 (not blocking the code, just unverified against
a real render — no render environment available in this offline session): a real Remotion render
confirming (a) a real b-roll match actually appears on screen, and (b) the corrected
last-cut-only transition actually looks fine to a human, including whether the bounded
~0.3s trailing-narration truncation is perceptible. Both are flagged as the first thing to check
on the next real run through this pipeline.

## Resolved questions
- **Is `TalkingHead.tsx` dead code a priority?** No — confirmed moot for the actual render path
  (`renderer_family_used: "explainer-teacher"` in real render reports). Documented as a note in
  phase-02/03 context, not a phase.
- **Was `video_compose.py`'s uncommitted diff related to this feature?** No — verified it's
  unrelated visual-spotcheck/luma and music-expected-flag QA work. Not touched by this plan
  except where phase-01/03 need it for wiring the new deterministic translator call site.
