import {
  AbsoluteFill,
  Audio,
  Easing,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useRemotionEnvironment,
  useVideoConfig,
} from "remotion";
// NOTE: @remotion/media <Video> was tried and reverted — it renders through a
// canvas that ignores objectFit:cover, letterboxing the PiP with white bars,
// and benchmarked no faster than OffthreadVideo for this h264 footage.
import { OffthreadVideo } from "remotion";
import { BrollLayer, type BrollClip } from "./BrollLayer";
import {
  BVP,
  CaptionLineView,
  ExplainerCard,
  OverlayCard,
  KeywordView,
  TRANSITION_S,
  PIP_BRIDGE_S,
  shouldUseBoardCards,
  type MonaCard,
  type MonaCaptionLine,
  type MonaKeyword,
} from "./MonaSample";

// ---------------------------------------------------------------------------
// MonaTimeline — a free-form edit-program interpreter. Instead of fixed prop
// slots, the whole edit is ONE flat list of timeline events that may overlap
// arbitrarily (a keyword + punch-in + sfx on the same beat, etc.). The AI
// director composes the program; this component just plays it back.
// All times are REAL seconds, already resolved from the word spine.
// ---------------------------------------------------------------------------

// Only files that actually exist in public/ — unknown names are skipped so a
// hallucinated sfx can never crash the render. The list lives in
// resource-manifest.json so the Python pipeline reads the exact same source of
// truth instead of re-deriving it from this file.
import resourceManifest from "./resource-manifest.json";

export const SFX_FILES = resourceManifest.sfx.map((entry) => entry.name);

export type TimelineEvent =
  | { type: "caption"; at: number; end: number; text: string; highlight?: string; highlightColor?: string }
  | {
      type: "keyword";
      at: number;
      end: number;
      text: string;
      color?: string;
      xPct?: number;
      yPct?: number;
      rotation?: number;
      fontSize?: number;
      anim?: "pop" | "drop" | "slide" | "whip";
    }
  | {
      type: "card";
      at: number;
      end: number;
      kicker: string;
      title: string;
      badge: string;
      bullets: string[];
      bulletTimes?: number[];
    }
  | { type: "pipHold"; at: number; end: number } // explicit A-roll-in-corner span
  | { type: "punchIn"; at: number; scale?: number; holdSeconds?: number }
  | { type: "sfx"; at: number; name: string; volume?: number }
  | { type: "shake"; at: number; durSeconds?: number; intensity?: number }
  | { type: "flash"; at: number; durSeconds?: number }
  | {
      type: "endcard";
      at: number;
      end: number;
      title: string;
      subtitle?: string;
      kicker?: string;
      accent?: string;
      freezeSrc?: string;
      artSrc?: string;
    };

// Loopable background-music beds available in public/ (placeholder synths —
// swap the files for generated tracks keeping the same names). Grouped by mood
// in resource-manifest.json, which is also what the director prompt offers.
export const BGM_FILES = resourceManifest.bgm.map((entry) => entry.name);

export interface MonaTimelineProps {
  [key: string]: unknown;
  videoSrc: string;
  // A lower-bitrate re-encode of `videoSrc`, browser-Player-only (see
  // resolve_cut.make_preview_proxy). `videoSrc` itself stays near-lossless for
  // the renderer to read frames from, which is unwatchable streamed straight
  // to a <video> tag — Chrome falls behind a 50-70 Mbps 1080x1920 stream and
  // OffthreadVideo's buffering pause takes the audio down with it. Absent on
  // older jobs or if the proxy encode failed; falls back to `videoSrc` then.
  previewVideoSrc?: string | null;
  events: TimelineEvent[];
  brandPill?: string;
  durationSeconds?: number; // real footage length — drives the progress bar
  // music bed under the voice; durationSeconds (of the audio file, probed by
  // the resolver) lets the renderer tile copies instead of using `loop`
  bgm?: { name: string; volume?: number; durationSeconds?: number };
  // absolute URL prefix for media (web UI preview); omit to use staticFile
  assetBase?: string;
  // b-roll overlays: video over the A-roll, A-roll audio kept. Per-job files,
  // not a shared library, so they arrive through props rather than through
  // resource-manifest.json. `audit` is what guarantees each `src` exists.
  broll?: BrollClip[];
  // Optional frame around the fullscreen A-roll. Inset footage sits on a
  // backdrop, which is how an unflattering room stops reading as "the room"
  // and starts reading as a deliberate look. Omit for edge-to-edge footage.
  frame?: MonaFrame;
}

export interface MonaFrame {
  inset?: number;      // px of backdrop visible on each side at fullscreen
  radius?: number;     // corner radius of the footage box
  border?: number;     // hairline around the footage box
  borderColor?: string;
  /** blur = the footage itself, blurred and darkened; dark/light = flat colour */
  background?: "blur" | "dark" | "light";
  blurPx?: number;
}

/** Constant-volume speech/pause spans. A volume *callback* on <Audio> rendered
 * silent on this Remotion version (Player and CLI) — never use `volume={(f)=>…}`. */
function duckSegments(
  durationSec: number,
  captions: { at: number; end: number }[],
  baseVolume: number,
): { start: number; end: number; volume: number }[] {
  const pauseVol = Math.min(0.55, baseVolume * 2);
  const edges: { t: number; d: number }[] = [{ t: 0, d: 0 }];
  for (const caption of captions) {
    edges.push({ t: Math.max(0, caption.at - 0.1), d: 1 });
    edges.push({ t: Math.min(durationSec, caption.end + 0.15), d: -1 });
  }
  edges.sort((a, b) => a.t - b.t || a.d - b.d);
  const segs: { start: number; end: number; volume: number }[] = [];
  let depth = 0;
  let prev = 0;
  let prevVol = pauseVol;
  for (const edge of edges) {
    if (edge.t > prev + 1e-4) segs.push({ start: prev, end: edge.t, volume: prevVol });
    depth = Math.max(0, depth + edge.d);
    prev = edge.t;
    prevVol = depth > 0 ? baseVolume : pauseVol;
  }
  if (durationSec > prev + 1e-4) segs.push({ start: prev, end: durationSec, volume: prevVol });
  return segs;
}

const BgmDucked: React.FC<{
  name: string;
  baseVolume: number;
  audioLenSeconds: number;
  captions: { at: number; end: number }[];
  resolveAsset: (name: string) => string;
}> = ({ name, baseVolume, audioLenSeconds, captions, resolveAsset }) => {
  const { fps, durationInFrames } = useVideoConfig();
  const durationSec = durationInFrames / fps;
  const audioLen = Math.max(1, audioLenSeconds);
  const src = resolveAsset(name);
  const pieces = [];
  let key = 0;
  for (const seg of duckSegments(durationSec, captions, baseVolume)) {
    let t = seg.start;
    while (t < seg.end - 1e-4) {
      const posInFile = t % audioLen;
      const chunk = Math.min(seg.end - t, audioLen - posInFile);
      pieces.push(
        <Sequence
          key={`bgm-${key++}`}
          from={Math.round(t * fps)}
          durationInFrames={Math.max(1, Math.round(chunk * fps))}
        >
          <Audio src={src} startFrom={Math.round(posInFile * fps)} volume={seg.volume} />
        </Sequence>,
      );
      t += chunk;
    }
  }
  return <>{pieces}</>;
};

// Assets normally live in the render's public dir (staticFile). The web UI
// serves the same files over HTTP from the job's media route instead, so it
// passes an absolute assetBase and we skip staticFile entirely — one component,
// two hosts, identical bytes.
const useAsset = (assetBase?: string) => (name: string) =>
  assetBase ? assetBase.replace(/\/?$/, "/") + name : staticFile(name);

const byType = <T extends TimelineEvent["type"]>(events: TimelineEvent[], t: T) =>
  events.filter((e): e is Extract<TimelineEvent, { type: T }> => e.type === t);

// Last-frame freeze + bottom CTA. The face stays; copy is typeset here so
// generated art never has to paint Vietnamese letters.
const EndCard: React.FC<{
  title: string;
  subtitle?: string;
  kicker?: string;
  accent?: string;
  freezeSrc?: string;
  artSrc?: string;
  resolveAsset: (name: string) => string;
}> = ({ title, subtitle, kicker, accent, freezeSrc, artSrc, resolveAsset }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const inP = interpolate(frame, [0, 0.35 * fps], [0, 1], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const ink = "#F6EDE8";
  const bar = accent && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(accent) ? accent : "#E10600";
  const rise = (1 - inP) * 36;
  return (
    <AbsoluteFill>
      {freezeSrc ? (
        <Img src={resolveAsset(freezeSrc)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      ) : null}
      {artSrc ? (
        <Img
          src={resolveAsset(artSrc)}
          style={{ width: "100%", height: "100%", objectFit: "cover", opacity: 0.28 * inP, mixBlendMode: "screen" }}
        />
      ) : null}
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(12,6,8,0.12) 0%, rgba(12,6,8,0.28) 42%, rgba(12,6,8,0.88) 100%)",
          opacity: inP,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 64,
          right: 64,
          bottom: 88,
          fontFamily: BVP,
          color: ink,
          transform: `translateY(${rise}px)`,
          opacity: inP,
        }}
      >
        {kicker ? (
          <div
            style={{
              fontSize: 22,
              fontWeight: 700,
              letterSpacing: "0.18em",
              textTransform: "uppercase",
              color: bar,
              marginBottom: 18,
            }}
          >
            {kicker}
          </div>
        ) : null}
        <div style={{ fontWeight: 800, fontSize: 64, lineHeight: 1.12, maxWidth: 920, textWrap: "balance" as const }}>
          {title}
        </div>
        <div
          style={{
            marginTop: 28,
            display: "inline-block",
            background: bar,
            color: "#14080A",
            fontWeight: 800,
            fontSize: 32,
            lineHeight: 1.25,
            padding: "18px 36px",
            borderRadius: 8,
            maxWidth: 920,
          }}
        >
          {subtitle || "XEM TIẾP"}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// Merge near-adjacent spans so the PiP never bounces fullscreen between them.
function mergeSpans(spans: { at: number; end: number }[]): { at: number; end: number }[] {
  const sorted = [...spans].sort((a, b) => a.at - b.at);
  const out: { at: number; end: number }[] = [];
  for (const s of sorted) {
    const last = out[out.length - 1];
    if (last && s.at - last.end <= PIP_BRIDGE_S) last.end = Math.max(last.end, s.end);
    else out.push({ at: s.at, end: s.end });
  }
  return out;
}

export const MonaTimeline: React.FC<MonaTimelineProps> = ({ videoSrc, previewVideoSrc, events, brandPill, durationSeconds, bgm, assetBase, frame: frameStyle, broll }) => {
  // The renderer must read `videoSrc` at full quality; only Studio/Player
  // benefit from the lighter proxy, and only when resolve actually made one.
  const { isRendering } = useRemotionEnvironment();
  const displaySrc = isRendering ? videoSrc : previewVideoSrc || videoSrc;
  const resolveAsset = useAsset(assetBase);
  const { fps, durationInFrames } = useVideoConfig();
  const frame = useCurrentFrame();
  const ease = Easing.inOut(Easing.cubic);

  const cards = byType(events, "card").sort((a, b) => a.at - b.at);
  const captions = byType(events, "caption").sort((a, b) => a.at - b.at);
  const keywords = byType(events, "keyword");
  const punchIns = byType(events, "punchIn");
  const sfx = byType(events, "sfx").filter((e) => (SFX_FILES as readonly string[]).includes(e.name));
  const shakes = byType(events, "shake");
  const flashes = byType(events, "flash");
  const endcards = byType(events, "endcard");
  const boardCards = shouldUseBoardCards(cards);

  // PiP spans: explicit pipHold events win; otherwise only full explainer boards.
  const holdEvents = byType(events, "pipHold");
  const pipSpans = mergeSpans(
    (holdEvents.length > 0 ? holdEvents : boardCards ? cards : []).map((c) => ({ at: c.at, end: c.end })),
  );

  let p = 0;
  for (const s of pipSpans) {
    const tF = TRANSITION_S * fps;
    const up = interpolate(frame, [s.at * fps, s.at * fps + tF], [0, 1], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    const down = interpolate(frame, [s.end * fps - tF, s.end * fps], [1, 0], { easing: ease, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    p = Math.max(p, Math.min(up, down));
  }

  // punch-in zoom (damped while in PiP so it never fights the morph)
  let zoom = 1;
  for (const pi of punchIns) {
    const inF = pi.at * fps;
    const hold = (pi.holdSeconds ?? 1.1) * fps;
    const target = pi.scale ?? 1.06;
    const s = interpolate(frame, [inF, inF + 0.2 * fps, inF + 0.2 * fps + hold, inF + 0.2 * fps + hold + 0.35 * fps], [1, target, target, 1], {
      easing: Easing.out(Easing.cubic),
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
    zoom = Math.max(zoom, s);
  }
  const punch = 1 + (zoom - 1) * (1 - p);

  // camera shake: decaying deterministic jitter (no Math.random — render-safe)
  let shakeX = 0;
  let shakeY = 0;
  for (const sh of shakes) {
    const t0 = sh.at * fps;
    const dur = (sh.durSeconds ?? 0.4) * fps;
    if (frame >= t0 && frame <= t0 + dur) {
      const k = 1 - (frame - t0) / dur;
      const amp = (sh.intensity ?? 10) * k * k;
      shakeX += Math.sin(frame * 2.7) * amp;
      shakeY += Math.cos(frame * 3.1) * amp * 0.6;
    }
  }

  // No idle camera motion. Shake and punch-in play only when the director
  // (or an explicit event) asked for them — the A-roll stays still at rest.

  // Reactive PiP size: the face starts comfortably LARGE when a card wipes in
  // (list is still mostly ghosts) and eases smaller as bullets fill the list —
  // anchored to the bottom-right corner, always above the caption zone.
  let reveal = 0; // 0..1 = how much of the active card's list is revealed
  const activeCard = cards.find((c) => frame >= c.at * fps && frame < c.end * fps);
  if (activeCard && (activeCard.bulletTimes?.length ?? 0) > 0) {
    const bts = activeCard.bulletTimes as number[];
    const done = bts.reduce((acc, bt) => acc + Math.min(1, Math.max(0, (frame / fps - bt) / 0.5)), 0);
    reveal = done / bts.length;
  }
  const pipW = interpolate(reveal, [0, 1], [460, 360]);
  const pipH = interpolate(reveal, [0, 1], [600, 470]);
  const pipLeft = 1000 - pipW; // right edge at x=1000 — clear of TikTok's icon rail
  const pipTop = 1440 - pipH; // bottom edge above the (raised) caption pill

  // A-roll box: fullscreen (p=0) -> bottom-right PiP (p=1). With a frame the
  // "fullscreen" end is inset instead of edge-to-edge.
  const fInset = frameStyle?.inset ?? 0;
  const fRadius = frameStyle ? frameStyle.radius ?? 44 : 0;
  const fBorder = frameStyle ? frameStyle.border ?? 0 : 0;
  const left = interpolate(p, [0, 1], [fInset, pipLeft]);
  const top = interpolate(p, [0, 1], [fInset, pipTop]);
  const width = interpolate(p, [0, 1], [1080 - fInset * 2, pipW]);
  const height = interpolate(p, [0, 1], [1920 - fInset * 2, pipH]);
  const radius = interpolate(p, [0, 1], [fRadius, 36]);
  const borderW = interpolate(p, [0, 1], [fBorder, 4]);
  // the backdrop belongs to the fullscreen state only — during a card the
  // A-roll is a PiP over the card and must not drag its own background along
  const backdropOpacity = 1 - p;
  const brandOp = interpolate(frame, [10, 25, 135, 155], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const inEndcard = endcards.some((e) => frame >= e.at * fps);

  return (
    <AbsoluteFill style={{ backgroundColor: "#fff" }}>
      {/* backdrop the inset footage sits on */}
      {frameStyle && backdropOpacity > 0.01 && (
        <AbsoluteFill style={{ opacity: backdropOpacity }}>
          {frameStyle.background === "blur" ? (
            <OffthreadVideo
              src={resolveAsset(displaySrc)}
              muted
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                // scale past the blur's soft edges, darken so the framed
                // footage stays the brightest thing on screen
                transform: `scale(1.25)`,
                filter: `blur(${frameStyle.blurPx ?? 34}px) brightness(0.55) saturate(1.1)`,
              }}
            />
          ) : (
            <AbsoluteFill
              style={{
                background:
                  frameStyle.background === "light"
                    ? "linear-gradient(160deg,#f6f7fb 0%,#e6e9f2 100%)"
                    : "linear-gradient(160deg,#12172a 0%,#0a0d18 60%,#161b30 100%)",
              }}
            />
          )}
        </AbsoluteFill>
      )}

      {/* cards behind the A-roll, push-sliding between adjacent ones */}
      {cards.map((c, i) => {
        const prev = cards[i - 1];
        const next = cards[i + 1];
        const card: MonaCard = {
          inSeconds: c.at,
          outSeconds: c.end,
          kicker: c.kicker,
          title: c.title,
          badge: c.badge,
          bullets: c.bullets,
          bulletTimes: c.bulletTimes,
        };
        return (
          <Sequence key={`card-${i}`} from={Math.round(c.at * fps)} durationInFrames={Math.max(1, Math.round((c.end - c.at) * fps))}>
            {boardCards ? (
              <ExplainerCard
                card={card}
                slideIn={!!prev && c.at - prev.end <= PIP_BRIDGE_S}
                slideOut={!!next && next.at - c.end <= PIP_BRIDGE_S}
              />
            ) : (
              <OverlayCard card={card} />
            )}
          </Sequence>
        );
      })}

      {/* A-roll with punch-in zoom + shake */}
      <div
        style={{
          position: "absolute",
          left: left + shakeX * (1 - p),
          top: top + shakeY * (1 - p),
          width,
          height,
          borderRadius: radius,
          overflow: "hidden",
          border: borderW > 0.5 ? `${borderW}px solid ${frameStyle?.borderColor ?? "#fff"}` : "none",
          boxShadow: p > 0.05 ? "0 18px 44px rgba(0,0,0,0.28)" : "none",
        }}
      >
        <OffthreadVideo src={resolveAsset(displaySrc)} style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${punch})` }} />
        {/* b-roll INSIDE the A-roll box, so the frame preset's inset, radius and
            border apply to it exactly as they do to the footage. Placing it
            outside would make an overlay bleed past the frame the rest of the
            video sits inside. */}
        {broll && broll.length > 0 && (
          <BrollLayer clips={broll} resolveAsset={resolveAsset} />
        )}
      </div>

      {/* brand pill (hook) */}
      {brandPill && (
        <div style={{ position: "absolute", top: 90, width: "100%", display: "flex", justifyContent: "center", opacity: brandOp }}>
          <div style={{ background: "linear-gradient(90deg,#2563EB 0%,#7C3AED 52%,#EC4899 100%)", color: "#fff", fontWeight: 800, fontSize: 34, letterSpacing: 0.5, padding: "14px 34px", borderRadius: 999, boxShadow: "0 10px 30px rgba(124,58,237,0.4)" }}>
            {brandPill}
          </div>
        </div>
      )}

      {/* floating keywords (fade while a card owns the screen) */}
      <AbsoluteFill style={{ opacity: 1 - p }}>
        {keywords.map((k, i) => {
          const kw: MonaKeyword = {
            text: k.text,
            color: k.color ?? "#FFFFFF",
            xPct: k.xPct ?? 20,
            yPct: k.yPct ?? 15,
            rotation: k.rotation ?? 0,
            inSeconds: k.at,
            outSeconds: k.end,
            fontSize: k.fontSize,
            anim: k.anim,
          };
          return (
            <Sequence key={`kw-${i}`} from={Math.round(k.at * fps)} durationInFrames={Math.max(1, Math.round((k.end - k.at) * fps))}>
              <KeywordView kw={kw} />
            </Sequence>
          );
        })}
      </AbsoluteFill>

      {/* captions — each clears shortly after its phrase ends */}
      <AbsoluteFill>
        {captions.map((c, i) => {
          const nextStart = captions[i + 1] ? captions[i + 1].at * 1000 : c.end * 1000 + 300;
          const visibleUntil = Math.min(nextStart, c.end * 1000 + 450);
          const line: MonaCaptionLine = { text: c.text, startMs: c.at * 1000, endMs: c.end * 1000, highlight: c.highlight, highlightColor: c.highlightColor };
          const dur = Math.max(6, Math.round(((visibleUntil - c.at * 1000) / 1000) * fps));
          const overlayHit = !boardCards && cards.some((card) => !(c.end <= card.at || c.at >= card.end));
          return (
            <Sequence key={`cap-${i}`} from={Math.round(c.at * fps)} durationInFrames={dur}>
              <CaptionLineView line={line} lift={overlayHit} />
            </Sequence>
          );
        })}
      </AbsoluteFill>

      {/* white flash accents */}
      {flashes.map((f, i) => {
        const dur = (f.durSeconds ?? 0.15) * fps;
        const op = interpolate(frame, [f.at * fps, f.at * fps + dur], [0.85, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        return frame >= f.at * fps && frame <= f.at * fps + dur ? (
          <AbsoluteFill key={`flash-${i}`} style={{ backgroundColor: "#fff", opacity: op }} />
        ) : null;
      })}

      {/* rainbow progress bar — fills the full width over the real runtime,
          so the "status bar" visibly moves instead of sitting frozen */}
      {!inEndcard && (
      <div
        style={{
          position: "absolute",
          left: 0,
          bottom: 0,
          height: 12,
          width: interpolate(
            frame,
            [0, (durationSeconds ?? durationInFrames / fps) * fps],
            [0, 1080],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          ),
          background: "linear-gradient(90deg,#F59E0B,#EC4899,#7C3AED,#2563EB,#06B6D4)",
          borderTopRightRadius: 8,
        }}
      />
      )}

      {/* endcard CTA — last-frame freeze + typeset copy, not a purple wipe */}
      {endcards.map((e, i) => (
        <Sequence key={`endcard-${i}`} from={Math.round(e.at * fps)} durationInFrames={Math.max(1, Math.round((e.end - e.at) * fps))}>
          <EndCard
            title={e.title}
            subtitle={e.subtitle}
            kicker={e.kicker}
            accent={e.accent}
            freezeSrc={e.freezeSrc}
            artSrc={e.artSrc}
            resolveAsset={resolveAsset}
          />
        </Sequence>
      ))}

      {/* looped music bed — auto-ducks under speech. Ducking is done with
          per-span <Sequence> segments at constant volume: the volume-callback
          form rendered SILENT with loop on this Remotion version, so each
          speaking/pause span gets its own constant-volume slice instead. */}
      {bgm && (bgm.name.startsWith("bgm_") || (BGM_FILES as readonly string[]).includes(bgm.name)) && (
        <BgmDucked
          name={bgm.name}
          baseVolume={Math.min(0.22, Math.max(0, bgm.volume ?? 0.16))}
          audioLenSeconds={bgm.durationSeconds ?? 30}
          captions={captions}
          resolveAsset={resolveAsset}
        />
      )}

      {/* sound design — every cue placed explicitly by the director.
          Duration must cover the longest real SFX (ding ~1.3s, whoosh ~0.8s);
          a hard 1.2s cap was clipping ding. Keep Sequence tight enough that
          concurrent SFX don't leak forever, but never shorter than the file. */}
      {sfx.map((s, i) => (
        <Sequence key={`sfx-${i}`} from={Math.max(0, Math.round(s.at * fps))} durationInFrames={Math.round(1.6 * fps)}>
          <Audio src={resolveAsset(s.name)} volume={Math.min(0.22, Math.max(0, s.volume ?? 0.16))} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
