import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, JobDetail, ProgressEvent, subscribeToJob, TimelineProps } from "../api/client";
import { AuditPanel } from "../components/audit-panel";
import { EventInspector } from "../components/event-inspector";
import { LogStream } from "../components/log-stream";
import { LookPreview } from "../components/look-preview";
import { ChatPanel } from "../components/chat-panel";
import { TranscriptEditor, TranscriptWord } from "../components/transcript-editor";
import { StageProgress } from "../components/stage-progress";
import { TimelinePreview } from "../components/timeline-preview";
import { RenderActions } from "../components/render-actions";
import { CoverPanel } from "../components/cover-panel";
import { RunChecklist } from "../components/run-checklist";
import { FlowSteps, FlowState } from "../components/flow-steps";
import { VersionHistory } from "../components/version-history";
import {
  BuildPlan,
  PREPARE_STAGES,
  clearBuildPlan,
  loadBuildPlan,
} from "../lib/pipeline-plan";
import { statusLabel } from "../lib/status";

const ACTIVE_STATUSES = ["queued", "running"];

type Pane = "run" | "chat" | "words" | "edit" | "cover";
type Watch = "final" | "preview";

const PANES: [Pane, string][] = [
  ["run", "Chạy"],
  ["chat", "Chat"],
  ["words", "Lời"],
  ["edit", "Timeline"],
  ["cover", "Ảnh"],
];

export const JobDetailPage: React.FC<{ jobId: string }> = ({ jobId }) => {
  const [job, setJob] = useState<JobDetail>();
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [draft, setDraft] = useState<TimelineProps | null>(null);
  const [seekTo, setSeekTo] = useState<number>();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [spine, setSpine] = useState<TranscriptWord[]>([]);
  const [cutRanges, setCutRanges] = useState<[number, number][]>([]);
  const [cutPending, setCutPending] = useState(false);
  const [cutError, setCutError] = useState("");
  const [plan, setPlan] = useState<BuildPlan | null>(() => loadBuildPlan(jobId));
  const [planNote, setPlanNote] = useState("");
  const [pane, setPane] = useState<Pane>("run");
  const [watch, setWatch] = useState<Watch>("preview");
  const autoEnqueued = useRef(false);
  const paneInit = useRef(false);

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
    setPane("run");
    setWatch("preview");
    paneInit.current = false;
  }, [jobId]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    if (!job || paneInit.current) return;
    paneInit.current = true;
    // Once a preview exists the next thing to do is almost always to revise
    // it, not to re-run stages — only default to the run tab before that.
    setPane(job.props ? "chat" : "run");
  }, [job]);

  useEffect(() => {
    if (job?.has_final) setWatch("final");
  }, [job?.has_final, jobId]);

  useEffect(() => {
    api.getSpine(jobId)
      .then((data) => {
        setSpine(data.words);
        setCutRanges(data.cut_ranges);
      })
      .catch(() => undefined);
  }, [jobId, job?.current_version]);

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
  const showStrip = busy || job.status === "failed" || job.status === "created";
  const opts = job.options || {};
  const liveProps = draft ?? job.props;
  const showFinal = watch === "final" && job.has_final;
  const previewStale = !busy && job.current_version > 0 && !hasProps;

  const stepState = (done: boolean, current: boolean): FlowState =>
    done ? "done" : current ? (busy ? "wait" : "now") : "todo";
  const flow = [
    { id: "cut", label: "Cắt xem trước", state: stepState(hasProps, !hasProps) },
    { id: "review", label: "Duyệt", state: stepState(job.has_final, hasProps && !job.has_final) },
    { id: "export", label: "Xuất MP4", state: stepState(job.has_final, hasProps && !job.has_final) },
  ];

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

  const exportLocal = (scale: number) => {
    if (
      !window.confirm(
        "Xuất MP4 sẽ chạy Remotion trên máy này (có thể 10–30+ phút, chiếm CPU). Tiếp tục?",
      )
    ) {
      return;
    }
    void api.render(jobId, scale).then(reload);
  };

  const seek = (seconds: number) => {
    setWatch("preview");
    setSeekTo(seconds);
  };

  const refreshPreview = () => {
    void api.runStages(jobId, { stages: ["audit", "resolve"], use_cache: false }).then(reload);
  };

  const prepare = () => {
    void api.runStages(jobId, { stages: [...PREPARE_STAGES], use_cache: true }).then(reload);
  };

  // Deterministic mark-cut/mark-keep — no LLM in the loop, so the index the
  // user selected is exactly the index that gets cut (see H3 in the review:
  // the old path sent free text through the LLM and trusted its echo back).
  const markRange = async (from: number, to: number, action: "cut" | "keep") => {
    if (cutPending || busy) return;
    setCutPending(true);
    setCutError("");
    try {
      await api.cuts(jobId, action === "cut" ? { cut: [[from, to]] } : { keep: [[from, to]] });
      await reload();
    } catch (e) {
      setCutError(
        e instanceof ApiError && e.code === "job_busy"
          ? "Job đang chạy, đợi xong rồi đánh dấu lại."
          : String(e).slice(0, 280),
      );
    } finally {
      setCutPending(false);
    }
  };

  return (
    <div className="studio">
      <header className="studio-head">
        <a
          className="studio-back"
          href={job.project_id ? `#/project/${job.project_id}` : "#/jobs"}
        >
          {job.project_id ? "← Project" : "← Bản dựng"}
        </a>
        <h1>{job.title || job.job_id}</h1>
        <span className={`badge ${job.status}`}>{statusLabel(job.status)}</span>
        <VersionHistory
          jobId={jobId}
          busy={busy}
          onChanged={reload}
          compact
        />
        <span className="studio-head-spacer" />
        {busy && (
          <button className="ghost error-text" onClick={() => api.cancel(jobId).then(reload)}>
            Huỷ
          </button>
        )}
        {job.has_final && (
          <a className="button-link" href={`${job.media_base}final.mp4`} download="final.mp4">
            Tải MP4
          </a>
        )}
        {!busy && hasProps && !job.has_final && (
          <button className="primary" onClick={() => exportLocal(1)}>
            Xuất MP4
          </button>
        )}
        {!busy && !hasProps && (
          <button className="primary" onClick={prepare}>
            Cắt & xem trước
          </button>
        )}
      </header>

      <FlowSteps steps={flow} />

      {showStrip && (
        <StageProgress stages={job.stages} renderPercent={renderPercent} />
      )}

      {planNote && (
        <div className="callout info" style={{ marginBottom: 12 }}>
          {planNote}{" "}
          <a href="#/cloud">Mở Lịch render →</a>
        </div>
      )}

      <div className="studio-body">
        <aside className="studio-player">
          {job.has_final && hasProps && (
            <div className="watch-switch" role="tablist" aria-label="Chọn bản xem">
              <button
                type="button"
                role="tab"
                className={watch === "final" ? "active" : ""}
                aria-selected={watch === "final"}
                onClick={() => setWatch("final")}
              >
                Bản xuất
              </button>
              <button
                type="button"
                role="tab"
                className={watch === "preview" ? "active" : ""}
                aria-selected={watch === "preview"}
                onClick={() => setWatch("preview")}
              >
                Xem trước
              </button>
            </div>
          )}

          {showFinal ? (
            <div className="player-wrap">
              <div className="player-shell">
                <video
                  src={`${job.media_base}final.mp4`}
                  controls
                  playsInline
                  style={{ width: "100%", height: "100%", objectFit: "contain" }}
                />
              </div>
            </div>
          ) : liveProps ? (
            <TimelinePreview
              props={liveProps}
              mediaBase={job.media_base}
              seekTo={seekTo}
            />
          ) : (
            <div className="player-wrap">
              <div className="player-shell player-empty">
                <p>
                  {job.status === "created"
                    ? "Chưa cắt. Bấm Cắt & xem trước."
                    : busy
                      ? "Đang dựng…"
                      : "Chưa có bản xem."}
                </p>
              </div>
            </div>
          )}

          {dirty && (
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" disabled={saving} onClick={() => void saveDraft()}>
                {saving ? "Đang lưu…" : "Lưu chỉnh sửa"}
              </button>
              <button className="ghost" onClick={() => setDraft(null)}>Hoàn tác</button>
            </div>
          )}

          {previewStale && (
            <div className="callout warn" style={{ marginTop: 10 }}>
              Bản <b>v{job.current_version}</b> chưa có preview.
              {busy ? (
                " Đang cắt lại…"
              ) : (
                <>
                  {" "}
                  <button className="ghost small" type="button" onClick={refreshPreview}>
                    Cập nhật preview
                  </button>
                </>
              )}
            </div>
          )}

          {hasProps && !job.has_final && !busy && (
            <div className="callout info" style={{ marginTop: 10 }}>
              Đây là <b>xem trước</b> — chưa phải file cuối. Ổn thì bấm <b>Xuất MP4</b> góc trên.
            </div>
          )}
          {job.has_final && (
            <div className="callout ok" style={{ marginTop: 10 }}>
              Đã có MP4. Dùng nút <b>Tải MP4</b> hoặc tab Bản xuất.
            </div>
          )}
        </aside>

        <section className="studio-inspector">
          <div className="studio-tabs" role="tablist">
            {PANES.map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                className={`tab ${pane === key ? "active" : ""}`}
                aria-selected={pane === key}
                onClick={() => setPane(key)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="pane" role="tabpanel">
            {pane === "chat" && (
              <ChatPanel jobId={jobId} busy={busy} onApplied={reload} embedded />
            )}

            {pane === "words" && (
              spine.length > 0 ? (
                <div className="stack">
                  <TranscriptEditor
                    words={spine}
                    onSeek={seek}
                    cutRanges={cutRanges}
                    disabled={busy || cutPending}
                    onMarkRange={(from, to, action) => void markRange(from, to, action)}
                  />
                  {cutPending && <p className="muted small">Đang lưu đánh dấu…</p>}
                  {cutError && <p className="error-text small">{cutError}</p>}
                </div>
              ) : (
                <p className="muted">Chưa có lời — chờ bước tách lời xong.</p>
              )
            )}

            {pane === "edit" && (
              liveProps ? (
                <EventInspector
                  events={liveProps.events}
                  onChange={(nextEvents) => setDraft({ ...liveProps, events: nextEvents })}
                  onSeek={seek}
                />
              ) : (
                <p className="muted">Chưa có timeline.</p>
              )
            )}

            {pane === "cover" && (
              <CoverPanel job={job} busy={busy} onQueued={reload} embedded />
            )}

            {pane === "run" && (
              <div className="stack">
                <RunChecklist
                  jobId={jobId}
                  busy={busy}
                  hasProps={hasProps}
                  hasFinal={job.has_final}
                  onQueued={reload}
                />

                <details className="options-summary">
                  <summary>Tuỳ chọn đang dùng</summary>
                  <div className="options-grid small">
                    <span>topic: {String(opts.topic || "—")}</span>
                    <span>ASR: {String(opts.asr_provider || "—")}</span>
                    <span>tempo: {String(opts.tempo ?? "—")}</span>
                    <span>bgm: {String(opts.bgm ?? "—")}</span>
                    <span>cold_open: {String(opts.cold_open ?? "—")}</span>
                    {job.cost_usd ? <span>chi phí: ${job.cost_usd}</span> : null}
                  </div>
                </details>

                <details className="options-summary">
                  <summary>Xuất thêm / cloud</summary>
                  <div style={{ marginTop: 10 }}>
                    <RenderActions
                      jobId={jobId}
                      busy={busy}
                      hasProps={hasProps}
                      onLocalRender={exportLocal}
                    />
                  </div>
                </details>

                <details className="options-summary">
                  <summary>Thử màu &amp; tiếng</summary>
                  <div style={{ marginTop: 10 }}>
                    <LookPreview
                      jobId={jobId}
                      options={job.options}
                      probe={job.probe}
                      hasProps={Boolean(job.props)}
                      busy={busy}
                      onApplied={reload}
                    />
                  </div>
                </details>

                <details className="options-summary">
                  <summary>Tiến độ từng bước</summary>
                  <div style={{ marginTop: 10 }}>
                    <StageProgress stages={job.stages} renderPercent={renderPercent} compact={false} />
                  </div>
                </details>

                <details className="options-summary">
                  <summary>Nhật ký</summary>
                  <div style={{ marginTop: 10 }}>
                    <LogStream events={events} />
                  </div>
                </details>

                {timelineViews.length > 0 && (
                  <details className="options-summary">
                    <summary>Mối nối đáng soi ({timelineViews.length})</summary>
                    <div style={{ marginTop: 10 }}>
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
                  </details>
                )}

                <details className="options-summary">
                  <summary>Kiểm duyệt</summary>
                  <div style={{ marginTop: 10 }}>
                    <AuditPanel audit={job.audit_report} verify={job.verify_report} mediaBase={job.media_base} />
                  </div>
                </details>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
};
