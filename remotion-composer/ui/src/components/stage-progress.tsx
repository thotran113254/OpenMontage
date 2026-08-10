import React from "react";
import { StageState } from "../api/client";
import {
  ALL_STAGES,
  GROUP_LABEL,
  STAGE_GROUP,
  STAGE_LABELS,
  StageGroup,
  StageKey,
} from "../lib/pipeline-plan";

export const StageProgress: React.FC<{
  stages: Record<string, StageState>;
  renderPercent?: number;
}> = ({ stages, renderPercent }) => {
  const groups: StageGroup[] = ["llm", "encode", "render"];
  // Display order follows ALL_STAGES but clustered visually by group label once.
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
                  <div className="name">{STAGE_LABELS[name]}</div>
                  <div className="meta">
                    <span className={`badge ${status}`}>{status}</span>
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
