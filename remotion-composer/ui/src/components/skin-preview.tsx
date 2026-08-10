import React, { useEffect, useRef, useState } from "react";
import { api, GradePreview as GradeReport } from "../api/client";
import { GRADE_VARIANT_LABEL, GradedPreviewGrid } from "./graded-preview-grid";
import { LookPresetPicker } from "./look-preset-picker";

type SkinKey = "skin_smooth" | "blemish_reduce";

const VARIANT_ORDER = ["raw", "hien_tai", "thu_nghiem"] as const;

/**
 * Sliders instead of the JSON box on the Màu tab: `skin_smooth` and
 * `blemish_reduce` are the two knobs people actually reach for on a
 * talking-head take, and a slider shows the number moving as you drag it.
 *
 * Every drag is an ffmpeg call (~1s, see `grade_still` in preview.py). Each
 * change is debounced 400ms and stamped with a request id; a response that
 * lands after a newer request was sent is thrown away, so a fast drag cannot
 * flash a stale frame back onto the screen.
 *
 * The frame picker below runs on seconds INTO THE RAW FOOTAGE, not the
 * timeline the <Player> above plays — resolve already cut/re-timed that file,
 * so the two clocks do not line up (see `_source_of` in api_previews.py).
 */
export const SkinPreviewPanel: React.FC<{
  jobId: string;
  currentOverrides: Record<string, number>;
  probe?: Record<string, unknown>;
  busy: boolean;
  onApplied: () => void;
}> = ({ jobId, currentOverrides, probe, busy, onApplied }) => {
  const duration = Number(probe?.duration_seconds ?? 0);
  const hasProbe = duration > 0;

  const [skinSmooth, setSkinSmooth] = useState(currentOverrides.skin_smooth ?? 0);
  const [blemishReduce, setBlemishReduce] = useState(currentOverrides.blemish_reduce ?? 0);
  const [touched, setTouched] = useState<Set<SkinKey>>(new Set());
  // Keys a preset carries beyond the two sliders (e.g. warmth from a broader
  // "look" preset) — no slider shows them, but Apply must still write them,
  // or picking such a preset would silently drop part of it.
  const [extraGrade, setExtraGrade] = useState<Record<string, number>>({});
  const [at, setAt] = useState(hasProbe ? String(Math.round((duration / 2) * 2) / 2) : "");
  const [report, setReport] = useState<GradeReport>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const reqIdRef = useRef(0);

  const touch = (key: SkinKey) => setTouched((prev) => new Set(prev).add(key));

  /** Only the keys the user actually dragged — never send an untouched 0. */
  const trialGrade = (): Record<string, number> | undefined => {
    if (touched.size === 0) return undefined;
    const grade: Record<string, number> = {};
    if (touched.has("skin_smooth")) grade.skin_smooth = skinSmooth;
    if (touched.has("blemish_reduce")) grade.blemish_reduce = blemishReduce;
    return grade;
  };

  /** Loads a picked preset's fragment into the sliders — never calls a job. */
  const applyPresetGrade = (grade: Record<string, number>) => {
    if (typeof grade.skin_smooth === "number") { setSkinSmooth(grade.skin_smooth); touch("skin_smooth"); }
    if (typeof grade.blemish_reduce === "number") { setBlemishReduce(grade.blemish_reduce); touch("blemish_reduce"); }
    const { skin_smooth, blemish_reduce, ...rest } = grade;
    setExtraGrade(rest);
  };

  const run = async () => {
    const reqId = ++reqIdRef.current;
    setLoading(true);
    setError("");
    try {
      const grade = trialGrade();
      const result = await api.previewGrade(jobId, {
        at: at.trim() ? Number(at) : null,
        ...(grade ? { grade } : {}),
      });
      if (reqId !== reqIdRef.current) return; // superseded by a later drag
      setReport(result);
    } catch (e) {
      if (reqId !== reqIdRef.current) return;
      setError(String(e));
    } finally {
      if (reqId === reqIdRef.current) setLoading(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(run, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skinSmooth, blemishReduce, at]);

  const apply = async () => {
    if (touched.size === 0) return;
    setLoading(true);
    setError("");
    try {
      await api.runStages(jobId, {
        stages: ["resolve"],
        use_cache: false,
        options: { grade_overrides: { ...currentOverrides, ...extraGrade, ...trialGrade() } },
      });
      onApplied();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const nudge = (seconds: number) => {
    const next = Math.max(0, Math.min(duration, Number(at || 0) + seconds));
    setAt(String(Math.round(next * 2) / 2));
  };

  // "raw" is the untouched frame the server always includes — shown alongside
  // hien_tai/thu_nghiem so "before" means the actual source, not just the
  // grade a previous Apply already baked in.
  const shown = report?.variants
    .filter((v) => (VARIANT_ORDER as readonly string[]).includes(v.name))
    .sort((a, b) => VARIANT_ORDER.indexOf(a.name as typeof VARIANT_ORDER[number])
      - VARIANT_ORDER.indexOf(b.name as typeof VARIANT_ORDER[number]));

  return (
    <div>
      <LookPresetPicker trialGrade={trialGrade()} onApply={applyPresetGrade} />
      {Object.keys(extraGrade).length > 0 && (
        <p className="muted small">
          Preset còn áp thêm: {Object.keys(extraGrade).join(", ")} — không có slider ở đây, nhưng
          vẫn được ghi khi bấm "Áp dụng &amp; cắt lại".
        </p>
      )}
      <label className="field">
        <span className="muted small">
          Giây trong <strong>footage gốc</strong> (khác giây trên khung xem ở trên, vì khung đó đã
          cắt/ghép rồi)
        </span>
        {hasProbe ? (
          <div className="row">
            <button type="button" onClick={() => nudge(-1)}>◀ 1s</button>
            <input
              type="range"
              min={0}
              max={duration}
              step={0.5}
              value={Number(at || 0)}
              onChange={(e) => setAt(e.target.value)}
              style={{ flex: 3 }}
            />
            <button type="button" onClick={() => nudge(1)}>1s ▶</button>
            <span className="muted small">{Number(at || 0).toFixed(1)}s / {duration.toFixed(1)}s</span>
          </div>
        ) : (
          <input value={at} placeholder="giữa video (chưa có probe)" onChange={(e) => setAt(e.target.value)} />
        )}
      </label>

      <label className="field">
        <span className="muted small">Làm mịn da (skin_smooth): {skinSmooth.toFixed(2)}</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={skinSmooth}
          onChange={(e) => { setSkinSmooth(Number(e.target.value)); touch("skin_smooth"); }}
        />
      </label>

      <label className="field">
        <span className="muted small">Giảm mụn/tì vết (blemish_reduce): {blemishReduce.toFixed(2)}</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={blemishReduce}
          onChange={(e) => { setBlemishReduce(Number(e.target.value)); touch("blemish_reduce"); }}
        />
      </label>

      <div className="row">
        <button disabled={loading} onClick={run}>
          {loading ? "Đang dựng…" : "Xem lại"}
        </button>
        <button className="primary" disabled={busy || loading || touched.size === 0} onClick={apply}>
          Áp dụng &amp; cắt lại
        </button>
      </div>

      {error && <p className="error-text small">{error}</p>}

      {shown && shown.length > 0 && (
        <>
          <p className="muted small">Bấm vào ảnh để xem cỡ lớn và so từng bản một.</p>
          <GradedPreviewGrid
            variants={shown}
            labelFor={(name) => GRADE_VARIANT_LABEL[name] ?? name}
            captionFor={(v) => `sáng ${v.face.luma?.toFixed(1) ?? "—"} · ám ấm ${v.face.warm_bias?.toFixed(2) ?? "—"}`}
          />
          <p className="muted small">
            Ở giây {report!.at_seconds} của footage gốc{report!.cached ? " · dùng lại ảnh đã dựng" : ""}.
            Độ mịn da đánh giá được trên ảnh tĩnh, nhưng độ nét thì KHÔNG — ảnh này bỏ qua 2 lượt
            encode x264 mà bản render thật sẽ chạy.
          </p>
        </>
      )}
    </div>
  );
};
