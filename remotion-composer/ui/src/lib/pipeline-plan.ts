/**
 * Shared stage groups for the autoedit pipeline.
 * Keep in sync with lib/talking_head_edit/job_store.py STAGES.
 */

export const ALL_STAGES = [
  "probe",
  "transcribe",
  "select",
  "direct",
  "audit",
  "calibrate",
  "resolve",
  "render",
  "verify",
] as const;

export type StageKey = (typeof ALL_STAGES)[number];

/** LLM + encode up to previewable props/src — no Remotion render. */
export const PREPARE_STAGES: StageKey[] = [
  "probe",
  "transcribe",
  "select",
  "direct",
  "audit",
  "calibrate",
  "resolve",
];

/** Everything including final MP4. */
export const FULL_STAGES: StageKey[] = [...ALL_STAGES];

/** Only Remotion + mechanical QA. */
export const RENDER_STAGES: StageKey[] = ["render", "verify"];

export const STAGE_LABELS: Record<StageKey, string> = {
  probe: "Đọc file",
  transcribe: "Tách lời",
  select: "Chọn take",
  direct: "AI dựng khung",
  audit: "Kiểm duyệt cắt",
  calibrate: "Dò thông số",
  resolve: "Cắt + encode",
  render: "Render MP4",
  verify: "Đo kiểm",
};

export type StageGroup = "llm" | "encode" | "render";

export const STAGE_GROUP: Record<StageKey, StageGroup> = {
  probe: "encode",
  transcribe: "encode",
  select: "llm",
  direct: "llm",
  audit: "llm",
  calibrate: "llm",
  resolve: "encode",
  render: "render",
  verify: "render",
};

export const GROUP_LABEL: Record<StageGroup, string> = {
  llm: "Gọi LLM (token)",
  encode: "Cơ học / encode local",
  render: "Render MP4 (CPU nặng)",
};

/** How the build should run after create. */
export type RunMode =
  | "prepare"
  | "prepare_and_queue"
  | "full_local"
  | "create_only";

export interface RunModeMeta {
  id: RunMode;
  title: string;
  summary: string;
  stages: StageKey[] | null; // null = full default chain
  run: boolean;
  autoEnqueueCloud: boolean;
  tone: "ok" | "warn" | "info";
  warnings: string[];
}

export const RUN_MODES: RunModeMeta[] = [
  {
    id: "prepare",
    title: "Dựng + duyệt (dừng trước render)",
    summary:
      "Chạy LLM + cắt/encode đến khi có preview. KHÔNG render MP4 — bạn xem lại rồi mới chọn render local / xếp batch cloud.",
    stages: PREPARE_STAGES,
    run: true,
    autoEnqueueCloud: false,
    tone: "ok",
    warnings: [
      "Tốn token ở bước Direct/Audit (LLM).",
      "Resolve encode local có thể vài phút — chưa phải render cuối.",
      "Sau khi xong: mở job → xem preview → Render local hoặc Xếp lịch cloud.",
    ],
  },
  {
    id: "prepare_and_queue",
    title: "Dựng rồi xếp lịch cloud batch",
    summary:
      "Giống trên, khi resolve xong tự xếp job vào lịch cloud. Bạn flush batch trên trang Lịch render (cần xác nhận tiền).",
    stages: PREPARE_STAGES,
    run: true,
    autoEnqueueCloud: true,
    tone: "info",
    warnings: [
      "Chỉ XẾP LỊCH — chưa thuê máy, chưa tốn tiền cloud.",
      "Footage sẽ rời máy khi bạn bấm Render batch / Cloud ngay (có modal xác nhận).",
      "Cloud config phải enabled khi flush; xếp lịch vẫn được khi cloud đang tắt.",
    ],
  },
  {
    id: "full_local",
    title: "Chạy full local (render ngay)",
    summary:
      "Cả pipeline trên máy này, gồm Render MP4 + verify. Không dừng để duyệt giữa chừng.",
    stages: null,
    run: true,
    autoEnqueueCloud: false,
    tone: "warn",
    warnings: [
      "Render Remotion có thể 10–30+ phút, chiếm CPU full — không nên song song nhiều job.",
      "Khó sửa prompt giữa chừng: muốn đổi khung phải dựng lại (Direct) rồi render lại.",
      "Muốn gom batch cloud: chọn “dừng trước render” thay vì mode này.",
    ],
  },
  {
    id: "create_only",
    title: "Chỉ tạo job — chạy tay từng bước",
    summary:
      "Tạo bản dựng, không tự chạy stage. Trên trang job bạn bấm từng nhóm (LLM → encode → render).",
    stages: null,
    run: false,
    autoEnqueueCloud: false,
    tone: "info",
    warnings: [
      "Job ở trạng thái created — chưa tốn token / CPU cho đến khi bạn bấm chạy.",
    ],
  },
];

export function stagesForMode(mode: RunMode): string[] | undefined {
  const meta = RUN_MODES.find((m) => m.id === mode);
  if (!meta || !meta.run) return undefined;
  return meta.stages ? [...meta.stages] : undefined;
}

/** sessionStorage key so job-detail can auto-enqueue after prepare finishes. */
export const buildPlanKey = (jobId: string) => `autoedit-build-plan:${jobId}`;

export interface BuildPlan {
  mode: RunMode;
  autoEnqueueCloud: boolean;
  stopBeforeRender: boolean;
  createdAt: number;
}

export function saveBuildPlan(jobId: string, plan: BuildPlan) {
  try {
    sessionStorage.setItem(buildPlanKey(jobId), JSON.stringify(plan));
  } catch {
    /* private mode */
  }
}

export function loadBuildPlan(jobId: string): BuildPlan | null {
  try {
    const raw = sessionStorage.getItem(buildPlanKey(jobId));
    return raw ? (JSON.parse(raw) as BuildPlan) : null;
  } catch {
    return null;
  }
}

export function clearBuildPlan(jobId: string) {
  try {
    sessionStorage.removeItem(buildPlanKey(jobId));
  } catch {
    /* */
  }
}
