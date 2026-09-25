import React, { useState } from "react";
import { api, GradePreview as GradeReport } from "../api/client";
import { GRADE_VARIANT_LABEL, GradedPreviewGrid } from "./graded-preview-grid";

/**
 * Colour loop that does not cost a resolve.
 *
 * The grade is baked into src.mp4 by ffmpeg, so the <Player> shows whatever the
 * last resolve produced — changing a number there means waiting ~4 minutes to
 * see it. These frames come straight off the raw source in about a second each.
 *
 * The numbers matter as much as the pictures: "trông vàng quá" is an argument,
 * `warm_bias` +7.5 against raw is a measurement. Sharpness is the exception —
 * a still skips the two x264 passes that decide how much sharpening survives,
 * so it cannot be judged here.
 */
export const GradePreviewPanel: React.FC<{
  jobId: string;
  currentOverrides: Record<string, number>;
  busy: boolean;
  onApplied: () => void;
}> = ({ jobId, currentOverrides, busy, onApplied }) => {
  const [at, setAt] = useState("");
  const [gradeText, setGradeText] = useState("");
  const [report, setReport] = useState<GradeReport>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const parseTrial = (): Record<string, number> | null => {
    if (!gradeText.trim()) return null;
    const parsed = JSON.parse(gradeText);
    if (typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Phải là object, ví dụ {\"warmth\": 4}");
    }
    return parsed as Record<string, number>;
  };

  const run = async () => {
    setLoading(true);
    setError("");
    try {
      const trial = parseTrial();
      setReport(await api.previewGrade(jobId, {
        at: at.trim() ? Number(at) : null,
        ...(trial ? { grade: trial } : {}),
      }));
    } catch (e) {
      setError(e instanceof SyntaxError ? `JSON không hợp lệ: ${e.message}` : String(e));
    } finally {
      setLoading(false);
    }
  };

  // Only the delta is stored: overrides sit ON TOP of whatever grade the
  // director writes next, so persisting the merged result would freeze the
  // director out of every future re-direct.
  const apply = async () => {
    setLoading(true);
    setError("");
    try {
      const trial = parseTrial();
      if (!trial) return;
      await api.runStages(jobId, {
        stages: ["resolve"],
        use_cache: false,
        options: { grade_overrides: { ...currentOverrides, ...trial } },
      });
      onApplied();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const rawWarm = report?.variants.find((v) => v.name === "raw")?.face.warm_bias ?? 0;

  return (
    <div>
      <div className="row">
        <label className="field">
          <span className="muted small">Giây trong footage gốc</span>
          <input value={at} placeholder="giữa video" onChange={(e) => setAt(e.target.value)} />
        </label>
        <label className="field" style={{ flex: 2 }}>
          <span className="muted small">Grade muốn thử (JSON, chỉ phần cần đổi)</span>
          <input
            value={gradeText}
            placeholder='{"warmth": 4, "vibrance": 0.15}'
            onChange={(e) => setGradeText(e.target.value)}
          />
        </label>
      </div>

      <div className="row">
        <button disabled={loading} onClick={run}>
          {loading ? "Đang dựng…" : "Xem thử màu (~1s/bản)"}
        </button>
        {gradeText.trim() && (
          <button className="primary" disabled={loading || busy} onClick={apply}>
            Áp dụng & cắt lại
          </button>
        )}
      </div>

      {error && <p className="error-text small">{error}</p>}

      {report && (
        <>
          <p className="muted small">Bấm vào ảnh để xem cỡ lớn và so từng bản một.</p>
          <GradedPreviewGrid
            variants={report.variants}
            labelFor={(name) => GRADE_VARIANT_LABEL[name] ?? name}
            captionFor={(variant) => {
              const warm = variant.face.warm_bias ?? 0;
              const delta = variant.name === "raw" ? 0 : warm - rawWarm;
              return `sáng ${variant.face.luma?.toFixed(1) ?? "—"} · ám ấm ${warm.toFixed(2)}`
                + (delta ? ` (${delta > 0 ? "+" : ""}${delta.toFixed(2)} so gốc)` : "");
            }}
          />
          <p className="muted small">
            Số đo trên vùng mặt, ở giây {report.at_seconds}. Ám ấm càng cao so bản gốc thì càng ngả
            vàng. Độ nét không đánh giá được trên ảnh tĩnh — nó do encode quyết định.
          </p>
          <p className="muted small">
            Khớp với render: {report.context.width}×{report.context.height} (khung “
            {report.context.frame_preset}”)
            {report.context.measured_sharpen
              ? ` · sharpen ${report.context.measured_sharpen.sharpen}/clarity ${report.context.measured_sharpen.clarity}`
              : ""}{" "}
            ·{" "}
            {report.context.sharpen_source === "measured"
              ? "độ nét lấy từ số đo trên chính video này"
              : report.context.sharpen_source === "human"
                ? "độ nét do bạn đặt trong grade_overrides"
                : "chỉ làm nét nếu thông số chỉnh ảnh có yêu cầu"}
            {report.cached ? " · dùng lại ảnh đã dựng" : ""}
          </p>
        </>
      )}
    </div>
  );
};
