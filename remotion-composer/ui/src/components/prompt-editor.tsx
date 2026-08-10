import React, { useEffect, useState } from "react";
import { AbEstimate, AbResult, PromptDetail, api } from "../api/client";

/**
 * Two columns: the template on the left, what will ACTUALLY be sent on the right.
 *
 * The rendered column is the reason this screen exists. A template with
 * `{{spine}}` in it tells nobody anything; seeing the real 4000 words that go out
 * is what makes a problem obvious.
 *
 * A/B shows its estimated cost before it runs, because the whole objection to A/B
 * is that it doubles a director call — that number has to be visible rather than
 * discovered afterwards.
 */
export const PromptEditor: React.FC<{ jobId?: string }> = ({ jobId }) => {
  const [ids, setIds] = useState<string[]>([]);
  const [promptId, setPromptId] = useState("structure");
  const [detail, setDetail] = useState<PromptDetail>();
  const [body, setBody] = useState("");
  const [rendered, setRendered] = useState("");
  const [renderError, setRenderError] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [diff, setDiff] = useState("");
  const [versionB, setVersionB] = useState("");
  const [estimate, setEstimate] = useState<AbEstimate>();
  const [results, setResults] = useState<AbResult[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listPrompts().then((entries) => setIds(entries.map((entry) => entry.id)))
      .catch(() => undefined);
  }, []);

  const load = (id: string, version?: string) => {
    setMessage("");
    setWarnings([]);
    setDiff("");
    api.getPrompt(id, version)
      .then((data) => {
        setDetail(data);
        setBody(data.body);
      })
      .catch((exception) => setMessage(String(exception)));
  };

  useEffect(() => {
    load(promptId);
  }, [promptId]);

  useEffect(() => {
    if (!jobId || !detail) {
      setRendered("");
      return;
    }
    setRenderError("");
    api.previewPrompt(promptId, jobId, detail.version)
      .then(setRendered)
      .catch((exception) => {
        setRendered("");
        setRenderError(String(exception).slice(0, 300));
      });
  }, [promptId, jobId, detail?.version]);

  useEffect(() => {
    if (!jobId) return;
    api.abResults("structure", jobId).then(setResults).catch(() => undefined);
  }, [jobId, busy]);

  const save = async () => {
    setBusy(true);
    setMessage("");
    try {
      const result = await api.savePromptOverride(promptId, { body, note });
      setWarnings(result.warnings || []);
      setMessage(`Đã lưu thành ${result.version} và đặt làm bản đang dùng.`);
      load(promptId, result.version);
    } catch (exception) {
      setMessage(String(exception).slice(0, 300));
    } finally {
      setBusy(false);
    }
  };

  const showDiff = async (version: string) => {
    setDiff(await api.promptDiff(promptId, version).catch((e) => String(e)));
  };

  const runAb = async () => {
    if (!jobId || !versionB) return;
    setBusy(true);
    setMessage("");
    try {
      await api.runAb("structure", {
        job_id: jobId, version_a: detail?.current ?? "v1", version_b: versionB,
      });
      setMessage("A/B đang chạy — theo dõi trong log tiến độ của job.");
    } catch (exception) {
      setMessage(String(exception).slice(0, 300));
    } finally {
      setBusy(false);
    }
  };

  const estimateAb = async () => {
    if (!jobId || !versionB) return;
    setEstimate(await api.estimateAb("structure", jobId, detail?.current ?? "v1", versionB)
      .catch(() => undefined));
  };

  return (
    <div className="stack">
      <div className="card">
        <div className="row" style={{ alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <label className="field small" style={{ minWidth: 160 }}>
            Prompt
            <select value={promptId} onChange={(event) => setPromptId(event.target.value)}>
              {ids.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </select>
          </label>
          <label className="field small" style={{ minWidth: 130 }}>
            Bản
            <select
              value={detail?.version ?? ""}
              onChange={(event) => load(promptId, event.target.value)}
            >
              {(detail?.versions ?? []).map((row) => (
                <option key={row.version} value={row.version}>
                  {row.version} · {row.source}
                  {row.is_current ? " · đang dùng" : ""}
                </option>
              ))}
            </select>
          </label>
          <span className="muted small">
            Placeholder: {(detail?.placeholders ?? []).join(", ") || "—"}
          </span>
        </div>
      </div>

      <div className="split">
        <div className="card">
          <h3>Template</h3>
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            style={{ minHeight: 420, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
          />
          <label className="field small">
            Ghi chú cho bản mới
            <input value={note} onChange={(event) => setNote(event.target.value)}
                   placeholder="VD: thử caption ngắn hơn" />
          </label>
          <div className="row">
            <button className="primary" disabled={busy || !body.trim()} onClick={save}>
              Lưu thành bản mới
            </button>
            {detail && detail.version !== "v1" && (
              <>
                <button className="ghost" onClick={() => void showDiff(detail.version)}>
                  So với v1
                </button>
                <button
                  className="ghost"
                  disabled={busy}
                  onClick={async () => {
                    if (!window.confirm(`Xoá override ${promptId}.${detail.version}?`)) return;
                    await api.deletePromptOverride(promptId, detail.version);
                    load(promptId);
                  }}
                >
                  Xoá override
                </button>
              </>
            )}
          </div>
          {message && <p className="small" style={{ marginTop: 8 }}>{message}</p>}
          {warnings.map((warning) => (
            <p key={warning} className="warn-text small">{warning}</p>
          ))}
        </div>

        <div className="card">
          <h3>Prompt thật sẽ gửi</h3>
          {!jobId && (
            <p className="muted small">
              Mở từ một job để xem prompt đã render với xương sống thật.
            </p>
          )}
          {renderError && <p className="warn-text small">{renderError}</p>}
          <pre className="log" style={{ maxHeight: 460, whiteSpace: "pre-wrap" }}>
            {rendered || "—"}
          </pre>
          {rendered && (
            <p className="muted small">
              {rendered.length.toLocaleString("vi-VN")} ký tự (~
              {Math.round(rendered.length / 2.2).toLocaleString("vi-VN")} token)
            </p>
          )}
        </div>
      </div>

      {diff && (
        <div className="card">
          <h3>Khác gì so với v1</h3>
          <pre className="log" style={{ maxHeight: 300 }}>{diff}</pre>
        </div>
      )}

      {promptId === "structure" && (
        <div className="card">
          <h3>A/B hai bản trên cùng xương sống</h3>
          <p className="muted small">
            Không render — so ở tầng spec + audit. Kết luận bằng luật cơ học, không hỏi model:
            đủ card theo brief &gt; phủ caption &gt; caption quá dài &gt; keyword bị card che &gt;
            cut bị verifier loại.
          </p>
          <div className="row" style={{ alignItems: "flex-end", gap: 10, flexWrap: "wrap" }}>
            <div className="field small" style={{ minWidth: 120 }}>
              Bản A
              <input value={detail?.current ?? "v1"} readOnly />
            </div>
            <label className="field small" style={{ minWidth: 120 }}>
              Bản B
              <select value={versionB} onChange={(event) => setVersionB(event.target.value)}>
                <option value="">chọn bản…</option>
                {(detail?.versions ?? [])
                  .filter((row) => row.version !== (detail?.current ?? "v1"))
                  .map((row) => (
                    <option key={row.version} value={row.version}>{row.version}</option>
                  ))}
              </select>
            </label>
            <button className="ghost" disabled={!jobId || !versionB} onClick={estimateAb}>
              Ước chi phí
            </button>
            <button className="primary" disabled={busy || !jobId || !versionB} onClick={runAb}>
              Chạy A/B
            </button>
          </div>
          {estimate && (
            <p className="warn-text small" style={{ marginTop: 8 }}>
              Dự kiến {estimate.calls} lượt gọi, ~
              {estimate.estimated_tokens_in.toLocaleString("vi-VN")} token vào
              {estimate.estimated_cost_usd ? ` · ~$${estimate.estimated_cost_usd}` : ""}.{" "}
              {estimate.note}
            </p>
          )}

          {results.map((result) => (
            <div key={result.created_at} className="cut" style={{ marginTop: 10 }}>
              <b>
                {result.verdict.winner === "tie" ? "Ngang nhau" : `Thắng: ${result.verdict.winner}`}
              </b>
              <div className="small">{result.verdict.reason}</div>
              <table className="small" style={{ marginTop: 8, width: "100%" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left" }} />
                    <th>card</th>
                    <th>phủ caption</th>
                    <th>caption &gt;9 từ</th>
                    <th>keyword bị che</th>
                    <th>cut bị loại</th>
                    <th>token</th>
                  </tr>
                </thead>
                <tbody>
                  {([["a", result.a], ["b", result.b]] as const).map(([label, branch]) => (
                    <tr key={label}>
                      <td>
                        <b>{label}</b> {branch.version}
                      </td>
                      <td style={{ textAlign: "center" }}>
                        {branch.cards}
                        {result.card_goal !== null && `/${result.card_goal}`}
                      </td>
                      <td style={{ textAlign: "center" }}>
                        {Math.round(branch.caption_coverage * 100)}%
                      </td>
                      <td style={{ textAlign: "center" }}>{branch.captions_over_9w}</td>
                      <td style={{ textAlign: "center" }}>{branch.keyword_in_card}</td>
                      <td style={{ textAlign: "center" }}>
                        {branch.cuts_rejected_by_verifier}/{branch.cuts_proposed}
                      </td>
                      <td style={{ textAlign: "center" }}>
                        {branch.tokens.in.toLocaleString("vi-VN")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
