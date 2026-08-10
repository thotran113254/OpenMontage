import React, { useState } from "react";
import { api, AudioPreview as AudioReport } from "../api/client";

/**
 * Cleanup presets, judged by ear.
 *
 * Same reason as the grade panel: the preset is baked into src.mp4, so the
 * <Player> only ever plays the one already chosen. These are raw-source samples,
 * a second each.
 *
 * There is deliberately no score here. A blind test on this footage found the
 * model could not hear technical quality at all — it scored 1.8-3.2 points of
 * difference between two byte-identical files. Listening is the measurement.
 */
export const AudioPreviewPanel: React.FC<{
  jobId: string;
  busy: boolean;
  onApplied: () => void;
}> = ({ jobId, busy, onApplied }) => {
  const [at, setAt] = useState("20");
  const [report, setReport] = useState<AudioReport>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setLoading(true);
    setError("");
    try {
      setReport(await api.previewAudio(jobId, { at: at.trim() ? Number(at) : undefined }));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const apply = async (preset: string) => {
    setLoading(true);
    setError("");
    try {
      await api.runStages(jobId, {
        stages: ["resolve"],
        use_cache: false,
        options: { audio_preset: preset },
      });
      onApplied();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="row">
        <label className="field">
          <span className="muted small">Giây trong footage gốc</span>
          <input value={at} onChange={(e) => setAt(e.target.value)} />
        </label>
        <button disabled={loading} onClick={run}>
          {loading ? "Đang xử lý…" : "Tạo mẫu để nghe"}
        </button>
      </div>

      {error && <p className="error-text small">{error}</p>}

      {report && (
        <>
          {report.samples.map((sample) => (
            <div key={sample.preset} className="sample-row">
              <div className="sample-head">
                <strong>{sample.preset}</strong>
                {sample.is_current && <span className="badge cached">đang dùng</span>}
                {!sample.is_current && (
                  <button
                    className="ghost small"
                    disabled={loading || busy}
                    onClick={() => apply(sample.preset)}
                  >
                    Chọn & cắt lại
                  </button>
                )}
              </div>
              <audio src={sample.url} controls style={{ width: "100%" }} />
            </div>
          ))}
          <p className="muted small">
            Nghe ở: khoảng lặng giữa câu (nhiễu nền), đuôi câu (vang), và đường dấu thanh — tiếng
            Việt mất dấu là mất nghĩa. Bản xử lý mạnh nhất luôn “sạch” nhất và thường kém tự nhiên
            nhất.
          </p>
        </>
      )}
    </div>
  );
};
