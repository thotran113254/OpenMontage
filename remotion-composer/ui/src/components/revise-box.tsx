import React, { useEffect, useState } from "react";
import { api, JobVersion } from "../api/client";

const EXAMPLES = [
  "Bỏ card thứ 2, giữ nguyên phần còn lại",
  "Đổi nhạc nền sang bgm_lofi_chill.mp3, nhỏ thôi",
  "Caption đoạn đầu ngắn lại, nhấn mạnh cụm “mất khách”",
  "Thêm keyword CHỐT ĐƠN màu xanh lá ở đoạn nói về doanh thu",
];

export const ReviseBox: React.FC<{
  jobId: string;
  busy: boolean;
  onQueued: () => void;
}> = ({ jobId, busy, onQueued }) => {
  const [instruction, setInstruction] = useState("");
  const [versions, setVersions] = useState<JobVersion[]>([]);
  const [current, setCurrent] = useState(0);
  const [error, setError] = useState("");

  const loadVersions = () =>
    api.versions(jobId)
      .then((data) => {
        setVersions(data.versions);
        setCurrent(data.current_version);
      })
      .catch((e) => setError(String(e)));

  useEffect(() => { loadVersions(); }, [jobId, busy]);

  const send = async () => {
    setError("");
    try {
      await api.revise(jobId, instruction);
      setInstruction("");
      onQueued();
    } catch (e) {
      setError(String(e));
    }
  };

  const rollback = async (version: number) => {
    setError("");
    try {
      await api.rollback(jobId, version);
      onQueued();
      loadVersions();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="card">
      <h2>Sửa bằng yêu cầu</h2>
      <p className="muted small" style={{ marginBottom: 8 }}>
        AI chỉ vá đúng chỗ bạn yêu cầu, phần đã duyệt giữ nguyên. Rẻ và nhanh hơn dựng lại —
        xong sẽ tự cắt lại để xem trước, chưa render.
      </p>
      <textarea
        value={instruction}
        placeholder="VD: bỏ card cuối, đổi nhạc êm hơn, caption đoạn mở đầu ngắn lại…"
        onChange={(e) => setInstruction(e.target.value)}
      />
      <div className="row" style={{ marginTop: 8, marginBottom: 10 }}>
        <button className="primary" disabled={busy || instruction.trim().length < 4} onClick={send}>
          Gửi yêu cầu sửa
        </button>
      </div>

      <div className="small muted" style={{ marginBottom: 12 }}>
        Gợi ý:{" "}
        {EXAMPLES.map((example, index) => (
          <button
            key={index}
            className="ghost small"
            style={{ padding: "2px 8px", marginRight: 6, marginTop: 4, fontSize: 11 }}
            onClick={() => setInstruction(example)}
          >
            {example}
          </button>
        ))}
      </div>

      {error && <p className="error-text small">{error}</p>}

      <h3>Lịch sử phiên bản</h3>
      {versions.length === 0 && <p className="muted small">Chưa có phiên bản nào.</p>}
      {versions.slice().reverse().map((version) => (
        <div key={version.version} className="cut" style={{ borderLeftColor: version.is_current ? "var(--info)" : undefined }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <b>v{version.version}</b>
            <span className="badge pending">{version.kind}</span>
            {version.is_current && <span className="badge running">đang dùng</span>}
            <span style={{ flex: 1 }} />
            {!version.is_current && (
              <button className="ghost" style={{ padding: "2px 10px" }}
                      disabled={busy} onClick={() => rollback(version.version)}>
                Dùng lại bản này
              </button>
            )}
          </div>
          {version.instruction && <div className="small" style={{ marginTop: 4 }}>“{version.instruction}”</div>}
          {version.changes && (
            <div className="muted small" style={{ marginTop: 4 }}>
              thêm {version.changes.added} · bỏ {version.changes.removed} · sửa {version.changes.modified}
              {version.changes.top_level_changed?.length
                ? ` · đổi ${version.changes.top_level_changed.join(", ")}`
                : ""}
            </div>
          )}
          {version.usage?.total_tokens && (
            <div className="muted small">{version.usage.total_tokens.toLocaleString("vi-VN")} token</div>
          )}
        </div>
      ))}
    </div>
  );
};
