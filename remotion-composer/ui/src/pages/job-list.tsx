import React, { useEffect, useState } from "react";
import { api, JobSummary } from "../api/client";

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
      <h2>Job ({jobs.length})</h2>
      {jobs.length === 0 && <p className="muted">Chưa có job nào. Bấm “+ Job mới” để bắt đầu.</p>}
      {jobs.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Trạng thái</th>
              <th>Tiến độ stage</th>
              <th>Phiên bản</th>
              <th>Chi phí</th>
              <th>Tạo lúc</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const stages = Object.entries(job.stages || {});
              const done = stages.filter(([, s]) => s.status === "completed").length;
              return (
                <tr key={job.job_id} className="clickable" onClick={() => onOpen(job.job_id)}>
                  <td>
                    <b>{job.title || job.job_id}</b>
                    <div className="muted small">{job.job_id}</div>
                  </td>
                  <td><span className={`badge ${job.status}`}>{job.status}</span></td>
                  <td>{done}/{stages.length}</td>
                  <td>v{job.current_version}</td>
                  <td>{job.cost_usd ? `$${job.cost_usd}` : "—"}</td>
                  <td className="muted small">{formatTime(job.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
};
