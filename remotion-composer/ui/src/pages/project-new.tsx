import React, { useState } from "react";
import { api } from "../api/client";

export const ProjectNewPage: React.FC<{ onCreated: (id: string) => void }> = ({
  onCreated,
}) => {
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [cardPlan, setCardPlan] = useState("");
  const [keyterms, setKeyterms] = useState("");
  const [mode, setMode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api.createProject({
        title: title.trim(),
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
    <div className="card" style={{ maxWidth: 640 }}>
      <h2>Project mới</h2>
      <p className="muted small">
        Project là một buổi quay: nguồn upload một lần, mọi bản dựng dùng chung. Cấu hình ở đây
        là mặc định cho mọi bản dựng — từng bản vẫn ghi đè được.
      </p>

      <label className="field">
        Tên project
        <input
          value={title}
          placeholder="VD: 3 sai lầm chatbot AI — buổi quay 04/08"
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>

      <label className="field">
        Chủ đề (giúp director bám nội dung)
        <input value={topic} onChange={(event) => setTopic(event.target.value)} />
      </label>

      <label className="field">
        Yêu cầu card
        <input
          value={cardPlan}
          placeholder="VD: đúng 4 card, mỗi card một sai lầm"
          onChange={(event) => setCardPlan(event.target.value)}
        />
      </label>

      <label className="field">
        Từ khoá gợi ý cho ASR
        <input
          value={keyterms}
          placeholder="Zalo OA, CRM — bỏ trống thì tự tách từ chủ đề"
          onChange={(event) => setKeyterms(event.target.value)}
        />
      </label>

      <label className="field">
        Kiểu ghép nhiều nguồn
        <select value={mode} onChange={(event) => setMode(event.target.value)}>
          <option value="">mặc định chung (auto)</option>
          <option value="auto">auto — tự phát hiện take trùng</option>
          <option value="sequential">sequential — ghép tuần tự</option>
          <option value="best_take">best_take — luôn chọn bản tốt nhất</option>
        </select>
      </label>

      <div className="callout info" style={{ marginBottom: 14 }}>
        <b>Sau khi tạo project</b>
        <ul>
          <li>Upload nguồn (A-roll bắt buộc) trên trang project.</li>
          <li>Tuỳ chỉnh assembly / keyterms ở tab Cấu hình.</li>
          <li>
            Bấm <b>+ Dựng bản mới</b> → chọn option + chế độ chạy (dừng trước render để duyệt/gộp
            batch, hoặc full local).
          </li>
        </ul>
      </div>

      {error && <p className="error-text small">{error}</p>}
      <div className="row">
        <button className="primary" disabled={busy || title.trim().length < 2} onClick={create}>
          Tạo project
        </button>
      </div>
    </div>
  );
};
