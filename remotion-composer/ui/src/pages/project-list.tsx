import React, { useEffect, useMemo, useState } from "react";
import { ProjectSummary, api } from "../api/client";

const groupKey = (folder: string | undefined) => folder?.trim() || "";

export const ProjectListPage: React.FC<{
  onOpen: (id: string) => void;
  onNew: () => void;
}> = ({ onOpen, onNew }) => {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.listProjects().then(setProjects).catch((exception) => setError(String(exception)));
  }, []);

  const grouped = useMemo(() => {
    const map = new Map<string, ProjectSummary[]>();
    for (const item of projects) {
      const key = groupKey(item.folder);
      const list = map.get(key) || [];
      list.push(item);
      map.set(key, list);
    }
    const keys = Array.from(map.keys()).sort((a, b) => {
      if (!a) return 1;
      if (!b) return -1;
      return a.localeCompare(b, "vi");
    });
    return keys.map((key) => ({ key, items: map.get(key) || [] }));
  }, [projects]);

  const hasGroups = grouped.some((g) => g.key);

  return (
    <div className="stack">
      <div className="card">
        <h2 style={{ margin: 0 }}>Nhà sáng tạo</h2>
        <p className="muted small" style={{ marginTop: 8 }}>
          Chọn người → mở video → gắn nhạc/tag → tạo short.
        </p>
      </div>

      {error && <p className="error-text small">{error}</p>}
      {projects.length === 0 && !error && (
        <div className="empty-state">
          <h2>Bắt đầu bằng nhà sáng tạo</h2>
          <p className="muted small" style={{ maxWidth: 420, margin: "0 auto 16px" }}>
            Tạo một project cho người đó. Rồi upload video talking-head — mỗi file thành một short.
          </p>
          <button className="primary" onClick={onNew}>
            + Thêm nhà sáng tạo
          </button>
        </div>
      )}

      {grouped.map((group) => (
        <div key={group.key || "_ungrouped"} className="stack">
          {hasGroups && (
            <h3 className="group-heading">{group.key || "Chưa chia nhóm"}</h3>
          )}
          <div className="person-grid">
            {group.items.map((project) => {
              const pending = project.pending_count ?? 0;
              const ready = project.ready_count ?? 0;
              const next =
                project.aroll_count === 0
                  ? "Chưa có video — bấm để upload"
                  : pending > 0
                    ? `${pending} video chờ tạo short`
                    : ready > 0
                      ? `${ready} short đã xong`
                      : "Mở project";
              return (
                <button
                  key={project.project_id}
                  type="button"
                  className="person-card"
                  onClick={() => onOpen(project.project_id)}
                >
                  {project.thumb ? (
                    <img
                      src={`/api/projects/${project.project_id}/sources/s0/thumb`}
                      alt=""
                    />
                  ) : (
                    <div className="person-ph">
                      {(project.title || "?").slice(0, 1).toUpperCase()}
                    </div>
                  )}
                  <div className="person-meta">
                    <b>{project.title}</b>
                    <span className="muted small">
                      {project.aroll_count} video
                      {project.aroll_count > 0
                        ? ` · ${ready} short xong · ${pending} chưa làm`
                        : ""}
                    </span>
                    <span className="person-next muted small">{next}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
};
