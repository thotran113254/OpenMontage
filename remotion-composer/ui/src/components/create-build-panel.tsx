import React, { useEffect, useState } from "react";
import { EditStyle, ProjectDetail, api } from "../api/client";
import { FlowSteps } from "./flow-steps";
import { StyleSelect } from "./style-select";
import { CreateBuildAdvanced } from "./create-build-advanced";
import { RUN_MODES, saveBuildPlan, stagesForMode, RunMode } from "../lib/pipeline-plan";

const SIMPLE_RUN_MODES = RUN_MODES.filter((m) =>
  m.id === "prepare" || m.id === "full_local" || m.id === "create_only",
);

type CutLevel = "light" | "normal" | "tight";

const CUT_LEVELS: { id: CutLevel; label: string; hint: string }[] = [
  { id: "light", label: "Cắt nhẹ", hint: "Giữ nhiều, chỉ bỏ chỗ thừa rõ ràng" },
  { id: "normal", label: "Cắt vừa", hint: "Mặc định — cân bằng giữ/bỏ" },
  { id: "tight", label: "Cắt kỹ", hint: "Bỏ thêm lặp từ/vấp nhẹ đã qua kiểm" },
];

const num = (value: unknown, fallback: number) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const toCutLevel = (value: unknown, fallback: CutLevel = "normal"): CutLevel =>
  value === "light" || value === "normal" || value === "tight" ? value : fallback;

export const CreateBuildPanel: React.FC<{
  project: ProjectDetail;
  open: boolean;
  initialSourceIds?: string[];
  onClose: () => void;
  onCreated: (jobId: string, allIds?: string[]) => void;
}> = ({ project, open, initialSourceIds, onClose, onCreated }) => {
  const defaults = project.defaults || {};
  const arollSources = project.sources.filter((s) => s.role === "aroll");
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState(String(defaults.prompt ?? ""));
  const [topic, setTopic] = useState(String(defaults.topic ?? ""));
  const [bgm, setBgm] = useState(true);
  const [bgmName, setBgmName] = useState(String(defaults.bgm_name ?? ""));
  const [bgmVolume, setBgmVolume] = useState(num(defaults.bgm_volume, 0.16));
  const [styleId, setStyleId] = useState(String(defaults.style_id ?? ""));
  const [styleListKey, setStyleListKey] = useState(0);
  const [styleSaveName, setStyleSaveName] = useState("");
  const [coldOpen, setColdOpen] = useState(false);
  const [hookPrompt, setHookPrompt] = useState(String(defaults.hook_prompt ?? ""));
  const [autoSharpen, setAutoSharpen] = useState(defaults.auto_sharpen === true);
  const [autoGrade, setAutoGrade] = useState(defaults.auto_grade === true);
  const [autoAudio, setAutoAudio] = useState(true);
  const [runMode, setRunMode] = useState<RunMode>("prepare");
  const [asrProvider, setAsrProvider] = useState("elevenlabs_scribe");
  const [showMore, setShowMore] = useState(false);
  const [cardPlan, setCardPlan] = useState(String(defaults.card_plan ?? ""));
  const [brandPill, setBrandPill] = useState(String(defaults.brand_pill ?? ""));
  const [language, setLanguage] = useState("vi");
  const [tempo, setTempo] = useState(1.06);
  const [whisperModel, setWhisperModel] = useState("medium");
  const [model, setModel] = useState("");
  const [verifierModel, setVerifierModel] = useState("");
  const [defaultDirectorModel, setDefaultDirectorModel] = useState("ag/gemini-3.7-flash-high");
  const [gatewayOk, setGatewayOk] = useState(true);
  const [audioPreset, setAudioPreset] = useState("shotgun");
  const [cutLevelState, setCutLevelState] = useState<CutLevel>(toCutLevel(defaults.cut_level));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pickedIds, setPickedIds] = useState<string[]>([]);
  const [scope, setScope] = useState<"each" | "together">("each");
  const [includeBroll, setIncludeBroll] = useState(true);

  useEffect(() => {
    if (!open) return;
    api.config()
      .then((cfg) => {
        setDefaultDirectorModel(cfg.director_model);
        setGatewayOk(cfg.gateway_configured);
      })
      .catch(() => undefined);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setPrompt(String(defaults.prompt ?? ""));
    setTopic(String(defaults.topic ?? ""));
    setCardPlan(String(defaults.card_plan ?? ""));
    setBrandPill(String(defaults.brand_pill ?? ""));
    setBgm(defaults.bgm !== false);
    setBgmName(String(defaults.bgm_name ?? ""));
    setBgmVolume(num(defaults.bgm_volume, 0.16));
    setCutLevelState(toCutLevel(defaults.cut_level));
    setStyleSaveName("");
    setRunMode("prepare");
    setError("");
    setShowMore(false);
    setIncludeBroll(true);
    const arollIds = project.sources.filter((s) => s.role === "aroll").map((s) => s.id);
    const given = (initialSourceIds || []).filter((id) => arollIds.includes(id));
    setPickedIds(given.length ? given : arollIds.length === 1 ? arollIds : []);
    setScope(given.length === 1 ? "together" : "each");

    const applySourceBgm = () => {
      if (given.length !== 1) return;
      const src = project.sources.find((item) => item.id === given[0]);
      if (!src?.bgm_name) return;
      setBgm(true);
      setBgmName(String(src.bgm_name));
      setBgmVolume(num(src.bgm_volume, 0.16));
    };

    const sid = String(defaults.style_id ?? "");
    setStyleId(sid);
    if (!sid) {
      applySourceBgm();
      return;
    }
    api.listEditStyles()
      .then((list) => {
        const style = list.find((item) => item.id === sid);
        if (!style) {
          applySourceBgm();
          return;
        }
        const opts = style.options || {};
        setPrompt(String(opts.prompt ?? ""));
        setTopic(String(opts.topic ?? ""));
        setCardPlan(String(opts.card_plan ?? ""));
        setBrandPill(String(opts.brand_pill ?? ""));
        setLanguage(String(opts.language ?? "vi"));
        setTempo(num(opts.tempo, 1.06));
        setBgm(opts.bgm !== false);
        setBgmName(String(opts.bgm_name ?? ""));
        setBgmVolume(num(opts.bgm_volume, 0.16));
        setColdOpen(Boolean(opts.cold_open));
        setHookPrompt(String(opts.hook_prompt ?? ""));
        setAsrProvider(String(opts.asr_provider ?? "elevenlabs_scribe"));
        setWhisperModel(String(opts.whisper_model ?? "medium"));
        setAutoSharpen(opts.auto_sharpen === true);
        setAutoGrade(opts.auto_grade === true);
        setAutoAudio(opts.auto_audio_preset !== false);
        setAudioPreset(String(opts.audio_preset ?? "shotgun"));
        setModel(String(opts.model ?? ""));
        setVerifierModel(String(opts.verifier_model ?? ""));
        setCutLevelState(toCutLevel(opts.cut_level));
        // Per-video BGM preference wins over project default style when opening one clip.
        applySourceBgm();
      })
      .catch(() => applySourceBgm());
  }, [open, project.project_id, initialSourceIds]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const aroll = arollSources.length;
  const mode: RunMode = runMode;
  const modeMeta = RUN_MODES.find((m) => m.id === mode) || RUN_MODES[0];
  const metaRun = modeMeta.run;

  const applyStyle = (style: EditStyle | null) => {
    if (!style) {
      setStyleId("");
      return;
    }
    const opts = style.options || {};
    setStyleId(style.id);
    setPrompt(String(opts.prompt ?? ""));
    setTopic(String(opts.topic ?? ""));
    setCardPlan(String(opts.card_plan ?? ""));
    setBrandPill(String(opts.brand_pill ?? ""));
    setLanguage(String(opts.language ?? "vi"));
    setTempo(num(opts.tempo, 1.06));
    setBgm(opts.bgm !== false);
    setBgmName(String(opts.bgm_name ?? ""));
    setBgmVolume(num(opts.bgm_volume, 0.16));
    setColdOpen(Boolean(opts.cold_open));
    setHookPrompt(String(opts.hook_prompt ?? ""));
    setAsrProvider(String(opts.asr_provider ?? "elevenlabs_scribe"));
    setWhisperModel(String(opts.whisper_model ?? "medium"));
    setAutoSharpen(opts.auto_sharpen === true);
    setAutoGrade(opts.auto_grade === true);
    setAutoAudio(opts.auto_audio_preset !== false);
    setAudioPreset(String(opts.audio_preset ?? "shotgun"));
    setModel(String(opts.model ?? ""));
    setVerifierModel(String(opts.verifier_model ?? ""));
    setCutLevelState(toCutLevel(opts.cut_level));
  };

  const currentOptions = (): Record<string, unknown> => ({
    prompt,
    topic,
    card_plan: cardPlan,
    brand_pill: brandPill,
    language,
    tempo,
    bgm,
    bgm_name: bgmName,
    bgm_volume: bgmVolume,
    cold_open: coldOpen,
    hook_prompt: hookPrompt,
    asr_provider: asrProvider,
    whisper_model: whisperModel,
    auto_sharpen: autoSharpen,
    auto_grade: autoGrade,
    auto_audio_preset: autoAudio,
    audio_preset: audioPreset,
    cut_level: cutLevelState,
  });

  const saveAsStyle = async () => {
    const name = styleSaveName.trim();
    if (name.length < 2) {
      setError("Đặt tên mẫu (ít nhất 2 ký tự) rồi lưu.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = await api.saveEditStyle({
        title: name,
        options: currentOptions(),
        source_project_id: project.project_id,
      });
      setStyleId(saved.id);
      setStyleSaveName("");
      setStyleListKey((n) => n + 1);
    } catch (e) {
      setError(String(e).slice(0, 300));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (aroll === 0) {
      setError("Chưa có video — upload clip talking-head trước.");
      return;
    }
    if (pickedIds.length === 0) {
      setError("Chọn ít nhất một video để tạo short.");
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
        bgm_name: bgmName,
        bgm_volume: bgmVolume,
        style_id: styleId,
        cold_open: coldOpen,
        hook_prompt: hookPrompt,
        asr_provider: asrProvider,
        whisper_model: whisperModel,
        model,
        verifier_model: verifierModel,
        auto_sharpen: autoSharpen,
        auto_grade: autoGrade,
        auto_audio_preset: autoAudio,
        audio_preset: audioPreset,
        cut_level: cutLevelState,
      };
      const stages = stagesForMode(mode);
      const perClip = scope === "each" && pickedIds.length > 1;
      if (perClip) {
        const batch = await api.createBuildsBatch(project.project_id, {
          source_ids: pickedIds,
          title: title.trim() || undefined,
          options,
          stages,
          run: metaRun,
          include_broll: includeBroll,
        });
        const ids = batch.jobs.map((row) => row.job_id);
        ids.forEach((jobId) => {
          saveBuildPlan(jobId, {
            mode,
            autoEnqueueCloud: false,
            stopBeforeRender: mode === "prepare",
            createdAt: Date.now(),
          });
        });
        if (batch.failed.length) {
          setError(batch.failed.map((row) => row.error).join(" · ").slice(0, 400));
        }
        onCreated(ids[0], ids);
      } else {
        const result = await api.createBuild(project.project_id, {
          title: title.trim() || undefined,
          options,
          stages,
          run: metaRun,
          source_ids: pickedIds,
          include_broll: includeBroll,
        });
        saveBuildPlan(result.job_id, {
          mode,
          autoEnqueueCloud: false,
          stopBeforeRender: mode === "prepare",
          createdAt: Date.now(),
        });
        onCreated(result.job_id, [result.job_id]);
      }
    } catch (e) {
      setError(String(e).slice(0, 400));
    } finally {
      setBusy(false);
    }
  };

  const batchN = scope === "each" && pickedIds.length > 1 ? pickedIds.length : 0;
  const cta = busy
    ? "Đang tạo…"
    : mode === "full_local"
      ? batchN ? `Tạo & xuất ${batchN} short` : "Tạo & xuất short"
      : mode === "prepare"
        ? batchN ? `Tạo & xem trước ${batchN} short` : "Tạo & xem trước"
        : batchN ? `Tạo ${batchN} job (chưa chạy)` : "Tạo job (chưa chạy)";

  return (
    <div className="lightbox-overlay" onClick={busy ? undefined : onClose}>
      <div
        className="card cloud-modal create-build-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="create-build-title"
      >
        <div className="create-build-body">
          <div className="row" style={{ alignItems: "center", marginBottom: 8 }}>
            <h2 id="create-build-title" style={{ margin: 0 }}>Tạo short</h2>
            <span className="muted small" style={{ flex: 1 }}>
              {project.title}
            </span>
            <button className="ghost small" disabled={busy} onClick={onClose}>
              Đóng
            </button>
          </div>

          <FlowSteps
            steps={[
              { id: "src", label: "Video", state: aroll > 0 ? "done" : "now" },
              { id: "opt", label: "Mẫu & chỉnh", state: aroll > 0 ? "now" : "todo" },
              { id: "run", label: "Chạy", state: "todo" },
            ]}
          />

          {aroll === 0 && (
            <div className="callout warn">
              Chưa có video. Đóng hộp này, upload mp4/mov, rồi tạo short lại.
            </div>
          )}

          <div className="callout info" style={{ marginTop: 8 }}>
            Chọn <b>một video</b> (hoặc vài clip) → mẫu → <b>Tạo & xem trước</b>.
            Muốn MP4 ngay thì chọn “Xuất MP4 luôn”.
          </div>

          <StyleSelect value={styleId} onPick={applyStyle} refreshKey={styleListKey} />

          {!gatewayOk && (
            <div className="callout warn">
              Chưa cấu hình gateway AI (<code>NINE_ROUTER_*</code> trong <code>.env</code>).
              Bước Direct/Audit sẽ lỗi khi chạy.
            </div>
          )}

          <label className="field">
            Chủ đề video (tuỳ chọn)
            <input
              value={topic}
              placeholder="VD: 3 sai lầm khi làm chatbot AI bán hàng"
              onChange={(e) => setTopic(e.target.value)}
            />
          </label>

          <label className="field">
            Yêu cầu riêng cho AI (tuỳ chọn)
            <textarea
              value={prompt}
              placeholder="VD: giữ giọng vui, nhấn phần chi phí, đừng cắt câu chào cuối…"
              onChange={(e) => setPrompt(e.target.value)}
            />
          </label>

          <p className="muted small">
            Model mặc định: <code>{defaultDirectorModel}</code>
            {verifierModel.trim() ? (
              <> · kiểm cắt: <code>{verifierModel.trim()}</code></>
            ) : null}
          </p>

          <label className="field">
            Tên short (tuỳ chọn)
            <input
              value={title}
              placeholder="Để trống thì máy tự đặt"
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>

          {arollSources.length > 1 && (
            <>
              <h3>Video sẽ tạo short ({pickedIds.length})</h3>
              {pickedIds.length === 0 && (
                <p className="error-text small">
                  Chọn video bên dưới — không chọn hết cả {arollSources.length} video trừ khi thật sự muốn.
                </p>
              )}
              <div className="check-list">
                {arollSources.map((clip) => {
                  const on = pickedIds.includes(clip.id);
                  return (
                    <label key={clip.id} className={`check-row ${on ? "on" : ""}`}>
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() =>
                          setPickedIds((current) =>
                            on ? current.filter((id) => id !== clip.id) : [...current, clip.id],
                          )
                        }
                      />
                      <span>
                        <b>{clip.label || clip.id}</b>
                        <span className="muted small">{clip.duration.toFixed(0)}s</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </>
          )}

          {arollSources.length === 1 && pickedIds.length === 1 && (
            <p className="muted small" style={{ marginTop: 4 }}>
              Video: <b>{arollSources[0].label || arollSources[0].id}</b> ·{" "}
              {arollSources[0].duration.toFixed(0)}s → một short
            </p>
          )}

          <div className="check-list">
            <label className={`check-row ${includeBroll ? "on" : ""}`}>
              <input
                type="checkbox"
                checked={includeBroll}
                onChange={(e) => setIncludeBroll(e.target.checked)}
              />
              <span>
                <b>Kèm hình phụ (b-roll)</b>
                <span className="muted small">Hình dùng chung trong project, không phải video chính</span>
              </span>
            </label>
          </div>

          <h3>Chỉnh nhanh (tuỳ chọn)</h3>
          <div className="check-list">
            <label className={`check-row ${bgm ? "on" : ""}`}>
              <input type="checkbox" checked={bgm} onChange={(e) => setBgm(e.target.checked)} />
              <span>
                <b>Nhạc nền</b>
                <span className="muted small">Nhạc nhỏ dưới lời nói</span>
              </span>
            </label>
            <label className={`check-row ${coldOpen ? "on" : ""}`}>
              <input type="checkbox" checked={coldOpen} onChange={(e) => setColdOpen(e.target.checked)} />
              <span>
                <b>Hook / teaser đầu video</b>
                <span className="muted small">Phát một câu đắt trước, rồi quay lại đầu — bỏ tick để tắt</span>
              </span>
            </label>
            {coldOpen && (
              <label className="field" style={{ marginTop: 4 }}>
                Gợi ý hook (tuỳ chọn)
                <input
                  value={hookPrompt}
                  placeholder="VD: câu sốc về chi phí, trọn câu, không cắt giữa chừng…"
                  onChange={(e) => setHookPrompt(e.target.value)}
                />
              </label>
            )}
            <label className={`check-row ${autoAudio ? "on" : ""}`}>
              <input type="checkbox" checked={autoAudio} onChange={(e) => setAutoAudio(e.target.checked)} />
              <span>
                <b>Chỉnh tiếng</b>
                <span className="muted small">Làm sạch mic / shotgun</span>
              </span>
            </label>
          </div>

          <h3>Mức cắt</h3>
          <div className="check-list">
            {CUT_LEVELS.map((level) => (
              <label key={level.id} className={`check-row ${cutLevelState === level.id ? "on" : ""}`}>
                <input
                  type="radio"
                  name="cut-level"
                  checked={cutLevelState === level.id}
                  onChange={() => setCutLevelState(level.id)}
                />
                <span>
                  <b>{level.label}</b>
                  <span className="muted small">{level.hint}</span>
                </span>
              </label>
            ))}
          </div>

          <div className="row" style={{ alignItems: "flex-end", marginTop: 8 }}>
            <label className="field" style={{ flex: 1 }}>
              Lưu chỉnh hiện tại thành mẫu dựng
              <input
                value={styleSaveName}
                placeholder="VD: Bán hàng polo đỏ"
                onChange={(e) => setStyleSaveName(e.target.value)}
              />
            </label>
            <button type="button" className="ghost" disabled={busy} onClick={() => void saveAsStyle()}>
              Lưu mẫu
            </button>
          </div>

          <button className="ghost small" type="button" onClick={() => setShowMore((v) => !v)}>
            {showMore ? "▾ Ẩn tuỳ chọn nâng cao" : "▸ Tuỳ chọn nâng cao — model, ASR, thẻ chữ, nhạc…"}
          </button>

          {showMore && (
            <CreateBuildAdvanced
              bgm={bgm}
              bgmName={bgmName}
              bgmVolume={bgmVolume}
              onBgmName={setBgmName}
              onBgmVolume={setBgmVolume}
              asrProvider={asrProvider}
              onAsrProvider={setAsrProvider}
              cardPlan={cardPlan}
              onCardPlan={setCardPlan}
              brandPill={brandPill}
              onBrandPill={setBrandPill}
              language={language}
              onLanguage={setLanguage}
              tempo={tempo}
              onTempo={setTempo}
              whisperModel={whisperModel}
              onWhisperModel={setWhisperModel}
              autoAudio={autoAudio}
              audioPreset={audioPreset}
              onAudioPreset={setAudioPreset}
              autoSharpen={autoSharpen}
              onAutoSharpen={setAutoSharpen}
              autoGrade={autoGrade}
              onAutoGrade={setAutoGrade}
              model={model}
              onModel={setModel}
              verifierModel={verifierModel}
              onVerifierModel={setVerifierModel}
              defaultDirectorModel={defaultDirectorModel}
              showScope={arollSources.length > 1 && pickedIds.length > 1}
              scope={scope}
              onScope={setScope}
            />
          )}

          <h3>Khi bấm chạy</h3>
          <div className="check-list">
            {SIMPLE_RUN_MODES.map((item) => (
              <label key={item.id} className={`check-row ${runMode === item.id ? "on" : ""}`}>
                <input
                  type="radio"
                  name="run-mode"
                  checked={runMode === item.id}
                  onChange={() => setRunMode(item.id)}
                />
                <span>
                  <b>{item.title}</b>
                  <span className="muted small">{item.summary}</span>
                </span>
              </label>
            ))}
          </div>
          {modeMeta.warnings.length > 0 && (
            <ul className="muted small" style={{ marginTop: 8 }}>
              {modeMeta.warnings.map((warning, index) => (
                <li key={index}>{warning}</li>
              ))}
            </ul>
          )}

          {error && <p className="error-text small">{error}</p>}
        </div>

        <div className="create-build-foot">
          <button className="ghost" disabled={busy} onClick={onClose}>Huỷ</button>
          <button className="primary" disabled={busy || aroll === 0} onClick={() => void submit()}>
            {cta}
          </button>
        </div>
      </div>
    </div>
  );
};
