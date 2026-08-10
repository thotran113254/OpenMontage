import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export const NewJobPage: React.FC<{ onCreated: (jobId: string) => void }> = ({ onCreated }) => {
  const [resources, setResources] = useState<any>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    input_path: "",
    title: "",
    prompt: "",
    topic: "",
    brand_pill: "",
    card_plan: "",
    whisper_model: "medium",
    verifier_model: "",
    language: "vi",
    tempo: 1.06,
    bgm: true,
    cold_open: true,
  });

  useEffect(() => {
    api.resources().then(setResources).catch((e) => setError(String(e)));
  }, []);

  const set = (key: string, value: unknown) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const { input_path, title, ...options } = form;
      const result = await api.createJob({ input_path, title, options });
      onCreated(result.job_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="split">
      <div className="card">
        <h2>Tạo job mới</h2>

        <div className="field">
          <label>Đường dẫn footage gốc (.mp4)</label>
          <input
            value={form.input_path}
            placeholder="C:\Users\...\Downloads\quay-goc.mp4"
            onChange={(e) => set("input_path", e.target.value)}
          />
          <div className="muted small" style={{ marginTop: 4 }}>
            File phải nằm trong thư mục được phép (mặc định: Downloads, Videos, projects/).
          </div>
        </div>

        <div className="row">
          <div className="field">
            <label>Tên job</label>
            <input value={form.title} onChange={(e) => set("title", e.target.value)} />
          </div>
          <div className="field">
            <label>Chữ trên pill thương hiệu</label>
            <input value={form.brand_pill} onChange={(e) => set("brand_pill", e.target.value)} />
          </div>
        </div>

        <div className="field">
          <label>Chủ đề video</label>
          <input
            value={form.topic}
            placeholder="3 sai lầm khi làm chatbot AI bán hàng"
            onChange={(e) => set("topic", e.target.value)}
          />
        </div>

        <div className="field">
          <label>Yêu cầu riêng cho video này (gửi thẳng cho AI director)</label>
          <textarea
            value={form.prompt}
            placeholder="VD: giữ giọng vui, nhấn mạnh phần chi phí, đừng cắt câu chào cuối…"
            onChange={(e) => set("prompt", e.target.value)}
          />
        </div>

        <div className="field">
          <label>Yêu cầu về card</label>
          <input
            value={form.card_plan}
            placeholder="VD: 4 card — 3 sai lầm (badge 1/2/3) + 1 giải pháp (badge 4)"
            onChange={(e) => set("card_plan", e.target.value)}
          />
        </div>

        <div className="row">
          <div className="field">
            <label>Model Whisper</label>
            <select value={form.whisper_model} onChange={(e) => set("whisper_model", e.target.value)}>
              {["tiny", "base", "small", "medium", "large-v2", "large-v3"].map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Ngôn ngữ</label>
            <input value={form.language} onChange={(e) => set("language", e.target.value)} />
          </div>
          <div className="field">
            <label>Tempo</label>
            <input
              type="number" step="0.01" min="1" max="1.2"
              value={form.tempo}
              onChange={(e) => set("tempo", Number(e.target.value))}
            />
          </div>
        </div>

        <div className="field">
          <label>Model kiểm cắt (verifier, tuỳ chọn)</label>
          <input
            value={form.verifier_model}
            placeholder="Để trống = dùng chung model director"
            onChange={(e) => set("verifier_model", e.target.value)}
          />
          <div className="muted small" style={{ marginTop: 4 }}>
            Model này chỉ trả lời giữ/bỏ từng đoạn cắt ở bước Audit — có thể chọn model rẻ/nhanh hơn
            director mà không ảnh hưởng chất lượng dựng cấu trúc/caption.
          </div>
        </div>

        <div className="row" style={{ marginBottom: 14 }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" style={{ width: 16 }} checked={form.bgm}
                   onChange={(e) => set("bgm", e.target.checked)} />
            Nhạc nền
          </label>
          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" style={{ width: 16 }} checked={form.cold_open}
                   onChange={(e) => set("cold_open", e.target.checked)} />
            Cold-open (teaser mở màn)
          </label>
        </div>

        {error && <p className="error-text small" style={{ marginBottom: 10 }}>{error}</p>}

        <button className="primary" disabled={!form.input_path || busy} onClick={submit}>
          {busy ? "Đang tạo…" : "Chạy job"}
        </button>
      </div>

      <div className="card">
        <h2>Kho tài nguyên khả dụng</h2>
        {!resources && <p className="muted">Đang tải…</p>}
        {resources && (
          <>
            <h3>Hiệu ứng âm thanh ({resources.sfx.length})</h3>
            <ul className="small stack">
              {resources.sfx.map((s: any) => (
                <li key={s.name}><b>{s.name}</b> <span className="muted">— {s.use}</span></li>
              ))}
            </ul>
            <h3>Nhạc nền ({resources.bgm.length})</h3>
            <ul className="small stack">
              {resources.bgm.map((b: any) => (
                <li key={b.name}><b>{b.name}</b> <span className="muted">— {b.mood}</span></li>
              ))}
            </ul>
            {resources.warnings?.length > 0 && (
              <>
                <h3>Cảnh báo</h3>
                {resources.warnings.map((w: string, i: number) => (
                  <p key={i} className="warn-text small">{w}</p>
                ))}
              </>
            )}
            <p className="muted small" style={{ marginTop: 12 }}>
              AI director chỉ được phép chọn trong danh sách này — tên file bịa sẽ bị loại ở bước audit.
            </p>
          </>
        )}
      </div>
    </div>
  );
};
