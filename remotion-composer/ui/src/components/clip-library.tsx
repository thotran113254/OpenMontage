import React, { useEffect, useMemo, useState } from "react";
import { Build, ProjectSource } from "../api/client";
import { ClipKind, trackClip } from "../lib/clip-track";
import { VideoWorkspace } from "./video-workspace";

const dayKey = (ts: number) => {
  const date = new Date((ts || 0) * 1000);
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
};

const monthKey = (ts: number) => dayKey(ts).slice(0, 7);

const dayLabel = (key: string) => {
  const today = dayKey(Date.now() / 1000);
  const yesterday = dayKey(Date.now() / 1000 - 86400);
  if (key === today) return "Hôm nay";
  if (key === yesterday) return "Hôm qua";
  const [y, m, d] = key.split("-");
  return `${d}/${m}/${y}`;
};

const monthLabel = (key: string) => {
  const [y, m] = key.split("-");
  const now = new Date();
  const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  if (key === thisMonth) return "Tháng này";
  const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const prevKey = `${prev.getFullYear()}-${String(prev.getMonth() + 1).padStart(2, "0")}`;
  if (key === prevKey) return "Tháng trước";
  return `Tháng ${Number(m)}/${y}`;
};

const formatViews = (value: unknown) => {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "";
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "")}k xem`;
  return `${n} xem`;
};

type Filter = "all" | ClipKind | "posted" | "unposted" | "no_bgm";
type ViewMode = "list" | "grid";
type GroupMode = "day" | "month";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "Tất cả" },
  { id: "unposted", label: "Chưa đăng" },
  { id: "posted", label: "Đã đăng" },
  { id: "empty", label: "Chưa tạo short" },
  { id: "ready", label: "Đã có short" },
  { id: "no_bgm", label: "Chưa gắn nhạc" },
  { id: "running", label: "Đang chạy" },
  { id: "failed", label: "Lỗi" },
];

const isPosted = (source: ProjectSource) => Boolean(source.meta?.posted);
const hasBgm = (source: ProjectSource) => Boolean(source.bgm_name);

export const ClipLibrary: React.FC<{
  projectId: string;
  sources: ProjectSource[];
  builds: Build[];
  defaults?: Record<string, unknown>;
  selected: string[];
  onSelected: (ids: string[]) => void;
  onBuildOne: (id: string) => void;
  onOpenJob: (jobId: string) => void;
  onChanged: () => void;
  onWorkspaceChange?: (open: boolean) => void;
}> = ({
  projectId,
  sources,
  builds,
  defaults = {},
  selected,
  onSelected,
  onBuildOne,
  onOpenJob,
  onChanged,
  onWorkspaceChange,
}) => {
  const clips = sources
    .filter((source) => source.role === "aroll")
    .slice()
    .sort((a, b) => (b.added_at || 0) - (a.added_at || 0) || b.order - a.order);

  const dense = clips.length >= 8;
  const [filter, setFilter] = useState<Filter>("all");
  const [tagFilter, setTagFilter] = useState("");
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [view, setView] = useState<ViewMode>(dense ? "list" : "grid");
  const [groupBy, setGroupBy] = useState<GroupMode>(dense ? "month" : "day");

  useEffect(() => {
    setQuery("");
    setFilter("all");
    setTagFilter("");
  }, [projectId]);

  const tracked = useMemo(
    () => clips.map((clip) => ({ clip, track: trackClip(clip.id, builds) })),
    [clips, builds],
  );

  const allTags = useMemo(() => {
    const set = new Set<string>();
    for (const clip of clips) {
      for (const tag of clip.tags || []) set.add(tag);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b, "vi"));
  }, [clips]);

  const counts = useMemo(() => {
    const next: Record<Filter, number> = {
      all: clips.length,
      empty: 0,
      running: 0,
      failed: 0,
      pending: 0,
      ready: 0,
      posted: 0,
      unposted: 0,
      no_bgm: 0,
    };
    for (const row of tracked) {
      next[row.track.kind] += 1;
      if (isPosted(row.clip)) next.posted += 1;
      else next.unposted += 1;
      if (!hasBgm(row.clip)) next.no_bgm += 1;
    }
    return next;
  }, [clips.length, tracked]);

  const q = query.trim().toLowerCase();
  const visible = tracked.filter((row) => {
    if (filter === "posted" && !isPosted(row.clip)) return false;
    else if (filter === "unposted" && isPosted(row.clip)) return false;
    else if (filter === "no_bgm" && hasBgm(row.clip)) return false;
    else if (filter !== "all" && filter !== "posted" && filter !== "unposted" && filter !== "no_bgm") {
      if (row.track.kind !== filter) return false;
    }
    if (tagFilter && !(row.clip.tags || []).includes(tagFilter)) return false;
    if (q) {
      const hay = [
        row.clip.label,
        row.clip.notes,
        ...(row.clip.tags || []),
        row.clip.bgm_name,
        String(row.clip.meta?.platform || ""),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const openSource = openId ? clips.find((clip) => clip.id === openId) : undefined;

  useEffect(() => {
    onWorkspaceChange?.(Boolean(openSource));
    return () => onWorkspaceChange?.(false);
  }, [openSource, onWorkspaceChange]);

  if (openSource) {
    return (
      <VideoWorkspace
        projectId={projectId}
        source={openSource}
        builds={builds}
        defaults={defaults}
        onChanged={onChanged}
        onClose={() => {
          setOpenId(null);
          onSelected([]);
        }}
        onCreateShort={(id) => {
          onSelected([]);
          onBuildOne(id);
        }}
        onOpenJob={onOpenJob}
      />
    );
  }

  const groups = visible.reduce<Record<string, typeof visible>>((acc, row) => {
    const key = groupBy === "month"
      ? monthKey(row.clip.added_at || 0)
      : dayKey(row.clip.added_at || 0);
    (acc[key] ||= []).push(row);
    return acc;
  }, {});
  const keys = Object.keys(groups).sort().reverse();
  const visibleIds = visible.map((row) => row.clip.id);
  const allOn = visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id));

  const toggle = (id: string) => {
    onSelected(selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id]);
  };

  if (clips.length === 0) {
    return <p className="muted small">Chưa có video — kéo file mp4/mov vào ô trên.</p>;
  }

  const quickFilters: Filter[] = ["unposted", "posted", "empty", "no_bgm"];

  return (
    <div className="stack">
      <div className="clip-toolbar card">
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            className="clip-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm tên, tag, ghi chú…"
            aria-label="Tìm video"
          />
          <div className="folder-chips" style={{ margin: 0 }}>
            <button
              type="button"
              className={`ghost small ${view === "list" ? "active" : ""}`}
              onClick={() => setView("list")}
            >
              Danh sách
            </button>
            <button
              type="button"
              className={`ghost small ${view === "grid" ? "active" : ""}`}
              onClick={() => setView("grid")}
            >
              Lưới
            </button>
            <button
              type="button"
              className={`ghost small ${groupBy === "day" ? "active" : ""}`}
              onClick={() => setGroupBy("day")}
            >
              Theo ngày
            </button>
            <button
              type="button"
              className={`ghost small ${groupBy === "month" ? "active" : ""}`}
              onClick={() => setGroupBy("month")}
            >
              Theo tháng
            </button>
          </div>
        </div>

        <div className="folder-chips" style={{ marginTop: 8 }}>
          <button
            type="button"
            className={`ghost small ${filter === "all" && !tagFilter ? "active" : ""}`}
            onClick={() => {
              setFilter("all");
              setTagFilter("");
            }}
          >
            Tất cả ({counts.all})
          </button>
          {quickFilters.map((id) => {
            const n = counts[id];
            if (!n) return null;
            return (
              <button
                key={id}
                type="button"
                className={`ghost small ${filter === id ? "active" : ""}`}
                onClick={() => setFilter(filter === id ? "all" : id)}
              >
                {FILTERS.find((item) => item.id === id)?.label} ({n})
              </button>
            );
          })}
          <button
            type="button"
            className={`ghost small ${showMoreFilters ? "active" : ""}`}
            onClick={() => setShowMoreFilters((value) => !value)}
          >
            {showMoreFilters ? "▾ Bớt" : "▸ Tag & thêm"}
          </button>
        </div>

        {showMoreFilters && (
          <>
            <div className="folder-chips" style={{ marginTop: 8 }}>
              {FILTERS.filter((item) => !["all", ...quickFilters].includes(item.id)).map((item) => {
                const n = counts[item.id];
                if (!n) return null;
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`ghost small ${filter === item.id ? "active" : ""}`}
                    onClick={() => setFilter(item.id)}
                  >
                    {item.label} ({n})
                  </button>
                );
              })}
            </div>
            {allTags.length > 0 && (
              <div className="folder-chips" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className={`ghost small ${!tagFilter ? "active" : ""}`}
                  onClick={() => setTagFilter("")}
                >
                  Mọi tag
                </button>
                {allTags.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    className={`ghost small ${tagFilter === tag ? "active" : ""}`}
                    onClick={() => setTagFilter(tagFilter === tag ? "" : tag)}
                  >
                    #{tag}
                  </button>
                ))}
              </div>
            )}
            <label className="check-row soft" style={{ margin: "8px 0 0", padding: "6px 10px", alignSelf: "flex-start" }}>
              <input
                type="checkbox"
                checked={allOn}
                onChange={() => onSelected(allOn ? [] : visibleIds)}
              />
              <span className="small">Chọn đang hiện ({visibleIds.length})</span>
            </label>
          </>
        )}

        <p className="muted small" style={{ margin: "8px 0 0" }}>
          Đang hiện <b>{visible.length}</b>/{clips.length}
          {selected.length ? ` · đã chọn ${selected.length}` : ""}
          {" · "}{counts.unposted} chưa đăng · {counts.no_bgm} chưa nhạc
        </p>
      </div>

      {visible.length === 0 && (
        <p className="muted small">Không khớp bộ lọc / tìm kiếm.</p>
      )}

      {keys.map((key) => (
        <div key={key} className="clip-group">
          <div className="clip-group-head">
            {groupBy === "month" ? monthLabel(key) : dayLabel(key)}
            <span className="muted"> · {groups[key].length}</span>
          </div>

          {view === "list" ? (
            <div className="clip-list">
              {groups[key].map(({ clip, track }) => {
                const product = track.product;
                const posted = isPosted(clip);
                const poster = product?.has_thumbnail
                  ? `${product.media_base || `/api/media/${product.job_id}/`}thumbnail.jpg`
                  : clip.thumb
                    ? `/api/projects/${projectId}/sources/${clip.id}/thumb`
                    : "";
                const views = formatViews(clip.meta?.views);
                const on = selected.includes(clip.id);
                return (
                  <div key={clip.id} className={`clip-row ${on ? "on" : ""}`}>
                    {showMoreFilters && (
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => toggle(clip.id)}
                        aria-label={`Chọn ${clip.label}`}
                      />
                    )}
                    <button type="button" className="clip-row-main" onClick={() => setOpenId(clip.id)}>
                      {poster ? <img src={poster} alt="" /> : <div className="clip-row-ph" />}
                      <div className="clip-row-meta">
                        <div className="clip-row-title">{clip.label || clip.id}</div>
                        <div className="clip-row-sub muted small">
                          {groupBy === "month" ? dayLabel(dayKey(clip.added_at || 0)) : null}
                          {groupBy === "month" ? " · " : ""}
                          {clip.duration.toFixed(0)}s
                          {clip.bgm_name
                            ? ` · ${clip.bgm_name.replace(/^bgm_/, "").replace(/\.mp3$/, "")}`
                            : " · chưa nhạc"}
                          {views ? ` · ${views}` : ""}
                          {posted && clip.meta?.platform ? ` · ${String(clip.meta.platform)}` : ""}
                        </div>
                        <div className="clip-row-tags">
                          <span className={`badge ${track.kind === "ready" ? "completed" : track.kind === "failed" ? "failed" : track.kind === "running" ? "running" : "pending"}`}>
                            {track.label}
                          </span>
                          {posted ? <span className="badge completed">Đã đăng</span> : <span className="badge pending">Chưa đăng</span>}
                          {(clip.tags || []).slice(0, 3).map((tag) => (
                            <span key={tag} className="badge pending">#{tag}</span>
                          ))}
                        </div>
                      </div>
                    </button>
                    <div className="clip-row-actions">
                      <button className="primary small" type="button" onClick={() => setOpenId(clip.id)}>
                        Mở
                      </button>
                      <button className="ghost small" type="button" onClick={() => onBuildOne(clip.id)}>
                        {product?.has_final ? "Dựng lại…" : "Dựng…"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="clip-grid">
              {groups[key].map(({ clip, track }) => {
                const product = track.product;
                const poster = product?.has_thumbnail
                  ? `${product.media_base || `/api/media/${product.job_id}/`}thumbnail.jpg`
                  : clip.thumb
                    ? `/api/projects/${projectId}/sources/${clip.id}/thumb`
                    : "";
                const posted = isPosted(clip);
                return (
                  <div key={clip.id} className={`clip-card ${track.kind}`}>
                    <button type="button" className="clip-open" onClick={() => setOpenId(clip.id)}>
                      {poster ? <img src={poster} alt="" /> : <div className="clip-ph" />}
                      <div className="clip-meta">
                        <b title={clip.label}>{clip.label || clip.id}</b>
                        <div className="muted small">{clip.duration.toFixed(0)}s</div>
                        <span className={`badge ${track.kind === "ready" ? "completed" : "pending"}`}>
                          {track.label}
                        </span>
                        {posted && <span className="badge completed">Đã đăng</span>}
                        {(clip.tags || []).slice(0, 2).map((tag) => (
                          <span key={tag} className="badge pending">#{tag}</span>
                        ))}
                      </div>
                    </button>
                    <div className="clip-actions">
                      <button className="primary small" type="button" onClick={() => setOpenId(clip.id)}>
                        Mở
                      </button>
                      <button className="ghost small" type="button" onClick={() => onBuildOne(clip.id)}>
                        Dựng…
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};
