import { isTransitionPresetName, resolveCutTransition } from "./preset-map";

// Minimal shape needed for grouping — kept structural (not importing the
// full `Cut` type from Explainer.tsx) to avoid a circular import between the
// main composition and this helper.
export interface CutTiming {
  id: string;
  in_seconds: number;
  out_seconds: number;
  transition_in?: string;
  transition_duration?: number;
}

export type RenderGroup<T extends CutTiming> =
  | { kind: "flat"; cut: T }
  | { kind: "run"; cuts: T[] };

// Two cuts are "contiguous" when the next one starts exactly where the
// previous one ends (frame-rounded, matching the same Math.round(seconds*fps)
// used everywhere else for cut positioning). Only contiguous runs are safe to
// wrap in <TransitionSeries> — it lays children back-to-back internally, so
// any gap or overlap in the source data would otherwise get silently erased.
function isContiguous(prev: CutTiming, cur: CutTiming, fps: number): boolean {
  const prevEndFrame = Math.round(prev.out_seconds * fps);
  const curStartFrame = Math.round(cur.in_seconds * fps);
  return prevEndFrame === curStartFrame;
}

/**
 * Splits a flat cuts array into render groups: contiguous runs that contain
 * at least one activated (non-"none", recognized) `transition_in` become a
 * single "run" group (rendered via <TransitionSeries>); everything else
 * (isolated cuts, gaps, overlaps, or runs where no transition is activated)
 * stays "flat" (rendered via the pre-existing absolute <Sequence> path).
 * This keeps every existing edit_decisions artifact — which never sets
 * transition_in — rendering through the exact same flat path as before.
 */
export function buildRenderGroups<T extends CutTiming>(cuts: T[], fps: number): RenderGroup<T>[] {
  const groups: RenderGroup<T>[] = [];
  let i = 0;

  while (i < cuts.length) {
    let j = i + 1;
    while (j < cuts.length && isContiguous(cuts[j - 1], cuts[j], fps)) {
      j++;
    }

    const run = cuts.slice(i, j);
    const hasActiveTransition = run.some(
      (cut, idx) => idx > 0 && isTransitionPresetName(cut.transition_in) && cut.transition_in !== "none"
    );

    if (run.length > 1 && hasActiveTransition) {
      groups.push({ kind: "run", cuts: run });
    } else {
      for (const cut of run) {
        groups.push({ kind: "flat", cut });
      }
    }

    i = j;
  }

  return groups;
}

/**
 * Real end of rendered content, in frames.
 *
 * <TransitionSeries> overlaps neighboring cuts by each transition's duration,
 * so a run's on-screen length is sum(cut durations) - sum(active transition
 * frames) — SHORTER than the last cut's out_seconds suggests. Computing the
 * composition duration from out_seconds alone leaves a dead tail of
 * background (seen as seconds of black) after the content ends. This mirrors
 * the exact grouping + per-transition clamping the renderer uses.
 */
export function computeContentDurationInFrames<T extends CutTiming>(
  cuts: T[],
  fps: number
): number {
  let end = 0;

  for (const group of buildRenderGroups(cuts, fps)) {
    if (group.kind === "flat") {
      end = Math.max(end, Math.round(group.cut.out_seconds * fps));
      continue;
    }

    const startFrame = Math.round(group.cuts[0].in_seconds * fps);
    const durations = group.cuts.map((c) =>
      Math.round((c.out_seconds - c.in_seconds) * fps)
    );

    let runFrames = 0;
    group.cuts.forEach((cut, idx) => {
      runFrames += durations[idx];
      if (idx === 0) return;
      const transition = resolveCutTransition(
        cut.transition_in,
        cut.transition_duration,
        fps,
        durations[idx - 1],
        durations[idx]
      );
      if (transition) {
        runFrames -= transition.timing.getDurationInFrames({ fps });
      }
    });

    end = Math.max(end, startFrame + runFrames);
  }

  return end;
}
