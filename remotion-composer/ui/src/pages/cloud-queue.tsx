import React, { useCallback, useEffect, useState } from "react";
import {
  api,
  CloudFlushCheck,
  CloudOperation,
  CloudPreview,
  CloudQueueEntry,
  CloudStatus,
} from "../api/client";
import { CloudConsentModal } from "../components/cloud-consent-modal";

const formatAge = (enqueuedAt: string) => {
  try {
    const epoch = Date.parse(enqueuedAt.endsWith("Z") ? enqueuedAt : `${enqueuedAt}Z`);
    if (Number.isNaN(epoch)) return enqueuedAt;
    const mins = Math.max(0, (Date.now() - epoch) / 60000);
    if (mins < 60) return `${mins.toFixed(0)} phút`;
    if (mins < 60 * 24) return `${(mins / 60).toFixed(1)} giờ`;
    return `${(mins / 1440).toFixed(1)} ngày`;
  } catch {
    return enqueuedAt;
  }
};

const thresholdLabel: Record<string, string> = {
  min_jobs: "đủ số job",
  min_total_render_minutes: "đủ phút render",
  max_wait_minutes: "chờ đủ lâu",
};

export const CloudQueuePage: React.FC<{
  onOpenJob: (id: string) => void;
}> = ({ onOpenJob }) => {
  const [status, setStatus] = useState<CloudStatus>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<CloudPreview | null>(null);
  const [executing, setExecuting] = useState(false);
  const [toast, setToast] = useState("");

  const load = useCallback(() => {
    api
      .cloudStatus()
      .then(setStatus)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [load]);

  const entries = status?.queue.entries ?? [];
  const flush: CloudFlushCheck | undefined = status?.flush_check;
  const op: CloudOperation | undefined = status?.operation;
  const cfg = status?.config;
  const ready = Boolean(status?.ready_to_flush);
  const cloudBusy = op?.status === "running";

  const removeEntry = async (jobId: string) => {
    setBusy(true);
    setError("");
    try {
      await api.cloudRemove(jobId);
      setToast(`Đã gỡ ${jobId} khỏi lịch`);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const clearAll = async () => {
    if (!window.confirm(`Xoá cả ${entries.length} job khỏi lịch batch?`)) return;
    setBusy(true);
    try {
      await api.cloudClear();
      setToast("Đã xoá batch queue");
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const startFlushPreview = async () => {
    setBusy(true);
    setError("");
    try {
      const body = await api.cloudPreview({ mode: "flush" });
      setPreview(body);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const confirmFlush = async (offerId: number) => {
    if (!preview?.dry_run_ref) return;
    setExecuting(true);
    setError("");
    try {
      const res = await api.cloudExecute({
        mode: "flush",
        offer_id: offerId,
        dry_run_ref: preview.dry_run_ref,
        confirm: true,
      });
      setPreview(null);
      setToast(res.message);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setExecuting(false);
    }
  };

  const dismissOp = async () => {
    try {
      await api.cloudOperationClear();
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  if (!status && !error) return <div className="card muted">Đang tải lịch render…</div>;

  return (
    <div className="stack">
      <div className="card">
        <div className="row" style={{ alignItems: "center" }}>
          <div style={{ flex: 1 }}>
            <h2 style={{ margin: 0 }}>Lịch render cloud</h2>
            <p className="muted small" style={{ marginTop: 4 }}>
              Xếp job vào batch trong ngày → khi đủ ngưỡng (hoặc muốn luôn) bấm{" "}
              <b>Render batch ngay</b>. Không tự chạy cuối ngày — bạn luôn bấm xác nhận.
            </p>
          </div>
          <button
            className="primary"
            disabled={busy || cloudBusy || entries.length === 0}
            onClick={() => void startFlushPreview()}
            title={
              entries.length === 0
                ? "Queue rỗng"
                : ready
                  ? "Ngưỡng đã đạt — render cả batch trên 1 máy thuê"
                  : "Chưa đạt ngưỡng — vẫn có thể force flush"
            }
          >
            {ready ? "⚡ Render batch ngay" : "Render batch ngay"}
          </button>
        </div>
      </div>

      {toast && (
        <div className="card ok-text small" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <span style={{ flex: 1 }}>{toast}</span>
          <button className="ghost small" onClick={() => setToast("")}>Đóng</button>
        </div>
      )}
      {error && <div className="card error-text small">{error}</div>}

      {/* Live cloud operation banner */}
      {op && op.status !== "idle" && (
        <div className={`card op-banner ${op.status}`}>
          <div className="row" style={{ alignItems: "center" }}>
            <div style={{ flex: 1 }}>
              <b>
                {op.status === "running" && "☁ Đang render cloud…"}
                {op.status === "completed" && "✓ Cloud xong"}
                {op.status === "failed" && "✗ Cloud lỗi"}
              </b>
              <div className="muted small" style={{ marginTop: 4 }}>
                {op.mode} · {op.job_ids?.join(", ")} · {op.message}
                {op.cost_usd != null && ` · $${op.cost_usd}`}
              </div>
              {op.error && <pre className="error-text small" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>{op.error}</pre>}
            </div>
            {op.status !== "running" && (
              <button className="ghost" onClick={() => void dismissOp()}>Đã hiểu</button>
            )}
          </div>
        </div>
      )}

      {/* Config + thresholds */}
      <div className="split cloud-dash">
        <div className="card">
          <h2>Trạng thái cloud</h2>
          <div className="metrics">
            <div className="metric">
              <b className={cfg?.enabled ? "ok-text" : "warn-text"}>
                {cfg?.enabled ? "Bật" : "Tắt"}
              </b>
              <span>config enabled</span>
            </div>
            <div className="metric">
              <b>{cfg?.pricing_mode ?? "—"}</b>
              <span>pricing</span>
            </div>
            <div className="metric">
              <b>${cfg?.max_dph_usd ?? "—"}</b>
              <span>max $/h</span>
            </div>
            <div className="metric">
              <b>${cfg?.max_total_usd_per_rental ?? "—"}</b>
              <span>ceiling / rental</span>
            </div>
          </div>
          {cfg?.reason && <p className="warn-text small" style={{ marginTop: 12 }}>{cfg.reason}</p>}
          <p className="muted small" style={{ marginTop: 10 }}>
            Bật cloud: đặt <code>enabled: true</code> trong{" "}
            <code>config/cloud-render.json</code> + Vast API key + R2. Xem{" "}
            <code>docs/cloud-render.md</code>.
          </p>
        </div>

        <div className="card">
          <h2>Ngưỡng flush batch</h2>
          <p className="muted small" style={{ marginBottom: 10 }}>
            Chỉ là gợi ý — quyết định vẫn là nút bên trên. Hiện:{" "}
            <b className={ready ? "ok-text" : "muted"}>
              {ready
                ? (flush?.thresholds_met ?? []).map((t) => thresholdLabel[t] || t).join(", ")
                : "chưa đạt ngưỡng nào"}
            </b>
          </p>
          <table>
            <tbody>
              <tr>
                <td>Số job trong lịch</td>
                <td>
                  <b>{flush?.job_count ?? 0}</b>
                  <span className="muted small"> / ≥ {flush?.thresholds?.min_jobs ?? 3}</span>
                </td>
              </tr>
              <tr>
                <td>Phút render ước tính</td>
                <td>
                  <b>~{flush?.estimated_render_minutes ?? 0}</b>
                  <span className="muted small">
                    {" "}/ ≥ {flush?.thresholds?.min_total_render_minutes ?? 20}
                  </span>
                </td>
              </tr>
              <tr>
                <td>Job cũ nhất chờ</td>
                <td>
                  <b>{flush?.oldest_age_minutes ?? 0} phút</b>
                  <span className="muted small">
                    {" "}/ ≥ {flush?.thresholds?.max_wait_minutes ?? 240}
                  </span>
                </td>
              </tr>
              <tr>
                <td>Chi phí batch (ước)</td>
                <td>
                  <b className="ok-text">~${flush?.estimated_cost_usd ?? 0}</b>
                  <span className="muted small">
                    {" "}vs ~${flush?.estimated_cost_if_rendered_separately_usd ?? 0} thuê lẻ
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
          {flush?.amortization_note && (
            <p className="muted small" style={{ marginTop: 10 }}>{flush.amortization_note}</p>
          )}
        </div>
      </div>

      {/* Queue table */}
      <div className="card">
        <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Đang chờ ({entries.length})</h2>
          <span style={{ flex: 1 }} />
          {entries.length > 0 && (
            <button className="ghost error-text" disabled={busy || cloudBusy} onClick={() => void clearAll()}>
              Xoá hết
            </button>
          )}
        </div>

        {entries.length === 0 ? (
          <div className="empty-queue">
            <p className="muted">Chưa có job nào trong lịch batch.</p>
            <p className="muted small" style={{ marginTop: 6 }}>
              Mở một bản dựng → bấm <b>Xếp lịch cloud</b> sau khi đã có preview/props.
              Trong ngày gom nhiều job, cuối ngày (hoặc khi đủ ngưỡng) quay lại đây bấm render batch.
            </p>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Trạng thái</th>
                <th>v</th>
                <th>~render</th>
                <th>Chờ</th>
                <th>Ghi chú</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry: CloudQueueEntry) => (
                <tr key={entry.job_id}>
                  <td>
                    <button className="linkish" onClick={() => onOpenJob(entry.job_id)}>
                      <b>{entry.title || entry.job_id}</b>
                    </button>
                    <div className="muted small">{entry.job_id}</div>
                    {entry.has_final && (
                      <span className="badge completed" title="Đã có final.mp4 — flush sẽ render lại">
                        đã có final
                      </span>
                    )}
                  </td>
                  <td>
                    <span className={`badge ${entry.status}`}>{entry.status}</span>
                    {entry.job_status && entry.job_status !== entry.status && (
                      <div className="muted small">{entry.job_status}</div>
                    )}
                  </td>
                  <td>v{entry.version_at_enqueue}</td>
                  <td>~{Math.round(entry.estimated_render_seconds)}s</td>
                  <td className="muted small">{formatAge(entry.enqueued_at)}</td>
                  <td className="muted small">{entry.note || "—"}</td>
                  <td>
                    <button
                      className="ghost small"
                      disabled={busy || cloudBusy}
                      onClick={() => void removeEntry(entry.job_id)}
                    >
                      Gỡ
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h2>Hai đường render</h2>
        <div className="path-grid">
          <div className="path-card">
            <h3>⚡ Chạy ngay</h3>
            <ul className="small muted">
              <li><b>Local</b> — nút “Render MP4” trên trang job (FIFO máy này, $0)</li>
              <li><b>Cloud 1 job</b> — “Cloud ngay” trên trang job (thuê máy cho 1 clip)</li>
            </ul>
          </div>
          <div className="path-card highlight">
            <h3>📅 Lịch batch (trang này)</h3>
            <ul className="small muted">
              <li>Xếp nhiều job trong ngày — free, không thuê máy</li>
              <li>Khi đủ ≥{flush?.thresholds?.min_jobs ?? 3} job (hoặc muốn) → Render batch ngay</li>
              <li>1 rental, amortize ~6 phút overhead — rẻ hơn thuê lẻ</li>
            </ul>
          </div>
        </div>
      </div>

      {preview && (
        <CloudConsentModal
          preview={preview}
          title="Xác nhận render cả batch cloud"
          busy={executing}
          onCancel={() => !executing && setPreview(null)}
          onConfirm={(id) => void confirmFlush(id)}
        />
      )}
    </div>
  );
};
