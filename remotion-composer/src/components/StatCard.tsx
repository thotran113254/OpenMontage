import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";

interface StatCardProps {
  stat: string;
  subtitle?: string;
  statFontSize?: number;
  subtitleFontSize?: number;
  color?: string;
  accentColor?: string;
  backgroundColor?: string;
  // When true, wraps the stat+subtitle in a rounded, bordered card matching
  // CalloutBox's visual language (dark panel, left accent bar) instead of
  // bare floating text. Used when this scene sits over a backgroundVideo
  // (b-roll cutaway) -- without a container, plain large colored text
  // directly over footage reads as an unstyled/raw overlay rather than a
  // "stat card" (confirmed against a real render).
  boxed?: boolean;
}

export const StatCard: React.FC<StatCardProps> = ({
  stat,
  subtitle,
  statFontSize = 128,
  subtitleFontSize = 36,
  color = "#FFFFFF",
  accentColor = "#F59E0B",
  backgroundColor = "#1F2937",
  boxed = false,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const scale = spring({
    frame,
    fps,
    config: { damping: 12, stiffness: 120 },
    from: 0.8,
    to: 1,
  });

  // Card opacity (boxed mode only) — rises together with the scale-in so
  // the panel and its content appear as one unit, not a container that
  // pops in solid before the number/subtitle render.
  const cardOpacity = spring({ frame, fps, config: { damping: 18 } });

  const subtitleOpacity = spring({
    // Tightened from -8 to -3 for the boxed variant so there's no gap
    // between the panel becoming visible and its content appearing (same
    // blank-flash issue found and fixed in CalloutBox).
    frame: frame - (boxed ? 3 : 8),
    fps,
    config: { damping: 20 },
  });

  const content = (
    <div style={{ textAlign: boxed ? "left" : "center" }}>
      <div
        style={{
          transform: boxed ? undefined : `scale(${scale})`,
          fontSize: boxed ? Math.round(statFontSize * 0.56) : statFontSize,
          color: accentColor,
          fontFamily: "Inter, system-ui, sans-serif",
          fontWeight: 800,
          lineHeight: 1.15,
        }}
      >
        {stat}
      </div>
      {subtitle && (
        <div
          style={{
            opacity: subtitleOpacity,
            fontSize: boxed ? Math.round(subtitleFontSize * 0.9) : subtitleFontSize,
            color: boxed ? "#E5E7EB" : color,
            fontFamily: "Inter, system-ui, sans-serif",
            fontWeight: 400,
            marginTop: boxed ? 10 : 16,
          }}
        >
          {subtitle}
        </div>
      )}
    </div>
  );

  if (boxed) {
    return (
      <AbsoluteFill
        style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: "10%" }}
      >
        <div
          style={{
            opacity: cardOpacity,
            transform: `scale(${scale})`,
            width: "72%",
            maxWidth: 1380,
            position: "relative",
            display: "flex",
            alignItems: "center",
            backgroundColor: "rgba(15, 23, 42, 0.88)",
            borderRadius: 12,
            padding: "36px 44px",
            boxShadow: "0 2px 12px rgba(0,0,0,0.25)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              position: "absolute", left: 0, top: 0, bottom: 0, width: 6,
              backgroundColor: accentColor, borderRadius: "12px 0 0 12px",
            }}
          />
          {content}
        </div>
      </AbsoluteFill>
    );
  }

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        background: backgroundColor,
      }}
    >
      {content}
    </AbsoluteFill>
  );
};
