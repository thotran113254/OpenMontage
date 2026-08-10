import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";

interface TextCardProps {
  text: string;
  fontSize?: number;
  color?: string;
  backgroundColor?: string;
  // Accent color for the title line (first line of a multi-line text).
  accentColor?: string;
  // When true, anchors the text to a lower-third banner instead of dead
  // center. Used when this card sits over a backgroundVideo (b-roll
  // cutaway over the still-playing anchor footage) so it doesn't cover the
  // speaker's face/mouth -- see SceneRenderer's maybeWrapWithBg.
  lowerThird?: boolean;
  // When true, skips the entrance spring entirely (card starts fully
  // visible). Used for continuation cuts: a single editorial card that was
  // split into two timeline cuts (e.g. around a pause-tighten splice) must
  // not replay its pop-in on the second segment.
  continuation?: boolean;
}

export const TextCard: React.FC<TextCardProps> = ({
  text,
  fontSize = 64,
  color = "#FFFFFF",
  backgroundColor = "#1F2937",
  accentColor = "#F59E0B",
  lowerThird = false,
  continuation = false,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const opacity = continuation ? 1 : spring({ frame, fps, config: { damping: 20 } });
  const scale = continuation
    ? 1
    : spring({
        frame,
        fps,
        config: { damping: 15, stiffness: 100 },
        from: 0.95,
        to: 1,
      });

  // First line is the title (accent color, bold); remaining lines are the
  // body. A single-line text renders as title-only.
  const lines = text.split("\n");
  const title = lines[0];
  const body = lines.slice(1).join("\n");

  const titleSize = lowerThird ? Math.round(fontSize * 0.72) : fontSize;
  const bodySize = Math.round(titleSize * 0.72);
  // Accent title only when there is a title/body hierarchy (or a
  // lower-third banner). A plain single-line full-bleed card keeps the
  // regular text color so existing explainer videos are unaffected.
  const titleColor = body || lowerThird ? accentColor : color;

  return (
    <AbsoluteFill
      style={{
        justifyContent: lowerThird ? "flex-end" : "center",
        alignItems: "center",
        paddingBottom: lowerThird ? "12%" : 0,
        // Solid background only makes sense for a full-bleed card (no
        // anchor footage underneath). Over a backgroundVideo the container
        // is already transparent (see SceneRenderer), so this never paints
        // an opaque box over the b-roll cutaway.
        background: lowerThird ? "transparent" : backgroundColor,
      }}
    >
      <div
        style={{
          opacity,
          transform: `scale(${scale})`,
          fontFamily: "Inter, system-ui, sans-serif",
          textAlign: "center",
          maxWidth: "84%",
          ...(lowerThird
            ? {
                background: "rgba(15, 23, 42, 0.72)",
                borderRadius: 16,
                padding: "28px 44px",
                borderLeft: `6px solid ${accentColor}`,
              }
            : {}),
        }}
      >
        <div
          style={{
            fontSize: titleSize,
            color: titleColor,
            fontWeight: 800,
            lineHeight: 1.2,
            letterSpacing: 1,
          }}
        >
          {title}
        </div>
        {body && (
          <div
            style={{
              fontSize: bodySize,
              color,
              fontWeight: 700,
              lineHeight: 1.3,
              marginTop: 10,
              whiteSpace: "pre-line",
            }}
          >
            {body}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};
