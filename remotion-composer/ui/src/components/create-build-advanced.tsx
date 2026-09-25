import React from "react";
import { BgmPicker } from "./bgm-picker";

/**
 * Everything in the create-build wizard that isn't needed for a first build:
 * model overrides, ASR/tempo/whisper tuning, audio preset, sharpen/grade,
 * card/brand text, language, and (when multiple videos are picked) whether
 * to build one short per video or merge them. All still reachable — just
 * collapsed by default so the wizard's main form stays short.
 */
export const CreateBuildAdvanced: React.FC<{
  bgm: boolean;
  bgmName: string;
  bgmVolume: number;
  onBgmName: (name: string) => void;
  onBgmVolume: (volume: number) => void;
  asrProvider: string;
  onAsrProvider: (value: string) => void;
  cardPlan: string;
  onCardPlan: (value: string) => void;
  brandPill: string;
  onBrandPill: (value: string) => void;
  language: string;
  onLanguage: (value: string) => void;
  tempo: number;
  onTempo: (value: number) => void;
  whisperModel: string;
  onWhisperModel: (value: string) => void;
  autoAudio: boolean;
  audioPreset: string;
  onAudioPreset: (value: string) => void;
  autoSharpen: boolean;
  onAutoSharpen: (value: boolean) => void;
  autoGrade: boolean;
  onAutoGrade: (value: boolean) => void;
  model: string;
  onModel: (value: string) => void;
  verifierModel: string;
  onVerifierModel: (value: string) => void;
  defaultDirectorModel: string;
  showScope: boolean;
  scope: "each" | "together";
  onScope: (value: "each" | "together") => void;
}> = ({
  bgm,
  bgmName,
  bgmVolume,
  onBgmName,
  onBgmVolume,
  asrProvider,
  onAsrProvider,
  cardPlan,
  onCardPlan,
  brandPill,
  onBrandPill,
  language,
  onLanguage,
  tempo,
  onTempo,
  whisperModel,
  onWhisperModel,
  autoAudio,
  audioPreset,
  onAudioPreset,
  autoSharpen,
  onAutoSharpen,
  autoGrade,
  onAutoGrade,
  model,
  onModel,
  verifierModel,
  onVerifierModel,
  defaultDirectorModel,
  showScope,
  scope,
  onScope,
}) => (
  <div className="stack" style={{ marginTop: 10 }}>
    {showScope && (
      <div className="check-list">
        <label className={`check-row ${scope === "each" ? "on" : ""}`}>
          <input
            type="radio"
            name="build-scope"
            checked={scope === "each"}
            onChange={() => onScope("each")}
          />
          <span>
            <b>Mỗi video một short</b>
            <span className="muted small">Khuyên dùng — đúng khi mỗi clip là nội dung khác nhau</span>
          </span>
        </label>
        <label className={`check-row ${scope === "together" ? "on" : ""}`}>
          <input
            type="radio"
            name="build-scope"
            checked={scope === "together"}
            onChange={() => onScope("together")}
          />
          <span>
            <b>Ghép thành một short</b>
            <span className="muted small">Chỉ khi đây là nhiều take của cùng một cảnh</span>
          </span>
        </label>
      </div>
    )}

    <div>
      <BgmPicker
        enabled={bgm}
        name={bgmName}
        volume={bgmVolume}
        onName={onBgmName}
        onVolume={onBgmVolume}
      />
    </div>

    <div className="field">
      <label>Làm nét / chỉnh màu</label>
      <span className="hint">Tự động — không cần chỉnh tay ở hầu hết video.</span>
    </div>
    <div className="check-list">
      <label className={`check-row ${autoSharpen ? "on" : ""}`}>
        <input type="checkbox" checked={autoSharpen} onChange={(e) => onAutoSharpen(e.target.checked)} />
        <span>
          <b>Làm nét</b>
          <span className="muted small">Tăng độ nét nhẹ</span>
        </span>
      </label>
      <label className={`check-row ${autoGrade ? "on" : ""}`}>
        <input type="checkbox" checked={autoGrade} onChange={(e) => onAutoGrade(e.target.checked)} />
        <span>
          <b>Chỉnh màu</b>
          <span className="muted small">Cân màu talking-head</span>
        </span>
      </label>
    </div>

    <label className="field">
      Nhận lời nói (ASR)
      <select value={asrProvider} onChange={(e) => onAsrProvider(e.target.value)}>
        <option value="elevenlabs_scribe">ElevenLabs Scribe — nhanh, tiếng Việt rõ</option>
        <option value="whisper_local">Whisper local — offline, chậm</option>
      </select>
    </label>
    <label className="field">
      Gợi ý thẻ (tuỳ chọn — để trống = AI tự quyết có/không)
      <input
        value={cardPlan}
        placeholder="VD: không thẻ / chỉ thêm khi có checklist. Không ghi số lượng."
        onChange={(e) => onCardPlan(e.target.value)}
      />
    </label>
    <div className="row">
      <label className="field">
        Nhãn thương hiệu (pill)
        <input
          value={brandPill}
          placeholder="VD: 3 SAI LẦM • CHATBOT AI"
          onChange={(e) => onBrandPill(e.target.value)}
        />
      </label>
      <label className="field">
        Ngôn ngữ
        <input value={language} onChange={(e) => onLanguage(e.target.value)} />
      </label>
      <label className="field">
        Tốc độ nói
        <input
          type="number"
          step="0.01"
          min="1"
          max="1.2"
          value={tempo.toFixed(2)}
          onChange={(e) => onTempo(Number(Number(e.target.value).toFixed(2)))}
        />
      </label>
    </div>
    <div className="row">
      <label className="field">
        Whisper (dự phòng)
        <select value={whisperModel} onChange={(e) => onWhisperModel(e.target.value)}>
          {["tiny", "base", "small", "medium", "large-v2", "large-v3"].map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </label>
      <label className="field">
        Preset âm thanh
        <select
          value={audioPreset}
          onChange={(e) => onAudioPreset(e.target.value)}
          disabled={autoAudio}
        >
          {["off", "voice", "voice_strong", "shotgun", "shotgun_dry"].map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </label>
    </div>
    <div className="row">
      <label className="field">
        Model AI dựng khung
        <input
          value={model}
          placeholder={`trống = ${defaultDirectorModel}`}
          onChange={(e) => onModel(e.target.value)}
        />
      </label>
      <label className="field">
        Model kiểm cắt (tuỳ chọn)
        <input
          value={verifierModel}
          placeholder={`trống = ${defaultDirectorModel}`}
          onChange={(e) => onVerifierModel(e.target.value)}
        />
      </label>
    </div>
  </div>
);
