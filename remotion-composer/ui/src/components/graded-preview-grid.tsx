import React, { useState } from "react";
import { GradeVariant } from "../api/client";
import { PreviewLightbox } from "./preview-lightbox";

/** `preview_grades` (preview.py) always names these three the same way. */
export const GRADE_VARIANT_LABEL: Record<string, string> = {
  raw: "Gốc (chưa xử lý)",
  hien_tai: "Đang dùng",
  thu_nghiem: "Thử nghiệm",
};

/**
 * Shared thumbnail grid + click-to-enlarge for graded-still comparisons.
 *
 * Both the Màu and Da tabs preview the same response shape
 * (`preview_grades`'s raw/hien_tai/thu_nghiem variants) at the same shrunken
 * `.preview-grid` size — 1-3 tiles auto-fit into ~120-420px each, too small
 * to judge skin texture or a colour shift by eye. This is the one place that
 * turns a variant into a clickable tile, so the fix lands in both tabs.
 *
 * Caption text stays with the caller: the Màu tab shows a warm-bias delta
 * against `raw`, the Da tab does not — only the grid/lightbox plumbing is
 * shared, not the domain-specific numbers.
 */
export const GradedPreviewGrid: React.FC<{
  variants: GradeVariant[];
  labelFor: (name: string) => string;
  captionFor: (variant: GradeVariant) => string;
}> = ({ variants, labelFor, captionFor }) => {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  if (variants.length === 0) return null;

  return (
    <>
      <div className="preview-grid">
        {variants.map((variant, i) => (
          <figure key={variant.name} className="preview-tile">
            <img src={variant.url} alt={variant.name} onClick={() => setOpenIndex(i)} />
            <figcaption>
              <strong>{labelFor(variant.name)}</strong>
              <span className="muted small">{captionFor(variant)}</span>
            </figcaption>
          </figure>
        ))}
      </div>

      {openIndex !== null && (
        <PreviewLightbox
          items={variants.map((v) => ({
            name: v.name,
            url: v.url,
            label: labelFor(v.name),
            caption: captionFor(v),
          }))}
          index={openIndex}
          onClose={() => setOpenIndex(null)}
          onNavigate={setOpenIndex}
        />
      )}
    </>
  );
};
