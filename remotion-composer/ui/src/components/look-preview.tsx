import React, { useState } from "react";
import { AudioPreviewPanel } from "./audio-preview";
import { ClipPreviewPanel } from "./clip-preview";
import { GradePreviewPanel } from "./grade-preview";
import { SkinPreviewPanel } from "./skin-preview";

type Tab = "grade" | "skin" | "audio" | "clip";

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "grade", label: "Màu", hint: "~1s mỗi bản" },
  { id: "skin", label: "Da", hint: "~1s mỗi lần" },
  { id: "audio", label: "Tiếng", hint: "~1s mỗi preset" },
  { id: "clip", label: "Clip duyệt", hint: "vài chục giây" },
];

/**
 * Everything the <Player> above cannot answer.
 *
 * The Player runs the real composition, so content is covered. Colour and audio
 * cleanup are not: ffmpeg bakes both into src.mp4 during resolve, so by the time
 * the Player reads the file the decision is already made and re-deciding costs a
 * full resolve. These panels run on the raw source instead — seconds, not minutes.
 */
export const LookPreview: React.FC<{
  jobId: string;
  options: Record<string, unknown>;
  /** Footage duration etc — absent until the job's probe stage has run. */
  probe?: Record<string, unknown>;
  hasProps: boolean;
  busy: boolean;
  onApplied: () => void;
}> = ({ jobId, options, probe, hasProps, busy, onApplied }) => {
  const [tab, setTab] = useState<Tab>("grade");
  const overrides = (options.grade_overrides as Record<string, number>) ?? {};

  return (
    <div className="card">
      <h2>Màu &amp; tiếng</h2>
      <div className="tabs">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            className={`tab ${tab === entry.id ? "active" : ""}`}
            onClick={() => setTab(entry.id)}
          >
            {entry.label} <span className="muted small">{entry.hint}</span>
          </button>
        ))}
      </div>

      {tab === "grade" && (
        <GradePreviewPanel
          jobId={jobId}
          currentOverrides={overrides}
          busy={busy}
          onApplied={onApplied}
        />
      )}
      {tab === "skin" && (
        <SkinPreviewPanel
          jobId={jobId}
          currentOverrides={overrides}
          probe={probe}
          busy={busy}
          onApplied={onApplied}
        />
      )}
      {tab === "audio" && <AudioPreviewPanel jobId={jobId} busy={busy} onApplied={onApplied} />}
      {tab === "clip" && <ClipPreviewPanel jobId={jobId} hasProps={hasProps} />}
    </div>
  );
};
