"""Pydantic schemas for the talking-head auto-edit HTTP API.

Provides strict type validation, OpenAPI documentation generation, and
synchronized TypeScript type definitions between the backend and UI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Common Enums & Primitives
# ---------------------------------------------------------------------------

StageName = Literal[
    "probe",
    "transcribe",
    "select",
    "direct",
    "audit",
    "calibrate",
    "resolve",
    "render",
    "verify",
    "visuals",
    "revise",
]

StageStatus = Literal["pending", "running", "completed", "failed"]

VersionKind = Literal["director", "revise", "manual", "manual_cuts", "rollback"]

SourceRole = Literal["a_roll", "broll", "audio_only", "scratch"]

AssemblyMode = Literal["best_take", "sequential", "manual"]


# ---------------------------------------------------------------------------
# Base Schema Configuration
# ---------------------------------------------------------------------------

class SchemaModel(BaseModel):
    """Base model with permissive extra-field allowance for backwards compatibility."""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        arbitrary_types_allowed=True,
    )


# ---------------------------------------------------------------------------
# Health & Config Schemas
# ---------------------------------------------------------------------------

class HealthResponse(SchemaModel):
    ok: bool = True
    auth: bool = False


class ConfigResponse(SchemaModel):
    director_model: str
    gateway_configured: bool


# ---------------------------------------------------------------------------
# Resource & Queue Schemas
# ---------------------------------------------------------------------------

class ResourceItem(SchemaModel):
    name: str
    path: Optional[str] = None
    category: Optional[str] = None
    duration_seconds: Optional[float] = None


class ResourceInventoryResponse(SchemaModel):
    sfx: List[Dict[str, Any]] = Field(default_factory=list)
    bgm: List[Dict[str, Any]] = Field(default_factory=list)
    group_labels: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    defaults: Dict[str, Any] = Field(default_factory=dict)


class QueueItem(SchemaModel):
    job_id: str
    stages: Optional[List[str]] = None
    use_cache: bool = True
    extra_args: List[str] = Field(default_factory=list)
    position: Optional[int] = None
    enqueued_at: Optional[float] = None


class QueueStatusResponse(SchemaModel):
    """What `JobQueue.status()` returns and the header's queue badge reads."""
    running: Optional[str] = None     # job id, or None when idle
    pending: int = 0


# ---------------------------------------------------------------------------
# Job Schemas
# ---------------------------------------------------------------------------

class StageState(SchemaModel):
    status: Optional[StageStatus] = None
    cached: Optional[bool] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class VersionEntry(SchemaModel):
    version: int
    # Optional: hand-made sample jobs carry versions with no kind, and one such
    # job must not turn the whole job list into a 500.
    kind: Optional[VersionKind] = None
    instruction: Optional[str] = None
    rolled_back_from: Optional[int] = None
    # Free-form: written by the audit/revise pipeline as
    # {cuts_proposed, cuts_applied, cuts_blocked:[{w,text,by,reason}],
    # options_changed, not_done} -- kept untyped here so a new key on that
    # side never needs a schema change on this one.
    outcome: Optional[Dict[str, Any]] = None


class JobSummaryResponse(SchemaModel):
    job_id: str
    title: str = ""
    status: str = "pending"
    created_at: float = 0.0
    current_version: int = 0
    cost_usd: Optional[float] = None
    project_id: Optional[str] = None
    stages: Dict[str, Any] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
    versions: List[VersionEntry] = Field(default_factory=list)


class CutDecision(SchemaModel):
    w: List[int]
    decision: Literal["remove", "keep"]
    reason: str
    source: Optional[str] = None
    before: Optional[str] = None
    cut: Optional[str] = None
    after: Optional[str] = None
    joined: Optional[str] = None


class AuditQuality(SchemaModel):
    caption_coverage: float = 0.0
    captions_over_9_words: int = 0
    keyword_in_card: List[str] = Field(default_factory=list)
    card_count: int = 0
    event_count: int = 0
    uncovered_words: List[int] = Field(default_factory=list)


class AuditReport(SchemaModel):
    cuts_proposed: int = 0
    cuts_accepted: int = 0
    cuts_kept: int = 0
    cut_decisions: List[CutDecision] = Field(default_factory=list)
    removed_resources: List[str] = Field(default_factory=list)
    quality: Optional[AuditQuality] = None


class VerifyIssue(SchemaModel):
    code: str
    message: str


class VerifyReport(SchemaModel):
    passed: bool
    duration_expected: float = 0.0
    duration_actual: float = 0.0
    av_drift: float = 0.0
    integrated_lufs: Optional[float] = None
    issues: List[Union[VerifyIssue, str]] = Field(default_factory=list)
    frames: List[Dict[str, Any]] = Field(default_factory=list)
    seams: Optional[List[float]] = None
    suspect_seams: Optional[List[Dict[str, Any]]] = None
    timeline_views: Optional[List[Dict[str, Any]]] = None
    broll: Optional[Dict[str, Any]] = None
    requires_human_review: List[str] = Field(default_factory=list)


class VisualsReport(SchemaModel):
    kicker: Optional[str] = None
    accent: Optional[str] = None
    thumbnail_text: Optional[str] = None
    files: Optional[Dict[str, Optional[str]]] = None
    errors: Optional[List[str]] = None


class TimelineProps(SchemaModel):
    videoSrc: str
    previewVideoSrc: Optional[str] = None
    events: List[Any] = Field(default_factory=list)
    durationSeconds: float
    brandPill: Optional[str] = None
    bgm: Optional[Dict[str, Any]] = None


class JobDetailResponse(JobSummaryResponse):
    audit_report: Optional[AuditReport] = None
    resolve_report: Optional[Dict[str, Any]] = None
    verify_report: Optional[VerifyReport] = None
    props: Optional[TimelineProps] = None
    has_final: bool = False
    has_thumbnail: Optional[bool] = None
    visuals_report: Optional[VisualsReport] = None
    media_base: str = ""
    probe: Optional[Dict[str, Any]] = None
    source_ids: Optional[List[str]] = None


class CreateJobRequest(SchemaModel):
    input_path: str = ""
    options: Optional[Dict[str, Any]] = None
    title: str = ""
    stages: Optional[List[str]] = None


class CreateJobResponse(SchemaModel):
    job_id: str
    queue_position: int


class RunStagesRequest(SchemaModel):
    stages: Optional[List[str]] = None
    use_cache: bool = True
    options: Optional[Dict[str, Any]] = None

class RunStagesResponse(SchemaModel):
    job_id: str
    queue_position: int



class ReviseRequest(SchemaModel):
    instruction: str
    options: Optional[Dict[str, Any]] = None


class RollbackRequest(SchemaModel):
    version: int


class RollbackResponse(SchemaModel):
    version: int
    job_id: str
    reverted_to: int
    # Kept identical to `reverted_to` -- the UI (`version-history.tsx`) already
    # reads this name specifically; `reverted_to` is the newer, clearer name.
    restored_from: int
    options_changed: Dict[str, Any] = Field(default_factory=dict)
    queue_position: Optional[int] = None


class UpdatePropsResponse(SchemaModel):
    version: int
    job_id: str


class CutsRequest(SchemaModel):
    cut: List[List[int]] = Field(default_factory=list)
    keep: List[List[int]] = Field(default_factory=list)


class CutsResponse(SchemaModel):
    version: int
    queue_position: Optional[int] = None
    report: Dict[str, Any] = Field(default_factory=dict)


class ChatRequest(SchemaModel):
    message: str
    model: Optional[str] = None
    dry_run: bool = False


class ChatResponse(SchemaModel):
    turn: Optional[Dict[str, Any]] = None
    applied: bool = False
    queue_position: Optional[int] = None
    preview_queued: Optional[bool] = None
    remedy: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    options_changed: Optional[Dict[str, Any]] = None
    not_done: Optional[List[str]] = None


class AutopilotRequest(SchemaModel):
    dry_run: bool = False
    options: Optional[Dict[str, Any]] = None
    stages: Optional[List[str]] = None
    use_cache: bool = True


class AutopilotResponse(SchemaModel):
    job_id: str
    queue_position: Optional[int] = None
    follow: str = ""
    dry_run: bool = False
    remedy: Optional[Dict[str, Any]] = None


class SpineWord(SchemaModel):
    word: str
    start: float
    end: float
    src: Optional[str] = None
    speaker: Optional[str] = None


class SpineResponse(SchemaModel):
    words: List[SpineWord] = Field(default_factory=list)
    boundaries: List[int] = Field(default_factory=list)
    cut_ranges: List[List[int]] = Field(default_factory=list)
    speakers: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Project Schemas
# ---------------------------------------------------------------------------

class SourceDetailResponse(SchemaModel):
    id: str
    file: Optional[str] = None
    filename: Optional[str] = None
    path: Optional[str] = None
    duration: float = 0.0
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    has_audio: bool = True
    role: str = "a_roll"
    order: int = 0
    take_group: Optional[str] = None
    label: str = ""
    tags: List[str] = Field(default_factory=list)
    notes: str = ""
    meta: Dict[str, Any] = Field(default_factory=dict)
    bgm_name: Optional[str] = None
    bgm_volume: Optional[float] = None
    added_at: float = 0.0
    sha256: Optional[str] = None
    has_transcript: bool = False
    size_bytes: Optional[int] = None
    thumb: Optional[str] = None
    transcript: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class ProjectSummaryResponse(SchemaModel):
    project_id: str
    title: str
    created_at: float
    updated_at: float
    source_count: int = 0
    build_count: int = 0
    total_duration: float = 0.0
    folder: Optional[str] = None


class ProjectDetailResponse(SchemaModel):
    project_id: str
    title: str
    created_at: float
    updated_at: float
    folder: Optional[str] = None
    assembly: Dict[str, Any] = Field(default_factory=dict)
    assembly_resolved: Optional[Dict[str, Any]] = None
    assembly_origin: Optional[Dict[str, Any]] = None
    keyterms: List[str] = Field(default_factory=list)
    defaults: Dict[str, Any] = Field(default_factory=dict)
    sources: List[SourceDetailResponse] = Field(default_factory=list)
    builds: List[Dict[str, Any]] = Field(default_factory=list)
    jobs: List[str] = Field(default_factory=list)


class CreateProjectRequest(SchemaModel):
    title: str = ""
    assembly: Optional[Dict[str, Any]] = None
    keyterms: Optional[List[str]] = None
    defaults: Optional[Dict[str, Any]] = None
    folder: Optional[str] = None


class UpdateProjectSettingsRequest(SchemaModel):
    title: Optional[str] = None
    assembly: Optional[Dict[str, Any]] = None
    keyterms: Optional[List[str]] = None
    defaults: Optional[Dict[str, Any]] = None
    folder: Optional[str] = None


class AddSourceFromPathRequest(SchemaModel):
    path: str
    role: Optional[str] = None
    order: Optional[int] = None
    take_group: Optional[str] = None
    label: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    bgm_name: Optional[str] = None
    bgm_volume: Optional[float] = None


class UpdateSourceRequest(SchemaModel):
    role: Optional[str] = None
    order: Optional[int] = None
    take_group: Optional[str] = None
    label: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    bgm_name: Optional[str] = None
    bgm_volume: Optional[float] = None
class AddSourcesResponse(SchemaModel):
    added: List[Dict[str, Any]] = Field(default_factory=list)
    failed: List[Dict[str, str]] = Field(default_factory=list)


class CreateProjectJobRequest(SchemaModel):
    source_ids: Optional[List[str]] = None
    options: Optional[Dict[str, Any]] = None
    title: Optional[str] = None
    stages: Optional[List[str]] = None
    run: bool = True


class CreateProjectJobResponse(SchemaModel):
    job_id: str
    project_id: str
    queue_position: Optional[int] = None
    run: bool = True


class CreateBatchJobsRequest(SchemaModel):
    source_ids: Optional[List[str]] = None
    title: Optional[str] = ""
    options: Optional[Dict[str, Any]] = None
    stages: Optional[List[str]] = None
    run: bool = True
    include_broll: bool = True

class CreateBatchJobsResponse(SchemaModel):
    jobs: List[Dict[str, Any]] = Field(default_factory=list)
    failed: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Look Preset Schemas
# ---------------------------------------------------------------------------

class SavePresetRequest(SchemaModel):
    name: str
    grade: Dict[str, Any]


class LookPresetResponse(SchemaModel):
    name: str
    grade: Dict[str, Any]
    created_at: Optional[str] = None   # ISO-8601, as look_presets.py stores it


# ---------------------------------------------------------------------------
# Edit Style Schemas
# ---------------------------------------------------------------------------

class CreateStyleRequest(SchemaModel):
    title: str
    options: Optional[Dict[str, Any]] = None
    source_project_id: Optional[str] = ""


class UpdateStyleRequest(SchemaModel):
    title: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    source_project_id: Optional[str] = ""


class EditStyleResponse(SchemaModel):
    """Shape of an entry in `config/edit-styles.json` (see edit_styles.py)."""
    id: str
    title: str
    options: Dict[str, Any] = Field(default_factory=dict)
    source_project_id: Optional[str] = ""
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


# ---------------------------------------------------------------------------
# Cloud Render Schemas
# ---------------------------------------------------------------------------

class EnqueueCloudRequest(SchemaModel):
    job_id: str
    note: str = ""


class EnqueueCloudResponse(SchemaModel):
    entry: Dict[str, Any]
    message: str


class ClearCloudQueueRequest(SchemaModel):
    confirm: bool = False


class PreviewCloudRequest(SchemaModel):
    mode: Literal["render_now", "flush"] = "render_now"
    job_id: Optional[str] = None
    job_ids: Optional[List[str]] = None
    target_gpu: Optional[str] = None
    max_dph_usd: Optional[float] = None


class ExecuteCloudRequest(SchemaModel):
    confirm: bool = False
    dry_run_ref: Optional[str] = None
    mode: Literal["render_now", "flush"] = "render_now"
    job_id: Optional[str] = None
    job_ids: Optional[List[str]] = None
    machine_id: Optional[int] = None
    target_gpu: Optional[str] = None
    max_dph_usd: Optional[float] = None
    label: Optional[str] = None
class CloudStatusResponse(SchemaModel):
    config: Dict[str, Any]
    queue: Dict[str, Any]
    flush_check: Dict[str, Any]
    operation: Optional[Dict[str, Any]] = None
    worker: Optional[Dict[str, Any]] = None
    ready_to_flush: Optional[bool] = False


class CloudQueueResponse(SchemaModel):
    entries: List[Dict[str, Any]] = Field(default_factory=list)
    flush_check: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Preview Schemas
# ---------------------------------------------------------------------------

class PreviewGradeRequest(SchemaModel):
    at: Optional[float] = None
    grade: Optional[Any] = None


class GradeVariant(SchemaModel):
    name: str
    image: str
    url: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None
    face: Optional[Dict[str, Any]] = None


class GradePreviewResponse(SchemaModel):
    at_seconds: float
    variants: List[GradeVariant] = Field(default_factory=list)
    contact_sheet: str
    contact_sheet_url: Optional[str] = None
    base_grade: Dict[str, Any] = Field(default_factory=dict)


class PreviewAudioRequest(SchemaModel):
    at: Optional[float] = None
    duration: Optional[float] = None
    presets: Optional[Any] = None

class AudioSample(SchemaModel):
    preset: str
    path: str
    rel: str
    url: Optional[str] = None


class AudioPreviewResponse(SchemaModel):
    at_seconds: float
    duration_seconds: float
    current_preset: str
    samples: List[AudioSample] = Field(default_factory=list)


class PreviewClipRequest(SchemaModel):
    start: Optional[float] = None
    duration: Optional[float] = None
    scale: Optional[float] = None


class ClipPreviewResponse(SchemaModel):
    path: str
    rel: str
    version: int
    start_seconds: float
    duration_seconds: float
    scale: float
    crf: int
    url: Optional[str] = None


# ---------------------------------------------------------------------------
# Prompt Schemas
# ---------------------------------------------------------------------------

class PutOverrideRequest(SchemaModel):
    body: str = ""
    version: Optional[str] = None
    note: Optional[str] = ""
    author: Optional[str] = "admin"
    make_current: bool = True


class SetCurrentVersionRequest(SchemaModel):
    version: str = ""


class ABTestRequest(SchemaModel):
    job_id: str = ""
    version_a: str = "v1"
    version_b: str = ""

class PromptSummaryResponse(SchemaModel):
    prompt_id: str
    title: str = ""
    description: str = ""
    current_version: str = "v1"
    versions: List[str] = Field(default_factory=list)
    has_override: bool = False


# ---------------------------------------------------------------------------
# Browser session auth
# ---------------------------------------------------------------------------

class LoginRequest(SchemaModel):
    token: str


class LoginResponse(SchemaModel):
    ok: bool = True


class LogoutResponse(SchemaModel):
    ok: bool = True


class AuthStatusResponse(SchemaModel):
    auth_required: bool
    authenticated: bool


# ---------------------------------------------------------------------------
# Automation API: POST /api/runs (one-call create + run), GET .../runs/{id}
# ---------------------------------------------------------------------------

RunState = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "awaiting_render",
]


class RunSource(SchemaModel):
    path: Optional[str] = None
    url: Optional[str] = None
    role: Optional[Literal["aroll", "broll"]] = None


class CreateRunRequest(SchemaModel):
    sources: List[RunSource] = Field(..., min_length=1)
    project_id: Optional[str] = None
    title: str = ""
    prompt: Optional[str] = None
    topic: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    render: bool = True
    render_scale: float = 1.0
    webhook_url: Optional[str] = None


class CreateRunResponse(SchemaModel):
    run_id: str
    status: str
    status_url: str
    events_url: str


class RunError(SchemaModel):
    code: str
    message: str


class RunVerify(SchemaModel):
    passed: Optional[bool] = None
    issues: List[Any] = Field(default_factory=list)


class RunOutputs(SchemaModel):
    mp4_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    verify: Optional[RunVerify] = None


class RunStatusResponse(SchemaModel):
    run_id: str
    state: RunState
    stage: Optional[str] = None
    percent: int = 0
    error: Optional[RunError] = None
    outputs: Optional[RunOutputs] = None
    current_version: int = 0
    versions: List[VersionEntry] = Field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


class ReviseRunRequest(SchemaModel):
    message: str


class ReviseRunResponse(SchemaModel):
    turn: Optional[Dict[str, Any]] = None
    applied: bool = False
    status_url: str = ""


class CancelRunResponse(SchemaModel):
    run_id: str
    cancelled: bool
