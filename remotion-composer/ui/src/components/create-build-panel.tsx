import React, { useEffect, useState } from "react";
import { ProjectDetail, api } from "../api/client";
import {
  RUN_MODES,
  RunMode,
  saveBuildPlan,
  stagesForMode,
} from "../lib/pipeline-plan";

export const CreateBuildPanel: React.FC<{
  project: ProjectDetail;
  open: boolean;
  onClose: () => void;
  onCreated: (jobId: string) => void;
}> = ({ project, open, onClose, onCreated }) => {
  const defaults = project.defaults || {};
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState(String(defaults.prompt ?? ""));
  const [topic, setTopic] = useState(String(defaults.topic ?? ""));
  const [cardPlan, setCardPlan] = useState(String(defaults.card_plan ?? ""));
  const [brandPill, setBrandPill] = useState(String(defaults.brand_pill ?? ""));
  const [language, setLanguage] = useState("vi");
  const [tempo, setTempo] = useState(1.06);
  const [bgm, setBgm] = useState(true);
  const [coldOpen, setColdOpen] = useState(true);
  const [asrProvider, setAsrProvider] = useState("elevenlabs_scribe");
  const [whisperModel, setWhisperModel] = useState("medium");
  const [model, setModel] = useState("");
  const [verifierModel, setVerifierModel] = useState("");
  const [autoSharpen, setAutoSharpen] = useState(true);
  const [autoGrade, setAutoGrade] = useState(true);
  const [autoAudio, setAutoAudio] = useState(true);
  const [audioPreset, setAudioPreset] = useState("shotgun");
  const [mode, setMode] = useState<RunMode>("prepare");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [cloudEnabled, setCloudEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setPrompt(String(defaults.prompt ?? ""));
    setTopic(String(defaults.topic ?? ""));
    setCardPlan(String(defaults.card_plan ?? ""));
    setBrandPill(String(defaults.brand_pill ?? ""));
    setMode("prepare");
    setError("");
    api
      .cloudStatus()
      .then((s) => setCloudEnabled(Boolean(s.config?.enabled)))
      .catch(() => setCloudEnabled(null));
  }, [open, project.project_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const meta = RUN_MODES.find((m) => m.id === mode)!;
  const aroll = project.sources.filter((s) => s.role === "aroll").length;

  const submit = async () => {
    if (aroll === 0) {
      setError("Chưa có A-roll (nguồn có lời) — upload nguồn trước.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const options: Record<string, unknown> = {
        prompt,
        topic,
        card_plan: cardPlan,
        brand_pill: brandPill,
        language,
        tempo,
        bgm,
        cold_open: coldOpen,
        asr_provider: asrProvider,
        whisper_model: whisperModel,
        model,
        verifier_model: verifierModel,
        auto_sharpen: autoSharpen,
        auto_grade: autoGrade,
        auto_audio_preset: autoAudio,
        audio_preset: audioPreset,
      };
      const stages = stagesForMode(mode);
      const result = await api.createBuild(project.project_id, {
        title: title.trim() || undefined,
        options,
        stages,
        run: meta.run,
      });
      saveBuildPlan(result.job_id, {
        mode,
        autoEnqueueCloud: meta.autoEnqueueCloud,
        stopBeforeRender: mode === "prepare" || mode === "prepare_and_queue",
        createdAt: Date.now(),
      });
      onCreated(result.job_id);
    } catch (e) {
      setError(String(e).slice(0, 400));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lightbox-overlay" onClick={busy ? undefined : onClose}>
      <div
        className="card cloud-modal create-build-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Dựng bản mới</h2>
          <span className="muted small" style={{ flex: 1 }}>
            Project: {project.title}
          </span>
          <button className="ghost small" disabled={busy} onClick={onClose}>
            Đóng
          </button>
        </div>

        {aroll === 0 && (
          <div className="callout warn">
            <b>Chưa có A-roll.</b> Pipeline neo theo lời nói — upload ít nhất một
            nguồn có tiếng trước khi dựng.
          </div>
        )}

        <h3>1. Nội dung & option</h3>
        <p className="muted small" style={{ marginBottom: 10 }}>
          Để trống chủ đề/card sẽ lấy mặc định project (tab Cấu hình). Prompt riêng
          gửi thẳng cho AI director.
        </p>

        <label className="field">
          Tên bản dựng (tuỳ chọn)
          <input
            value={title}
            placeholder="VD: nhấn phần chi phí — v1"
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>

        <label className="field">
          Prompt riêng cho bản này
          <textarea
            value={prompt}
            placeholder="VD: giữ giọng vui, đừng cắt câu chào cuối, card nhấn ROI…"
            onChange={(e) => setPrompt(e.target.value)}
          />
        </label>

        <div className="row">
          <label className="field">
            Chủ đề
            <input value={topic} onChange={(e) => setTopic(e.target.value)} />
          </label>
          <label className="field">
            Card plan
            <input
              value={cardPlan}
              placeholder="4 card: 3 sai lầm + 1 giải pháp"
              onChange={(e) => setCardPlan(e.target.value)}
            />
          </label>
        </div>

        <div className="row">
          <label className="field">
            Brand pill
            <input value={brandPill} onChange={(e) => setBrandPill(e.target.value)} />
          </label>
          <label className="field">
            Ngôn ngữ
            <input value={language} onChange={(e) => setLanguage(e.target.value)} />
          </label>
          <label className="field">
            Tempo
            <input
              type="number"
              step="0.01"
              min="1"
              max="1.2"
              value={tempo}
              onChange={(e) => setTempo(Number(e.target.value))}
            />
          </label>
        </div>

        <div className="row" style={{ marginBottom: 12 }}>
          <label className="check-inline">
            <input type="checkbox" checked={bgm} onChange={(e) => setBgm(e.target.checked)} />
            Nhạc nền
          </label>
          <label className="check-inline">
            <input
              type="checkbox"
              checked={coldOpen}
              onChange={(e) => setColdOpen(e.target.checked)}
            />
            Cold-open
          </label>
          <label className="check-inline">
            <input
              type="checkbox"
              checked={autoSharpen}
              onChange={(e) => setAutoSharpen(e.target.checked)}
            />
            Auto sharpen
          </label>
          <label className="check-inline">
            <input
              type="checkbox"
              checked={autoGrade}
              onChange={(e) => setAutoGrade(e.target.checked)}
            />
            Auto grade
          </label>
        </div>

        <button
          className="ghost small"
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? "▾ Ẩn nâng cao" : "▸ ASR / model / audio (nâng cao)"}
        </button>

        {showAdvanced && (
          <div className="stack" style={{ marginTop: 10 }}>
            <div className="row">
              <label className="field">
                ASR
                <select
                  value={asrProvider}
                  onChange={(e) => setAsrProvider(e.target.value)}
                >
                  <option value="elevenlabs_scribe">ElevenLabs Scribe (nhanh, cần key)</option>
                  <option value="whisper_local">Whisper local (offline, chậm)</option>
                </select>
              </label>
              <label className="field">
                Whisper fallback
                <select
                  value={whisperModel}
                  onChange={(e) => setWhisperModel(e.target.value)}
                >
                  {["tiny", "base", "small", "medium", "large-v2", "large-v3"].map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="row">
              <label className="field">
                Model director (trống = env)
                <input
                  value={model}
                  placeholder="vd. llg/gemini-3.5-flash"
                  onChange={(e) => setModel(e.target.value)}
                />
              </label>
              <label className="field">
                Model verifier cắt
                <input
                  value={verifierModel}
                  placeholder="trống = dùng model director"
                  onChange={(e) => setVerifierModel(e.target.value)}
                />
              </label>
            </div>
            <div className="row">
              <label className="check-inline">
                <input
                  type="checkbox"
                  checked={autoAudio}
                  onChange={(e) => setAutoAudio(e.target.checked)}
                />
                Auto audio preset
              </label>
              <label className="field">
                Audio preset (khi tắt auto)
                <select
                  value={audioPreset}
                  onChange={(e) => setAudioPreset(e.target.value)}
                  disabled={autoAudio}
                >
                  {["off", "voice", "voice_strong", "shotgun", "shotgun_dry"].map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        )}

        <h3 style={{ marginTop: 16 }}>2. Cách chạy</h3>
        <div className="mode-grid">
          {RUN_MODES.map((item) => (
            <label
              key={item.id}
              className={`mode-card tone-${item.tone} ${mode === item.id ? "selected" : ""}`}
            >
              <input
                type="radio"
                name="run-mode"
                checked={mode === item.id}
                onChange={() => setMode(item.id)}
              />
              <div>
                <b>{item.title}</b>
                <p className="muted small">{item.summary}</p>
              </div>
            </label>
          ))}
        </div>

        <div className={`callout ${meta.tone}`}>
          <b>Cảnh báo — {meta.title}</b>
          <ul>
            {meta.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
            {mode === "prepare_and_queue" && cloudEnabled === false && (
              <li className="warn-text">
                Cloud đang <b>tắt</b> (config). Vẫn xếp lịch được; flush sẽ bị chặn cho đến khi
                bật <code>enabled: true</code>.
              </li>
            )}
            {mode === "full_local" && (
              <li>
                Stages: probe → … → resolve → <b>render</b> → verify — không dừng duyệt.
              </li>
            )}
            {(mode === "prepare" || mode === "prepare_and_queue") && (
              <li>
                Stages dừng sau <b>resolve</b> (có preview). Render / cloud do bạn bấm sau.
              </li>
            )}
          </ul>
        </div>

        {error && <p className="error-text small">{error}</p>}

        <div className="row" style={{ marginTop: 14 }}>
          <button className="ghost" disabled={busy} onClick={onClose}>
            Huỷ
          </button>
          <button
            className="primary"
            disabled={busy || aroll === 0}
            onClick={() => void submit()}
          >
            {busy
              ? "Đang tạo…"
              : meta.run
                ? `Tạo & chạy — ${meta.title.split("(")[0].trim()}`
                : "Tạo job (không chạy)"}
          </button>
        </div>
      </div>
    </div>
  );
};
