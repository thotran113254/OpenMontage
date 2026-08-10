import React, { useState } from "react";
import { api, ClipPreview as ClipReport } from "../api/client";

/**
 * The approval step before spending ~6 minutes on a full render.
 *
 * Unlike the grade and audio panels, this one is not filling a gap the <Player>
 * leaves in *content* — the Player already shows every card, caption and cut.
 * What it cannot show is what the encoder does: the deliverable's CRF decides
 * how much of the sharpening actually reaches the viewer, and judder only ever
 * appears in an encoded file. So this uses the real render path at half size.
 */
export const ClipPreviewPanel: React.FC<{ jobId: string; hasProps: boolean }> = ({
  jobId,
  hasProps,
}) => {
  const [start, setStart] = useState("0");
  const [duration, setDuration] = useState("5");
  const [clip, setClip] = useState<ClipReport>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setLoading(true);
    setError("");
    try {
      setClip(await api.previewClip(jobId, {
        start: Number(start) || 0,
        duration: Number(duration) || 5,
        scale: 0.5,
      }));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  if (!hasProps) {
    return <p className="muted small">Chưa có bản dựng — chạy “Cắt lại” trước.</p>;
  }

  return (
    <div>
      <div className="row">
        <label className="field">
          <span className="muted small">Từ giây</span>
          <input value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          <span className="muted small">Dài (giây)</span>
          <input value={duration} onChange={(e) => setDuration(e.target.value)} />
        </label>
        <button disabled={loading} onClick={run}>
          {loading ? "Đang render…" : "Render clip duyệt"}
        </button>
      </div>

      <p className="muted small">
        Nửa kích thước, cùng encoder với bản cuối. Mất vài chục giây — đây là chỗ phát hiện giật và
        mềm nét trước khi tốn 6 phút render full.
      </p>

      {error && <p className="error-text small">{error}</p>}

      {clip && (
        <>
          <video src={clip.url} controls style={{ width: "100%", borderRadius: 12 }} />
          <p className="muted small">
            v{clip.version} · từ {clip.start_seconds}s · dài {clip.duration_seconds}s · scale{" "}
            {clip.scale} · crf {clip.crf}
          </p>
        </>
      )}
    </div>
  );
};
