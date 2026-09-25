import React, { useEffect, useState } from "react";
import { EditStyle, ProjectSummary, api } from "../api/client";
import { BgmPicker } from "../components/bgm-picker";
import { FlowSteps } from "../components/flow-steps";

const num = (value: unknown, fallback: number) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

export const StylesPage: React.FC = () => {
  const [styles, setStyles] = useState<EditStyle[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [fromProject, setFromProject] = useState("");
  const [fromTitle, setFromTitle] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [draftId, setDraftId] = useState("");
  const [draftTitle, setDraftTitle] = useState("Mẫu mới");
  const [draftPrompt, setDraftPrompt] = useState("");
  const [bgm, setBgm] = useState(true);
  const [bgmName, setBgmName] = useState("bgm_tech_pulse.mp3");
  const [bgmVolume, setBgmVolume] = useState(0.15);
  const [coldOpen, setColdOpen] = useState(false);

  const load = () => {
    api.listEditStyles().then(setStyles).catch((e) => setError(String(e)));
    api.listProjects().then(setProjects).catch(() => undefined);
  };

  useEffect(() => {
    load();
  }, []);

  const options = () => ({
    prompt: draftPrompt,
    bgm,
    bgm_name: bgmName,
    bgm_volume: bgmVolume,
    cold_open: coldOpen,
    asr_provider: "elevenlabs_scribe",
    auto_sharpen: false,
    auto_grade: false,
    auto_audio_preset: true,
    tempo: 1.06,
    language: "vi",
  });

  const save = async () => {
    setError("");
    setNote("");
    try {
      if (draftId) {
        await api.updateEditStyle(draftId, { title: draftTitle, options: options() });
        setNote("Đã cập nhật mẫu.");
      } else {
        const saved = await api.saveEditStyle({ title: draftTitle, options: options() });
        setDraftId(saved.id);
        setNote("Đã lưu mẫu mới.");
      }
      load();
    } catch (e) {
      setError(String(e).slice(0, 300));
    }
  };

  const copyProject = async () => {
    if (!fromProject) return;
    setError("");
    try {
      const saved = await api.styleFromProject(fromProject, fromTitle || undefined);
      setNote(`Đã lấy mẫu từ ${saved.title}`);
      load();
      setDraftId(saved.id);
      setDraftTitle(saved.title);
      const opts = saved.options;
      setDraftPrompt(String(opts.prompt ?? ""));
      setBgm(opts.bgm !== false);
      setBgmName(String(opts.bgm_name ?? ""));
      setBgmVolume(num(opts.bgm_volume, 0.16));
      setColdOpen(Boolean(opts.cold_open));
    } catch (e) {
      setError(String(e).slice(0, 300));
    }
  };

  const edit = (style: EditStyle) => {
    setDraftId(style.id);
    setDraftTitle(style.title);
    const opts = style.options;
    setDraftPrompt(String(opts.prompt ?? ""));
    setBgm(opts.bgm !== false);
    setBgmName(String(opts.bgm_name ?? ""));
    setBgmVolume(num(opts.bgm_volume, 0.16));
    setColdOpen(Boolean(opts.cold_open));
  };

  return (
    <div className="stack">
      <div className="card">
        <h2 style={{ margin: 0 }}>Mẫu dựng</h2>
        <div style={{ marginTop: 12 }}>
          <FlowSteps
            steps={[
              { id: "s", label: "Lưu mẫu", state: "now" },
              { id: "v", label: "Chọn khi tạo short", state: "todo" },
              { id: "r", label: "Xuất short", state: "todo" },
            ]}
          />
        </div>
        <p className="muted small">
          Một mẫu = nhạc + tuỳ chọn dựng. Khi tạo short, chọn mẫu này — rồi chỉnh thêm nếu cần.
        </p>
      </div>

      <div className="card">
        <h3>{draftId ? "Sửa mẫu" : "Mẫu mới"}</h3>
        <label className="field">
          Tên
          <input value={draftTitle} onChange={(e) => setDraftTitle(e.target.value)} />
        </label>
        <label className="field">
          Prompt / ghi chú cho AI
          <textarea
            value={draftPrompt}
            placeholder="VD: giữ giọng vui, card ít chữ…"
            onChange={(e) => setDraftPrompt(e.target.value)}
          />
        </label>
        <label className={`check-row ${coldOpen ? "on" : ""}`}>
          <input type="checkbox" checked={coldOpen} onChange={(e) => setColdOpen(e.target.checked)} />
          <span>
            <b>Mở nhanh</b>
            <span className="muted small">Cắt câu chào đầu</span>
          </span>
        </label>
        <label className={`check-row ${bgm ? "on" : ""}`} style={{ marginTop: 8 }}>
          <input type="checkbox" checked={bgm} onChange={(e) => setBgm(e.target.checked)} />
          <span>
            <b>Nhạc nền</b>
          </span>
        </label>
        <div style={{ marginTop: 10 }}>
          <BgmPicker
            enabled={bgm}
            name={bgmName}
            volume={bgmVolume}
            onName={setBgmName}
            onVolume={setBgmVolume}
          />
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" onClick={() => void save()}>
            {draftId ? "Cập nhật mẫu" : "Lưu mẫu mới"}
          </button>
          {draftId && (
            <button
              className="ghost"
              onClick={() => {
                setDraftId("");
                setDraftTitle("Mẫu mới");
              }}
            >
              Tạo mẫu khác
            </button>
          )}
        </div>
      </div>

      <div className="card">
        <h3>Lấy mẫu từ nhà sáng tạo khác</h3>
        <div className="row">
          <label className="field">
            Nhà sáng tạo
            <select value={fromProject} onChange={(e) => setFromProject(e.target.value)}>
              <option value="">Chọn nhà sáng tạo…</option>
              {projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Tên mẫu (tuỳ chọn)
            <input
              value={fromTitle}
              placeholder="Để trống thì lấy tên nhà sáng tạo"
              onChange={(e) => setFromTitle(e.target.value)}
            />
          </label>
        </div>
        <button className="ghost" disabled={!fromProject} onClick={() => void copyProject()}>
          Sao chép cấu hình thành mẫu dùng chung
        </button>
      </div>

      <div className="card">
        <h3>Mẫu đã lưu ({styles.length})</h3>
        {styles.length === 0 && <p className="muted small">Chưa có mẫu — lưu ở trên.</p>}
        {styles.map((style) => {
          const opts = style.options;
          return (
            <div key={style.id} className="cut project-row">
              <div style={{ flex: 1, minWidth: 0 }}>
                <b>{style.title}</b>
                <div className="muted small">
                  {opts.bgm === false
                    ? "Không nhạc"
                    : `${String(opts.bgm_name || "AI chọn")} · ${Math.round(num(opts.bgm_volume, 0.16) * 100)}%`}
                  {opts.prompt ? ` · “${String(opts.prompt).slice(0, 48)}”` : ""}
                </div>
              </div>
              <button className="ghost small" onClick={() => edit(style)}>Sửa</button>
              <button
                className="danger-link"
                onClick={() => {
                  if (!window.confirm(`Xoá mẫu “${style.title}”?`)) return;
                  void api.deleteEditStyle(style.id).then(load);
                }}
              >
                Xoá
              </button>
            </div>
          );
        })}
      </div>

      {note && <p className="ok-text small">{note}</p>}
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
};
