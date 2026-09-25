import React from "react";
import { OutcomeGroups } from "../lib/outcome";

/** Renders the "Đã làm / Bị chặn / Chưa làm được" groups built by lib/outcome.ts. */
export const OutcomeSummary: React.FC<OutcomeGroups> = ({ done, blocked, notDone, totalApplied }) => {
  if (!done.length && !blocked.length && !notDone.length && totalApplied === undefined) return null;
  return (
    <div className="outcome-summary" style={{ marginTop: 4 }}>
      {done.length > 0 && (
        <div className="muted small">
          <b>Đã làm:</b> {done.join(" · ")}
        </div>
      )}
      {totalApplied !== undefined && (
        <div className="muted small" style={{ fontSize: 11, opacity: 0.8 }}>
          Tổng đang cắt: {totalApplied}
        </div>
      )}
      {blocked.length > 0 && (
        <div className="warn-text small">
          <b>Bị chặn:</b> {blocked.join(" · ")}
        </div>
      )}
      {notDone.length > 0 && (
        <div className="muted small">
          <b>Chưa làm được:</b> {notDone.join(" · ")}
        </div>
      )}
    </div>
  );
};
