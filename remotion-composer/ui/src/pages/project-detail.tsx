import React, { useEffect, useState } from "react";
import { ProjectDetail, TakeSuggestion, api } from "../api/client";
import { AssemblySettings } from "../components/assembly-settings";
import { BuildCompare } from "../components/build-compare";
import { CreateBuildPanel } from "../components/create-build-panel";
import { PromptEditor } from "../components/prompt-editor";
import { SourceCard } from "../components/source-card";
import { SourceUploader } from "../components/source-uploader";

type Tab = "sources" | "builds" | "settings" | "prompts";

const TABS: [Tab, string][] = [
  ["sources", "Nguồn quay"],
  ["builds", "Bản dựng"],
  ["settings", "Cấu hình"],
  ["prompts", "Prompt"],
];

export const ProjectDetailPage: React.FC<{
  projectId: string;
  onOpenJob: (jobId: string) => void;
  onDeleted: () => void;
}> = ({ projectId, onOpenJob, onDeleted }) => {
  const [project, setProject] = useState<ProjectDetail>();
  const [suggestions, setSuggestions] = useState<TakeSuggestion[]>([]);
  const [tab, setTab] = useState<Tab>("sources");
  const [error, setError] = useState("");
  const [wizardOpen, setWizardOpen] = useState(false);

  const load = () =>
    api.getProject(projectId)
      .then(setProject)
      .catch((exception) => setError(String(exception)));

  useEffect(() => {
    void load();
    api.takeSuggestions(projectId).then(setSuggestions).catch(() => undefined);
  }, [projectId]);

  const remove = async () => {
    if (!project) return;
    if (!window.confirm(
      `Xoá project "${project.title}" và cả ${project.builds.length} bản dựng bên trong?`)) return;
    await api.deleteProject(projectId);
    onDeleted();
  };

  if (!project) {
    return error ? <p className="error-text">{error}</p> : <p className="muted">Đang tải…</p>;
  }

  const aroll = project.sources.filter((source) => source.role === "aroll");

  return (
    <div className="stack">
      <div className="card">
        <div className="row" style={{ alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>{project.title}</h2>
          <span className="muted small">{project.project_id}</span>
          <span style={{ flex: 1 }} />
          <button
            className="primary"
            disabled={aroll.length === 0}
            onClick={() => setWizardOpen(true)}
            title={aroll.length === 0 ? "Cần A-roll trước" : "Chọn option + cách chạy"}
          >
            + Dựng bản mới
          </button>
          <button className="ghost" onClick={remove}>Xoá project</button>
        </div>
        {aroll.length === 0 ? (
          <p className="warn-text small" style={{ marginTop: 8 }}>
            Chưa có nguồn có lời nói. Upload A-roll ở tab Nguồn quay trước khi dựng.
          </p>
        ) : (
          <p className="muted small" style={{ marginTop: 8 }}>
            Luồng gợi ý: upload nguồn → (tuỳ) Cấu hình → <b>+ Dựng bản mới</b> → chọn{" "}
            <b>dừng trước render</b> để duyệt/gộp batch, hoặc <b>full local</b> nếu muốn MP4 ngay.
          </p>
        )}
        {error && <p className="error-text small">{error}</p>}
      </div>

      <CreateBuildPanel
        project={project}
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={(jobId) => {
          setWizardOpen(false);
          onOpenJob(jobId);
        }}
      />

      <div className="tabs">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            className={`tab ${tab === key ? "active" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
            {key === "sources" ? ` (${project.sources.length})` : ""}
            {key === "builds" ? ` (${project.builds.length})` : ""}
          </button>
        ))}
      </div>

      {tab === "sources" && (
        <div className="stack">
          <SourceUploader projectId={projectId} onUploaded={() => void load()} />

          {suggestions.length > 0 && (
            <div className="card">
              <h3>Gợi ý nhóm take</h3>
              <p className="muted small">
                Chỉ là gợi ý. Gộp sai làm bước chọn take xoá nội dung thật, nên máy không tự áp —
                bạn đặt nhóm ở từng nguồn bên dưới.
              </p>
              {suggestions.map((suggestion) => (
                <div key={suggestion.take_group} className="small" style={{ marginTop: 4 }}>
                  <b>{suggestion.sources.join(" + ")}</b> → «{suggestion.take_group}» · tin cậy{" "}
                  {Math.round(suggestion.confidence * 100)}% · {suggestion.reason}
                </div>
              ))}
            </div>
          )}

          {project.sources
            .slice()
            .sort((left, right) => left.order - right.order)
            .map((source) => (
              <SourceCard
                key={source.id}
                projectId={projectId}
                source={source}
                onChanged={() => void load()}
              />
            ))}
          {project.sources.length === 0 && (
            <p className="muted small">Chưa có nguồn nào — kéo file vào ô trên.</p>
          )}
        </div>
      )}

      {tab === "builds" && (
        <div className="stack">
          <div className="card">
            <h3>Các bản dựng</h3>
            {project.builds.length === 0 && (
              <p className="muted small">Chưa dựng bản nào.</p>
            )}
            {project.builds.map((item) => (
              <div key={item.job_id} className="cut">
                <div className="row" style={{ alignItems: "center", gap: 8 }}>
                  <b>{item.job_id}</b>
                  <span className="badge pending">{item.status}</span>
                  <span className="muted small">v{item.current_version}</span>
                  {item.has_final && <span className="badge running">có final.mp4</span>}
                  <span style={{ flex: 1 }} />
                  <button className="ghost small" onClick={() => onOpenJob(item.job_id)}>
                    Mở
                  </button>
                </div>
                {item.prompt && <div className="small">“{item.prompt}”</div>}
              </div>
            ))}
          </div>
          <div className="card">
            <h3>So sánh hai bản</h3>
            <BuildCompare builds={project.builds} onOpen={onOpenJob} />
          </div>
        </div>
      )}

      {tab === "settings" && (
        <AssemblySettings project={project} onSaved={() => void load()} />
      )}

      {tab === "prompts" && (
        <PromptEditor jobId={project.builds.find((item) => item.current_version > 0)?.job_id} />
      )}
    </div>
  );
};
