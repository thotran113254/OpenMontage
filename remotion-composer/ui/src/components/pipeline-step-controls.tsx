import React, { useState } from "react";
import { api } from "../api/client";
import {
  FULL_STAGES,
  PREPARE_STAGES,
  RENDER_STAGES,
} from "../lib/pipeline-plan";

/**
 * Explicit stage-group runners for step-by-step control on job detail.
 */
export const PipelineStepControls: React.FC<{
  jobId: string;
  busy: boolean;
  hasProps: boolean;
  hasFinal: boolean;
  onQueued: () => void;
}> = ({ jobId, busy, hasProps, hasFinal, onQueued }) => {
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  const run = async (stages: string[], label: string) => {
    setWorking(true);
    setError("");
    try {
      await api.runStages(jobId, { stages, use_cache: true });
      onQueued();
    } catch (e) {
      setError(`${label}: ${String(e).slice(0, 240)}`);
    } finally {
      setWorking(false);
    }
  };

  const disabled = busy || working;

  return (
    <div className="pipeline-steps">
      <span className="render-label">Chạy theo bước</span>
      <div className="row" style={{ gap: 8 }}>
        <button
          disabled={disabled}
          title="probe → resolve (có LLM + encode). Dừng trước render."
          onClick={() => void run([...PREPARE_STAGES], "Chuẩn bị")}
        >
          ① LLM + encode (dừng trước render)
        </button>
        <button
          disabled={disabled || !hasProps}
          title={!hasProps ? "Cần xong bước ① (có props)" : "render + verify"}
          onClick={() => void run([...RENDER_STAGES], "Render")}
        >
          ② Render + verify
        </button>
        <button
          disabled={disabled}
          title="Full pipeline local"
          onClick={() => void run([...FULL_STAGES], "Full")}
        >
          Full local
        </button>
        <button
          disabled={disabled}
          title="Chỉ Direct + Audit (tốn token, không encode)"
          onClick={() =>
            void run(["direct", "audit"], "AI khung")
          }
        >
          Chỉ AI khung
        </button>
      </div>
      {hasProps && !hasFinal && (
        <p className="ok-text small" style={{ marginTop: 6 }}>
          Đã có preview (props) — có thể Render local / Xếp lịch cloud bên dưới, hoặc bấm ②.
        </p>
      )}
      {!hasProps && (
        <p className="muted small" style={{ marginTop: 6 }}>
          Chưa có preview: chạy ① trước. Render bị khoá cho đến khi resolve xong.
        </p>
      )}
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
};
