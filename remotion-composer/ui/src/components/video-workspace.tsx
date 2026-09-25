import React, { useEffect, useMemo, useRef, useState } from "react";
import { Build, ProjectSource, api } from "../api/client";
import { BgmPicker } from "./bgm-picker";
import { trackClip } from "../lib/clip-track";
import { statusLabel } from "../lib/status";
import { PREPARE_STAGES, saveBuildPlan } from "../lib/pipeline-plan";

const mediaUrl = (projectId: string, sourceId: string) =>
  `/api/projects/${projectId}/sources/${sourceId}/file`;

const parseTags = (raw: string) =>
  raw
    .split(/[,，;#\n]+/)
    .map((item) => item.trim())
    .filter(Boolean);

const numOrEmpty = (value: unknown) => {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : "";
};

/**
 * One-clip page: primary job is run a short; metadata is secondary.
 */
export const VideoWorkspace: React.FC<{
  projectId: string;
  source: ProjectSource;
  builds: Build[];
  defaults?: Record<string, unknown>;
  onChanged: () => void;
  onClose: () => void;
  onCreateShort: (sourceId: string) => void;
  onOpenJob: (jobId: string) => void;
}> = ({
  projectId,
  source,
  builds,
  defaults = {},
  onChanged,
  onClose,
  onCreateShort,
  onOpenJob,
}) => {
  const track = trackClip(source.id, builds);
  const clipBuilds = useMemo(
    () =>
      builds
        .filter((build) => (build.source_ids || []).includes(source.id))
        .slice()
        .sort((a, b) => (b.created_at || 0) - (a.created_at || 0)),
    [builds, source.id],
  );

  const [label, setLabel] = useState(source.label || "");
  const [notes, setNotes] = useState(source.notes || "");
  const [tagDraft, setTagDraft] = useState((source.tags || []).join(", "));
  const [bgmName, setBgmName] = useState(source.bgm_name || "");
  const [bgmVolume, setBgmVolume] = useState(
    typeof source.bgm_volume === "number" ? source.bgm_volume : 0.16,
  );
  const [posted, setPosted] = useState(Boolean(source.meta?.posted));
  const [platform, setPlatform] = useState(String(source.meta?.platform ?? ""));
  const [postUrl, setPostUrl] = useState(String(source.meta?.url ?? ""));
  const [views, setViews] = useState(numOrEmpty(source.meta?.views));
  const [likes, setLikes] = useState(numOrEmpty(source.meta?.likes));
  const [mix, setMix] = useState(Boolean(source.bgm_name));
  const [metaOpen, setMetaOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [dirty, setDirty] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const bgmRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    setLabel(source.label || "");
    setNotes(source.notes || "");
    setTagDraft((source.tags || []).join(", "));
    setBgmName(source.bgm_name || "");
    setBgmVolume(typeof source.bgm_volume === "number" ? source.bgm_volume : 0.16);
    setPosted(Boolean(source.meta?.posted));
    setPlatform(String(source.meta?.platform ?? ""));
    setPostUrl(String(source.meta?.url ?? ""));
    setViews(numOrEmpty(source.meta?.views));
    setLikes(numOrEmpty(source.meta?.likes));
    setMix(Boolean(source.bgm_name));
    setSaved("");
    setError("");
    setDirty(false);
  }, [source.id, source.added_at]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const video = videoRef.current;
    const bgm = bgmRef.current;
    if (!video || !bgm) return;

    const syncPlay = () => {
      if (!mix || !bgmName) return;
      bgm.currentTime = video.currentTime;
      bgm.volume = Math.max(0, Math.min(1, bgmVolume));
      void bgm.play().catch(() => undefined);
    };
    const syncPause = () => bgm.pause();
    const syncTime = () => {
      if (!mix || !bgmName) return;
      if (Math.abs(bgm.currentTime - video.currentTime) > 0.35) {
        bgm.currentTime = video.currentTime;
      }
      bgm.volume = Math.max(0, Math.min(1, bgmVolume));
    };

    video.addEventListener("play", syncPlay);
    video.addEventListener("pause", syncPause);
    video.addEventListener("seeking", syncTime);
    video.addEventListener("timeupdate", syncTime);
    return () => {
      video.removeEventListener("play", syncPlay);
      video.removeEventListener("pause", syncPause);
      video.removeEventListener("seeking", syncTime);
      video.removeEventListener("timeupdate", syncTime);
      bgm.pause();
    };
  }, [mix, bgmName, bgmVolume, source.id]);

  const markDirty = () => {
    setDirty(true);
    setSaved("");
  };

  const buildMeta = (): Record<string, unknown> => {
    const base: Record<string, unknown> = { ...(source.meta || {}) };
    base.posted = posted;
    if (posted) {
      if (!base.posted_at) base.posted_at = new Date().toISOString().slice(0, 10);
    } else {
      delete base.posted_at;
    }
    if (platform.trim()) base.platform = platform.trim();
    else delete base.platform;
    if (postUrl.trim()) base.url = postUrl.trim();
    else delete base.url;
    if (views.trim() !== "" && Number.isFinite(Number(views))) base.views = Number(views);
    else delete base.views;
    if (likes.trim() !== "" && Number.isFinite(Number(likes))) base.likes = Number(likes);
    else delete base.likes;
    return base;
  };

  const persist = async () => {
    await api.updateSource(projectId, source.id, {
      label: label.trim() || source.id,
      notes,
      tags: parseTags(tagDraft),
      meta: buildMeta(),
      bgm_name: bgmName,
      bgm_volume: bgmName ? bgmVolume : null,
    });
    setDirty(false);
    setSaved("Đã lưu");
    onChanged();
  };

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await persist();
    } catch (exception) {
      setError(String(exception).slice(0, 300));
    } finally {
      setBusy(false);
    }
  };

  const runPrepare = async () => {
    setRunning(true);
    setError("");
    try {
      if (dirty) await persist();
      const result = await api.createBuild(projectId, {
        title: label.trim() || undefined,
        source_ids: [source.id],
        include_broll: true,
        stages: [...PREPARE_STAGES],
        run: true,
        options: {
          bgm: Boolean(bgmName) || defaults.bgm !== false,
          bgm_name: bgmName || String(defaults.bgm_name ?? ""),
          bgm_volume: bgmName ? bgmVolume : numOrEmpty(defaults.bgm_volume) ? Number(defaults.bgm_volume) : 0.16,
          style_id: String(defaults.style_id ?? ""),
          language: String(defaults.language ?? "vi"),
          auto_sharpen: defaults.auto_sharpen === true,
          auto_grade: defaults.auto_grade === true,
          auto_audio_preset: defaults.auto_audio_preset !== false,
          asr_provider: String(defaults.asr_provider ?? "elevenlabs_scribe"),
        },
      });
      saveBuildPlan(result.job_id, {
        mode: "prepare",
        autoEnqueueCloud: false,
        stopBeforeRender: true,
        createdAt: Date.now(),
      });
      onChanged();
      onOpenJob(result.job_id);
    } catch (exception) {
      setError(String(exception).slice(0, 400));
    } finally {
      setRunning(false);
    }
  };

  const product = track.product;
  const latest = track.latest;

  return (
    <div className="video-workspace">
      <div className="video-workspace-bar">
        <button
          className="ghost small"
          type="button"
          onClick={() => {
            if (dirty && !window.confirm("Chưa lưu thay đổi. Thoát tiếp?")) return;
            onClose();
          }}
        >
          ← Danh sách
        </button>
        <span className={`badge ${track.kind === "ready" ? "completed" : track.kind === "failed" ? "failed" : track.kind === "running" ? "running" : "pending"}`}>
          {track.label}
        </span>
        {posted && <span className="badge completed">Đã đăng</span>}
        <span style={{ flex: 1 }} />
        <button className="ghost" type="button" disabled={busy || !dirty} onClick={() => void save()}>
          {busy ? "Đang lưu…" : saved && !dirty ? "Đã lưu" : "Lưu"}
        </button>
        {latest && (
          <button className="ghost" type="button" onClick={() => onOpenJob(latest.job_id)}>
            {product?.has_final ? "Xem thành phẩm" : "Xem tiến độ"}
          </button>
        )}
        <button className="ghost" type="button" disabled={running} onClick={() => onCreateShort(source.id)}>
          Tuỳ chọn…
        </button>
        <button
          className="primary"
          type="button"
          disabled={running || busy}
          onClick={() => void runPrepare()}
        >
          {running ? "Đang xếp hàng…" : product?.has_final ? "Dựng lại" : "Chạy dựng short"}
        </button>
      </div>

      <div className="card callout info" style={{ margin: 0 }}>
        <p className="muted small" style={{ margin: 0 }}>
          Video gốc: <b>{label || source.id}</b>. Bấm <b>Chạy dựng short</b> để xem trước. Xong thì mở bản dựng → <b>Xuất MP4</b>.
          Tab <b>Kết quả</b> cũng liệt kê mọi bản dựng.
        </p>
      </div>

      <div className="video-workspace-grid">
        <section className="card video-workspace-main">
          <label className="field" style={{ marginBottom: 10 }}>
            Tên video
            <input
              value={label}
              onChange={(event) => {
                setLabel(event.target.value);
                markDirty();
              }}
              placeholder="VD: Clip 19/08 — giá CRM"
            />
          </label>

          <video
            key={source.id}
            ref={videoRef}
            src={mediaUrl(projectId, source.id)}
            controls
            playsInline
            preload="metadata"
            className="video-workspace-player"
          />
          <audio
            ref={bgmRef}
            src={bgmName ? `/api/bgm/file/${encodeURIComponent(bgmName)}` : undefined}
            preload="none"
          />

          <div className="row" style={{ marginTop: 10, gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button
              type="button"
              className={mix && bgmName ? "primary small" : "ghost small"}
              disabled={!bgmName}
              onClick={() => setMix((value) => !value)}
            >
              {mix && bgmName ? "● Nghe kèm BGM" : "Nghe kèm BGM"}
            </button>
            <span className="muted small">
              {source.duration.toFixed(0)}s · gốc
            </span>
          </div>

          <div className="card" style={{ marginTop: 14, background: "var(--surface)" }}>
            <h3 style={{ marginTop: 0 }}>Kết quả dựng ({clipBuilds.length})</h3>
            {clipBuilds.length === 0 ? (
              <p className="muted small" style={{ marginBottom: 0 }}>
                Chưa có bản dựng. Bấm <b>Chạy dựng short</b> ở trên — xong sẽ hiện ở đây và mở trang tiến độ.
              </p>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {clipBuilds.map((item) => (
                  <div key={item.job_id} className="clip-row" style={{ padding: "8px 10px" }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="clip-row-title">{item.title || item.job_id}</div>
                      <div className="muted small">
                        {statusLabel(item.status)}
                        {item.has_final ? " · đã có MP4" : ""}
                      </div>
                    </div>
                    <span className={`badge ${item.status}`}>{statusLabel(item.status)}</span>
                    {item.has_final && (
                      <a
                        className="ghost small"
                        href={`${item.media_base || `/api/media/${item.job_id}/`}final.mp4`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Xem MP4
                      </a>
                    )}
                    <button className="primary small" type="button" onClick={() => onOpenJob(item.job_id)}>
                      Mở
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        <aside className="stack video-workspace-side">
          <section className="card">
            <h3 style={{ marginTop: 0 }}>Nhạc nền (cho short)</h3>
            <p className="muted small" style={{ marginTop: 0 }}>
              Lưu vào video này — lần chạy dựng sẽ dùng nhạc này.
            </p>
            <BgmPicker
              enabled
              name={bgmName}
              volume={bgmVolume}
              soloPreview={false}
              hint="12–18% thường vừa dưới lời."
              onName={(name) => {
                setBgmName(name);
                setMix(Boolean(name));
                markDirty();
              }}
              onVolume={(volume) => {
                setBgmVolume(volume);
                markDirty();
              }}
            />
          </section>

          <section className="card">
            <button
              type="button"
              className="ghost small"
              style={{ marginBottom: 8 }}
              onClick={() => setMetaOpen((value) => !value)}
            >
              {metaOpen ? "▾ Ẩn tag / đăng bài" : "▸ Tag, ghi chú, đã đăng…"}
            </button>
            {metaOpen && (
              <div className="stack">
                <label className="field">
                  Tag
                  <input
                    value={tagDraft}
                    onChange={(event) => {
                      setTagDraft(event.target.value);
                      markDirty();
                    }}
                    placeholder="tiktok, bán hàng"
                  />
                </label>
                <label className="field">
                  Ghi chú
                  <textarea
                    value={notes}
                    onChange={(event) => {
                      setNotes(event.target.value);
                      markDirty();
                    }}
                    rows={2}
                  />
                </label>
                <label className="check-row soft">
                  <input
                    type="checkbox"
                    checked={posted}
                    onChange={(event) => {
                      setPosted(event.target.checked);
                      markDirty();
                    }}
                  />
                  <span><b>Đã đăng</b></span>
                </label>
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <label className="field" style={{ flex: 1, minWidth: 100 }}>
                    Nền tảng
                    <input value={platform} onChange={(e) => { setPlatform(e.target.value); markDirty(); }} />
                  </label>
                  <label className="field" style={{ flex: 1, minWidth: 100 }}>
                    Views
                    <input value={views} onChange={(e) => { setViews(e.target.value); markDirty(); }} />
                  </label>
                  <label className="field" style={{ flex: 1, minWidth: 100 }}>
                    Likes
                    <input value={likes} onChange={(e) => { setLikes(e.target.value); markDirty(); }} />
                  </label>
                </div>
                <label className="field">
                  Link bài
                  <input value={postUrl} onChange={(e) => { setPostUrl(e.target.value); markDirty(); }} />
                </label>
              </div>
            )}
          </section>
        </aside>
      </div>

      {error && <p className="error-text small">{error}</p>}
    </div>
  );
};
