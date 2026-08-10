import React, { useState } from "react";
import { Build } from "../api/client";

/**
 * Two finished builds side by side.
 *
 * Plain `<video>`, not the Remotion Player: these are already rendered files, and
 * a Player would re-do in the browser what the render already baked in.
 */
export const BuildCompare: React.FC<{
  builds: Build[];
  onOpen: (jobId: string) => void;
}> = ({ builds, onOpen }) => {
  const finished = builds.filter((build) => build.has_final);
  const [left, setLeft] = useState(finished[0]?.job_id ?? "");
  const [right, setRight] = useState(finished[1]?.job_id ?? "");

  if (finished.length < 2) {
    return (
      <p className="muted small">
        Cần ít nhất 2 bản dựng đã render xong để so sánh (hiện có {finished.length}).
      </p>
    );
  }

  const picker = (value: string, set: (id: string) => void, label: string) => (
    <label className="field small" style={{ flex: 1 }}>
      {label}
      <select value={value} onChange={(event) => set(event.target.value)}>
        {finished.map((build) => (
          <option key={build.job_id} value={build.job_id}>
            {build.job_id} · v{build.current_version}
          </option>
        ))}
      </select>
    </label>
  );

  const pane = (jobId: string) => {
    const build = finished.find((item) => item.job_id === jobId);
    if (!build) return null;
    return (
      <div>
        <video
          src={`/api/media/${jobId}/final.mp4`}
          controls
          style={{ width: "100%", borderRadius: 8, background: "#000" }}
        />
        <div className="muted small" style={{ marginTop: 6 }}>
          {build.prompt ? `“${build.prompt}”` : "không có yêu cầu riêng"}
        </div>
        <div className="muted small">
          v{build.current_version} · ${build.cost_usd ?? 0}
        </div>
        <button className="ghost small" onClick={() => onOpen(jobId)}>
          Mở bản dựng này
        </button>
      </div>
    );
  };

  return (
    <div>
      <div className="row" style={{ gap: 10 }}>
        {picker(left, setLeft, "Bản bên trái")}
        {picker(right, setRight, "Bản bên phải")}
      </div>
      <div className="split" style={{ marginTop: 10 }}>
        {pane(left)}
        {pane(right)}
      </div>
    </div>
  );
};
