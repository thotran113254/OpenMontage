import React, { useEffect, useState } from "react";
import { ChatTurn, api } from "../api/client";

/**
 * Revise as a conversation. Replaces `revise-box`'s one-shot request.
 *
 * The difference that matters is history: "bỏ card 2" then "thêm lại card đó"
 * only works if the second turn knows about the first. The server keeps the last
 * three turns and feeds them to the prompt; this panel shows them so the user can
 * see what context the model has.
 *
 * Dry run exists because a patch is cheap to compute and expensive to undo by
 * hand — looking at the diff first is usually the right move.
 */

const EXAMPLES = [
  "Bỏ card thứ 2, giữ nguyên phần còn lại",
  "Đổi nhạc nền sang bgm_lofi_chill.mp3, nhỏ thôi",
  "Caption đoạn đầu ngắn lại, nhấn cụm “mất khách”",
  "Thêm lại card vừa bỏ",
];

export const ChatPanel: React.FC<{
  jobId: string;
  busy: boolean;
  onApplied: () => void;
}> = ({ jobId, busy, onApplied }) => {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [lastDiff, setLastDiff] = useState<unknown>(null);

  const load = () =>
    api.chatHistory(jobId).then((data) => setTurns(data.turns)).catch(() => undefined);

  useEffect(() => {
    void load();
  }, [jobId, busy]);

  const send = async (dryRun: boolean) => {
    setPending(true);
    setError("");
    setLastDiff(null);
    try {
      const result = await api.chat(jobId, message, dryRun);
      setLastDiff(result.diff?.diff ?? null);
      if (!dryRun) {
        setMessage("");
        onApplied();
      }
      setTurns(result.history);
      void load();
    } catch (exception) {
      setError(String(exception).slice(0, 400));
      void load();
    } finally {
      setPending(false);
    }
  };

  const disabled = busy || pending || message.trim().length < 4;

  return (
    <div className="card">
      <h2>Sửa bằng hội thoại</h2>
      <p className="muted small" style={{ marginBottom: 8 }}>
        Ba lượt gần nhất được đưa vào ngữ cảnh, nên bạn nói “cái đó”, “như lúc trước” được.
        AI chỉ vá đúng chỗ yêu cầu; phần đã duyệt giữ nguyên.
      </p>

      {turns.length > 0 && (
        <div className="log" style={{ maxHeight: 220, marginBottom: 10 }}>
          {turns.map((turn, index) => (
            <div key={`${turn.ts}-${index}`} style={{ marginBottom: 6 }}>
              <span className={`badge ${turn.applied ? "running" : "pending"}`}>
                {turn.applied ? `v${turn.to_version}` : turn.dry_run ? "thử" : "lỗi"}
              </span>{" "}
              <b>{turn.message}</b>
              <div className="muted small">
                {turn.error ? turn.error : turn.result}
                {turn.tokens ? ` · ${turn.tokens.toLocaleString("vi-VN")} token` : ""}
              </div>
            </div>
          ))}
        </div>
      )}

      <textarea
        value={message}
        placeholder="VD: bỏ card cuối, đổi nhạc êm hơn, thêm lại card vừa bỏ…"
        onChange={(event) => setMessage(event.target.value)}
      />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" disabled={disabled} onClick={() => void send(false)}>
          {pending ? "Đang sửa…" : "Áp thay đổi"}
        </button>
        <button className="ghost" disabled={disabled} onClick={() => void send(true)}>
          Xem trước (không tạo version)
        </button>
      </div>

      <div className="small muted" style={{ marginTop: 10 }}>
        Gợi ý:{" "}
        {EXAMPLES.map((example) => (
          <button
            key={example}
            className="ghost small"
            style={{ padding: "2px 8px", marginRight: 6, marginTop: 4, fontSize: 11 }}
            onClick={() => setMessage(example)}
          >
            {example}
          </button>
        ))}
      </div>

      {error && <p className="error-text small">{error}</p>}
      {lastDiff !== null && (
        <>
          <h3>Thay đổi đề xuất</h3>
          <pre className="log" style={{ maxHeight: 220 }}>
            {JSON.stringify(lastDiff, null, 2)}
          </pre>
        </>
      )}
    </div>
  );
};
