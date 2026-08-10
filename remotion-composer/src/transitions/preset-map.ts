import { linearTiming, type TransitionPresentation, type TransitionTiming } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { wipe } from "@remotion/transitions/wipe";

// The 4 presets confirmed by the user (matches real CapCut usage history —
// mostly hard cuts, occasional slide). "none" is a passthrough/no-op: it must
// never insert a <TransitionSeries.Transition>, it just means "hard cut".
export const TRANSITION_PRESET_NAMES = ["none", "fade", "slide", "wipe"] as const;
export type TransitionPresetName = (typeof TRANSITION_PRESET_NAMES)[number];

export function isTransitionPresetName(value: unknown): value is TransitionPresetName {
  return typeof value === "string" && (TRANSITION_PRESET_NAMES as readonly string[]).includes(value);
}

// Short and fixed so a transition doesn't meaningfully eat into a cut's
// perceived on-screen duration. Overridable per-cut via the existing
// edit_decisions `transition_duration` (seconds) field — no new schema field
// needed.
export const DEFAULT_TRANSITION_DURATION_FRAMES = 14;

// slide/wipe both support this direction value. The user's real CapCut usage
// is overwhelmingly slide-from-left; hardcoding one direction (YAGNI) avoids
// inventing a schema field the 4-preset scope never asked for.
const DEFAULT_DIRECTION = "from-left" as const;

export interface TransitionConfig {
  presentation: TransitionPresentation<any>;
  timing: TransitionTiming;
}

function buildPresentation(name: Exclude<TransitionPresetName, "none">): TransitionPresentation<any> {
  switch (name) {
    case "fade":
      return fade();
    case "slide":
      return slide({ direction: DEFAULT_DIRECTION });
    case "wipe":
      return wipe({ direction: DEFAULT_DIRECTION });
  }
}

/**
 * Resolves a safe transition config for the adjacency entering `cut`, given
 * the neighboring (already frame-rounded) durations on both sides.
 *
 * Returns null when the transition should NOT be applied — either because
 * `transition_in` is absent/"none"/unrecognized, or because the surrounding
 * cuts are too short to safely fit the requested overlap. Returning null is
 * always safe: the caller falls back to a hard cut, which is the pre-existing
 * behavior this change must not regress.
 */
export function resolveCutTransition(
  transitionIn: string | undefined,
  requestedDurationSeconds: number | undefined,
  fps: number,
  prevDurationFrames: number,
  nextDurationFrames: number
): TransitionConfig | null {
  if (!isTransitionPresetName(transitionIn) || transitionIn === "none") {
    return null;
  }

  const requestedFrames =
    requestedDurationSeconds != null && requestedDurationSeconds > 0
      ? Math.round(requestedDurationSeconds * fps)
      : DEFAULT_TRANSITION_DURATION_FRAMES;

  // Clamp to at most half of either neighboring cut so both sides keep a
  // visible, non-overlapped portion; Remotion also hard-errors if a sequence
  // is shorter than its adjacent transition, so this clamp doubles as the
  // guard against that.
  const safeFrames = Math.min(
    requestedFrames,
    Math.floor(prevDurationFrames / 2),
    Math.floor(nextDurationFrames / 2)
  );

  if (safeFrames < 2) {
    return null;
  }

  return {
    presentation: buildPresentation(transitionIn),
    timing: linearTiming({ durationInFrames: safeFrames }),
  };
}
