import React, { useEffect, useState } from "react";
import { api, LookPreset } from "../api/client";

/**
 * Dropdown + save box for reusable look presets (`config/look-presets.json`),
 * split out of `skin-preview.tsx` to keep that file under the 200-line budget.
 *
 * Owns its own list/name/error/saving state — `skin-preview.tsx` only plugs in
 * two callbacks: `trialGrade` (what the sliders currently hold, for saving)
 * and `onApply` (load a chosen preset's fragment into the sliders).
 *
 * Picking a preset from the dropdown NEVER calls a job — it only calls
 * `onApply` so the parent can preview it. Writing it into `grade_overrides`
 * for real is the separate "Áp dụng & cắt lại" button in the parent.
 *
 * Preset store is global (not per-job) — fetched once on mount, not per jobId.
 */
export const LookPresetPicker: React.FC<{
  trialGrade: Record<string, number> | undefined;
  onApply: (grade: Record<string, number>) => void;
}> = ({ trialGrade, onApply }) => {
  const [presets, setPresets] = useState<LookPreset[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      setPresets(await api.listLookPresets());
      setError("");
    } catch (e) {
      // Preset store failing must never break the slider/preview above it.
      setError(String(e));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = (value: string) => {
    setSelected(value);
    const preset = presets.find((p) => p.name === value);
    if (preset) onApply(preset.grade);
  };

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed || !trialGrade) return;
    if (presets.some((p) => p.name === trimmed)) {
      if (!window.confirm(`Preset "${trimmed}" đã tồn tại. Ghi đè?`)) return;
    }
    setSaving(true);
    setError("");
    try {
      await api.saveLookPreset({ name: trimmed, grade: trialGrade });
      await load();
      setSelected(trimmed);
      setName("");
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="field">
      <span className="muted small">Preset da (dùng lại được cho mọi job)</span>
      <select value={selected} onChange={(e) => select(e.target.value)}>
        <option value="">— không dùng preset —</option>
        {presets.map((preset) => (
          <option key={preset.name} value={preset.name}>
            {preset.name}
          </option>
        ))}
      </select>
      <div className="row">
        <input
          placeholder="đặt tên preset để lưu"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ flex: 2 }}
        />
        <button type="button" disabled={saving || !name.trim() || !trialGrade} onClick={save}>
          {saving ? "Đang lưu…" : "Lưu thành preset"}
        </button>
      </div>
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
};
