# Phase 02 — Footage Edit-Analysis Tool + Artifact Schema

## Context Links
- Tool contract: `tools/base_tool.py` (BaseTool, ToolResult)
- Naming/registration: `AGENT_GUIDE.md:468-483`, `tools/tool_registry.py:118-134`
- Google-genai pattern: `tools/graphics/google_imagen.py`, `tools/google_credentials.py`
- Proven prompt+schema: scratchpad `edit_workflow_prompt.txt`
- Output feeds: `schemas/artifacts/edit_decisions.schema.json`
- Playbook params: Phase 01 `styles/ugc-talking-head.yaml`
- Real inventory: Phase 01b `assets_library/resource_inventory.json`

## Overview
- **Priority:** P1
- **Status:** completed
- **Blocked by:** Phase 01 (playbook), Phase 01b (real sfx + transition inventory — required for the constrained prompt).
- **Description:** New registered analysis tool that takes a raw talking-head video + a style playbook + the real resource inventory and returns a structured `footage_edit_plan` artifact: transcript, beats (zoom / transition / b-roll need+description / sfx per beat), and filler/dead-air `removal_spans` (from Phase 03). Wraps Gemini video analysis using the proven prompt, parameterized by the playbook AND constrained to the real inventory.

## Key Insights
- No existing Gemini video tool — `video_analyzer.py:32` is local-only (yt-dlp/whisper/scenedetect). This is genuinely new, not a duplicate. (DRY check passed.)
- **Model default = `gemini-3.1-flash-lite` with `thinking_level=HIGH` (user decision).** Rationale: target output is deliberately simple (cut points, zoom in/out, sfx, b-roll cue) — not sophisticated multi-transition creative editing; flash-lite is far faster/cheaper (20s vs minutes, verified this session) and handles all languages well. `gemini-3.5-flash` retained as an OPTIONAL upgrade path (model enum), not the default.
- The earlier A/B finding (flash-lite defaulted to repetitive "cut") is mitigated **architecturally, not by model choice**: the prompt (a) enumerates the real `available_transitions[]`/`available_sound_effects[]` from P01b and forbids inventing any not on the list, and (b) explicitly instructs Gemini to VARY usage across beats rather than repeat one option. This is the actual fix for repetitiveness.
- Established SDK access: `tools/google_credentials.py` + google-genai, `GEMINI_API_KEY` already in `.env` (loaded by `base_tool._load_dotenv()`, `base_tool.py:23-58`). Reuse — do NOT re-implement auth.
- Tool class naming: PascalCase, NO "Tool" suffix (`AGENT_GUIDE.md:470`) → class `FootageEditAnalyzer`, `name="footage_edit_analyzer"`, `capability="analysis"`.
- `agent_skills` must point at the multimodal Layer-3 skill (`ai-multimodal` per task; verify exact installed skill name via registry before hardcoding — `video-understand` also exists).
- Output must map onto `edit_decisions` fields so the edit-director can translate 1:1: beat→`cuts[]`, b-roll window→`overlays[]`, sfx→`audio.sfx[]`.

## Requirements
**Functional**
- `execute(inputs)` accepts: `input_path` (video), `playbook_path` (yaml), `inventory_path` (default `assets_library/resource_inventory.json`), `model` (default `gemini-3.1-flash-lite`, enum incl. `gemini-3.5-flash` as upgrade), `thinking_level` (default `HIGH`), `output_dir`.
- Loads playbook + inventory. Injects `pacing_rules`, `sfx_style`, b-roll/zoom/beat `quality_rules`, AND the real `available_transitions[]` / `available_sound_effects[]` lists into the prompt.
- **Constrained-choice prompt:** replace the current free-enum transition/sfx fields (`edit_workflow_prompt.txt` uses open enums like `whip_pan`/`j_cut`) with an explicit instruction: "Choose `transition.type` ONLY from available_transitions and `sound_effect.type` ONLY from available_sound_effects. Never invent a value not in these lists. Vary your choices across beats — do not repeat the same transition/effect on consecutive beats unless the rhythm truly demands it."
- Uploads/streams the video to Gemini, requests STRICT JSON, parses + validates against new `footage_edit_plan` schema.
- Merges Phase 03 `removal_spans` (dead-air/filler) into the artifact (tool calls the P03 detector, or accepts its output as an input param — see P03).
- Writes `footage_edit_plan.json` to `output_dir`; returns `ToolResult(success, data, artifacts, model, cost_usd, duration_seconds)`.
- Graceful failure: no `GEMINI_API_KEY` → `ToolResult(success=False, error=...)`; malformed JSON → one repair retry, then fail with raw output attached.

**Non-functional**
- File < 200 lines (CLAUDE.md). Split prompt-building + parsing into `tools/analysis/footage_edit_prompt.py` helper if needed to stay under limit.
- Deterministic-ish: pass low temperature; record `model` in result for reproducibility.

## Architecture
```
FootageEditAnalyzer.execute
  ├─ load playbook yaml + resource_inventory.json (P01b)
  ├─ build_prompt(playbook_params, available_transitions[], available_sound_effects[])
  │     from edit_workflow_prompt.txt schema, with constrained-choice + vary-across-beats rules
  ├─ genai client (google_credentials) -> upload video
  │     -> generate JSON (model=gemini-3.1-flash-lite, thinking_level=HIGH)
  ├─ parse+validate -> beats[], transcript[], pacing_profile
  ├─ post-validate: assert every beat transition/sfx ∈ inventory (reject+repair if not)
  ├─ merge removal_spans (Phase 03 speech_gap_detector)
  └─ write footage_edit_plan.json
```
New artifact `footage_edit_plan` is an **advisory** artifact (grounding), NOT a canonical stage artifact. Canonical `edit_decisions` is still authored by the edit-director agent (edit stage `tools_available: []`, `hybrid.yaml:159`). This respects "Python = tools, agent = decisions" (`AGENT_GUIDE.md:80`).

## Related Code Files
- **Create:** `tools/analysis/footage_edit_analyzer.py` (class `FootageEditAnalyzer`)
- **Create (if >200 lines):** `tools/analysis/footage_edit_prompt.py` (prompt builder + JSON parse)
- **Create:** `schemas/artifacts/footage_edit_plan.schema.json`
- **Read (no edit):** `tools/google_credentials.py`, `tools/graphics/google_imagen.py`, `tools/base_tool.py`, `schemas/artifacts/edit_decisions.schema.json`
- **Consumes:** Phase 03 detector output; Phase 01 playbook

## `footage_edit_plan` schema (fields)
Mirror `edit_workflow_prompt.txt` + add removal + provenance:
- `version` const "1.0"
- `source` { path, duration_seconds }
- `style_playbook` (path used)
- `transcript[]` { start, end, text }
- `pacing_profile` { total_beats, avg_beat_length_seconds, editing_philosophy }
- `beats[]` { beat_index, start_time, end_time, spoken_text, zoom{action,reason,recommended_scale_range}, transition{type ∈ available_transitions, reason}, b_roll{needed,suggested_visual,reason}, sound_effect{type ∈ available_sound_effects ∪ "none", trigger, reason} }  ← type fields validated against inventory
- `inventory_used` { transitions[], sound_effects[] }  (provenance of the constrained menu)
- `removal_spans[]` { start_seconds, end_seconds, kind: "filler"|"dead_air", text?, confidence }  ← from Phase 03
- `model`, `_analysis_meta`

## Implementation Steps
1. Confirm google-genai client construction from `tools/google_credentials.py` (read it first); reuse its helper — do not new up a raw client.
2. Write `footage_edit_plan.schema.json` (fields above); keep `additionalProperties:false` at top level.
3. Scaffold `FootageEditAnalyzer(BaseTool)`: identity fields, `capability="analysis"`, `runtime=API`, `dependencies=["env:GEMINI_API_KEY","python:google.genai"]`, `agent_skills=[<verified multimodal skill>]`, `input_schema`, `estimate_cost` (per-video Gemini token estimate).
4. Implement prompt builder: start from `edit_workflow_prompt.txt`; append style constraints from playbook (min/max beat seconds, b-roll trigger rule) AND the injected `available_transitions[]`/`available_sound_effects[]` from the inventory + the constrained-choice + vary-across-beats instruction (replaces the open transition/sfx enums).
5. Implement video upload + `generate_content` with JSON response; `model=gemini-3.1-flash-lite`, `thinking_level=HIGH`, temperature low.
6. Parse JSON; on failure, one strict-repair retry ("return ONLY valid JSON"); validate against schema. Then assert every `beats[].transition.type` and `beats[].sound_effect.type` is a member of the inventory; if any is out-of-set, re-prompt once with the offending values listed, else drop to nearest valid / `none`.
7. Call Phase 03 `SpeechGapDetector` (or accept `removal_spans` param) and merge.
8. Write artifact + return `ToolResult`.
9. Register check: `python -c "from tools.tool_registry import registry; registry.discover(); print(registry.get('footage_edit_analyzer').get_info()['status'])"`.

## Todo List
- [x] Verify google_credentials client helper — DONE 2026-07-09: `tools/google_credentials.py` is the service-account/Vertex path (used by `google_imagen.py`); this tool uses the simpler AI-Studio API-key path (`GEMINI_API_KEY`/`GOOGLE_API_KEY` via `google.genai.Client(api_key=...)`), matching the already-proven `ab_test_flash_lite.py` pattern — no service-account auth needed for this tool
- [x] Write footage_edit_plan.schema.json — `schemas/artifacts/footage_edit_plan.schema.json`
- [x] Load + inject resource_inventory.json (available_transitions/sfx) — `tools/analysis/footage_edit_prompt.py::load_inventory`/`inventory_names`
- [x] Implement FootageEditAnalyzer (+ prompt helper if needed), model=gemini-3.1-flash-lite/HIGH — `tools/analysis/footage_edit_analyzer.py` (class `FootageEditAnalyzer`); split into `footage_edit_prompt.py`, `footage_edit_artifact.py`, `footage_edit_genai_client.py`, `footage_edit_schema.py` to stay under ~200 lines/file
- [x] Constrained-choice + vary-across-beats prompt — `footage_edit_prompt.py::build_prompt` injects `available_transitions[]`/`available_sound_effects[]` verbatim + anti-repetition instruction
- [x] JSON parse + repair retry + schema validate + inventory-membership assertion — `footage_edit_genai_client.py::generate_with_repair` (one repair retry, raises `GeminiJSONError` with raw text on second failure); `footage_edit_prompt.py::validate_and_clamp_beats` clamps out-of-inventory choices to `"none"` and records the violation in `_analysis_meta.inventory_violations` (clamp-and-flag, not clamp-and-hide); artifact validated against schema in the real test run (`jsonschema.validate` passed)
- [x] Merge Phase 03 removal_spans — DEVIATION (documented): Phase 03 (`SpeechGapDetector`) is not implemented yet in this codebase, so the tool does not call it directly. It accepts `removal_spans` (inline list) or `removal_spans_path` (JSON file) as optional inputs per the phase spec's documented fallback ("accepts its output as an input param — see P03"); defaults to `[]`. `tools/analysis/footage_edit_artifact.py::load_removal_spans`.
- [x] Confirm registry auto-discovery + AVAILABLE status — verified: `registry.discover(); registry.get('footage_edit_analyzer').get_info()['status'] == 'available'`
- [x] Verify agent_skills name against installed skills — confirmed `ai-multimodal` exists at `C:\Users\PC\.claude\skills\ai-multimodal\` (used this session for the proven video-analysis prototype)

## Success Criteria
- Tool auto-discovered; `get_info()["status"] == "available"` with key set.
- On the reference mp4 (with `gemini-3.1-flash-lite`/HIGH), produces schema-valid `footage_edit_plan` whose b-roll windows overlap the 3 known overlay ranges (regression check).
- EVERY beat's `transition.type`/`sound_effect.type` is a member of the P01b inventory (zero invented values), and transitions are not all identical across beats (variety check — the architectural fix for the earlier repetitiveness finding).
- Output artifact maps 1:1 to `edit_decisions` (documented in schema comments).

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Gemini returns non-JSON / markdown fences | High | Med | Strip fences, one strict-repair retry, then fail loudly with raw text |
| flash-lite still repeats one transition despite instruction | Med | Med | Inventory-membership + variety check in code; re-prompt once; last resort round-robin fill from inventory |
| Beat names an out-of-inventory transition/sfx | Med | Med | Post-validate membership; re-prompt with offenders; map to nearest valid or `none` |
| P01b inventory missing at run time | Low | High | Fail fast with clear error; do not fall back to free-choice (defeats the fix) |
| Model/version drift changes output shape | Med | Med | Pin model in input; validate every response against schema |
| Cost surprise on long footage | Med | Med | `estimate_cost` + announce before call (`AGENT_GUIDE.md:96`); sample mode |
| `agent_skills` points at wrong skill name | Med | Low | Verify against registry/`.agents/skills` before commit |
| File > 200 lines | Med | Low | Split prompt/parse into `footage_edit_prompt.py` |

## Security Considerations
- `GEMINI_API_KEY` read from env only; never logged or written into artifacts.
- Video uploaded to Google — note data-egress in `estimate_cost`/announcement; respect Decision Communication Contract before the paid call.

## Next Steps
Feeds Phase 04 (all downstream directors). Requires Phase 03 for `removal_spans`.
