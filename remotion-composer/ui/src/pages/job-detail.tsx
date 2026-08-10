import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, JobDetail, ProgressEvent, subscribeToJob, TimelineProps } from "../api/client";
import { AuditPanel } from "../components/audit-panel";
import { EventInspector } from "../components/event-inspector";
import { LogStream } from "../components/log-stream";
import { LookPreview } from "../components/look-preview";
import { ChatPanel } from "../components/chat-panel";
import { TranscriptEditor, TranscriptWord } from "../components/transcript-editor";
import { StageProgress } from "../components/stage-progress";
import { TimelinePreview } from "../components/timeline-preview";
import { RenderActions } from "../components/render-actions";
import { PipelineStepControls } from "../components/pipeline-step-controls";
import {
  BuildPlan,
  clearBuildPlan,
  loadBuildPlan,
} from "../lib/pipeline-plan";

const ACTIVE_STATUSES = ["queued", "running"];

export const JobDetailPage: React.FC<{ jobId: string }> = ({ jobId }) => {
  const [job, setJob] = useState<JobDetail>();
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [draft, setDraft] = useState<TimelineProps | null>(null);
  const [seekTo, setSeekTo] = useState<number>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [spine, setSpine] = useState<TranscriptWord[]>([]);
  const [cutRanges, setCutRanges] = useState<[number, number][]>([]);
  const [plan, setPlan] = useState<BuildPlan | null>(() => loadBuildPlan(jobId));
  const [planNote, setPlanNote] = useState("");
  const autoEnqueued = useRef(false);

  const reload = useCallback(
    () => api.getJob(jobId).then((detail) => {
      setJob(detail);
      setDraft((current) => current ?? detail.props);
    }).catch((e) => setError(String(e))),
    [jobId],
  );

  useEffect(() => {
    setPlan(loadBuildPlan(jobId));
    autoEnqueued.current = false;
    setPlanNote("");
  }, [jobId]);

  useEffect(() => { reload(); }, [reload]);

  // The transcript is fetched separately: it is large, and the job detail
  // endpoint is polled on every stage boundary.
  useEffect(() => {
    api.getSpine(jobId)
      .then((data) => {
        setSpine(data.words);
        setCutRanges(data.cut_ranges);
      })
      .catch(() => undefined);
  }, [jobId, job?.current_version]);

  // live progress; a stage boundary is also when new artifacts appear
  useEffect(() => {
    const unsubscribe = subscribeToJob(jobId, (event) => {
      setEvents((current) => [...current, event]);
      if (["stage_end", "stage_failed", "job_end", "stream_end"].includes(event.type)) {
        setDraft(null);
        reload();
      }
    });
    return unsubscribe;
  }, [jobId, reload]);

  // After prepare (+ auto queue): once props exist and we are not busy, enqueue cloud once.
  useEffect(() => {
    if (!job || !plan?.autoEnqueueCloud || autoEnqueued.current) return;
    if (ACTIVE_STATUSES.includes(job.status)) return;
    if (!job.props) return;
    autoEnqueued.current = true;
    api
      .cloudEnqueue(jobId, "auto sau prepare")
      .then((res) => {
        setPlanNote(res.message + " — mở Lịch render khi muốn flush batch.");
        clearBuildPlan(jobId);
        setPlan((p) => (p ? { ...p, autoEnqueueCloud: false } : p));
      })
      .catch((e) => setPlanNote(`Xếp lịch cloud thất bại: ${String(e).slice(0, 200)}`));
  }, [job, plan, jobId]);

  if (error) return <div className="card error-text">{error}</div>;
  if (!job) return <div className="card muted">Đang tải…</div>;

  const renderPercent = [...events].reverse().find((e) => e.stage === "render" && e.percent)?.percent;
  const busy = ACTIVE_STATUSES.includes(job.status);
  const dirty = draft && JSON.stringify(draft) !== JSON.stringify(job.props);
  const timelineViews = job.verify_report?.timeline_views ?? [];
  const hasProps = Boolean(job.props);
  const readyForRenderGate = hasProps && !job.has_final && !busy;
  const resolveDone = job.stages?.resolve?.status === "completed";
  const renderPending = !job.stages?.render?.status || job.stages.render.status === "pending";
  const stopGate = readyForRenderGate && (resolveDone || plan?.stopBeforeRender) && renderPending;

  const saveDraft = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await api.saveProps(jobId, draft);
      setDraft(null);
      await reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const opts = job.options || {};

  return (
    <>
      <div className="card">
        <h2>
          {job.title || job.job_id} <span className={`badge ${job.status}`}>{job.status}</span>{" "}
          <span className="muted small">v{job.current_version} · ${job.cost_usd ?? 0}</span>
        </h2>

        {stopGate && (
          <div className="callout ok" style={{ marginBottom: 12 }}>
            <b>Đã dừng trước render — sẵn sàng duyệt / gom batch</b>
            <p className="small" style={{ marginTop: 4 }}>
              Preview Remotion Player bên dưới khớp component sẽ render. Bạn có thể sửa event/chat,
              rồi chọn: <b>Render MP4 local</b> (máy này, $0) · <b>Xếp lịch cloud</b> (gom batch, chưa
              tốn tiền) · <b>Cloud ngay</b> (cần xác nhận chi phí).
            </p>
            <p className="warn-text small" style={{ marginTop: 6 }}>
              ⚠ Render local chiếm CPU lâu. Cloud đẩy footage ra máy lạ (Vast + R2) — luôn có
              modal xác nhận trước khi thuê.
            </p>
          </div>
        )}

        {planNote && (
          <div className="callout info" style={{ marginBottom: 12 }}>
            {planNote}{" "}
            <a href="#/cloud">Mở Lịch render →</a>
          </div>
        )}

        {job.status === "created" && (
          <div className="callout info" style={{ marginBottom: 12 }}>
            <b>Job mới tạo — chưa chạy stage.</b> Dùng “Chạy theo bước” bên dưới: ① LLM + encode
            trước, ② Render khi đã duyệt.
          </div>
        )}

        <StageProgress stages={job.stages} renderPercent={renderPercent} />

        <details className="options-summary" style={{ marginTop: 12 }}>
          <summary className="muted small">Option job (đang áp dụng)</summary>
          <div className="options-grid small">
            <span>topic: {String(opts.topic || "—")}</span>
            <span>card: {String(opts.card_plan || "—")}</span>
            <span>ASR: {String(opts.asr_provider || "—")}</span>
            <span>tempo: {String(opts.tempo ?? "—")}</span>
            <span>bgm: {String(opts.bgm ?? "—")}</span>
            <span>cold_open: {String(opts.cold_open ?? "—")}</span>
            <span>prompt: {String(opts.prompt || "—").slice(0, 120)}</span>
          </div>
        </details>

        <div className="row" style={{ marginTop: 14 }}>
          <button disabled={busy} onClick={() => api.runStages(jobId, { stages: ["direct"], use_cache: false }).then(reload)}>
            Dựng lại khung (AI)
          </button>
          <button disabled={busy} onClick={() => api.runStages(jobId, { stages: ["resolve"], use_cache: false }).then(reload)}>
            Cắt lại
          </button>
          <button
            disabled={busy}
            title="Chạy hết chain, tự sửa MỘT lần theo remedy đã biết nếu verify fail. Lỗi lạ thì dừng và báo."
            onClick={() => api.autopilot(jobId).then(reload)}
          >
            🤖 Autopilot
          </button>
          {busy && <button className="ghost error-text" onClick={() => api.cancel(jobId).then(reload)}>Huỷ</button>}
        </div>

        <div style={{ marginTop: 14 }}>
          <PipelineStepControls
            jobId={jobId}
            busy={busy}
            hasProps={hasProps}
            hasFinal={job.has_final}
            onQueued={reload}
          />
        </div>

        <div style={{ marginTop: 14 }}>
          <RenderActions
            jobId={jobId}
            busy={busy}
            hasProps={hasProps}
            onLocalRender={(scale) => {
              if (
                !window.confirm(
                  "Render MP4 local sẽ chạy Remotion trên máy này (có thể 10–30+ phút, full CPU). Tiếp tục?",
                )
              ) {
                return;
              }
              void api.render(jobId, scale).then(reload);
            }}
          />
        </div>
      </div>

      <div className="split">
        <div>
          <div className="card">
            <h2>Xem trước (đúng component sẽ render)</h2>
            {draft || job.props ? (
              <TimelinePreview
                props={(draft ?? job.props)!}
                mediaBase={job.media_base}
                seekTo={seekTo}
              />
            ) : (
              <p className="muted">Chưa có bản dựng — chờ stage “Cắt + ghép” xong.</p>
            )}
            {dirty && (
              <div className="row" style={{ marginTop: 12 }}>
                <button className="primary" disabled={saving} onClick={saveDraft}>
                  {saving ? "Đang lưu…" : "Lưu bản chỉnh thành phiên bản mới"}
                </button>
                <button className="ghost" onClick={() => setDraft(null)}>Hoàn tác</button>
              </div>
            )}
          </div>

          {job.has_final && (
            <div className="card">
              <h2>Bản render cuối</h2>
              <video
                src={`${job.media_base}final.mp4`}
                controls
                style={{ width: "100%", maxWidth: 380, borderRadius: 12, display: "block", margin: "0 auto" }}
              />
            </div>
          )}

          <LookPreview
            jobId={jobId}
            options={job.options}
            probe={job.probe}
            hasProps={Boolean(job.props)}
            busy={busy}
            onApplied={reload}
          />

          <div className="card">
            <h2>Nhật ký tiến độ</h2>
            <LogStream events={events} />
          </div>
        </div>

        <div>
          {(draft ?? job.props) && (
            <div className="card">
              <h2>Timeline — sửa trực tiếp</h2>
              <p className="muted small" style={{ marginBottom: 10 }}>
                Sửa ở đây không gọi AI và không render lại: thay đổi hiện ngay trên khung xem trước.
              </p>
              <EventInspector
                events={(draft ?? job.props)!.events}
                onChange={(nextEvents) => setDraft({ ...(draft ?? job.props)!, events: nextEvents })}
                onSeek={setSeekTo}
              />
            </div>
          )}

          {spine.length > 0 && (
            <div className="card">
              <h2>Lời nói</h2>
              <p className="muted small" style={{ marginBottom: 10 }}>
                Đây là giao diện chính: hệ thống nghĩ theo câu nói, không theo frame.
                Bấm một từ để nhảy tới đúng chỗ đó trên bản dựng.
              </p>
              <TranscriptEditor
                words={spine}
                onSeek={setSeekTo}
                cutRanges={cutRanges}
                onMarkRange={(from, to, action) =>
                  void api.chat(
                    jobId,
                    action === "cut"
                      ? `Cắt bỏ đoạn từ chỉ số ${from} đến ${to}`
                      : `Giữ lại đoạn từ chỉ số ${from} đến ${to}, đừng cắt`,
                  ).then(reload)}
              />
            </div>
          )}

          <ChatPanel jobId={jobId} busy={busy} onApplied={reload} />

          {timelineViews.length > 0 && (
            <div className="card">
              <h2>Mối nối đáng soi</h2>
              <p className="muted small" style={{ marginBottom: 10 }}>
                Dải frame + dạng sóng + nhãn từ quanh mối cắt. Vạch đỏ là điểm nối —
                nó nên rơi vào chỗ im lặng, không vào giữa một từ.
              </p>
              {timelineViews.map((view) => (
                <div key={view.path} style={{ marginBottom: 12 }}>
                  <div className="small" style={{ marginBottom: 4 }}>
                    <b>{view.at.toFixed(2)}s</b> — {view.reasons.join("; ")}
                  </div>
                  <img
                    src={`${job.media_base}${view.path.split("/").pop()}`}
                    alt={`mối nối ${view.at}`}
                    style={{ width: "100%", borderRadius: 8 }}
                  />
                </div>
              ))}
            </div>
          )}

          <AuditPanel audit={job.audit_report} verify={job.verify_report} mediaBase={job.media_base} />
        </div>
      </div>
    </>
  );
};
