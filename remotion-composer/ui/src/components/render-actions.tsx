import React, { useCallback, useEffect, useState } from "react";
import { api, CloudPreview, CloudQueueEntry } from "../api/client";
import { CloudConsentModal } from "./cloud-consent-modal";

/**
 * Render CTAs on job detail: local now / schedule cloud batch / cloud now.
 * Keeps the three paths visible so users never wonder "where did render go".
 */
export const RenderActions: React.FC<{
  jobId: string;
  busy: boolean;
  hasProps: boolean;
  onLocalRender: (scale: number) => void;
  onChanged?: () => void;
}> = ({ jobId, busy, hasProps, onLocalRender, onChanged }) => {
  const [queued, setQueued] = useState(false);
  const [entry, setEntry] = useState<CloudQueueEntry | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [preview, setPreview] = useState<CloudPreview | null>(null);
  const [executing, setExecuting] = useState(false);
  const [cloudEnabled, setCloudEnabled] = useState(false);

  const refresh = useCallback(() => {
    api
      .cloudJobQueued(jobId)
      .then((res) => {
        setQueued(res.queued);
        setEntry(res.entry);
      })
      .catch(() => undefined);
    api
      .cloudStatus()
      .then((s) => setCloudEnabled(Boolean(s.config?.enabled)))
      .catch(() => undefined);
  }, [jobId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const schedule = async () => {
    setWorking(true);
    setError("");
    setMessage("");
    try {
      const res = await api.cloudEnqueue(jobId);
      setMessage(res.message + " — mở trang Lịch render để flush batch.");
      setQueued(true);
      setEntry(res.entry);
      onChanged?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
    }
  };

  const unschedule = async () => {
    setWorking(true);
    setError("");
    try {
      await api.cloudRemove(jobId);
      setMessage("Đã gỡ khỏi lịch batch");
      setQueued(false);
      setEntry(null);
      onChanged?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
    }
  };

  const cloudNowPreview = async () => {
    setWorking(true);
    setError("");
    try {
      const body = await api.cloudPreview({ mode: "render_now", job_id: jobId });
      setPreview(body);
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
    }
  };

  const confirmCloudNow = async (offerId: number) => {
    if (!preview?.dry_run_ref) return;
    setExecuting(true);
    setError("");
    try {
      const res = await api.cloudExecute({
        mode: "render_now",
        job_id: jobId,
        offer_id: offerId,
        dry_run_ref: preview.dry_run_ref,
        confirm: true,
      });
      setPreview(null);
      setMessage(res.message);
      refresh();
      onChanged?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setExecuting(false);
    }
  };

  const disabled = busy || working || !hasProps;

  return (
    <div className="render-actions">
      {!hasProps && (
        <p className="muted small">Chưa có preview — chạy Cắt & xem trước trước.</p>
      )}

      <div className="render-group">
        <span className="render-label">Xuất video trên máy này</span>
        <div className="row" style={{ gap: 8 }}>
          <button
            className="primary"
            disabled={disabled}
            onClick={() => onLocalRender(1)}
            title="Xuất MP4 full trên máy này — miễn phí, vào hàng đợi. Có xác nhận trước khi chạy."
          >
            Xuất MP4 trên máy này
          </button>
          <button
            disabled={disabled}
            onClick={() => onLocalRender(0.5)}
            title="Bản nháp 540p nhanh hơn"
          >
            Nháp 540p
          </button>
        </div>
        <p className="muted small">
          Render có thể 10–30 phút, chiếm CPU.
        </p>
      </div>

      {(cloudEnabled || queued) && (
      <div className="render-group">
        <span className="render-label">Cloud — lịch (gom batch) hoặc ngay</span>
        <div className="row" style={{ gap: 8 }}>
          {!queued ? (
            <button
              disabled={disabled}
              onClick={() => void schedule()}
              title="Xếp vào batch queue — KHÔNG thuê máy, $0. Flush sau trên Lịch render."
            >
              📅 Xếp lịch cloud
            </button>
          ) : (
            <button
              className="ghost"
              disabled={busy || working}
              onClick={() => void unschedule()}
              title="Gỡ khỏi batch queue"
            >
              ✓ Đã xếp lịch{entry ? ` (v${entry.version_at_enqueue})` : ""} · Gỡ
            </button>
          )}
          <button
            disabled={disabled}
            onClick={() => void cloudNowPreview()}
            title="Thuê máy Vast.ai ngay — có modal chi phí + checkbox xác nhận"
          >
            ☁ Cloud ngay…
          </button>
          <a className="ghost button-link" href="#/cloud">
            Mở lịch batch →
          </a>
        </div>
        <p className="warn-text small">
          ⚠ Cloud: footage rời máy (R2 + Vast). “Xếp lịch” chỉ ghi queue local. “Cloud ngay” /
          “Render batch” mới tốn tiền — luôn bắt xác nhận.
        </p>
      </div>
      )}
      {message && <p className="ok-text small" style={{ marginTop: 8 }}>{message}</p>}
      {error && <p className="error-text small" style={{ marginTop: 8 }}>{error}</p>}
      {queued && (
        <p className="muted small" style={{ marginTop: 6 }}>
          Job đang trong lịch batch. Khi đủ ngưỡng (hoặc muốn luôn), vào{" "}
          <a href="#/cloud">Lịch render</a> bấm <b>Render batch ngay</b>.
        </p>
      )}

      {preview && (
        <CloudConsentModal
          preview={preview}
          title="Xác nhận cloud render 1 job"
          busy={executing}
          onCancel={() => !executing && setPreview(null)}
          onConfirm={(id) => void confirmCloudNow(id)}
        />
      )}
    </div>
  );
};
