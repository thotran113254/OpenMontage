import { Player, PlayerRef } from "@remotion/player";
import React, { useEffect, useRef } from "react";
import { MonaTimeline } from "../../../src/mona/MonaTimeline";
import { TimelineProps } from "../api/client";

/**
 * Preview runs the SAME component the renderer runs, so what you approve here
 * is what the MP4 will contain. Media comes from the job's media route via
 * assetBase instead of a public dir — identical bytes, different transport.
 */
export const TimelinePreview: React.FC<{
  props: TimelineProps;
  mediaBase: string;
  seekTo?: number;
}> = ({ props, mediaBase, seekTo }) => {
  const player = useRef<PlayerRef>(null);
  const fps = 30;
  const durationInFrames = Math.max(1, Math.ceil((props.durationSeconds || 1) * fps));

  useEffect(() => {
    if (seekTo !== undefined && player.current) {
      player.current.seekTo(Math.round(seekTo * fps));
    }
  }, [seekTo]);

  return (
    <div className="player-wrap">
      <div className="player-shell">
        <Player
          ref={player}
          component={MonaTimeline as never}
          inputProps={{ ...props, assetBase: mediaBase }}
          durationInFrames={durationInFrames}
          fps={fps}
          compositionWidth={1080}
          compositionHeight={1920}
          style={{ width: "100%", height: "100%" }}
          controls
          acknowledgeRemotionLicense
          // Player's default pool is 5 real <audio>/<video> elements shared
          // across EVERY simultaneously-mounted media tag in the composition —
          // this timeline alone can have dozens of sfx events plus several
          // tiled BgmDucked <Audio> copies (see MonaTimeline.tsx) competing for
          // those 5 slots. When the pool is exhausted, whichever tag loses the
          // race plays silently with no console warning — which is exactly
          // "nhạc nền có, giọng nói không": bgm/sfx happened to grab a slot,
          // the A-roll's own audio didn't. Raised well above any plausible
          // simultaneous count (measured: 27 sfx events on one real job).
          numberOfSharedAudioTags={40}
        />
      </div>
      <div className="muted small">
        {props.durationSeconds}s · {props.events.length} event
        {props.bgm ? ` · nhạc ${props.bgm.name}` : " · không nhạc nền"}
      </div>
    </div>
  );
};
