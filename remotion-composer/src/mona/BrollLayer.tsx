import { AbsoluteFill, OffthreadVideo, Sequence, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
// NOTE: OffthreadVideo, NOT @remotion/media <Video>. <Video> renders through a
// canvas that ignores objectFit:cover, which letterboxes the clip with white
// bars — already hit and reverted once for the A-roll (see MonaTimeline). Do not
// "modernise" this back.

export interface BrollClip {
  /** second on the output timeline where the overlay starts */
  start: number;
  /** how long it covers, in seconds — the resolver already trimmed the file to match */
  duration: number;
  /** bare filename; resolved through the same asset base as every other medium */
  src: string;
  fit?: "cover" | "contain";
  opacity?: number;
}

export const BROLL_FADE_S = 0.2;

/**
 * One b-roll clip: video over the A-roll, audio untouched.
 *
 * Keeping the A-roll's sound is what makes this simple enough to be safe. The
 * overlay is a picture layer inside [start, start+duration] and nothing else —
 * no mixing, so the word clock is unaffected and the loudness normalisation
 * downstream sees exactly what it saw before. Giving b-roll its own audio would
 * bring back every sync problem this pipeline spent its effort eliminating; that
 * is deliberately out of scope here.
 */
const BrollClipView: React.FC<{ clip: BrollClip; resolveAsset: (name: string) => string }> = ({
  clip,
  resolveAsset,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.max(1, Math.round(clip.duration * fps));
  const fadeFrames = Math.max(1, Math.round(BROLL_FADE_S * fps));

  // Fade both ends. A hard appear/disappear on a full-frame layer reads as a
  // glitch rather than as an edit.
  const fade = interpolate(
    frame,
    [0, fadeFrames, Math.max(fadeFrames + 1, totalFrames - fadeFrames), totalFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill style={{ opacity: fade * (clip.opacity ?? 1) }}>
      <OffthreadVideo
        src={resolveAsset(clip.src)}
        style={{ width: "100%", height: "100%", objectFit: clip.fit ?? "cover" }}
      />
    </AbsoluteFill>
  );
};

/**
 * All b-roll overlays, sequenced.
 *
 * Sits between the A-roll and the card layer: b-roll covers the footage, and
 * cards, keywords and captions stay above it. A caption hidden behind an overlay
 * is a subtitle that does not exist, so that ordering is not a preference.
 */
export const BrollLayer: React.FC<{
  clips: BrollClip[];
  resolveAsset: (name: string) => string;
}> = ({ clips, resolveAsset }) => {
  const { fps } = useVideoConfig();
  if (!clips.length) return null;

  return (
    <>
      {clips.map((clip, index) => (
        <Sequence
          key={`broll-${index}-${clip.src}`}
          from={Math.round(clip.start * fps)}
          durationInFrames={Math.max(1, Math.round(clip.duration * fps))}
        >
          <BrollClipView clip={clip} resolveAsset={resolveAsset} />
        </Sequence>
      ))}
    </>
  );
};
