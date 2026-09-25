import React, { useEffect, useState } from "react";
import { AssemblyConfig, EditStyle, ProjectDetail, api } from "../api/client";
import { BgmPicker } from "./bgm-picker";
import { StyleSelect } from "./style-select";

/**
 * The assembly config, with the source of every value visible.
 *
 * Showing whether a value came from the global defaults or from this project is
 * the point: without it nobody can tell what they are actually overriding, and a
 * setting that looks chosen but is merely inherited is how a whole shoot ends up
 * built the wrong way.
 */

const ORIGIN_LABEL: Record<string, string> = {
  global: "mặc định chung",
  project: "project này",
  job: "job",
};

const Origin: React.FC<{ from?: string }> = ({ from }) => (
  <span className="muted small" style={{ marginLeft: 6 }}>
    ({ORIGIN_LABEL[from ?? "global"] ?? from})
  </span>
);

export const AssemblySettings: React.FC<{
  project: ProjectDetail;
  onSaved: () => void;
}> = ({ project, onSaved }) => {
  const [draft, setDraft] = useState<Partial<AssemblyConfig>>(project.assembly ?? {});
  const [keyterms, setKeyterms] = useState((project.keyterms || []).join(", "));
  const [defaults, setDefaults] = useState<Record<string, unknown>>({
    topic: String(project.defaults?.topic ?? ""),
    card_plan: String(project.defaults?.card_plan ?? ""),
    brand_pill: String(project.defaults?.brand_pill ?? ""),
    prompt: String(project.defaults?.prompt ?? ""),
    style_id: String(project.defaults?.style_id ?? ""),
    bgm: project.defaults?.bgm !== false,
    bgm_name: String(project.defaults?.bgm_name ?? ""),
    bgm_volume: project.defaults?.bgm_volume ?? 0.16,
    cold_open: Boolean(project.defaults?.cold_open),
    hook_prompt: String(project.defaults?.hook_prompt ?? ""),
    asr_provider: String(project.defaults?.asr_provider ?? "elevenlabs_scribe"),
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [styleId, setStyleId] = useState(String(project.defaults?.style_id ?? ""));
  const [styleListKey, setStyleListKey] = useState(0);
  const [styleNote, setStyleNote] = useState("");
  const [folder, setFolder] = useState(String(project.folder ?? ""));

  const resolved = project.assembly_resolved;
  const origin = project.assembly_origin || {};

  const set = <K extends keyof AssemblyConfig>(key: K, value: AssemblyConfig[K] | undefined) =>
    setDraft((current) => {
      const next = { ...current };
      if (value === undefined) delete next[key];
      else next[key] = value;
      return next;
    });

  const save = async () => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await api.updateProjectSettings(project.project_id, {
        assembly: draft,
        keyterms: keyterms.split(",").map((term) => term.trim()).filter(Boolean),
        defaults,
        folder,
      });
      setSaved(true);
      onSaved();
    } catch (exception) {
      setError(String(exception).slice(0, 300));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="card">
        <h3>Kiểu dựng mặc định</h3>
        <p className="muted small">
          Kiểu này áp cho mọi video của nhà sáng tạo: prompt, nhạc, volume, tick.
        </p>
        <label className="field">
          Nhóm
          <input
            value={folder}
            placeholder="Để trống nếu chưa chia team"
            onChange={(event) => setFolder(event.target.value)}
          />
        </label>
        <StyleSelect
          value={styleId}
          refreshKey={styleListKey}
          onPick={(style: EditStyle | null) => {
            setStyleId(style?.id || "");
            if (!style) return;
            const opts = style.options || {};
            setDefaults((current) => ({
              topic: String(opts.topic ?? current.topic),
              card_plan: String(opts.card_plan ?? current.card_plan),
              brand_pill: String(opts.brand_pill ?? current.brand_pill),
              prompt: String(opts.prompt ?? current.prompt),
              style_id: style.id,
              bgm: opts.bgm !== false,
              bgm_name: String(opts.bgm_name ?? ""),
              bgm_volume: opts.bgm_volume ?? 0.16,
              cold_open: Boolean(opts.cold_open),
              hook_prompt: String(opts.hook_prompt ?? current.hook_prompt ?? ""),
              asr_provider: String(opts.asr_provider ?? "elevenlabs_scribe"),
            }));
            setStyleNote(`Đã nạp kiểu “${style.title}” — bấm Lưu cấu hình.`);
          }}
        />
        <div className="row">
          <button
            className="ghost"
            type="button"
            onClick={() => {
              void api
                .saveEditStyle({
                  title: `${project.title} — kiểu`,
                  options: defaults,
                  source_project_id: project.project_id,
                })
                .then((savedStyle) => {
                  setStyleId(savedStyle.id);
                  setStyleListKey((n) => n + 1);
                  setStyleNote(`Đã lưu kiểu “${savedStyle.title}”.`);
                })
                .catch((e) => setError(String(e).slice(0, 240)));
            }}
          >
            Lưu thành kiểu dùng chung
          </button>
        </div>
        {styleNote && <p className="ok-text small">{styleNote}</p>}
      </div>
      <div className="card">
        <h3>Cách ghép nhiều nguồn</h3>
        <p className="muted small">
          Bỏ trống một mục = kế thừa mặc định chung. Ghi đè ở đây áp cho mọi bản dựng của project.
        </p>

        <label className="field">
          Kiểu ghép
          <Origin from={origin.mode} />
          <select
            value={draft.mode ?? ""}
            onChange={(event) =>
              set("mode", (event.target.value || undefined) as AssemblyConfig["mode"])}
          >
            <option value="">kế thừa ({resolved.mode})</option>
            <option value="auto">auto — tự phát hiện take trùng</option>
            <option value="sequential">sequential — ghép tuần tự, không chọn take</option>
            <option value="best_take">best_take — luôn chọn bản tốt nhất</option>
          </select>
        </label>

        <label className="field">
          Đổi khung theo người nói
          <Origin from={origin.speaker_aware} />
          <select
            value={draft.speaker_aware === undefined ? "" : String(draft.speaker_aware)}
            onChange={(event) => {
              const raw = event.target.value;
              set("speaker_aware",
                  raw === "" ? undefined
                    : raw === "auto" ? "auto"
                      : (raw === "true") as AssemblyConfig["speaker_aware"]);
            }}
          >
            <option value="">kế thừa ({String(resolved.speaker_aware)})</option>
            <option value="auto">auto — bật khi thật có 2 người nói</option>
            <option value="true">bật</option>
            <option value="false">tắt</option>
          </select>
        </label>

        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={draft.broll_overlay ?? resolved.broll_overlay}
            onChange={(event) => set("broll_overlay", event.target.checked)}
          />
          Cho phép phủ b-roll lên A-roll
          <Origin from={origin.broll_overlay} />
        </label>

        <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={draft.cross_source_cut ?? resolved.cross_source_cut}
            onChange={(event) => set("cross_source_cut", event.target.checked)}
          />
          Cho phép cut/card xuyên ranh giới nguồn
          <Origin from={origin.cross_source_cut} />
        </label>
        <p className="muted small" style={{ marginTop: -4 }}>
          Tắt là đúng trong hầu hết trường hợp: ghép giữa câu từ hai lần quay khác nhau nghe rõ mối.
        </p>

        <label className="field">
          Dò nhóm take
          <Origin from={origin.take_detect} />
          <select
            value={draft.take_detect ?? ""}
            onChange={(event) =>
              set("take_detect",
                  (event.target.value || undefined) as AssemblyConfig["take_detect"])}
          >
            <option value="">kế thừa ({resolved.take_detect})</option>
            <option value="suggest">suggest — chỉ gợi ý, người xác nhận</option>
            <option value="apply">apply — tự gộp (đoán sai sẽ mất nội dung)</option>
            <option value="off">off — không dò</option>
          </select>
        </label>
      </div>

      <div className="card">
        <h3>Từ khoá gợi ý cho ASR</h3>
        <p className="muted small">
          Tên brand, thuật ngữ mà ASR hay nghe sai. Bỏ trống = tự tách từ chủ đề và card plan.
        </p>
        <input
          value={keyterms}
          placeholder="Zalo OA, CRM, chatbot AI"
          onChange={(event) => setKeyterms(event.target.value)}
        />
      </div>

      <div className="card">
        <h3>Mặc định cho bản dựng mới</h3>
        {([
          ["topic", "Chủ đề"],
          ["card_plan", "Gợi ý thẻ (tuỳ chọn — để trống = AI tự quyết)"],
          ["hook_prompt", "Gợi ý hook/teaser (khi bật cold-open)"],
          ["brand_pill", "Nhãn thương hiệu (pill)"],
          ["prompt", "Yêu cầu riêng"],
        ] as const).map(([key, label]) => (
          <label key={key} className="field">
            {label}
            <input
              value={String(defaults[key] ?? "")}
              onChange={(event) =>
                setDefaults((current) => ({ ...current, [key]: event.target.value }))}
            />
          </label>
        ))}
        <label className={`check-row ${defaults.bgm !== false ? "on" : ""}`} style={{ marginTop: 8 }}>
          <input
            type="checkbox"
            checked={defaults.bgm !== false}
            onChange={(event) =>
              setDefaults((current) => ({ ...current, bgm: event.target.checked }))}
          />
          <span>
            <b>Nhạc nền</b>
            <span className="muted small">Lưu cùng volume vào kiểu</span>
          </span>
        </label>
        <div style={{ marginTop: 10 }}>
          <BgmPicker
            enabled={defaults.bgm !== false}
            name={String(defaults.bgm_name ?? "")}
            volume={Number(defaults.bgm_volume ?? 0.16)}
            onName={(name) => setDefaults((current) => ({ ...current, bgm_name: name }))}
            onVolume={(volume) => setDefaults((current) => ({ ...current, bgm_volume: volume }))}
          />
        </div>
      </div>

      {error && <p className="error-text small">{error}</p>}
      {saved && <p className="ok-text small">Đã lưu.</p>}
      <div className="row">
        <button className="primary" disabled={busy} onClick={save}>
          Lưu cấu hình
        </button>
      </div>
    </div>
  );
};
