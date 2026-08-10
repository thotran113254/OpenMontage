import { fitText } from "@remotion/layout-utils";
import {
  AbsoluteFill,
  Audio,
  Easing,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { loadFont } from "@remotion/google-fonts/BeVietnamPro";

// ---------------------------------------------------------------------------
// MONA-style talking-head sample (white dot-grid explainer look).
// Reproduces the reference format faithfully: speech-tracking caption pills,
// full-screen WHITE dot-grid explainer cards (gradient title + numbered badge
// + per-line bullet reveal + colored icons), and — the signature move — the
// A-roll itself shrinks into a rounded bottom-right PiP as a card wipes in,
// rather than a hard cut. Rainbow accent stays pinned bottom-left.
// ---------------------------------------------------------------------------

export const { fontFamily: BVP } = loadFont("normal", {
  weights: ["500", "600", "700", "800"],
  subsets: ["vietnamese", "latin"],
});

export const BRAND_GRADIENT = "linear-gradient(90deg,#2563EB 0%,#7C3AED 52%,#EC4899 100%)";
export const ICON_COLORS = ["#7C3AED", "#EC4899", "#2563EB", "#06B6D4", "#F59E0B"];
export const TRANSITION_S = 0.45; // A-roll <-> PiP morph duration

export interface MonaCaptionLine {
  text: string;
  startMs: number;
  endMs: number;
  highlight?: string;
  // semantic accent for the highlighted phrase — red for mistakes/warnings,
  // green for solutions, yellow for numbers; defaults to the brand blue.
  highlightColor?: string;
}

export interface MonaCard {
  inSeconds: number;
  outSeconds: number;
  kicker: string;
  title: string;
  badge: string;
  bullets: string[];
  // Optional absolute seconds (video timeline) at which each bullet appears —
  // lets points reveal in sync with speech across a long card while the A-roll
  // stays shrunk in the PiP. Falls back to an even cascade when omitted.
  bulletTimes?: number[];
}

// A floating kinetic keyword that pops around the speaker, synced to an
// emphasized spoken word. Colors/rotation scattered like the reference.
export interface MonaKeyword {
  text: string;
  color: string;
  xPct: number; // 0..100 horizontal center
  yPct: number; // 0..100 vertical center
  rotation: number; // degrees
  inSeconds: number;
  outSeconds: number;
  fontSize?: number;
  anim?: "pop" | "drop" | "slide" | "whip"; // entrance flavor
}

// A quick zoom "punch-in" on the A-roll at an emphasized beat — the classic
// short-form retention move: snap in fast, hold, ease back out.
export interface MonaPunchIn {
  atSeconds: number;
  scale?: number; // default 1.06
  holdSeconds?: number; // default 1.1
}

export interface MonaSampleProps {
  [key: string]: unknown;
  videoSrc: string;
  lines: MonaCaptionLine[];
  keywords?: MonaKeyword[];
  brandPill?: string;
  card?: MonaCard;
  cards?: MonaCard[];
  punchIns?: MonaPunchIn[];
}

// --- Rainbow corner accent (brand mark, always on) ----------------------------
export const RainbowCorner: React.FC = () => (
  <div
    style={{
      position: "absolute",
      left: 0,
      bottom: 0,
      width: 230,
      height: 12,
      background: "linear-gradient(90deg,#F59E0B,#EC4899,#7C3AED,#2563EB,#06B6D4)",
      borderTopRightRadius: 8,
    }}
  />
);

// --- Caption pills, one per phrase-line (never split a phrase) -----------------
const CaptionPill: React.FC<{ lines: MonaCaptionLine[] }> = ({ lines }) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill>
      {lines.map((line, i) => {
        const nextStart = lines[i + 1]?.startMs ?? line.endMs + 300;
        // Clear the pill shortly after the phrase is spoken instead of holding
        // it until the next phrase — otherwise it "freezes" through pauses and
        // at the very end of the video.
        const visibleUntil = Math.min(nextStart, line.endMs + 450);
        const from = Math.round((line.startMs / 1000) * fps);
        const dur = Math.max(6, Math.round(((visibleUntil - line.startMs) / 1000) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={dur}>
            <CaptionLineView line={line} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const CaptionLineView: React.FC<{ line: MonaCaptionLine }> = ({ line }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  // Snappier entrance with a slight overshoot so each phrase "lands" — keeps
  // the eye locked on the caption instead of it drifting in passively.
  const entrance = spring({ frame, fps, config: { damping: 13, stiffness: 190, mass: 0.7 } });
  const popScale = interpolate(entrance, [0, 1], [0.9, 1]);

  let parts: { t: string; hot: boolean }[] = [{ t: line.text, hot: false }];
  if (line.highlight && line.text.includes(line.highlight)) {
    const idx = line.text.indexOf(line.highlight);
    parts = [
      { t: line.text.slice(0, idx), hot: false },
      { t: line.highlight, hot: true },
      { t: line.text.slice(idx + line.highlight.length), hot: false },
    ].filter((p) => p.t.length > 0);
  }

  // 330px bottom clearance keeps the pill above TikTok/Reels UI chrome
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 330 }}>
      <div
        style={{
          opacity: entrance,
          transform: `translateY(${interpolate(entrance, [0, 1], [26, 0])}px) scale(${popScale})`,
          background: "rgba(20,22,32,0.94)",
          borderRadius: 18,
          padding: "18px 32px",
          maxWidth: "88%",
          textAlign: "center",
          boxShadow: "0 12px 34px rgba(0,0,0,0.4)",
        }}
      >
        <span style={{ fontSize: 54, fontWeight: 800, fontFamily: BVP, color: "#FFFFFF", lineHeight: 1.3 }}>
          {parts.map((p, i) => (
            <span key={i} style={{ color: p.hot ? (line.highlightColor ?? "#38BDF8") : "#FFFFFF" }}>
              {p.t}
            </span>
          ))}
        </span>
      </div>
    </AbsoluteFill>
  );
};

// --- Floating kinetic keywords (pop around the speaker, over the A-roll) -------
const FloatingKeywords: React.FC<{ keywords: MonaKeyword[]; damp: number }> = ({ keywords, damp }) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill style={{ opacity: damp }}>
      {keywords.map((k, i) => {
        const from = Math.round(k.inSeconds * fps);
        const dur = Math.max(1, Math.round((k.outSeconds - k.inSeconds) * fps));
        return (
          <Sequence key={i} from={from} durationInFrames={dur}>
            <KeywordView kw={k} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const KeywordView: React.FC<{ kw: MonaKeyword }> = ({ kw }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width } = useVideoConfig();
  const anim = kw.anim ?? "pop";

  // Low-damping spring => natural overshoot => snappy "pop".
  const s = spring({ frame, fps, config: { damping: 9, stiffness: 190, mass: 0.8 } });

  // gentle idle float + micro-wobble so it feels alive, not pasted-on
  const t = frame / fps;
  const floatY = Math.sin(t * Math.PI * 1.3) * 5;
  const wobble = Math.sin(t * Math.PI * 1.7) * 0.8;

  // exit: shrink + fade over the last ~9 frames
  const exit = interpolate(frame, [durationInFrames - 9, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const exitScale = interpolate(exit, [0, 1], [0.8, 1]);

  // entrance transform per flavor
  let scale = interpolate(s, [0, 1], [0.35, 1]);
  let tx = 0;
  let ty = 0;
  if (anim === "drop") {
    ty = interpolate(s, [0, 1], [-90, 0]);
    scale = interpolate(s, [0, 1], [0.7, 1]);
  } else if (anim === "slide") {
    tx = interpolate(s, [0, 1], [-120, 0]);
    scale = 1;
  } else if (anim === "whip") {
    tx = interpolate(s, [0, 1], [140, 0]);
    scale = interpolate(s, [0, 1], [0.6, 1]);
  }

  // Edge-safe anchoring so long words never get clipped:
  // left-ish x -> anchor to the left, right-ish x -> anchor to the right,
  // middle -> center. Keeps the text fully on-screen regardless of length.
  const side = kw.xPct <= 35 ? "left" : kw.xPct >= 65 ? "right" : "center";

  // Fit the word to the space its anchor leaves. Measured with the real font
  // rather than estimated from character count: a per-character width guess
  // under-reads accented Vietnamese uppercase and let long keywords run off
  // the right edge even after being "shrunk to fit".
  const availableWidth =
    (side === "left"
      ? width * (1 - Math.max(4, kw.xPct) / 100)
      : side === "right"
        ? width * (1 - Math.max(4, 100 - kw.xPct) / 100)
        : width * Math.min(kw.xPct, 100 - kw.xPct) / 100 * 2) - EDGE_MARGIN_PX;

  const requestedSize = kw.fontSize ?? 76;
  const fitted = fitText({
    text: kw.text.toUpperCase(),
    withinWidth: Math.max(120, availableWidth),
    fontFamily: BVP,
    fontWeight: 800,
    letterSpacing: "0.5px",
    textTransform: "uppercase",
  });
  const size = Math.max(MIN_KEYWORD_FONT_PX, Math.min(requestedSize, fitted.fontSize));
  const outer: React.CSSProperties = {
    position: "absolute",
    top: `${kw.yPct}%`,
    transformOrigin: `${side} center`,
    transform: `translateY(-50%) translate(${tx}px, ${ty + floatY}px) rotate(${kw.rotation + wobble}deg) scale(${scale * exitScale})`,
    opacity: Math.min(Math.max(s, 0), exit),
    fontFamily: BVP,
    fontWeight: 800,
    fontSize: size,
    color: kw.color,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    WebkitTextStroke: "3px #ffffff",
    paintOrder: "stroke fill",
    textShadow: "0 8px 20px rgba(0,0,0,0.5)",
    whiteSpace: "nowrap",
  };
  if (side === "left") {
    outer.left = `${Math.max(4, kw.xPct)}%`;
  } else if (side === "right") {
    outer.right = `${Math.max(4, 100 - kw.xPct)}%`;
  } else {
    outer.left = `${kw.xPct}%`;
    outer.transform = `translateX(-50%) translateY(-50%) translate(${tx}px, ${ty + floatY}px) rotate(${kw.rotation + wobble}deg) scale(${scale * exitScale})`;
  }

  return <div style={outer}>{kw.text}</div>;
};

// --- White dot-grid explainer card content ------------------------------------
// Keyword text must clear the frame edge and stay readable at any length.
export const EDGE_MARGIN_PX = 40;
export const MIN_KEYWORD_FONT_PX = 44;

export const CARD_XFADE_S = 0.5; // push-slide duration between back-to-back cards

export const ExplainerCard: React.FC<{ card: MonaCard; slideIn?: boolean; slideOut?: boolean }> = ({
  card,
  slideIn,
  slideOut,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const rise = spring({ frame, fps, config: { damping: 22, stiffness: 130 } });

  // slow-drifting soft blobs for depth
  const drift = interpolate(frame, [0, 120], [0, 40]);

  // Push-slide between adjacent cards: incoming content slides in from the
  // right, outgoing slides off to the left — the dot-grid background stays
  // put so it reads as a content swap, not a hard scene cut.
  const xf = CARD_XFADE_S * fps;
  const ease = Easing.inOut(Easing.cubic);
  const inX = slideIn
    ? interpolate(frame, [0, xf], [1080, 0], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" })
    : 0;
  const outX = slideOut
    ? interpolate(frame, [durationInFrames - xf, durationInFrames], [0, -1080], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" })
    : 0;
  const contentX = inX + outX;

  return (
    <AbsoluteFill
      style={{
        background: "#FFFFFF",
        backgroundImage: "radial-gradient(circle, rgba(120,130,160,0.20) 1.7px, transparent 1.7px)",
        backgroundSize: "40px 40px",
        overflow: "hidden",
      }}
    >
      {/* depth blobs */}
      <div style={{ position: "absolute", top: 200 + drift, right: -120, width: 380, height: 380, borderRadius: "50%", background: "radial-gradient(circle,#EC489922,transparent 70%)", filter: "blur(20px)" }} />
      <div style={{ position: "absolute", bottom: 300 - drift, left: -140, width: 420, height: 420, borderRadius: "50%", background: "radial-gradient(circle,#2563EB22,transparent 70%)", filter: "blur(20px)" }} />

      <div style={{ position: "absolute", top: 120, left: 70, right: 70, transform: `translateX(${contentX}px)` }}>
        {/* Kicker pill */}
        <div
          style={{
            display: "inline-block",
            background: BRAND_GRADIENT,
            color: "#fff",
            fontFamily: BVP,
            fontWeight: 800,
            fontSize: 30,
            letterSpacing: 0.5,
            padding: "12px 28px",
            borderRadius: 999,
            transform: `translateY(${interpolate(rise, [0, 1], [-22, 0])}px)`,
            boxShadow: "0 8px 22px rgba(124,58,237,0.28)",
          }}
        >
          {card.kicker}
        </div>

        {/* Badge + gradient title — auto-shrink long titles; roomy line-height
            so Vietnamese diacritics are never clipped top/bottom. */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 26, marginTop: 40 }}>
          <div
            style={{
              width: 96,
              height: 96,
              borderRadius: "50%",
              background: BRAND_GRADIENT,
              color: "#fff",
              fontFamily: BVP,
              fontWeight: 800,
              fontSize: 56,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              marginTop: 6,
              boxShadow: "0 10px 24px rgba(124,58,237,0.3)",
              transform: `scale(${interpolate(rise, [0, 1], [0.6, 1])})`,
            }}
          >
            {card.badge}
          </div>
          <div
            style={{
              fontFamily: BVP,
              fontWeight: 800,
              fontSize: card.title.length > 12 ? 78 : 96,
              lineHeight: 1.18,
              paddingTop: 6,
              paddingBottom: 10,
              background: BRAND_GRADIENT,
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              color: "transparent",
            }}
          >
            {card.title}
          </div>
        </div>

        {/* Bullets — "ghost" teasing keeps the wait alive: upcoming points sit
            blurred/dim on screen (open loop — the viewer knows more is coming),
            the spoken one word-staggers in bright, and passed ones dim so the
            eye always has a single focus. Progress dots track position. */}
        {(() => {
          const delays = card.bullets.map((_, i) =>
            card.bulletTimes && card.bulletTimes[i] != null
              ? Math.max(0, Math.round((card.bulletTimes[i] - card.inSeconds) * fps))
              : 14 + i * 22,
          );
          const revealedCount = delays.filter((d) => frame >= d).length;
          return (
            <>
              <div style={{ display: "flex", gap: 12, marginTop: 34 }}>
                {card.bullets.map((_, i) => (
                  <div
                    key={`dot-${i}`}
                    style={{
                      width: 16,
                      height: 16,
                      borderRadius: "50%",
                      background: i < revealedCount ? BRAND_GRADIENT : "#E2E8F0",
                      transition: "none",
                    }}
                  />
                ))}
              </div>
              <div style={{ marginTop: 40, display: "flex", flexDirection: "column", gap: 34 }}>
                {card.bullets.map((b, i) => {
                  const d = delays[i];
                  const revealed = frame >= d;
                  const isActive = revealed && revealedCount - 1 === i;
                  const bp = spring({ frame: frame - d, fps, config: { damping: 14, stiffness: 160, mass: 0.9 } });
                  // ghost -> full: opacity 0.16->1, blur 5px->0, slide -30->0
                  const op = revealed ? interpolate(bp, [0, 1], [0.16, isActive ? 1 : 0.62]) : 0.16;
                  const blur = revealed ? (1 - bp) * 5 : 5;
                  const words = b.split(" ");
                  return (
                    <div
                      key={i}
                      style={{
                        opacity: op,
                        filter: blur > 0.2 ? `blur(${blur.toFixed(1)}px)` : "none",
                        transform: `translateX(${revealed ? interpolate(bp, [0, 1], [-30, 0]) : 0}px)`,
                        display: "flex",
                        alignItems: "center",
                        gap: 22,
                      }}
                    >
                      <div
                        style={{
                          width: 44,
                          height: 44,
                          borderRadius: 12,
                          background: revealed ? ICON_COLORS[i % ICON_COLORS.length] : "#CBD5E1",
                          flexShrink: 0,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          transform: `rotate(${revealed ? interpolate(bp, [0, 1], [-90, 0]) : 0}deg)`,
                        }}
                      >
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                          <path d="M5 12.5l4.5 4.5L19 7" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </div>
                      <div style={{ fontFamily: BVP, fontWeight: 600, fontSize: 50, color: revealed ? "#1F2937" : "#94A3B8", lineHeight: 1.28 }}>
                        {revealed
                          ? words.map((w, wi) => {
                              // word-by-word stagger (~2 frames apart) after reveal
                              const wp = interpolate(frame - d - wi * 2, [0, 6], [0.25, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
                              return (
                                <span key={wi} style={{ opacity: wp }}>
                                  {w}{" "}
                                </span>
                              );
                            })
                          : b}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          );
        })()}
      </div>
    </AbsoluteFill>
  );
};

// --- Progress [0..1] of A-roll morphing into the corner PiP -------------------
// Adjacent/near-adjacent cards are merged into one continuous PiP span so the
// A-roll never bounces fullscreen->PiP->fullscreen at a card boundary — the
// face stays parked in the corner while card content swaps underneath.
export const PIP_BRIDGE_S = 1.5; // gaps shorter than this keep the PiP held

export function mergedPipSpans(cards: MonaCard[]): { inS: number; outS: number }[] {
  const sorted = [...cards].sort((a, b) => a.inSeconds - b.inSeconds);
  const spans: { inS: number; outS: number }[] = [];
  for (const c of sorted) {
    const last = spans[spans.length - 1];
    if (last && c.inSeconds - last.outS <= PIP_BRIDGE_S) {
      last.outS = Math.max(last.outS, c.outSeconds);
    } else {
      spans.push({ inS: c.inSeconds, outS: c.outSeconds });
    }
  }
  return spans;
}

export function pipProgress(frame: number, fps: number, cards: MonaCard[]): number {
  let p = 0;
  const ease = Easing.inOut(Easing.cubic); // smooth accel/decel morph
  for (const s of mergedPipSpans(cards)) {
    const inF = s.inS * fps;
    const outF = s.outS * fps;
    const tF = TRANSITION_S * fps;
    const up = interpolate(frame, [inF, inF + tF], [0, 1], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    const down = interpolate(frame, [outF - tF, outF], [1, 0], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    p = Math.max(p, Math.min(up, down));
  }
  return p;
}

// Zoom factor for the A-roll from punch-in beats: snap in (0.2s), hold, ease
// out (0.35s). Damped by pip progress so it never fights the PiP morph.
export function punchScale(frame: number, fps: number, punchIns: MonaPunchIn[]): number {
  let z = 1;
  for (const pi of punchIns) {
    const inF = pi.atSeconds * fps;
    const hold = (pi.holdSeconds ?? 1.1) * fps;
    const target = pi.scale ?? 1.06;
    const s = interpolate(
      frame,
      [inF, inF + 0.2 * fps, inF + 0.2 * fps + hold, inF + 0.2 * fps + hold + 0.35 * fps],
      [1, target, target, 1],
      { easing: Easing.out(Easing.cubic), extrapolateLeft: "clamp", extrapolateRight: "clamp" },
    );
    z = Math.max(z, s);
  }
  return z;
}

export const MonaSample: React.FC<MonaSampleProps> = ({
  videoSrc,
  lines,
  keywords,
  brandPill,
  card,
  cards,
  punchIns,
}) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  const cardList = cards ?? (card ? [card] : []);
  const kw = keywords ?? [];
  const p = pipProgress(frame, fps, cardList);
  // punch-in only meaningful while the A-roll is (near) fullscreen
  const punch = 1 + (punchScale(frame, fps, punchIns ?? []) - 1) * (1 - p);

  // A-roll box: fullscreen (p=0) -> bottom-right PiP (p=1)
  const left = interpolate(p, [0, 1], [0, 720]);
  const top = interpolate(p, [0, 1], [0, 1090]);
  const width = interpolate(p, [0, 1], [1080, 300]);
  const height = interpolate(p, [0, 1], [1920, 390]);
  const radius = interpolate(p, [0, 1], [0, 36]);
  const borderW = interpolate(p, [0, 1], [0, 4]);

  const brandOp = interpolate(frame, [10, 25, 135, 155], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Cards sorted once; each knows whether a neighbour touches it so the
  // content push-slides between back-to-back cards instead of hard-swapping.
  const sortedCards = [...cardList].sort((a, b) => a.inSeconds - b.inSeconds);

  return (
    <AbsoluteFill style={{ backgroundColor: "#fff" }}>
      {/* White explainer cards sit behind the A-roll while active */}
      {sortedCards.map((c, i) => {
        const prev = sortedCards[i - 1];
        const next = sortedCards[i + 1];
        const slideIn = !!prev && c.inSeconds - prev.outSeconds <= PIP_BRIDGE_S;
        const slideOut = !!next && next.inSeconds - c.outSeconds <= PIP_BRIDGE_S;
        return (
          <Sequence
            key={`card-${i}`}
            from={Math.round(c.inSeconds * fps)}
            durationInFrames={Math.round((c.outSeconds - c.inSeconds) * fps)}
          >
            <ExplainerCard card={c} slideIn={slideIn} slideOut={slideOut} />
          </Sequence>
        );
      })}

      {/* A-roll — morphs from fullscreen into the corner PiP */}
      <div
        style={{
          position: "absolute",
          left,
          top,
          width,
          height,
          borderRadius: radius,
          overflow: "hidden",
          border: borderW > 0.5 ? `${borderW}px solid #fff` : "none",
          boxShadow: p > 0.05 ? "0 18px 44px rgba(0,0,0,0.28)" : "none",
        }}
      >
        <OffthreadVideo
          src={staticFile(videoSrc)}
          style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${punch})` }}
        />
      </div>

      {/* Top brand pill (hook) */}
      {brandPill && (
        <div style={{ position: "absolute", top: 90, width: "100%", display: "flex", justifyContent: "center", opacity: brandOp }}>
          <div style={{ background: BRAND_GRADIENT, color: "#fff", fontFamily: BVP, fontWeight: 800, fontSize: 34, letterSpacing: 0.5, padding: "14px 34px", borderRadius: 999, boxShadow: "0 10px 30px rgba(124,58,237,0.4)" }}>
            {brandPill}
          </div>
        </div>
      )}

      {/* Floating keywords over the A-roll (fade as a card takes over) */}
      {kw.length > 0 && <FloatingKeywords keywords={kw} damp={1 - p} />}

      {/* Captions (topmost) */}
      <CaptionPill lines={lines} />

      {/* Rainbow brand corner */}
      <RainbowCorner />

      {/* --- Sound design ------------------------------------------------ */}
      {/* pop on each keyword */}
      {kw.map((k, i) => (
        <Sequence key={`pop-${i}`} from={Math.round(k.inSeconds * fps)} durationInFrames={fps}>
          <Audio src={staticFile("sfx_pop.mp3")} volume={0.5} />
        </Sequence>
      ))}
      {/* whoosh when each card wipes in */}
      {cardList.map((c, i) => (
        <Sequence key={`whoosh-${i}`} from={Math.max(0, Math.round((c.inSeconds - 0.15) * fps))} durationInFrames={Math.round(0.6 * fps)}>
          <Audio src={staticFile("sfx_whoosh.mp3")} volume={0.45} />
        </Sequence>
      ))}
      {/* soft pop as each bullet lands — audio accent glued to the text reveal */}
      {cardList.flatMap((c, i) =>
        (c.bulletTimes ?? []).map((bt, j) => (
          <Sequence key={`bullet-pop-${i}-${j}`} from={Math.max(0, Math.round(bt * fps))} durationInFrames={Math.round(0.5 * fps)}>
            <Audio src={staticFile("sfx_pop.mp3")} volume={0.3} />
          </Sequence>
        )),
      )}
    </AbsoluteFill>
  );
};
