import React, { useEffect, useState } from "react";
import { ProjectSummary, api } from "../api/client";

export const ProjectListPage: React.FC<{
  onOpen: (id: string) => void;
  onNew: () => void;
}> = ({ onOpen, onNew }) => {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [error, setError] = useState("");

  const load = () =>
    api.listProjects().then(setProjects).catch((exception) => setError(String(exception)));

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="stack">
      <div className="card">
        <div className="row" style={{ alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>Project</h2>
          <span className="muted small">
            Nguồn upload một lần, dựng bao nhiêu bản cũng được.
          </span>
          <span style={{ flex: 1 }} />
          <button className="primary" onClick={onNew}>+ Project mới</button>
        </div>
      </div>

      {error && <p className="error-text small">{error}</p>}
      {projects.length === 0 && !error && (
        <p className="muted small">Chưa có project nào.</p>
      )}

      {projects.map((project) => (
        <div key={project.project_id} className="card" style={{ display: "flex", gap: 12 }}>
          {project.thumb ? (
            <img
              src={`/api/projects/${project.project_id}/sources/s0/thumb`}
              alt=""
              style={{ width: 64, borderRadius: 6, alignSelf: "flex-start" }}
            />
          ) : null}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="row" style={{ alignItems: "center", gap: 8 }}>
              <b>{project.title}</b>
              <span className="muted small">{project.project_id}</span>
            </div>
            <div className="muted small" style={{ marginTop: 4 }}>
              {project.source_count} nguồn ({project.aroll_count} có lời) ·{" "}
              {project.total_seconds.toFixed(0)}s tổng · {project.build_count} bản dựng
            </div>
          </div>
          <button className="ghost" onClick={() => onOpen(project.project_id)}>Mở</button>
        </div>
      ))}
    </div>
  );
};
