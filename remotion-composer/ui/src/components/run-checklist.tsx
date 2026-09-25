import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import {
  ALL_STAGES,
  PREPARE_STAGES,
  RENDER_STAGES,
  StageKey,
} from "../lib/pipeline-plan";

type FlagId = "prepare" | "direct" | "resolve" | "render";

const FLAGS: {
  id: FlagId;
  label: string;
  hint: string;
  stages: StageKey[];
  needsProps?: boolean;
}[] = [
  {
    id: "prepare",
    label: "Cắt & xem trước",
    hint: "Tách lời, AI cắt, encode preview — chưa xuất MP4",
    stages: [...PREPARE_STAGES],
  },
  {
    id: "direct",
    label: "Dựng lại khung AI",
    hint: "Chỉ đạo diễn + kiểm duyệt, không encode lại",
    stages: ["direct", "audit"],
  },
  {
    id: "resolve",
    label: "Cắt lại file",
    hint: "Encode lại bản xem từ khung hiện tại",
    stages: ["resolve"],
    needsProps: true,
  },
  {
    id: "render",
    label: "Xuất MP4",
    hint: "10–30 phút, chiếm CPU máy này",
    stages: [...RENDER_STAGES],
    needsProps: true,
  },
];

const orderIndex = (name: string) => {
  const i = (ALL_STAGES as readonly string[]).indexOf(name);
  return i < 0 ? 99 : i;
};

const CONFIRM_RENDER =
  "Xuất MP4 sẽ chạy Remotion trên máy này (có thể 10–30+ phút, chiếm CPU). Tiếp tục?";

/** The one thing to do next, derived from the job's current state. */
const primaryAction = (hasProps: boolean, hasFinal: boolean) => {
  if (!hasProps) {
    return { stages: [...PREPARE_STAGES], label: "Cắt & xem trước", confirm: false };
  }
  if (!hasFinal) {
    return { stages: [...RENDER_STAGES], label: "Xuất MP4", confirm: true };
  }
  return {
    stages: ["direct", "audit"] as StageKey[],
    label: "Dựng lại khung AI",
    confirm: false,
  };
};

export const RunChecklist: React.FC<{
  jobId: string;
  busy: boolean;
  hasProps: boolean;
  hasFinal: boolean;
  onQueued: () => void;
}> = ({ jobId, busy, hasProps, hasFinal, onQueued }) => {
  const [picked, setPicked] = useState<Record<FlagId, boolean>>({
    prepare: !hasProps,
    direct: false,
    resolve: false,
    render: hasProps && !hasFinal,
  });
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setPicked({
      prepare: !hasProps,
      direct: false,
      resolve: false,
      render: hasProps && !hasFinal,
    });
  }, [jobId, hasProps, hasFinal]);

  const activeFlags = useMemo(
    () => FLAGS.filter((flag) => picked[flag.id] && !(flag.needsProps && !hasProps)),
    [picked, hasProps],
  );

  const stages = useMemo(() => {
    const set = new Set<string>();
    for (const flag of activeFlags) {
      for (const name of flag.stages) set.add(name);
    }
    return [...set].sort((a, b) => orderIndex(a) - orderIndex(b));
  }, [activeFlags]);

  const toggle = (id: FlagId) => {
    setPicked((current) => ({ ...current, [id]: !current[id] }));
  };

  const runStages = async (targetStages: string[], confirmRender: boolean) => {
    if (!targetStages.length) return;
    if (confirmRender && !window.confirm(CONFIRM_RENDER)) return;
    setWorking(true);
    setError("");
    try {
      await api.runStages(jobId, { stages: targetStages, use_cache: true });
      onQueued();
    } catch (e) {
      setError(String(e).slice(0, 280));
    } finally {
      setWorking(false);
    }
  };

  const disabled = busy || working;
  const primary = primaryAction(hasProps, hasFinal);
  // Ticked-checkbox count, not the number of underlying pipeline stages a
  // flag expands to (e.g. "Xuất MP4" alone maps to 2 stages: render+verify) —
  // showing the expanded count is what produced the old "Chạy 2 bước đã
  // tick" mismatch when only 1 box was ticked.
  const pickedCount = activeFlags.length;

  return (
    <div className="stack">
      <button
        className="primary"
        disabled={disabled}
        onClick={() => void runStages(primary.stages, primary.confirm)}
      >
        {working ? "Đang xếp…" : busy ? "Đang chạy…" : primary.label}
      </button>
      {hasFinal && (
        <p className="ok-text small">Đã xuất MP4. Bấm lại nếu muốn dựng lại khung AI.</p>
      )}

      <details className="options-summary">
        <summary>Nâng cao — chạy nhiều bước cùng lúc</summary>
        <div className="stack" style={{ marginTop: 10 }}>
          <p className="muted small">Tick việc cần làm, rồi bấm chạy. Có thể tick nhiều mục một lúc.</p>
          <div className="check-list">
            {FLAGS.map((flag) => {
              const locked = Boolean(flag.needsProps && !hasProps);
              const on = picked[flag.id] && !locked;
              return (
                <label key={flag.id} className={`check-row ${on ? "on" : ""} ${locked ? "locked" : ""}`}>
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={disabled || locked}
                    onChange={() => toggle(flag.id)}
                  />
                  <span>
                    <b>{flag.label}</b>
                    <span className="muted small">
                      {locked ? "Cần xong Cắt & xem trước trước" : flag.hint}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <button
              disabled={disabled || pickedCount === 0}
              onClick={() => void runStages(stages, picked.render)}
            >
              {working ? "Đang xếp…" : busy ? "Đang chạy…" : `Chạy ${pickedCount} bước đã tick`}
            </button>
          </div>
        </div>
      </details>

      {error && <p className="error-text small">{error}</p>}
    </div>
  );
};
