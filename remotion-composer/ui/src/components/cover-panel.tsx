import React, { useState } from "react";
import { api, JobDetail } from "../api/client";

export const CoverPanel: React.FC<{
  job: JobDetail;
  busy: boolean;
  onQueued: () => void;
  embedded?: boolean;
}> = ({ job, busy, onQueued, embedded }) => {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const report = job.visuals_report;
  const thumb = job.has_thumbnail ? `${job.media_base}thumbnail.jpg` : null;
  const coverAi = report?.files?.cover_ai ? `${job.media_base}${report.files.cover_ai}` : null;

  const imgW = embedded ? 220 : 160;

  const generate = async () => {
    setWorking(true);
    setError("");
    try {
      await api.generateVisuals(job.job_id);
      onQueued();
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
    }
  };

  const body = (
    <>
      {!embedded && <h2>Ảnh đại diện & outro</h2>}
      <div className="row" style={{ gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        {thumb && (
          <a href={thumb} download="thumbnail.jpg">
            <img
              src={thumb}
              alt="Ảnh đại diện"
              style={{ width: imgW, borderRadius: 8, display: "block" }}
            />
          </a>
        )}
        {coverAi && (
          <a href={coverAi} download="cover-ai.png">
            <img
              src={coverAi}
              alt="Bìa AI"
              style={{ width: imgW, borderRadius: 8, display: "block" }}
            />
          </a>
        )}
      </div>
      {report?.kicker && (
        <p className="small" style={{ marginTop: 8 }}>
          Outro: <b>{report.kicker}</b>
          {report.accent ? ` · ${report.accent}` : ""}
        </p>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <button
          className="primary"
          disabled={busy || working || !job.props}
          onClick={() => void generate()}
        >
          {working ? "Đang xếp…" : thumb ? "Tạo lại ảnh + outro" : "Tạo ảnh đại diện + outro"}
        </button>
      </div>
      {error && <p className="error-text small" style={{ marginTop: 8 }}>{error}</p>}
    </>
  );

  return embedded ? body : <div className="card">{body}</div>;
};
