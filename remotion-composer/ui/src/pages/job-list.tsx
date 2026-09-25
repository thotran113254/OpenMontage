import React, { useEffect, useState } from "react";
import { api, JobSummary } from "../api/client";
import { statusLabel } from "../lib/status";

const formatTime = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString("vi-VN", { hour12: false });

export const JobListPage: React.FC<{ onOpen: (id: string) => void }> = ({ onOpen }) => {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api.listJobs().then(setJobs).catch((e) => setError(String(e)));
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, []);

  if (error) return <div className="card error-text">{error}</div>;

  return (
    <div className="card">
      <h2>Kết quả dựng ({jobs.length})</h2>
      {jobs.length === 0 && (
        <div className="empty-state">
          <h2>Chưa có bản dựng nào</h2>
          <p className="muted">
            Vào <b>Nhà sáng tạo</b> → mở video → <b>Chạy dựng short</b>.
          </p>
          <p style={{ marginTop: 16 }}>
            <button
              className="primary"
              type="button"
              onClick={() => {
                window.location.hash = "/projects";
              }}
            >
              Mở danh sách nhà sáng tạo
            </button>
          </p>
        </div>
      )}
      {jobs.length > 0 && (
        <>
          <table className="responsive-hide">
            <thead>
              <tr>
                <th>Bản dựng</th>
                <th>Trạng thái</th>
                <th>Tạo lúc</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.job_id} className="clickable" onClick={() => onOpen(job.job_id)}>
                  <td>
                    <b>{job.title || job.job_id}</b>
                    <div className="muted small">v{job.current_version}</div>
                  </td>
                  <td><span className={`badge ${job.status}`}>{statusLabel(job.status)}</span></td>
                  <td className="muted small">{formatTime(job.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="job-cards">
            {jobs.map((job) => (
              <div
                key={job.job_id}
                className="card project-row"
                role="button"
                tabIndex={0}
                onClick={() => onOpen(job.job_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpen(job.job_id);
                  }
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b>{job.title || job.job_id}</b>
                  <div className="muted small">{formatTime(job.created_at)}</div>
                </div>
                <span className={`badge ${job.status}`}>{statusLabel(job.status)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
};
