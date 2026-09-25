/** Vietnamese labels for job / stage status. Never show raw English in the UI. */

export const STATUS_LABEL: Record<string, string> = {
  created: "Chưa chạy",
  queued: "Đang chờ",
  running: "Đang chạy",
  completed: "Xong",
  completed_with_warnings: "Xong",
  failed: "Lỗi",
  cancelled: "Đã huỷ",
  pending: "Chờ",
};

export function statusLabel(status: string | undefined): string {
  if (!status) return "—";
  return STATUS_LABEL[status] || status;
}
