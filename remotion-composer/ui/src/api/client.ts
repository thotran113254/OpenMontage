// Thin API layer over the Python job server. Vite proxies /api to it.
// Synchronized with the FastAPI backend OpenAPI schema via schema.d.ts.

import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

export type ApiJobSummary = components["schemas"]["JobSummaryResponse"];
export type ApiJobDetail = components["schemas"]["JobDetailResponse"];
export type ApiProjectDetail = components["schemas"]["ProjectDetailResponse"];
export type ApiProjectSummary = components["schemas"]["ProjectSummaryResponse"];
export type ApiCreateProjectRequest = components["schemas"]["CreateProjectRequest"];
export type ApiCreateJobRequest = components["schemas"]["CreateJobRequest"];
export type ApiReviseRequest = components["schemas"]["ReviseRequest"];
export type ApiRollbackRequest = components["schemas"]["RollbackRequest"];
export type ApiChatRequest = components["schemas"]["ChatRequest"];
export type ApiChatResponse = components["schemas"]["ChatResponse"];
export type ApiGradePreviewResponse = components["schemas"]["GradePreviewResponse"];
export type ApiAudioPreviewResponse = components["schemas"]["AudioPreviewResponse"];
export type ApiClipPreviewResponse = components["schemas"]["ClipPreviewResponse"];
export type ApiCloudStatusResponse = components["schemas"]["CloudStatusResponse"];
export type ApiCloudQueueResponse = components["schemas"]["CloudQueueResponse"];

export type StageName =
  | "probe" | "transcribe" | "select" | "direct" | "audit" | "calibrate"
  | "resolve" | "render" | "verify" | "visuals";

export interface StageState {
  status?: "pending" | "running" | "completed" | "failed";
  cached?: boolean;
  duration_seconds?: number;
  error?: string | null;
  result?: Record<string, unknown>;
}

export interface JobSummary {
  job_id: string;
  title: string;
  status: string;
  created_at: number;
  current_version: number;
  cost_usd?: number;
  project_id?: string;
  stages: Record<StageName, StageState>;
  options: Record<string, unknown>;
}

export interface JobDetail extends JobSummary {
  audit_report: AuditReport | null;
  resolve_report: Record<string, any> | null;
  verify_report: VerifyReport | null;
  props: TimelineProps | null;
  has_final: boolean;
  has_thumbnail?: boolean;
  visuals_report?: {
    kicker?: string;
    accent?: string;
    thumbnail_text?: string;
    files?: Record<string, string | null>;
    errors?: string[];
  } | null;
  media_base: string;
  probe?: Record<string, unknown>;
}

export interface AuditReport {
  cuts_proposed: number;
  cuts_accepted: number;
  cuts_kept: number;
  cut_decisions: {
    w: [number, number];
    decision: "remove" | "keep";
    reason: string;
    source?: string;
    before?: string;
    cut?: string;
    after?: string;
    joined?: string;
  }[];
  removed_resources: string[];
  quality: {
    caption_coverage: number;
    captions_over_9_words: number;
    keyword_in_card: string[];
    card_count: number;
    event_count: number;
    uncovered_words: number[];
  };
}

export interface VerifyIssue {
  /** Stable code autopilot matches on; see lib/talking_head_edit/remedies.py. */
  code: string;
  message: string;
}

export interface TimelineViewRef {
  at: number;
  /** Job-relative path; the media route serves it by filename. */
  path: string;
  score: number;
  reasons: string[];
}

export interface VerifyReport {
  passed: boolean;
  duration_expected: number;
  duration_actual: number;
  av_drift: number;
  integrated_lufs: number | null;
  issues: (VerifyIssue | string)[];
  frames: { at: number; mean_luma: number | null; frame: string | null }[];
  seams?: number[];
  suspect_seams?: { at: number; score: number; reasons: string[] }[];
  timeline_views?: TimelineViewRef[];
  broll?: { broll_present: boolean | null; clips: unknown[] };
  requires_human_review: string[];
}

export interface SpineResponse {
  words: { word: string; start: number; end: number; src?: string; speaker?: string }[];
  boundaries: number[];
  cut_ranges: [number, number][];
  speakers: string[];
}

export interface TimelineProps {
  videoSrc: string;
  /** Lighter re-encode of videoSrc for the Player; MonaTimeline falls back to
   * videoSrc when absent (older jobs, or the proxy encode failed). */
  previewVideoSrc?: string | null;
  events: any[];
  durationSeconds: number;
  brandPill?: string;
  bgm?: { name: string; volume: number; durationSeconds: number };
}

/** One rejected cut the audit/verifier kept instead of removing. */
export interface OutcomeBlockedCut {
  w?: [number, number];
  text?: string;
  reason?: string;
}

/**
 * What actually happened for a version/turn, in terms the UI can render as
 * "Đã làm / Bị chặn / Chưa làm được" instead of a raw developer summary.
 * Written by the pipeline after audit (`state.versions[i].outcome`); a chat
 * turn only ever carries `options_changed` + `not_done`. All fields are
 * optional and read defensively — the backend schema for this is landing
 * concurrently, so older jobs/turns simply omit it.
 *
 * `cuts_proposed`/`cuts_applied`/`cuts_blocked` describe only what THIS
 * version/turn asked for, not the whole job — `cuts_total_applied` is the
 * separate whole-video count.
 */
export interface VersionOutcome {
  cuts_proposed?: number;
  cuts_applied?: number;
  cuts_blocked?: OutcomeBlockedCut[];
  cuts_total_applied?: number;
  options_changed?: Record<string, unknown>;
  not_done?: string[];
}

export interface JobVersion {
  version: number;
  kind: "director" | "revise" | "manual" | "rollback";
  instruction?: string;
  is_current: boolean;
  has_props: boolean;
  usage?: { total_tokens?: number };
  changes?: {
    added: number; removed: number; modified: number; top_level_changed: string[];
  };
  /** See `VersionOutcome`. Absent on jobs built before this landed. */
  outcome?: VersionOutcome | null;
}

/** One graded frame of the RAW source — what resolve would bake into src.mp4. */
export interface GradeVariant {
  name: string;
  image: string;
  url: string;
  grade?: Record<string, number>;
  /** `grade` with the measured sharpening layered on, i.e. what ffmpeg ran. */
  applied_grade?: Record<string, number>;
  /** `warm_bias` is the useful one: how far above the source the grade pushes warm. */
  stats: { luma?: number; warm_bias?: number };
  face: { luma?: number; warm_bias?: number };
}

/** What resolve would actually encode — the preview matches these, not the nominal size. */
export interface GradeContext {
  width: number;
  height: number;
  source_width: number | null;
  measured_sharpen: { sharpen: number; clarity: number } | null;
  /** measured = opt-in calibration · human = explicit override · grade = requested grade only */
  sharpen_source: "measured" | "human" | "grade";
  frame_preset: string;
}

export interface GradePreview {
  at_seconds: number;
  variants: GradeVariant[];
  contact_sheet: string;
  contact_sheet_url: string;
  base_grade: Record<string, number>;
  context: GradeContext;
  cached: boolean;
}

/** A named, reusable grade fragment — global, not tied to any job (`config/look-presets.json`). */
export interface LookPreset {
  name: string;
  grade: Record<string, number>;
  created_at: string;
}

/** Prompt + BGM + ticks reused across projects (`config/edit-styles.json`). */
export interface EditStyle {
  id: string;
  title: string;
  source_project_id?: string;
  options: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface BgmTrack {
  name: string;
  group: string;
  mood: string;
  custom?: boolean;
}

export interface AudioSample {
  preset: string;
  url: string;
  is_current: boolean;
}

export interface AudioPreview {
  at_seconds: number;
  duration_seconds: number;
  current_preset?: string;
  samples: AudioSample[];
}

export interface ClipPreview {
  url: string;
  version: number;
  start_seconds: number;
  duration_seconds: number;
  scale: number;
  crf: number;
}

export interface ProgressEvent {
  ts: number;
  type: string;
  stage: string;
  message: string;
  percent?: number;
}

// ---- projects, prompts, chat -------------------------------------------
// Shapes copied from real responses rather than guessed — see the endpoint
// tests in tests/test_talking_head_project_api.py for the authoritative source.

export interface AssemblyConfig {
  mode: "auto" | "sequential" | "best_take";
  speaker_aware: "auto" | boolean;
  broll_overlay: boolean;
  cross_source_cut: boolean;
  take_detect: "suggest" | "apply" | "off";
}

export interface ProjectSource {
  id: string;
  file: string;
  label: string;
  duration: number;
  width: number;
  height: number;
  role: "aroll" | "broll";
  speech: boolean;
  order: number;
  take_group: string;
  thumb: string | null;
  has_audio: boolean;
  mean_volume_db: number | null;
  warnings?: string[];
  added_at?: number;
  /** Free-form tags for filtering across weeks/months. */
  tags?: string[];
  /** Human note for this clip. */
  notes?: string;
  /**
   * Flexible metrics / CRM fields the user owns.
   * Common keys: posted, posted_at, platform, url, views, likes…
   */
  meta?: Record<string, unknown>;
  /** Preferred BGM for this clip (from shared library). Empty = AI pick at build. */
  bgm_name?: string;
  bgm_volume?: number | null;
}

export interface ProjectSummary {
  project_id: string;
  title: string;
  folder?: string;
  created_at: number;
  source_count: number;
  aroll_count: number;
  ready_count?: number;
  pending_count?: number;
  total_seconds: number;
  build_count: number;
  thumb: string | null;
}

export interface Build {
  job_id: string;
  title: string;
  status: string;
  created_at: number;
  current_version: number;
  cost_usd: number;
  has_final: boolean;
  has_thumbnail?: boolean;
  media_base?: string;
  prompt: string;
  source_ids?: string[];
  source_labels?: string[];
  stages: Record<string, string | null>;
}

export interface ProjectDetail {
  project_id: string;
  title: string;
  folder?: string;
  sources: ProjectSource[];
  assembly: Partial<AssemblyConfig>;
  assembly_resolved: AssemblyConfig;
  /** Which layer each resolved value came from — the UI shows what is inherited. */
  assembly_origin: Record<keyof AssemblyConfig, "global" | "project" | "job">;
  keyterms: string[];
  defaults: Record<string, unknown>;
  builds: Build[];
}

export interface TakeSuggestion {
  take_group: string;
  sources: string[];
  confidence: number;
  reason: string;
}

export interface PromptVersionInfo {
  version: string;
  source: "builtin" | "override";
  is_current: boolean;
  note?: string;
  author?: string;
}

export interface PromptCatalogEntry {
  id: string;
  current: string;
  versions: PromptVersionInfo[];
}

export interface PromptDetail extends PromptCatalogEntry {
  version: string;
  body: string;
  placeholders: string[];
}

export interface AbEstimate {
  versions: string[];
  words: number;
  calls: number;
  estimated_tokens_in: number;
  estimated_cost_usd: number;
  note: string;
}

export interface AbBranch {
  version: string;
  cards: number;
  caption_coverage: number;
  captions_over_9w: number;
  keyword_in_card: number;
  cuts_proposed: number;
  cuts_rejected_by_verifier: number;
  tokens: { in: number; out: number };
  cost_usd: number;
}

export interface AbResult {
  created_at: number;
  words: number;
  card_goal: number | null;
  a: AbBranch;
  b: AbBranch;
  verdict: { winner: "a" | "b" | "tie"; reason: string };
  total_cost_usd: number;
}

export interface ChatTurn {
  ts: number;
  message: string;
  applied: boolean;
  dry_run: boolean;
  result: string;
  to_version?: number | null;
  tokens?: number;
  error?: string;
  /** Job options this turn changed, e.g. `{ tempo: 1.1 }`. See `VersionOutcome`. */
  options_changed?: Record<string, unknown> | null;
  /** Requests from the message this turn could not do, in plain Vietnamese. */
  not_done?: string[] | null;
}

export interface ChatResult {
  message: string;
  applied: boolean;
  version: number | null;
  report: Record<string, unknown>;
  diff: { diff?: Record<string, unknown> } | null;
  tokens: number;
  history: ChatTurn[];
  /** Set when audit+resolve were queued after an applied chat turn. */
  preview_queued?: boolean;
  queue_position?: number;
}

/** Thrown by `json()` on a non-OK response. `code` is the server's machine-readable
 * error code (e.g. `"unauthorized"`, `"job_busy"`) when the body is JSON shaped
 * `{code, message}`; empty string otherwise (plain-text error bodies). */
export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Registered once by the app shell so any 401 anywhere flips the whole app
 * back to the login screen, not just the call site that happened to fail. */
export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler;
}

const parseErrorBody = (detail: string): { code: string; message: string } => {
  try {
    const parsed = JSON.parse(detail) as { code?: unknown; message?: unknown };
    if (parsed && typeof parsed === "object") {
      return {
        code: typeof parsed.code === "string" ? parsed.code : "",
        message: typeof parsed.message === "string" ? parsed.message : detail,
      };
    }
  } catch {
    /* plain-text error body, not JSON */
  }
  return { code: "", message: detail };
};

const json = async <T,>(response: Response): Promise<T> => {
  if (!response.ok) {
    const detail = await response.text();
    const { code, message } = parseErrorBody(detail);
    if (response.status === 401) unauthorizedHandler?.();
    throw new ApiError(
      response.status,
      code,
      message || `${response.status} ${response.statusText}`,
    );
  }
  return response.json() as Promise<T>;
};

/** For endpoints that return text/plain: a rendered prompt or a unified diff. */
const text = async (response: Response): Promise<string> => {
  if (!response.ok) {
    throw new Error((await response.text()) || `${response.status}`);
  }
  return response.text();
};

export interface AutoeditConfig {
  director_model: string;
  gateway_configured: boolean;
}

export interface AuthStatus {
  auth_required: boolean;
  authenticated: boolean;
}

export const api = {
  // ---- auth (cookie session; the token itself never touches client code) --
  authStatus: () => fetch("/api/auth/status").then(json<AuthStatus>),

  authLogin: (accessToken: string) =>
    fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: accessToken }),
    }).then(json<{ authenticated: boolean }>),

  authLogout: () =>
    fetch("/api/auth/logout", { method: "POST" }).then(json<{ authenticated: boolean }>),

  config: () => fetch("/api/config").then(json<AutoeditConfig>),
  resources: () => fetch("/api/resources").then(json<any>),
  queue: () => fetch("/api/queue").then(json<{ running: string | null; pending: number }>),
  listJobs: () => fetch("/api/jobs").then(json<JobSummary[]>),
  getJob: (id: string) => fetch(`/api/jobs/${id}`).then(json<JobDetail>),
  getSpine: (id: string) => fetch(`/api/jobs/${id}/spine`).then(json<SpineResponse>),

  createJob: (body: { input_path: string; title?: string; options?: Record<string, unknown> }) =>
    fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ job_id: string; queue_position: number }>),

  runStages: (id: string, body: { stages?: string[]; use_cache?: boolean; options?: Record<string, unknown> }) =>
    fetch(`/api/jobs/${id}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ queue_position: number }>),

  render: (id: string, scale = 1) =>
    fetch(`/api/jobs/${id}/render?scale=${scale}`, { method: "POST" }).then(json<any>),

  generateVisuals: (id: string) =>
    fetch(`/api/jobs/${id}/visuals`, { method: "POST" }).then(json<{ job_id: string; queue_position: number }>),

  cancel: (id: string) =>
    fetch(`/api/jobs/${id}/cancel`, { method: "POST" }).then(json<{ cancelled: boolean }>),

  revise: (id: string, instruction: string) =>
    fetch(`/api/jobs/${id}/revise`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction }),
    }).then(json<{ queue_position: number }>),

  versions: (id: string) =>
    fetch(`/api/jobs/${id}/versions`).then(
      json<{ current_version: number; versions: JobVersion[] }>,
    ),

  rollback: (id: string, version: number) =>
    fetch(`/api/jobs/${id}/rollback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    }).then(
      json<{
        version: number;
        restored_from: number;
        /** Job options restored to their value as of `restored_from`. */
        options_changed?: Record<string, unknown>;
        /** Set when the server queued a re-cut (resolve) for the restored spec. */
        queue_position?: number;
      }>,
    ),

  /** Deterministic cut/keep — no LLM in the loop. `w` ranges are word indices. */
  cuts: (id: string, body: { cut?: [number, number][]; keep?: [number, number][] }) =>
    fetch(`/api/jobs/${id}/cuts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(
      json<{
        version: number;
        queue_position?: number;
        report?: Record<string, unknown>;
      }>,
    ),

  saveProps: (id: string, props: TimelineProps) =>
    fetch(`/api/jobs/${id}/props`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(props),
    }).then(json<{ version: number }>),

  // Previews of what ffmpeg bakes into src.mp4 at resolve time. The <Player>
  // cannot show these — grade and audio preset are already baked by the time it
  // reads the file — so without them tuning either costs a full resolve.
  previewGrade: (id: string, body: { at?: number | null; grade?: Record<string, number> }) =>
    fetch(`/api/jobs/${id}/preview/grade`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<GradePreview>),

  // ---- look presets -------------------------------------------------------
  // Global grade fragments, reusable across every job — unlike grade_overrides,
  // which lives inside a single job.json. See docs/talking-head-autoedit.md.
  listLookPresets: () => fetch("/api/look-presets").then(json<LookPreset[]>),

  saveLookPreset: (body: { name: string; grade: Record<string, number> }) =>
    fetch("/api/look-presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<LookPreset>),

  deleteLookPreset: (name: string) =>
    fetch(`/api/look-presets/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
      json<{ deleted: string }>,
    ),

  listEditStyles: () => fetch("/api/edit-styles").then(json<EditStyle[]>),

  saveEditStyle: (body: {
    title: string;
    options: Record<string, unknown>;
    source_project_id?: string;
  }) =>
    fetch("/api/edit-styles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<EditStyle>),

  updateEditStyle: (
    id: string,
    body: { title: string; options: Record<string, unknown>; source_project_id?: string },
  ) =>
    fetch(`/api/edit-styles/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<EditStyle>),

  deleteEditStyle: (id: string) =>
    fetch(`/api/edit-styles/${encodeURIComponent(id)}`, { method: "DELETE" }).then(
      json<{ deleted: string }>,
    ),

  styleFromProject: (projectId: string, title?: string, options?: Record<string, unknown>) =>
    fetch(`/api/edit-styles/from-project/${encodeURIComponent(projectId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, options }),
    }).then(json<EditStyle>),

  listBgm: () => fetch("/api/bgm").then(json<{ tracks: BgmTrack[]; groups: Record<string, string> }>),

  uploadBgm: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return fetch("/api/bgm", { method: "POST", body: form }).then(json<BgmTrack>);
  },

  deleteBgm: (name: string) =>
    fetch(`/api/bgm/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
      json<{ deleted: string }>,
    ),

  previewAudio: (id: string, body: { at?: number; duration?: number; presets?: string[] }) =>
    fetch(`/api/jobs/${id}/preview/audio`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<AudioPreview>),

  previewClip: (id: string, body: { start?: number; duration?: number; scale?: number }) =>
    fetch(`/api/jobs/${id}/preview/clip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<ClipPreview>),

  // ---- projects ----------------------------------------------------------
  listProjects: () => fetch("/api/projects").then(json<ProjectSummary[]>),
  getProject: (id: string) => fetch(`/api/projects/${id}`).then(json<ProjectDetail>),

  createProject: (body: {
    title: string;
    folder?: string;
    assembly?: Partial<AssemblyConfig>;
    keyterms?: string[];
    defaults?: Record<string, unknown>;
  }) =>
    fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ project_id: string }>),

  deleteProject: (id: string) =>
    fetch(`/api/projects/${id}?confirm=true`, { method: "DELETE" }).then(json<any>),

  updateProjectSettings: (
    id: string,
    body: {
      title?: string;
      folder?: string;
      assembly?: Partial<AssemblyConfig>;
      keyterms?: string[];
      defaults?: Record<string, unknown>;
    },
  ) =>
    fetch(`/api/projects/${id}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<any>),

  updateSource: (
    projectId: string,
    sourceId: string,
    body: {
      role?: string;
      order?: number;
      take_group?: string;
      label?: string;
      tags?: string[];
      notes?: string;
      meta?: Record<string, unknown>;
      bgm_name?: string;
      bgm_volume?: number | null;
    },
  ) =>
    fetch(`/api/projects/${projectId}/sources/${sourceId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<ProjectSource>),

  deleteSource: (projectId: string, sourceId: string, force = false) =>
    fetch(`/api/projects/${projectId}/sources/${sourceId}${force ? "?force=true" : ""}`, {
      method: "DELETE",
    }).then(json<{ removed: string; was_used_by: string[] }>),

  takeSuggestions: (id: string) =>
    fetch(`/api/projects/${id}/take-suggestions`).then(json<TakeSuggestion[]>),

  listBuilds: (id: string) => fetch(`/api/projects/${id}/jobs`).then(json<Build[]>),

  createBuild: (id: string, body: {
    options?: Record<string, unknown>;
    title?: string;
    source_ids?: string[];
    include_broll?: boolean;
    /** Omit = full pipeline. Pass e.g. prepare stages to stop before render. */
    stages?: string[];
    /** Default true. false = create job only, do not enqueue the worker. */
    run?: boolean;
  }) =>
    fetch(`/api/projects/${id}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{
      job_id: string;
      project_id: string;
      queue_position: number | null;
      run: boolean;
      stages?: string[] | null;
    }>),

  createBuildsBatch: (id: string, body: {
    source_ids: string[];
    options?: Record<string, unknown>;
    title?: string;
    include_broll?: boolean;
    stages?: string[];
    run?: boolean;
  }) =>
    fetch(`/api/projects/${id}/jobs/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{
      jobs: Array<{
        job_id: string;
        project_id: string;
        queue_position: number | null;
        run: boolean;
      }>;
      failed: Array<{ source_id: string; error: string }>;
    }>),

  // ---- prompts -----------------------------------------------------------
  listPrompts: () => fetch("/api/prompts").then(json<PromptCatalogEntry[]>),

  getPrompt: (id: string, version?: string) =>
    fetch(`/api/prompts/${id}${version ? `?version=${version}` : ""}`).then(json<PromptDetail>),

  /** The prompt as it would actually be SENT, using this job's real spine. */
  previewPrompt: (id: string, jobId: string, version?: string) =>
    fetch(
      `/api/prompts/${id}/preview?job_id=${encodeURIComponent(jobId)}` +
        (version ? `&version=${version}` : ""),
    ).then(text),

  savePromptOverride: (
    id: string,
    body: { body: string; version?: string; note?: string; make_current?: boolean },
  ) =>
    fetch(`/api/prompts/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ version: string; warnings: string[] }>),

  deletePromptOverride: (id: string, version: string) =>
    fetch(`/api/prompts/${id}/${version}`, { method: "DELETE" }).then(json<any>),

  promptDiff: (id: string, version: string, against = "v1") =>
    fetch(`/api/prompts/${id}/diff?version=${version}&against=${against}`).then(text),

  /** Cost preview — shown BEFORE the run, because A/B doubles a director call. */
  estimateAb: (id: string, jobId: string, versionA: string, versionB: string) =>
    fetch(
      `/api/prompts/${id}/ab/estimate?job_id=${encodeURIComponent(jobId)}` +
        `&version_a=${versionA}&version_b=${versionB}`,
    ).then(json<AbEstimate>),

  runAb: (id: string, body: { job_id: string; version_a: string; version_b: string }) =>
    fetch(`/api/prompts/${id}/ab`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ started: boolean }>),

  abResults: (id: string, jobId: string) =>
    fetch(`/api/prompts/${id}/ab/results?job_id=${encodeURIComponent(jobId)}`).then(
      json<AbResult[]>,
    ),

  // ---- autopilot + chat --------------------------------------------------
  autopilot: (id: string, body: { stages?: string[]; use_cache?: boolean } = {}) =>
    fetch(`/api/jobs/${id}/autopilot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ queue_position: number }>),

  chatHistory: (id: string) =>
    fetch(`/api/jobs/${id}/chat`).then(json<{ turns: ChatTurn[] }>),

  chat: (id: string, message: string, dryRun = false) =>
    fetch(`/api/jobs/${id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, dry_run: dryRun }),
    }).then(json<ChatResult>),

  // ---- cloud batch queue + paid render -----------------------------------
  // Schedule = free local enqueue. Preview = dry_run (offers/cost). Execute
  // = background rent after confirm:true. See server/api_cloud.py.
  cloudStatus: () => fetch("/api/cloud/status").then(json<CloudStatus>),

  cloudQueue: () =>
    fetch("/api/cloud/queue").then(
      json<{ entries: CloudQueueEntry[]; flush_check: CloudFlushCheck }>,
    ),

  cloudEnqueue: (jobId: string, note = "") =>
    fetch("/api/cloud/queue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId, note }),
    }).then(json<{ entry: CloudQueueEntry; message: string }>),

  cloudRemove: (jobId: string) =>
    fetch(`/api/cloud/queue/${encodeURIComponent(jobId)}`, { method: "DELETE" }).then(
      json<{ removed: string }>,
    ),

  cloudClear: () =>
    fetch("/api/cloud/queue/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    }).then(json<{ cleared: boolean }>),

  cloudPreview: (body: {
    mode: "render_now" | "flush";
    job_id?: string;
    job_ids?: string[];
    pricing_mode?: "bid" | "on-demand";
  }) =>
    fetch("/api/cloud/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<CloudPreview>),

  cloudExecute: (body: {
    mode: "render_now" | "flush";
    job_id?: string;
    job_ids?: string[];
    offer_id: number;
    dry_run_ref: string;
    max_total_usd?: number;
    pricing_mode?: "bid" | "on-demand";
    confirm: true;
  }) =>
    fetch("/api/cloud/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<{ started: boolean; operation: CloudOperation; message: string }>),

  cloudOperation: () => fetch("/api/cloud/operation").then(json<CloudOperation>),

  cloudOperationClear: () =>
    fetch("/api/cloud/operation/clear", { method: "POST" }).then(json<CloudOperation>),

  cloudJobQueued: (jobId: string) =>
    fetch(`/api/cloud/job/${encodeURIComponent(jobId)}/queued`).then(
      json<{ queued: boolean; entry: CloudQueueEntry | null }>,
    ),
};

// ---- cloud types ---------------------------------------------------------

export interface CloudQueueEntry {
  job_id: string;
  project_id?: string | null;
  version_at_enqueue: number;
  enqueued_at: string;
  estimated_render_seconds: number;
  duration_seconds: number;
  note: string;
  status: string;
  attempts: number;
  last_error?: string | null;
  title?: string;
  job_status?: string;
  has_final?: boolean;
  has_props?: boolean;
  current_version?: number;
}

export interface CloudFlushCheck {
  job_count: number;
  estimated_render_minutes: number;
  oldest_age_minutes: number;
  thresholds: {
    min_jobs?: number;
    min_total_render_minutes?: number;
    max_wait_minutes?: number;
  };
  thresholds_met: string[];
  estimated_cost_usd: number;
  estimated_cost_if_rendered_separately_usd: number;
  amortization_note?: string;
  error?: string;
}

export interface CloudConfigSnapshot {
  enabled: boolean;
  pricing_mode: string | null;
  max_dph_usd: number | null;
  max_total_usd_per_rental: number | null;
  max_runtime_minutes: number | null;
  max_batch_runtime_minutes: number | null;
  batch: Record<string, number>;
  render_seconds_per_video_second: number;
  reason: string | null;
  valid: boolean;
  error: string | null;
}

export interface CloudOperation {
  status: "idle" | "running" | "completed" | "failed";
  mode: string | null;
  job_ids: string[];
  started_at: number | null;
  finished_at: number | null;
  message: string;
  error: string | null;
  result: Record<string, unknown> | null;
  cost_usd: number | null;
}

export interface CloudOffer {
  offer_id: number;
  dph_usd: number;
  cpu_cores: number;
  geolocation: string;
  reliability: number;
  gpu_name?: string | null;
}

export interface CloudPreview {
  would_execute: boolean;
  error?: string;
  offers?: CloudOffer[];
  recommended_offer_id?: number;
  pricing_mode?: string;
  on_demand_alternative_dph_usd?: number | null;
  kit_size_bytes?: number;
  jobs?: number;
  estimated_render_minutes?: number;
  estimated_overhead_minutes?: number;
  estimated_cost_usd?: number;
  cost_if_rendered_separately_usd?: number;
  estimated_local_render_minutes?: number;
  ceilings?: Record<string, number>;
  warnings?: string[];
  dry_run_ref?: string;
  announce_text: string;
  config: CloudConfigSnapshot;
}

export interface CloudStatus {
  config: CloudConfigSnapshot;
  queue: { count: number; entries: CloudQueueEntry[] };
  flush_check: CloudFlushCheck;
  operation: CloudOperation;
  ready_to_flush: boolean;
}

/**
 * SSE stream of a job's progress.
 *
 * Closing on `stream_end` is required, not tidiness: EventSource reconnects
 * automatically whenever the server closes the connection, so a finished job
 * would otherwise reconnect every few seconds forever, replaying an end marker
 * each time.
 */
export const subscribeToJob = (
  jobId: string,
  onEvent: (event: ProgressEvent) => void,
): (() => void) => {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as ProgressEvent & { type: string };
      if (event.type === "stream_end") {
        source.close();
        onEvent(event);
        return;
      }
      onEvent(event);
    } catch {
      /* heartbeat or partial frame — nothing to do */
    }
  };
  return () => source.close();
};
