import React from "react";
import { AuditReport, VerifyReport } from "../api/client";

/**
 * The trust panel. Everything the machine decided on your behalf is shown
 * here with its reason — especially the cuts it refused to make, and the
 * checks it explicitly could NOT perform.
 */
export const AuditPanel: React.FC<{
  audit: AuditReport | null;
  verify: VerifyReport | null;
  mediaBase: string;
}> = ({ audit, verify, mediaBase }) => (
  <>
    {audit && (
      <div className="card">
        <h2>Kiểm duyệt bản dựng</h2>
        <div className="metrics" style={{ marginBottom: 14 }}>
          <div className="metric">
            <b>{(audit.quality.caption_coverage * 100).toFixed(1)}%</b>
            <span>lời nói có caption</span>
          </div>
          <div className="metric">
            <b>{audit.quality.card_count}</b><span>card</span>
          </div>
          <div className="metric">
            <b>{audit.quality.event_count}</b><span>event</span>
          </div>
          <div className="metric">
            <b className={audit.quality.captions_over_9_words ? "warn-text" : "ok-text"}>
              {audit.quality.captions_over_9_words}
            </b>
            <span>caption quá 9 từ</span>
          </div>
          <div className="metric">
            <b className={audit.quality.keyword_in_card.length ? "warn-text" : "ok-text"}>
              {audit.quality.keyword_in_card.length}
            </b>
            <span>keyword bị card che</span>
          </div>
          <div className="metric">
            <b>{audit.cuts_accepted}/{audit.cuts_proposed}</b>
            <span>cut được chấp nhận</span>
          </div>
        </div>

        {audit.cut_decisions.length > 0 && (
          <>
            <h3>Từng đoạn cắt được đề xuất</h3>
            {audit.cut_decisions.map((cut, index) => (
              <div key={index} className={`cut ${cut.decision}`}>
                <div>
                  <span className={`badge ${cut.decision === "remove" ? "failed" : "completed"}`}>
                    {cut.decision === "remove" ? "CẮT" : "GIỮ"}
                  </span>{" "}
                  <span className="muted small">
                    từ {cut.w[0]}–{cut.w[1]} · {cut.source === "mechanical" ? "luật cứng" : "AI kiểm"}
                  </span>
                </div>
                <div className="ctx">
                  …{cut.before} <b>{cut.cut}</b> {cut.after}…
                </div>
                <div className="muted small" style={{ marginTop: 4 }}>{cut.reason}</div>
              </div>
            ))}
          </>
        )}

        {audit.removed_resources.length > 0 && (
          <>
            <h3>Đã loại bỏ vì không dùng được</h3>
            {audit.removed_resources.map((item, index) => (
              <p key={index} className="warn-text small">• {item}</p>
            ))}
          </>
        )}

        {audit.quality.uncovered_words.length > 0 && (
          <p className="warn-text small" style={{ marginTop: 10 }}>
            Còn {audit.quality.uncovered_words.length} từ chưa có caption (chỉ số:{" "}
            {audit.quality.uncovered_words.slice(0, 15).join(", ")}…)
          </p>
        )}
      </div>
    )}

    {verify && (
      <div className="card">
        <h2>
          Đo kiểm bản render{" "}
          <span className={`badge ${verify.passed ? "completed" : "completed_with_warnings"}`}>
            {verify.passed ? "không thấy lỗi máy đo được" : "có cảnh báo"}
          </span>
        </h2>
        <div className="metrics" style={{ marginBottom: 12 }}>
          <div className="metric">
            <b>{verify.duration_actual}s</b><span>thực tế (mong đợi {verify.duration_expected}s)</span>
          </div>
          <div className="metric">
            <b>{verify.av_drift}s</b><span>lệch hình/tiếng</span>
          </div>
          <div className="metric">
            <b>{verify.integrated_lufs ?? "—"}</b><span>LUFS tổng</span>
          </div>
        </div>

        {/* Issues carry a code now, so autopilot can look up a remedy. Old
            reports hold plain strings — both shapes are rendered. */}
        {verify.issues.map((issue, index) => (
          <p key={index} className="warn-text small">
            • {typeof issue === "string" ? issue : issue.message}
            {typeof issue === "object" && issue.code && (
              <span className="muted small"> [{issue.code}]</span>
            )}
          </p>
        ))}

        <h3>Máy KHÔNG kiểm được — cần bạn nhìn</h3>
        {verify.requires_human_review.map((item, index) => (
          <p key={index} className="small muted">• {item}</p>
        ))}
        <div className="frames" style={{ marginTop: 10 }}>
          {verify.frames.filter((f) => f.frame).map((frame, index) => (
            <figure key={index}>
              <img src={`${mediaBase}frames/${frame.frame!.split("/").pop()}`} alt={`frame ${frame.at}s`} />
              <figcaption>{frame.at}s · luma {frame.mean_luma?.toFixed(0)}</figcaption>
            </figure>
          ))}
        </div>
      </div>
    )}
  </>
);
