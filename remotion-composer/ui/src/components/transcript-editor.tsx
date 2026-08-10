import React, { useMemo, useRef, useState } from "react";

/**
 * Word-level transcript as the primary interface.
 *
 * Text-first on purpose: people think in sentences, not frames. Click a word to
 * seek; shift-click to select a range and mark it cut or kept.
 *
 * Virtualized from the start rather than "optimised later" — a 4000-word take
 * renders 4000 spans, and the lag is immediate and unpleasant. Only the visible
 * window plus a margin is mounted.
 */

export interface TranscriptWord {
  word: string;
  start: number;
  end: number;
  src?: string;
  speaker?: string;
}

const ROW_HEIGHT = 30;
const WORDS_PER_ROW = 10;
const OVERSCAN_ROWS = 6;

export const TranscriptEditor: React.FC<{
  words: TranscriptWord[];
  /** Word index → second on the rendered timeline; without it the source second is used. */
  timeOf?: (index: number) => number;
  onSeek?: (seconds: number) => void;
  onMarkRange?: (from: number, to: number, action: "cut" | "keep") => void;
  cutRanges?: [number, number][];
  height?: number;
}> = ({ words, timeOf, onSeek, onMarkRange, cutRanges = [], height = 360 }) => {
  const [scrollTop, setScrollTop] = useState(0);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [selection, setSelection] = useState<[number, number] | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const rowCount = Math.ceil(words.length / WORDS_PER_ROW);
  const firstRow = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN_ROWS);
  const lastRow = Math.min(
    rowCount,
    Math.ceil((scrollTop + height) / ROW_HEIGHT) + OVERSCAN_ROWS,
  );

  const isCut = useMemo(() => {
    const flags = new Set<number>();
    for (const [from, to] of cutRanges) {
      for (let index = from; index <= to; index += 1) flags.add(index);
    }
    return flags;
  }, [cutRanges]);

  const click = (index: number, shiftKey: boolean) => {
    if (shiftKey && anchor !== null) {
      const range: [number, number] = anchor <= index ? [anchor, index] : [index, anchor];
      setSelection(range);
      return;
    }
    setAnchor(index);
    setSelection(null);
    onSeek?.(timeOf ? timeOf(index) : words[index].start);
  };

  const rows: React.ReactNode[] = [];
  for (let row = firstRow; row < lastRow; row += 1) {
    const from = row * WORDS_PER_ROW;
    rows.push(
      <div
        key={row}
        style={{
          position: "absolute",
          top: row * ROW_HEIGHT,
          left: 0,
          right: 0,
          height: ROW_HEIGHT,
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "0 8px",
        }}
      >
        <span className="muted small" style={{ width: 46, flexShrink: 0 }}>
          {from}
        </span>
        {words.slice(from, from + WORDS_PER_ROW).map((word, offset) => {
          const index = from + offset;
          const selected = selection && index >= selection[0] && index <= selection[1];
          const cut = isCut.has(index);
          return (
            <span
              key={index}
              onClick={(event) => click(index, event.shiftKey)}
              title={`${index} · ${(timeOf ? timeOf(index) : word.start).toFixed(2)}s${
                word.src ? ` · ${word.src}` : ""
              }`}
              style={{
                cursor: "pointer",
                padding: "1px 4px",
                borderRadius: 4,
                background: selected ? "rgba(56,189,248,0.28)" : "transparent",
                textDecoration: cut ? "line-through" : undefined,
                opacity: cut ? 0.45 : 1,
                whiteSpace: "nowrap",
              }}
            >
              {word.word}
            </span>
          );
        })}
      </div>,
    );
  }

  return (
    <div>
      <div className="row" style={{ alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span className="muted small">
          {words.length.toLocaleString("vi-VN")} từ · bấm để nhảy tới, shift-bấm để chọn đoạn
        </span>
        <span style={{ flex: 1 }} />
        {selection && (
          <>
            <span className="small">
              đã chọn {selection[0]}–{selection[1]}
            </span>
            <button
              className="ghost small"
              onClick={() => onMarkRange?.(selection[0], selection[1], "cut")}
            >
              Đánh dấu cắt
            </button>
            <button
              className="ghost small"
              onClick={() => onMarkRange?.(selection[0], selection[1], "keep")}
            >
              Giữ lại
            </button>
            <button className="ghost small" onClick={() => setSelection(null)}>
              Bỏ chọn
            </button>
          </>
        )}
      </div>

      <div
        ref={containerRef}
        onScroll={(event) => setScrollTop((event.target as HTMLDivElement).scrollTop)}
        style={{
          height,
          overflowY: "auto",
          position: "relative",
          background: "#151922",
          borderRadius: 8,
          border: "1px solid #2b3240",
        }}
      >
        <div style={{ height: rowCount * ROW_HEIGHT, position: "relative" }}>{rows}</div>
      </div>
    </div>
  );
};
