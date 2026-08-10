import React, { useEffect } from "react";

export interface LightboxItem {
  name: string;
  url: string;
  label: string;
  caption?: string;
}

/**
 * Full-size viewer for a `.preview-grid` of graded stills.
 *
 * Thumbnails in that grid shrink to ~120-210px wide once there are 2-3 of
 * them side by side — too small to judge skin texture or a colour shift by
 * eye, which is the whole point of the comparison. Any tile opens here at
 * near-native size, with the other variants one click (or arrow key) away
 * for an immediate side-by-side without re-opening anything.
 */
export const PreviewLightbox: React.FC<{
  items: LightboxItem[];
  index: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
}> = ({ items, index, onClose, onNavigate }) => {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (items.length < 2) return;
      if (e.key === "ArrowRight") onNavigate((index + 1) % items.length);
      if (e.key === "ArrowLeft") onNavigate((index - 1 + items.length) % items.length);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, items.length, onClose, onNavigate]);

  const item = items[index];
  if (!item) return null;

  return (
    <div className="lightbox-overlay" onClick={onClose}>
      <button className="lightbox-close" onClick={onClose} aria-label="Đóng">
        ✕
      </button>
      <div className="lightbox-content" onClick={(e) => e.stopPropagation()}>
        <img src={item.url} alt={item.name} />
        <div className="lightbox-nav">
          {items.length > 1 && (
            <button onClick={() => onNavigate((index - 1 + items.length) % items.length)}>◀</button>
          )}
          <strong>{item.label}</strong>
          {items.length > 1 && (
            <button onClick={() => onNavigate((index + 1) % items.length)}>▶</button>
          )}
        </div>
        {item.caption && <span className="muted small">{item.caption}</span>}
      </div>
    </div>
  );
};
