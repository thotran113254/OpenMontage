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

/** Plain-language stage names for non-technical users. The technical stage id
 * (`probe`, `calibrate`, …) still shows up as a title tooltip wherever these
 * labels are rendered — see stage-progress.tsx. */
export const STAGE_LABELS: Record<StageKey, string> = {
  probe: "Đọc file",
  transcribe: "Tách lời",
  select: "Chọn take",
  direct: "AI dựng khung",
  audit: "Kiểm cắt",
  calibrate: "Dò màu/tiếng",
  resolve: "Cắt & ghép",
  render: "Render MP4",
  verify: "Kiểm file cuối",
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

export const USER_PHASES: { id: string; label: string; stages: StageKey[] }[] = [
  { id: "prep", label: "1. Lọc nội dung", stages: ["probe", "transcribe", "select"] },
  { id: "ai", label: "2. AI dựng khung", stages: ["direct", "audit", "calibrate"] },
  { id: "out", label: "3. Cắt & xuất", stages: ["resolve", "render", "verify"] },
];

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
    title: "Xem trước (khuyên dùng)",
    summary:
      "Cắt và xem khung trước, chưa xuất MP4. Bạn duyệt xong rồi mới bấm render trên máy này.",
    stages: PREPARE_STAGES,
    run: true,
    autoEnqueueCloud: false,
    tone: "ok",
    warnings: [
      "Tốn token ở bước Direct/Audit (LLM).",
      "Encode preview có thể vài phút — chưa phải file cuối.",
      "Sau khi xong: mở bản dựng → xem preview → Xuất MP4 trên máy này.",
    ],
  },
  {
    id: "prepare_and_queue",
    title: "Xem trước rồi xếp lịch cloud",
    summary:
      "Giống xem trước, khi xong tự xếp vào lịch cloud. Flush batch trên trang Lịch cloud (cần xác nhận tiền).",
    stages: PREPARE_STAGES,
    run: true,
    autoEnqueueCloud: true,
    tone: "info",
    warnings: [
      "Chỉ xếp lịch — chưa thuê máy, chưa tốn tiền cloud.",
      "Footage rời máy khi bạn bấm Render batch / Cloud ngay (có xác nhận).",
      "Cloud phải bật khi flush; xếp lịch vẫn được khi cloud đang tắt.",
    ],
  },
  {
    id: "full_local",
    title: "Xuất MP4 luôn (máy này)",
    summary:
      "Chạy hết pipeline trên máy này, gồm render MP4. Không dừng giữa chừng để duyệt.",
    stages: null,
    run: true,
    autoEnqueueCloud: false,
    tone: "warn",
    warnings: [
      "Render Remotion có thể 10–30+ phút, chiếm CPU — đừng chạy nhiều bản cùng lúc.",
      "Khó sửa prompt giữa chừng: muốn đổi khung phải dựng lại rồi render lại.",
      "Muốn xem trước rồi mới xuất: chọn “Xem trước”.",
    ],
  },
  {
    id: "create_only",
    title: "Chỉ tạo job — chạy tay từng bước",
    summary:
      "Tạo bản dựng, không tự chạy. Trên trang bản dựng bạn bấm từng nhóm (LLM → encode → render).",
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
