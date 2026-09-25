import React, { useEffect, useRef, useState } from "react";
import { BgmTrack, api } from "../api/client";

export const BgmPicker: React.FC<{
  enabled: boolean;
  name: string;
  volume: number;
  onName: (name: string) => void;
  onVolume: (volume: number) => void;
  /** When false, hide the standalone BGM audio player (video mix preview handles it). */
  soloPreview?: boolean;
  hint?: string;
}> = ({ enabled, name, volume, onName, onVolume, soloPreview = true, hint }) => {
  const [tracks, setTracks] = useState<BgmTrack[]>([]);
  const [groups, setGroups] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = () =>
    api.listBgm()
      .then((data) => {
        setTracks(data.tracks);
        setGroups(data.groups);
      })
      .catch((e) => setError(String(e).slice(0, 200)));

  useEffect(() => {
    void load();
  }, []);

  const grouped = tracks.reduce<Record<string, BgmTrack[]>>((acc, track) => {
    const key = track.group || "other";
    (acc[key] ||= []).push(track);
    return acc;
  }, {});

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const saved = await api.uploadBgm(file);
      await load();
      onName(saved.name);
    } catch (e) {
      setError(String(e).slice(0, 240));
    } finally {
      setBusy(false);
    }
  };

  if (!enabled) {
    return <p className="muted small">Tắt nhạc nền thì không trộn BGM.</p>;
  }

  return (
    <div className="stack">
      <label className="field">
        Chọn nhạc
        <select value={name} onChange={(e) => onName(e.target.value)}>
          <option value="">AI tự chọn khi tạo short</option>
          {Object.entries(grouped).map(([group, list]) => (
            <optgroup key={group} label={groups[group] || group}>
              {list.map((track) => (
                <option key={track.name} value={track.name}>
                  {track.mood ? `${track.name.replace(/^bgm_/, "").replace(/\.mp3$/, "")} — ${track.mood}` : track.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {soloPreview && name ? (
        <audio src={`/api/bgm/file/${encodeURIComponent(name)}`} controls preload="none" style={{ width: "100%" }} />
      ) : null}
      <label className="field">
        Độ lớn ({Math.round(volume * 100)}%)
        <input
          type="range"
          min={0.08}
          max={0.22}
          step={0.01}
          value={volume}
          onChange={(e) => onVolume(Number(e.target.value))}
        />
        <span className="hint">{hint || "Nền dưới lời. 12–18% thường vừa."}</span>
      </label>
      <div className="row">
        <button type="button" className="ghost small" disabled={busy} onClick={() => inputRef.current?.click()}>
          {busy ? "Đang tải…" : "Tải nhạc của tôi…"}
        </button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept=".mp3,.wav,.m4a,.ogg,.aac,.flac,audio/*"
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          void upload(file);
        }}
      />
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
};
