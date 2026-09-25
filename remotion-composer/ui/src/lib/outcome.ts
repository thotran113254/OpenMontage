/**
 * Turns the backend's outcome/report data into three plain-Vietnamese
 * groups — "Đã làm" / "Bị chặn" / "Chưa làm được" — instead of raw developer
 * text like `sửa 102, đổi ['cut_remove']`.
 *
 * Shared by version-history.tsx (full `state.versions[i].outcome`) and
 * chat-panel.tsx (a turn only ever carries `options_changed` + `not_done`).
 */
import { OutcomeBlockedCut } from "../api/client";

const OPTION_LABELS: Record<string, string> = {
  frame_preset: "Khung",
  bgm: "Nhạc nền",
  bgm_name: "Nhạc nền",
  bgm_volume: "Âm lượng nhạc",
  tempo: "Tốc độ nói",
  cut_level: "Mức cắt",
  cold_open: "Hook đầu",
};

/** Per-key value translations — falls back to the raw value (never the raw
 * key) when a key has no map or a value isn't in it. */
const OPTION_VALUE_LABELS: Record<string, Record<string, string>> = {
  cut_level: { light: "nhẹ", normal: "vừa", tight: "kỹ" },
  frame_preset: { none: "full khung", dark: "khung nền tối", light: "khung nền sáng", blur: "khung nền mờ" },
};

function formatOptionValue(key: string, value: unknown): string {
  if (typeof value === "boolean") return value ? "bật" : "tắt";
  if (value === null || value === undefined || value === "") return "—";
  const mapped = OPTION_VALUE_LABELS[key]?.[String(value)];
  return mapped ?? String(value);
}

function formatOptionChange(key: string, value: unknown): string {
  return `Đổi ${OPTION_LABELS[key] || key} → ${formatOptionValue(key, value)}`;
}

/** Plain-language bullets for a raw `{key: value}` options-changed map — used
 * for rollback's restored options as well as the outcome groups below. */
export function describeOptionsChanged(
  options: Record<string, unknown> | null | undefined,
): string[] {
  return Object.entries(options || {}).map(([key, value]) => formatOptionChange(key, value));
}

export interface OutcomeGroups {
  done: string[];
  blocked: string[];
  notDone: string[];
  /** Whole-video cut count (all versions combined), shown smaller/separately
   * from the "cut theo yêu cầu" line for this version/turn. */
  totalApplied?: number;
}

export interface OutcomeSource {
  /** This version/turn's own requested cuts — not the whole job's. */
  cuts_proposed?: number;
  cuts_applied?: number;
  cuts_blocked?: OutcomeBlockedCut[] | null;
  /** Whole-video count of cuts currently applied, across every version. */
  cuts_total_applied?: number | null;
  options_changed?: Record<string, unknown> | null;
  not_done?: string[] | null;
}

export function outcomeGroups(source: OutcomeSource | null | undefined): OutcomeGroups {
  const done: string[] = [];
  const blocked: string[] = [];
  const notDone: string[] = [];
  if (!source) return { done, blocked, notDone };

  if (source.cuts_applied || source.cuts_proposed) {
    const applied = source.cuts_applied ?? 0;
    const proposed = source.cuts_proposed;
    done.push(proposed ? `Cắt theo yêu cầu: ${applied}/${proposed}` : `Cắt theo yêu cầu: ${applied}`);
  }
  done.push(...describeOptionsChanged(source.options_changed));
  for (const cut of source.cuts_blocked || []) {
    const text = cut.text ? `“${cut.text}”` : "một đoạn";
    blocked.push(cut.reason ? `${text} — ${cut.reason}` : text);
  }
  for (const item of source.not_done || []) {
    if (item) notDone.push(item);
  }
  return {
    done,
    blocked,
    notDone,
    totalApplied: source.cuts_total_applied ?? undefined,
  };
}

/**
 * Stopgap cleanup for legacy one-line turn summaries authored server-side
 * (e.g. `"sửa 102, đổi ['cut_remove']"`) — strips Python list/quote syntax so
 * at least no raw developer punctuation reaches the user. Only used when a
 * turn has neither `options_changed` nor `not_done` yet; safe to delete once
 * every turn carries the structured fields.
 */
export function humanizeTurnResult(text: string): string {
  return text
    .replace(
      /\[\s*((?:'[^']*'|"[^"]*")(?:\s*,\s*(?:'[^']*'|"[^"]*"))*)\s*\]/g,
      (_match, inner: string) => inner.replace(/['"]/g, ""),
    )
    .replace(/['"]/g, "");
}
