import React, { useState } from "react";
import { api } from "../api/client";
import { FlowSteps } from "../components/flow-steps";

export const ProjectNewPage: React.FC<{
  onCreated: (id: string) => void;
  onBack: () => void;
}> = ({ onCreated, onBack }) => {
  const [title, setTitle] = useState("");
  const [folder, setFolder] = useState("");
  const [topic, setTopic] = useState("");
  const [cardPlan, setCardPlan] = useState("");
  const [keyterms, setKeyterms] = useState("");
  const [mode, setMode] = useState("");
  const [showMore, setShowMore] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const titleOk = title.trim().length >= 2;

  const create = async () => {
    if (!titleOk) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.createProject({
        title: title.trim(),
        folder: folder.trim() || undefined,
        assembly: mode ? { mode: mode as never } : {},
        keyterms: keyterms.split(",").map((term) => term.trim()).filter(Boolean),
        defaults: { topic, card_plan: cardPlan },
      });
      onCreated(result.project_id);
    } catch (exception) {
      setError(String(exception).slice(0, 300));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ maxWidth: 560 }}>
      <button className="ghost small" type="button" onClick={onBack}>
        ← Nhà sáng tạo
      </button>
      <h2 style={{ marginTop: 12 }}>Tạo project cho nhà sáng tạo</h2>
      <FlowSteps
        steps={[
          { id: "name", label: "Đặt tên", state: "now" },
          { id: "up", label: "Upload video", state: "todo" },
          { id: "t", label: "Chọn mẫu", state: "todo" },
          { id: "build", label: "Xuất short", state: "todo" },
        ]}
      />
      <p className="muted small">
        Một người = một project. Video hàng ngày upload vào đây — mỗi file tạo một short riêng.
      </p>

      <label className="field">
        Tên nhà sáng tạo
        <input
          value={title}
          autoFocus
          placeholder="VD: An"
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && titleOk && !busy) void create();
          }}
        />
        {!titleOk && (
          <span className="hint">Cần ít nhất 2 ký tự.</span>
        )}
      </label>

      <button
        className="ghost small"
        type="button"
        onClick={() => setShowMore((v) => !v)}
      >
        {showMore ? "▾ Ẩn tuỳ chọn" : "▸ Nhóm, chủ đề… (không bắt buộc)"}
      </button>

      {showMore && (
        <div className="stack" style={{ marginTop: 12 }}>
          <label className="field">
            Nhóm
            <input
              value={folder}
              placeholder="VD: Studio — để trống nếu chưa cần"
              onChange={(event) => setFolder(event.target.value)}
            />
            <span className="hint">Chỉ khi có nhiều team. Một studio vài người thì để trống.</span>
          </label>
          <label className="field">
            Chủ đề video mặc định
            <input
              value={topic}
              placeholder="VD: 3 sai lầm chatbot AI"
              onChange={(event) => setTopic(event.target.value)}
            />
            <span className="hint">Giúp AI bám nội dung khi cắt. Có thể điền sau.</span>
          </label>

          <label className="field">
            Kế hoạch thẻ chữ trên video
            <input
              value={cardPlan}
              placeholder="VD: đúng 4 thẻ, mỗi thẻ một sai lầm"
              onChange={(event) => setCardPlan(event.target.value)}
            />
          </label>

          <label className="field">
            Từ khoá nhận lời (ASR)
            <input
              value={keyterms}
              placeholder="Zalo OA, CRM — để trống thì tự tách từ chủ đề"
              onChange={(event) => setKeyterms(event.target.value)}
            />
          </label>

          <label className="field">
            Khi có nhiều clip cùng nội dung
            <select value={mode} onChange={(event) => setMode(event.target.value)}>
              <option value="">Tự chọn (mặc định)</option>
              <option value="auto">Tự phát hiện take trùng</option>
              <option value="sequential">Ghép tuần tự theo thứ tự upload</option>
              <option value="best_take">Luôn lấy take tốt nhất</option>
            </select>
          </label>
        </div>
      )}

      <div className="callout info" style={{ margin: "16px 0 14px" }}>
        Sau khi tạo: vào project → kéo video → bấm <b>Tạo short</b> → chọn mẫu (hoặc chỉnh tay) → chạy.
      </div>

      {error && <p className="error-text small">{error}</p>}
      <div className="row">
        <button className="ghost" type="button" onClick={onBack}>
          Huỷ
        </button>
        <button className="primary" disabled={busy || !titleOk} onClick={() => void create()}>
          {busy ? "Đang tạo…" : "Tạo project"}
        </button>
      </div>
    </div>
  );
};
