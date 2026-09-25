import React, { useEffect, useState } from "react";
import { ChatTurn, api } from "../api/client";
import { VersionHistory } from "./version-history";
import { outcomeGroups, humanizeTurnResult } from "../lib/outcome";
import { OutcomeSummary } from "./outcome-summary";
import { useStickToBottom } from "../lib/use-stick-to-bottom";
import { ScrollToBottomPill } from "./scroll-to-bottom-pill";

/**
 * Revise as a conversation — the only revision workflow on the job page.
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
  embedded?: boolean;
}> = ({ jobId, busy, onApplied, embedded }) => {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [lastDiff, setLastDiff] = useState<unknown>(null);
  const [previewNote, setPreviewNote] = useState("");
  const [sentSeq, setSentSeq] = useState(0);
  const { ref: logRef, contentRef: logContentRef, pinned, pendingCount, onScroll, onKeyDown, scrollToBottom } =
    useStickToBottom<HTMLDivElement, HTMLDivElement>(turns.length);

  const load = () =>
    api.chatHistory(jobId).then((data) => setTurns(data.turns)).catch(() => undefined);

  useEffect(() => {
    void load();
  }, [jobId, busy]);

  useEffect(() => {
    if (!busy && !pending) setPreviewNote("");
  }, [busy, pending]);

  // A message the user just sent should always land at the bottom, even if
  // they'd scrolled up to read history — runs after `turns` re-renders so the
  // log element exists and scrollHeight is up to date.
  useEffect(() => {
    if (sentSeq > 0) scrollToBottom({ smooth: true });
  }, [sentSeq, scrollToBottom]);

  const send = async (dryRun: boolean) => {
    setPending(true);
    setError("");
    setLastDiff(null);
    setPreviewNote("");
    try {
      const result = await api.chat(jobId, message, dryRun);
      setLastDiff(result.diff?.diff ?? null);
      if (!dryRun) {
        setMessage("");
        if (result.preview_queued) {
          setPreviewNote(
            result.version
              ? `Đã lưu v${result.version}. Đang cập nhật preview (cắt lại)…`
              : "Đang cập nhật preview…",
          );
        }
        onApplied();
      }
      setTurns(result.history);
      setSentSeq((n) => n + 1);
      void load();
    } catch (exception) {
      setError(String(exception).slice(0, 400));
      void load();
    } finally {
      setPending(false);
    }
  };

  const disabled = busy || pending || message.trim().length < 4;

  const body = (
    <>
      {!embedded && <h2>Sửa bằng hội thoại</h2>}
      {embedded && (
        <p className="muted small" style={{ marginTop: 0 }}>
          Mỗi lần áp dụng tạo phiên bản mới và tự cắt lại để cập nhật preview.
        </p>
      )}

      {turns.length > 0 && (
        <div className="scroll-pane" style={{ marginBottom: 10 }}>
          <div
            className="log"
            style={{ maxHeight: 220 }}
            ref={logRef}
            tabIndex={0}
            role="log"
            aria-label="Hội thoại sửa"
            onScroll={onScroll}
            onKeyDown={onKeyDown}
          >
            <div ref={logContentRef}>
              {turns.map((turn, index) => {
                const groups = outcomeGroups(turn);
                const hasStructured = groups.done.length > 0 || groups.notDone.length > 0;
                return (
                  <div key={`${turn.ts}-${index}`} style={{ marginBottom: 6 }}>
                    <span className={`badge ${turn.applied ? "running" : "pending"}`}>
                      {turn.applied ? `v${turn.to_version}` : turn.dry_run ? "thử" : "lỗi"}
                    </span>{" "}
                    <b>{turn.message}</b>
                    {turn.error ? (
                      <div className="error-text small">{turn.error}</div>
                    ) : hasStructured ? (
                      <OutcomeSummary {...groups} />
                    ) : (
                      <div className="muted small">{humanizeTurnResult(turn.result)}</div>
                    )}
                    {turn.tokens ? (
                      <div className="muted small">{turn.tokens.toLocaleString("vi-VN")} token</div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
          <ScrollToBottomPill
            visible={!pinned}
            count={pendingCount}
            onClick={() => scrollToBottom({ smooth: true })}
          />
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
        <details>
          <summary>Gợi ý câu lệnh</summary>
          <div style={{ marginTop: 6 }}>
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
        </details>
      </div>

      {error && <p className="error-text small">{error}</p>}
      {previewNote && (
        <div className="callout info small" style={{ marginTop: 8 }}>
          {previewNote}
        </div>
      )}
      {lastDiff !== null && (
        <>
          <h3>Thay đổi đề xuất</h3>
          <pre className="log" style={{ maxHeight: 220 }}>
            {JSON.stringify(lastDiff, null, 2)}
          </pre>
        </>
      )}

      {embedded && (
        <VersionHistory jobId={jobId} busy={busy || pending} onChanged={onApplied} />
      )}
    </>
  );

  return embedded ? body : <div className="card">{body}</div>;
};
