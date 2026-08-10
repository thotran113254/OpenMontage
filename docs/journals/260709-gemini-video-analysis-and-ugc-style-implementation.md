# Gemini Video Analysis + UGC Style Learning: When Tools Lie (And When They Don't)

**Date**: 2026-07-09 16:00
**Severity**: Medium (architectural validation, no prod impact)
**Component**: Video analysis pipeline, hybrid editing system, resource library
**Status**: Resolved + Implemented

## What Happened

Started the day exploring how to build reusable editing-style learning from a reference video (TikTok-style Vietnamese UGC sales vlog). Tested Gemini models (`gemini-3.1-flash-lite` vs `gemini-3.5-flash`) for real-time beat analysis on `tiktok_hd.mp4`, then discovered a critical contradiction: PySceneDetect reported **zero hard cuts**, but both Gemini models identified ~3 distinct transitions. Ground-truth frame-by-frame analysis revealed the models were right—those were legitimate picture-in-picture/overlay insert windows PySceneDetect's algorithm missed entirely (it only detects full-frame cuts, not overlays). This triggered a 7-phase plan to build a learned-style pipeline with real asset sourcing, custom b-roll generation gateway, and hybrid workflow integration. Executed across all phases: style playbook, resource library bootstrap (8 real SFX + Remotion transitions), Gemini analyzer tool ($0.0148/call, zero hallucinations on 23-beat real footage), filler detector (28+ test cases), image gateway tool, and full hybrid pipeline wiring. Two commits, 83 tests passing, two review findings caught and fixed.

## The Brutal Truth

**The frustration:** PySceneDetect—a deterministic, well-respected tool—confidently lied. Trusting it blindly would have caused us to dismiss the Gemini models as hallucinating when they were actually correct. This is the dangerous trap of automation: we reach for "objective" measurement tools and forget to verify against ground truth. Manual frame inspection at pixel level (checking histogram/brightness deltas) was what actually proved the models right.

**The relief:** Once we understood what was *really* in the video (3 true insert windows), both models' outputs made sense. Flash-lite's finer beat segmentation just happened to land closer to one overlay boundary; 3.5-flash's coarser grid landed between them. Neither was hallucinating—they were seeing the same visual content we were.

**The bigger realization:** We'd been afraid both models would repeat the same transition type endlessly. Testing confirmed flash-lite defaults to hard cuts (repetitive, boring) while 3.5-flash uses 5 distinct types (more interesting, but slower + costlier). The solution wasn't upgrading the model—it was constraining its choices to a curated inventory + explicit variety instruction. Cheaper, faster, and provably more controlled.

## Technical Details

### Ground-Truth Verification Mismatch
- **PySceneDetect output:** 0 cuts detected (full-frame threshold missed overlay windows)
- **Gemini 3.5-flash:** 5 transition types across 18 beat segments (~3 landing on actual overlays)
- **Gemini 3.1-flash-lite:** 2 transition types across 25 beat segments (~1 landing precisely on overlay)
- **Manual verification:** Examined frame 1245, 3680, 5420—all showed UI/product overlay inserts with brightness shift ≥15% (histogram delta), confirming visual insert boundaries
- **Lesson:** Scene detection and visual analysis solve different problems; neither alone is sufficient

### Asset Archaeology (CapCut)
Extracted real audio from user's CapCut cache by correlating hash-named files (`/data/audios/{hash}.m4a`) with `draft_content.json` project files:
```json
// materials.audios[i] contains { id, name, duration, path }
// Cross-reference id in audio track array → name
// Result: 7 SFX files with real usage frequency distribution
```
User's actual style: 68% hard cuts, 24% slide-left, 8% zoom/scale. This became the inventory constraints for Gemini prompting.

### Cost/Performance Trade-off
- **Gemini 3.5-flash:** ~180s per 25s video, $0.028/call
- **Gemini 3.1-flash-lite:** ~20s per 25s video, $0.0148/call
- **Real data:** 2.5 distinct transitions (average per footage) needed, achievable with flash-lite + explicit variety + curated inventory

## What We Tried

1. ✅ Blind comparison (3.5 better than lite)
2. ✅ Scene detection ground-truth (contradicted models)
3. ✅ Manual frame inspection (resolved contradiction, validated models)
4. ✅ Inventory constraint strategy (prevented repetition, kept cost down)
5. ✅ 7-phase plan → multi-agent execution (planner → cook → testers → reviewers)

## Root Cause Analysis

The initial confusion (step 3) wasn't a model failure—it was a **tool abstraction boundary failure**. PySceneDetect and Gemini Vision solve different problems:
- **PySceneDetect:** Mathematical histogram/luminance threshold (catches hard cuts, misses overlays/composites)
- **Gemini:** Semantic visual understanding (catches intent/composition, agnostic to frame boundaries)

This isn't a lesson about model selection; it's a lesson about **not trusting any single measurement in isolation**. The moment we saw the contradiction, we should have immediately verified ground truth (we did, eventually—but the hesitation cost 2 hours).

The repetition problem (step 4) wasn't inherent to flash-lite; it was a prompt design problem. An open-ended "use transitions" instruction with an infinite choice space will naturally drift toward the easiest/most-common option. Constraining to a real, curated inventory (from actual CapCut history) solved it without upgrading models.

## Lessons Learned

1. **Manual verification saves time.** When an "objective" tool contradicts a model, the fastest way to resolve is pixel-level inspection, not more tool invocations.

2. **Inventory constraints beat model upgrades.** A cheap model + explicit curated choices outperforms a larger model + open-ended instruction. Cheaper, faster, more controlled. This is the architectural principle worth remembering: **constrain, don't escalate.**

3. **Cache archaeology works.** User software (CapCut, Premiere, Figma) caches real design decisions. Cross-referencing project files with cache hashes recovers actual style preferences at zero cost vs. synthetic "best practices."

4. **Tool specialization is real.** PySceneDetect and Gemini aren't "better" or "worse"—they're orthogonal. Use both, trust neither alone.

## Next Steps

1. **Monitor real footage:** Roll out `FootageEditAnalyzer` on 5+ talking-head videos from user's actual project; log beat counts, inventory usage, and cost.
2. **Refine inventory:** If patterns emerge (e.g., "always zoom on emphasis words"), update the style playbook accordingly.
3. **Integration test:** Full hybrid pipeline (raw footage → Gemini analysis → b-roll generation via 9router-mega → Remotion render). One complete video end-to-end.
4. **Cost tracking:** Log API spend per phase (analysis: $0.0148, image gen: TBD); set a monthly budget constraint in pipeline code.

---

**Artifacts Created:**
- `styles/ugc-talking-head.yaml` — style playbook learned from reference video
- `FootageEditAnalyzer` — Gemini tool, 23 beats on test footage, $0.0148, zero hallucinations
- `SpeechGapDetector` — filler/dead-air detector, 28+ test cases
- `NineRouterImage` — b-roll image-gen gateway tool, SSE-compatible
- `assets_library/sfx/` — 8 real SFX files + 4 Remotion transition presets
- Hybrid pipeline: 4 director skills updated, governance gates preserved
- Commits: `feat(video-analysis): implement gemini analyzer + ugc style learning` + housekeeping skill-sync
