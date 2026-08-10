import React, { useState } from "react";
import { CloudOffer, CloudPreview } from "../api/client";

/** Consent gate before any paid cloud rent — mirrors CLI --cloud-yes. */
export const CloudConsentModal: React.FC<{
  preview: CloudPreview;
  title: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (offerId: number) => void;
}> = ({ preview, title, busy, onCancel, onConfirm }) => {
  const offers = preview.offers ?? [];
  const [offerId, setOfferId] = useState<number>(
    preview.recommended_offer_id ?? offers[0]?.offer_id ?? 0,
  );
  const [ack, setAck] = useState(false);
  const selected = offers.find((o) => o.offer_id === offerId) ?? offers[0];

  if (!preview.would_execute) {
    return (
      <div className="lightbox-overlay" onClick={onCancel}>
        <div className="card cloud-modal" onClick={(e) => e.stopPropagation()}>
          <h2>Không thuê được máy cloud</h2>
          <p className="error-text" style={{ marginTop: 8 }}>{preview.error || "Lỗi không rõ"}</p>
          {preview.config?.reason && (
            <p className="muted small" style={{ marginTop: 8 }}>{preview.config.reason}</p>
          )}
          <div className="row" style={{ marginTop: 16 }}>
            <button className="primary" onClick={onCancel}>Đóng</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="lightbox-overlay" onClick={busy ? undefined : onCancel}>
      <div className="card cloud-modal" onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        <p className="muted small" style={{ marginTop: 4, marginBottom: 12 }}>
          Footage và render kit sẽ upload lên máy Vast.ai (qua R2), rồi xoá khi xong.
          Bạn phải xác nhận trước khi thuê — không có auto-flush.
        </p>

        <div className="metrics" style={{ marginBottom: 14 }}>
          <div className="metric">
            <b>~${preview.estimated_cost_usd ?? "—"}</b>
            <span>ước tính 1 rental</span>
          </div>
          <div className="metric">
            <b>
              ~{((preview.estimated_overhead_minutes ?? 0) + (preview.estimated_render_minutes ?? 0)).toFixed(1)}p
            </b>
            <span>overhead + render</span>
          </div>
          <div className="metric">
            <b>{preview.jobs ?? 1}</b>
            <span>job trong batch</span>
          </div>
          <div className="metric">
            <b>~{preview.estimated_local_render_minutes ?? "—"}p</b>
            <span>local (miễn phí)</span>
          </div>
        </div>

        <h3>Chọn offer</h3>
        <div className="stack" style={{ maxHeight: 200, overflowY: "auto", marginBottom: 12 }}>
          {offers.map((offer: CloudOffer) => {
            const recommended = offer.offer_id === preview.recommended_offer_id;
            return (
              <label
                key={offer.offer_id}
                className={`offer-row ${offer.offer_id === offerId ? "selected" : ""}`}
              >
                <input
                  type="radio"
                  name="cloud-offer"
                  checked={offer.offer_id === offerId}
                  onChange={() => setOfferId(offer.offer_id)}
                  disabled={busy}
                />
                <span>
                  <b>#{offer.offer_id}</b> · {offer.cpu_cores} vCPU · ${offer.dph_usd.toFixed(4)}/h
                  · {offer.geolocation || "?"} · reliability {Number(offer.reliability).toFixed(3)}
                  {recommended ? " · đề xuất" : ""}
                </span>
              </label>
            );
          })}
        </div>

        {selected && (
          <p className="muted small" style={{ marginBottom: 10 }}>
            Pricing: <b>{preview.pricing_mode}</b>
            {preview.on_demand_alternative_dph_usd != null && (
              <> · on-demand alt ~${preview.on_demand_alternative_dph_usd.toFixed(4)}/h</>
            )}
            {" · "}ceiling ${preview.ceilings?.max_total_usd_per_rental}/rental
          </p>
        )}

        {(preview.warnings ?? []).length > 0 && (
          <ul className="small warn-text" style={{ marginBottom: 12, paddingLeft: 18 }}>
            {preview.warnings!.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}

        <label className="ack-row">
          <input
            type="checkbox"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
            disabled={busy}
          />
          <span>
            Tôi hiểu sẽ tốn tiền (~${preview.estimated_cost_usd}) và footage rời máy này.
            Xác nhận thuê offer #{offerId}.
          </span>
        </label>

        <div className="row" style={{ marginTop: 16 }}>
          <button className="ghost" disabled={busy} onClick={onCancel}>Huỷ</button>
          <button
            className="primary"
            disabled={busy || !ack || !offerId}
            onClick={() => onConfirm(offerId)}
          >
            {busy ? "Đang bắt đầu…" : "Thuê máy & render ngay"}
          </button>
        </div>
      </div>
    </div>
  );
};
