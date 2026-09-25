import React from "react";
import { StageState } from "../api/client";
import {
  ALL_STAGES,
  GROUP_LABEL,
  STAGE_GROUP,
  STAGE_LABELS,
  USER_PHASES,
  StageGroup,
  StageKey,
} from "../lib/pipeline-plan";
import { statusLabel } from "../lib/status";

const SHORT: Record<StageKey, string> = {
  probe: "File",
  transcribe: "Lời",
  select: "Take",
  direct: "AI",
  audit: "Duyệt",
  calibrate: "Dò",
  resolve: "Cắt",
  render: "Xuất",
  verify: "Đo",
};

export const StageProgress: React.FC<{
  stages: Record<string, StageState>;
  renderPercent?: number;
  compact?: boolean;
}> = ({ stages, renderPercent, compact = true }) => {
  if (compact) {
    const running = ALL_STAGES.find((name) => stages?.[name]?.status === "running");
    const failed = ALL_STAGES.find((name) => stages?.[name]?.status === "failed");
    const done = ALL_STAGES.filter((name) => stages?.[name]?.status === "completed").length;
    const phaseState = (phaseStages: StageKey[]) => {
      if (phaseStages.some((name) => stages?.[name]?.status === "failed")) return "failed";
      if (phaseStages.some((name) => stages?.[name]?.status === "running")) return "running";
      if (phaseStages.every((name) => stages?.[name]?.status === "completed")) return "completed";
      if (phaseStages.some((name) => stages?.[name]?.status === "completed")) return "partial";
      return "pending";
    };
    const hint = failed
      ? `Lỗi: ${STAGE_LABELS[failed]}`
      : running
        ? running === "render" && renderPercent !== undefined
          ? `Đang xuất ${renderPercent}%`
          : `Đang ${STAGE_LABELS[running].toLowerCase()}`
        : done === ALL_STAGES.length
          ? "Đủ bước"
          : `${done}/${ALL_STAGES.length} bước`;

    return (
      <div className="stage-strip">
        <div className="user-phase-bar" role="list" aria-label="Tiến trình tổng quan">
          {USER_PHASES.map((phase) => {
            const status = phaseState(phase.stages);
            return (
              <div
                key={phase.id}
                role="listitem"
                className={`user-phase ${status}`}
                title={phase.label}
              >
                {phase.label}
              </div>
            );
          })}
        </div>
        <div className="stage-strip-inner">
        <div className="stage-dots" role="list">
          {ALL_STAGES.map((name) => {
            const status = stages?.[name]?.status || "pending";
            return (
              <div
                key={name}
                role="listitem"
                className={`stage-dot ${status}`}
                title={`${STAGE_LABELS[name]} (${name}) — ${statusLabel(status)}`}
              >
                <span className="stage-dot-mark" aria-hidden />
                <span className="stage-dot-name">{SHORT[name]}</span>
              </div>
            );
          })}
        </div>
        <span className="stage-strip-hint muted small">{hint}</span>
        </div>
      </div>
    );
  }

  const groups: StageGroup[] = ["llm", "encode", "render"];
  const byGroup = (g: StageGroup) =>
    ALL_STAGES.filter((name) => STAGE_GROUP[name] === g);

  return (
    <div className="stage-board">
      {groups.map((group) => (
        <div key={group} className={`stage-group group-${group}`}>
          <div className="stage-group-label">{GROUP_LABEL[group]}</div>
          <div className="stages">
            {byGroup(group).map((name: StageKey) => {
              const stage = stages?.[name] || {};
              const status = stage.status || "pending";
              return (
                <div key={name} className={`stage ${status}`}>
                  <div className="name" title={name}>{STAGE_LABELS[name]}</div>
                  <div className="meta">
                    <span className={`badge ${status}`}>{statusLabel(status)}</span>
                    {stage.cached && (
                      <span className="badge cached" style={{ marginLeft: 4 }}>
                        cache
                      </span>
                    )}
                  </div>
                  <div className="meta">
                    {name === "render" &&
                    status === "running" &&
                    renderPercent !== undefined
                      ? `${renderPercent}%`
                      : stage.duration_seconds
                        ? `${stage.duration_seconds}s`
                        : "\u00a0"}
                  </div>
                  {stage.error && (
                    <div className="meta error-text">{stage.error.slice(0, 90)}</div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
};
