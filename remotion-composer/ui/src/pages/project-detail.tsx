import React, { useEffect, useState } from "react";
import { ProjectDetail, TakeSuggestion, api } from "../api/client";
import { AssemblySettings } from "../components/assembly-settings";
import { BuildCompare } from "../components/build-compare";
import { CreateBuildPanel } from "../components/create-build-panel";
import { PromptEditor } from "../components/prompt-editor";
import { ClipLibrary } from "../components/clip-library";
import { SourceCard } from "../components/source-card";
import { SourceUploader } from "../components/source-uploader";
import { countByKind } from "../lib/clip-track";
import { statusLabel } from "../lib/status";

type Tab = "videos" | "builds" | "advanced";

const TABS: [Tab, string][] = [
  ["videos", "Video"],
  ["builds", "Bản dựng"],
  ["advanced", "Tuỳ chỉnh"],
];

export const ProjectDetailPage: React.FC<{
  projectId: string;
  onOpenJob: (jobId: string) => void;
  onDeleted: () => void;
}> = ({ projectId, onOpenJob, onDeleted }) => {
  const [project, setProject] = useState<ProjectDetail>();
  const [suggestions, setSuggestions] = useState<TakeSuggestion[]>([]);
  const [tab, setTab] = useState<Tab>("videos");
  const [error, setError] = useState("");
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardIds, setWizardIds] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [inWorkspace, setInWorkspace] = useState(false);

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
      `Xoá project “${project.title}” và cả ${project.builds.length} thành phẩm bên trong?`)) return;
    await api.deleteProject(projectId);
    onDeleted();
  };

  if (!project) {
    return error ? <p className="error-text">{error}</p> : <p className="muted">Đang tải…</p>;
  }

  const aroll = project.sources.filter((source) => source.role === "aroll");
  const counts = countByKind(aroll.map((s) => s.id), project.builds);
  const uploaded = aroll.length > 0;

  const openWizard = (ids?: string[]) => {
    if (ids?.length) {
      setWizardIds(ids);
    } else if (selected.length) {
      setWizardIds(selected);
    } else {
      setWizardIds([]);
    }
    setWizardOpen(true);
  };

  return (
    <div className="stack">
      {!inWorkspace && (
        <div className="card">
          <a className="studio-back" href="#/projects">← Nhà sáng tạo</a>
          <div className="row" style={{ alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
            <div style={{ minWidth: 0 }}>
              <h2 style={{ margin: 0 }}>{project.title}</h2>
            <p className="muted small" style={{ margin: "4px 0 0" }}>
              {aroll.length} video · {project.builds.length} bản dựng
              {counts.ready ? ` · ${counts.ready} đã có MP4` : ""}
              {(() => {
                const posted = aroll.filter((s) => s.meta?.posted).length;
                const unposted = aroll.length - posted;
                return ` · ${posted} đã đăng · ${unposted} chưa đăng`;
              })()}
              {counts.running ? ` · ${counts.running} đang chạy` : ""}
            </p>
            </div>
            <span style={{ flex: 1 }} />
            {selected.length > 0 && (
              <button className="ghost" onClick={() => openWizard(selected)}>
                Tạo short ({selected.length})
              </button>
            )}
            {aroll.length > 0 && (
              <button className="primary" onClick={() => openWizard()}>
                Tạo short mới
              </button>
            )}
          </div>
          {uploaded && (
            <p className="muted small" style={{ marginTop: 10 }}>
              Mở video → <b>Chạy dựng short</b> (xem trước). Bản dựng nằm tab <b>Bản dựng</b> hoặc trong trang chi tiết video.
            </p>
          )}
          {!uploaded && (
            <p className="muted small" style={{ marginTop: 10 }}>
              Upload video talking-head bên dưới. Mỗi file = một video để gắn nhạc / tag / tạo short.
            </p>
          )}
          {error && <p className="error-text small">{error}</p>}
        </div>
      )}

      <CreateBuildPanel
        project={project}
        open={wizardOpen}
        initialSourceIds={wizardIds}
        onClose={() => setWizardOpen(false)}
        onCreated={(jobId, allIds) => {
          setWizardOpen(false);
          setSelected([]);
          if (allIds && allIds.length > 1) {
            setTab("builds");
            void load();
            return;
          }
          onOpenJob(jobId);
        }}
      />

      {!inWorkspace && (
        <div className="tabs">
          {TABS.map(([key, label]) => (
            <button
              key={key}
              className={`tab ${tab === key ? "active" : ""}`}
              onClick={() => setTab(key)}
            >
              {label}
              {key === "videos" ? ` (${aroll.length})` : ""}
              {key === "builds" ? ` (${project.builds.length})` : ""}
            </button>
          ))}
        </div>
      )}

      {(tab === "videos" || inWorkspace) && (
        <div className="stack">
          {!inWorkspace && (
            <SourceUploader
              projectId={projectId}
              compact={uploaded}
              onUploaded={() => void load()}
            />
          )}
          <ClipLibrary
            projectId={projectId}
            sources={project.sources}
            builds={project.builds}
            defaults={project.defaults}
            selected={selected}
            onSelected={setSelected}
            onBuildOne={(id) => openWizard([id])}
            onOpenJob={onOpenJob}
            onChanged={() => void load()}
            onWorkspaceChange={setInWorkspace}
          />
        </div>
      )}

      {!inWorkspace && tab === "builds" && (
        <div className="stack">
          <div className="card">
            <h3>Kết quả dựng</h3>
            <p className="muted small" style={{ marginTop: 0 }}>
              Xem trước và MP4 đã xuất. Bấm một dòng để mở studio (Xuất MP4 nếu chưa có file).
            </p>
            {project.builds.length === 0 && (
              <p className="muted small">
                Chưa có short. Mở một video → <b>Chạy dựng short</b>, hoặc bấm <b>Tạo short</b> ở trên.
              </p>
            )}
            {project.builds.map((item) => (
              <div
                key={item.job_id}
                className="cut project-row"
                role="button"
                tabIndex={0}
                onClick={() => onOpenJob(item.job_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onOpenJob(item.job_id);
                  }
                }}
              >
                {item.has_final && item.has_thumbnail ? (
                  <img src={`${item.media_base}thumbnail.jpg`} alt="" />
                ) : null}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b>{item.title || item.job_id}</b>
                  <div className="muted small">
                    {item.source_labels && item.source_labels.length > 0
                      ? `Từ video: ${item.source_labels.join(" · ")}`
                      : item.prompt
                        ? `“${item.prompt}”`
                        : ""}
                    {item.has_final ? " · đã có MP4" : " · xem trước (chưa MP4)"}
                  </div>
                </div>
                <span className={`badge ${item.status}`}>{statusLabel(item.status)}</span>
                {item.has_final && (
                  <>
                    <a
                      className="ghost small"
                      href={`${item.media_base}final.mp4`}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Xem MP4
                    </a>
                    <a
                      className="ghost small"
                      href={`${item.media_base}final.mp4`}
                      download={`${item.title || item.job_id}.mp4`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      Tải
                    </a>
                  </>
                )}
              </div>
            ))}
          </div>
          <details className="options-summary">
            <summary>So sánh hai bản</summary>
            <div style={{ marginTop: 10 }}>
              <BuildCompare builds={project.builds} onOpen={onOpenJob} />
            </div>
          </details>
          <p>
            <button className="danger-link" type="button" onClick={() => void remove()}>
              Xoá project…
            </button>
          </p>
        </div>
      )}

      {!inWorkspace && tab === "advanced" && (
        <div className="stack">
          <div className="card">
            <h3>Tuỳ chỉnh nâng cao</h3>
            <p className="muted small">
              Không bắt buộc cho short thường. Dùng khi cần cấu hình mặc định, prompt, hoặc xem file gốc / b-roll.
            </p>
          </div>
          <details className="options-summary" open>
            <summary>Cấu hình project</summary>
            <div style={{ marginTop: 10 }}>
              <AssemblySettings project={project} onSaved={() => void load()} />
            </div>
          </details>
          <details className="options-summary">
            <summary>Prompt (job gần nhất)</summary>
            <div style={{ marginTop: 10 }}>
              <PromptEditor jobId={project.builds.find((item) => item.current_version > 0)?.job_id} />
            </div>
          </details>
          <details className="options-summary">
            <summary>File gốc & b-roll ({project.sources.length})</summary>
            <div className="stack" style={{ marginTop: 10 }}>
              <SourceUploader projectId={projectId} onUploaded={() => void load()} />
              {suggestions.length > 0 && (
                <div className="card">
                  <h3>Gợi ý nhóm take</h3>
                  <p className="muted small">
                    Chỉ khi nhiều file là cùng một cảnh quay lại. Máy không tự áp.
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
          </details>
        </div>
      )}
    </div>
  );
};
