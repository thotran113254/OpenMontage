import React, { useState } from "react";
import { ProjectSource, api } from "../api/client";

/**
 * One source: what was measured, and the four things a human may change.
 *
 * `role` and `take_group` are editable because the machine only guesses at them.
 * Duration, size and loudness are measurements and are shown read-only — letting
 * a client overwrite a measurement would put a guess where a fact was.
 */
export const SourceCard: React.FC<{
  projectId: string;
  source: ProjectSource;
  onChanged: () => void;
}> = ({ projectId, source, onChanged }) => {
  const [takeGroup, setTakeGroup] = useState(source.take_group);
  const [label, setLabel] = useState(source.label);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const patch = async (body: Parameters<typeof api.updateSource>[2]) => {
    setBusy(true);
    setError("");
    try {
      await api.updateSource(projectId, source.id, body);
      onChanged();
    } catch (exception) {
      setError(String(exception).slice(0, 200));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (force = false) => {
    if (!window.confirm(`Xoá nguồn "${source.label}"?`)) return;
    setBusy(true);
    setError("");
    try {
      await api.deleteSource(projectId, source.id, force);
      onChanged();
    } catch (exception) {
      const message = String(exception);
      // 409 means a build still uses it. Offer the forced path rather than
      // leaving the user stuck with an error they cannot act on.
      if (message.includes("đang được dùng")) {
        if (window.confirm(`${message}\n\nXoá luôn?`)) {
          await api.deleteSource(projectId, source.id, true).then(onChanged);
        }
      } else {
        setError(message.slice(0, 240));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cut" style={{ display: "flex", gap: 12 }}>
      {source.thumb ? (
        <img
          src={`/api/projects/${projectId}/sources/${source.id}/thumb`}
          alt={source.label}
          style={{ width: 72, borderRadius: 6, alignSelf: "flex-start" }}
        />
      ) : (
        <div
          style={{
            width: 72,
            height: 128,
            borderRadius: 6,
            background: "#2b3240",
            display: "grid",
            placeItems: "center",
            fontSize: 11,
          }}
          className="muted"
        >
          no thumb
        </div>
      )}

      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="row" style={{ alignItems: "center", gap: 8 }}>
          <b>{source.id}</b>
          <span className={`badge ${source.role === "broll" ? "pending" : "running"}`}>
            {source.role}
          </span>
          <span className="muted small">{source.duration.toFixed(1)}s</span>
          <span className="muted small">
            {source.width}×{source.height}
          </span>
          {source.mean_volume_db !== null && (
            <span className="muted small">{source.mean_volume_db.toFixed(0)} dB</span>
          )}
          <span style={{ flex: 1 }} />
          <button className="ghost small" disabled={busy} onClick={() => remove()}>
            Xoá
          </button>
        </div>

        {(source.warnings || []).map((warning) => (
          <div key={warning} className="warn-text small">
            {warning}
          </div>
        ))}

        <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <label className="field small" style={{ minWidth: 120 }}>
            Vai
            <select
              value={source.role}
              disabled={busy}
              onChange={(event) => patch({ role: event.target.value })}
            >
              <option value="aroll">aroll — có lời nói</option>
              <option value="broll">broll — chỉ hình phủ</option>
            </select>
          </label>

          <label className="field small" style={{ minWidth: 120 }}>
            Nhóm take
            <input
              value={takeGroup}
              disabled={busy || source.role === "broll"}
              onChange={(event) => setTakeGroup(event.target.value)}
              onBlur={() => takeGroup !== source.take_group && patch({ take_group: takeGroup })}
            />
          </label>

          <label className="field small" style={{ flex: 1, minWidth: 140 }}>
            Nhãn
            <input
              value={label}
              disabled={busy}
              onChange={(event) => setLabel(event.target.value)}
              onBlur={() => label !== source.label && patch({ label })}
            />
          </label>

          <label className="field small" style={{ width: 84 }}>
            Thứ tự
            <input
              type="number"
              value={source.order}
              disabled={busy}
              onChange={(event) => patch({ order: Number(event.target.value) })}
            />
          </label>
        </div>

        {error && <div className="error-text small">{error}</div>}
      </div>
    </div>
  );
};
