import { Build } from "../api/client";
import { statusLabel } from "./status";

export type ClipKind = "ready" | "running" | "failed" | "pending" | "empty";

export type ClipTrack = {
  kind: ClipKind;
  label: string;
  latest?: Build;
  product?: Build;
};

const ACTIVE = new Set(["running", "queued", "created"]);

export function trackClip(sourceId: string, builds: Build[]): ClipTrack {
  const hits = builds
    .filter((build) => (build.source_ids || []).includes(sourceId))
    .slice()
    .sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  const product = hits.find((build) => build.has_final);
  const latest = hits[0];
  if (product && latest && !latest.has_final && ACTIVE.has(latest.status)) {
    return { kind: "running", label: "Đang tạo short mới", latest, product };
  }
  if (product) {
    return { kind: "ready", label: "Đã có short", latest: product, product };
  }
  if (!latest) return { kind: "empty", label: "Chưa tạo short" };
  if (latest.status === "failed") {
    return { kind: "failed", label: "Tạo short lỗi", latest };
  }
  if (ACTIVE.has(latest.status)) {
    return { kind: "running", label: statusLabel(latest.status), latest };
  }
  if (!latest.has_final && latest.status === "completed") {
    return { kind: "pending", label: "Đã xem trước", latest };
  }
  return { kind: "pending", label: statusLabel(latest.status), latest };
}

export function countByKind(sourceIds: string[], builds: Build[]) {
  const counts = { empty: 0, running: 0, failed: 0, pending: 0, ready: 0 };
  for (const id of sourceIds) {
    counts[trackClip(id, builds).kind] += 1;
  }
  return counts;
}
