import React, { useEffect, useState } from "react";
import { api, JobVersion } from "../api/client";
import { describeOptionsChanged, outcomeGroups } from "../lib/outcome";
import { OutcomeSummary } from "./outcome-summary";

interface RollbackNote {
  /** The version being restored from — matches the server's `restored_from`. */
  version: number;
  options: string[];
}

/** Spec versions (v1, v2, …) for one build — not prompt template versions. */
export const VersionHistory: React.FC<{
  jobId: string;
  busy: boolean;
  onChanged: () => void;
  compact?: boolean;
}> = ({ jobId, busy, onChanged, compact = false }) => {
  const [versions, setVersions] = useState<JobVersion[]>([]);
  const [current, setCurrent] = useState(0);
  const [error, setError] = useState("");
  const [rollbackPending, setRollbackPending] = useState(false);
  const [rollbackNote, setRollbackNote] = useState<RollbackNote | null>(null);

  const load = () =>
    api.versions(jobId)
      .then((data) => {
        setVersions(data.versions);
        setCurrent(data.current_version);
      })
      .catch((e) => setError(String(e)));

  useEffect(() => {
    void load();
  }, [jobId, busy]);

  // The server queues a re-cut (resolve) after rollback rather than copying
  // the old preview — keep the "đang cắt lại" note up until that job finishes,
  // so nobody mistakes the restored version's stale preview for the ready one.
  useEffect(() => {
    if (!busy) setRollbackNote(null);
  }, [busy]);

  const rollback = async (version: number) => {
    if (!window.confirm(`Dùng lại bản v${version}? Sẽ tạo phiên bản mới từ bản đó.`)) return;
    setError("");
    setRollbackPending(true);
    try {
      const result = await api.rollback(jobId, version);
      setRollbackNote({
        version: result.restored_from ?? version,
        options: describeOptionsChanged(result.options_changed),
      });
      onChanged();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setRollbackPending(false);
    }
  };

  const rollbackDisabled = busy || rollbackPending;

  const rollbackCallout = rollbackNote && (
    <div className="callout info small" style={{ marginBottom: 8 }}>
      Đang cắt lại theo v{rollbackNote.version}… preview sẽ cập nhật khi xong.
      {rollbackNote.options.length > 0 && (
        <div style={{ marginTop: 4 }}>Đã khôi phục: {rollbackNote.options.join(" · ")}</div>
      )}
    </div>
  );

  if (compact) {
    if (versions.length <= 1) {
      return <span className="muted small">v{current || 1}</span>;
    }
    return (
      <div className="stack" style={{ gap: 4 }}>
        <label className="version-picker">
          <span className="muted small">Phiên bản</span>
          <select
            value={current}
            disabled={rollbackDisabled}
            onChange={(event) => {
              const target = Number(event.target.value);
              if (target !== current) void rollback(target);
            }}
          >
            {versions.map((row) => (
              <option key={row.version} value={row.version}>
                v{row.version} · {row.kind}
                {row.is_current ? " (đang dùng)" : ""}
                {!row.has_props ? " · chưa preview" : ""}
              </option>
            ))}
          </select>
        </label>
        {rollbackNote && (
          <span className="muted small">
            Đang cắt lại theo v{rollbackNote.version}…
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="version-history">
      <h3>Phiên bản dựng</h3>
      <p className="muted small" style={{ marginTop: 0 }}>
        Mỗi lần AI sửa hoặc bạn quay lại bản cũ tạo thêm một phiên bản (v1, v2…).
        Khác với mẫu prompt ở tab Tuỳ chỉnh project.
      </p>
      {rollbackCallout}
      {error && <p className="error-text small">{error}</p>}
      {versions.length === 0 && <p className="muted small">Chưa có phiên bản.</p>}
      {versions.slice().reverse().map((version) => (
        <div
          key={version.version}
          className="cut"
          style={{ borderLeftColor: version.is_current ? "var(--info)" : undefined }}
        >
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <b>v{version.version}</b>
            <span className="badge pending">{version.kind}</span>
            {version.is_current && <span className="badge running">đang dùng</span>}
            {!version.has_props && (
              <span className="badge failed">chưa preview</span>
            )}
            <span style={{ flex: 1 }} />
            {!version.is_current && (
              <button
                className="ghost"
                style={{ padding: "2px 10px" }}
                disabled={rollbackDisabled}
                onClick={() => void rollback(version.version)}
              >
                Dùng lại
              </button>
            )}
          </div>
          {version.instruction && (
            <div className="small" style={{ marginTop: 4 }}>
              “{version.instruction}”
            </div>
          )}
          {(() => {
            const groups = outcomeGroups(version.outcome);
            if (groups.done.length || groups.blocked.length || groups.notDone.length) {
              return <OutcomeSummary {...groups} />;
            }
            // Older versions (built before `outcome` existed) fall back to
            // the raw diff counters so history isn't blank.
            if (version.changes) {
              return (
                <div className="muted small" style={{ marginTop: 4 }}>
                  thêm {version.changes.added} · bỏ {version.changes.removed} · sửa{" "}
                  {version.changes.modified}
                  {version.changes.top_level_changed?.length
                    ? ` · đổi ${version.changes.top_level_changed.join(", ")}`
                    : ""}
                </div>
              );
            }
            return null;
          })()}
        </div>
      ))}
    </div>
  );
};
