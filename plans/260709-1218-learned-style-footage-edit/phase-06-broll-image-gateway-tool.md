# Phase 06 — B-roll Image Gateway Tool (NineRouter, OpenAI-Images-Compatible)

## Context Links
- Shape reference: `tools/graphics/google_imagen.py` (existing image-gen BaseTool in this project — read first)
- Contract: `tools/base_tool.py`; registration: `tools/tool_registry.py:118-134`
- Selector routing: `AGENT_GUIDE.md:487-495` (`image_selector` auto-discovers any `capability="image_generation"` tool)
- Env (already added by main session — DO NOT re-add): `NINE_ROUTER_API_KEY`, `NINE_ROUTER_BASE_URL=https://9router-mega.thotran.com/v1`
- Consumed by: Phase 04 asset-director (b-roll image generation when no real footage)

## Overview
- **Priority:** P2
- **Status:** completed
- **Description:** New registered image-generation tool wrapping the user's hosted OpenAI-Images-compatible gateway. Generates b-roll images (chat-UI / product-demo mocks) when the footage-edit-analyzer flags a b-roll need with no real footage. Exposes `capability="image_generation"` so `image_selector` routes to it automatically.

## Key Insights
- Follows the established image-tool contract (`google_imagen.py`): `BaseTool`, `capability="image_generation"`, `runtime=API`, `tier=GENERATE`, env checked dynamically (NOT via `dependencies` binary check), writes PNG to disk, returns `ToolResult`.
- Selector auto-discovery: once registered with `capability="image_generation"`, `image_selector` picks it up with zero selector code changes (`AGENT_GUIDE.md:487`). No manifest edit needed.
- **Nonstandard response:** gateway returns `Accept: text/event-stream` (SSE), not a plain JSON body. A sync tool must consume the event stream and assemble the final image. The exact final-event schema (base64 vs URL, event name, terminal marker) is UNDOCUMENTED here → verify empirically with one small live call during implementation before finalizing parsing.
- API shape (given): `POST {base_url}/images/generations`, header `Authorization: Bearer {key}`, `Accept: text/event-stream`, body `{"model":"cx/gpt-5.5-image","prompt":str,"n":1,"size":"auto","quality":"auto","background":"auto","image_detail":"high","output_format":"png"}`.

## Requirements
**Functional**
- Class `NineRouterImage(BaseTool)` (PascalCase, no "Tool" suffix), `name="nine_router_image"`, `provider="nine_router"`, `capability="image_generation"`, `runtime=API`, `tier=GENERATE`, `stability=EXPERIMENTAL` (nonstandard SSE, unverified).
- `get_status()`: AVAILABLE iff `NINE_ROUTER_API_KEY` and `NINE_ROUTER_BASE_URL` both set (read `os.environ` only — never hardcode key).
- `install_instructions`: reference the two env vars (names only, no value).
- `input_schema`: `prompt` (required), `size` (default "auto"), `quality` (default "auto"), `output_format` (default "png"), `output_dir`, `model` (default `cx/gpt-5.5-image`).
- `execute`: POST with SSE accept header; stream-consume; extract final image (base64 → decode to PNG, or URL → download); write PNG to `output_dir`; return `ToolResult(success, artifacts=[png_path], model, cost_usd, duration_seconds)`.
- Robust SSE handling: iterate `response.iter_lines()`, parse `data:` events, detect terminal/`[DONE]`, tolerate keepalive/comment lines, surface gateway error events as `ToolResult(success=False, error=...)`.
- Timeout + one retry on transient network error; clear error if key missing or stream yields no image.

**Non-functional**
- File < 200 lines. Uses `requests` (already used in `google_imagen.py:184`) with `stream=True`.
- `estimate_cost`: best-effort constant (gateway cost unknown) + announce before call per Decision Communication Contract (`AGENT_GUIDE.md:96`).

## Architecture
```
NineRouterImage.execute
  ├─ validate env (key + base_url)
  ├─ POST {base_url}/images/generations  (Bearer key, Accept: text/event-stream, stream=True)
  ├─ consume SSE: for line in iter_lines -> parse data: events
  │     -> accumulate until terminal event carrying image (b64 | url)   [VERIFY schema live]
  ├─ decode/download -> write <output_dir>/<slug>.png
  └─ ToolResult(success, artifacts=[png])
```
Registered → `image_selector` routes b-roll image requests here (P04 asset-director). Output PNG referenced by `asset_manifest` → `edit_decisions.overlays[]`.

## Related Code Files
- **Create:** `tools/graphics/nine_router_image.py` (class `NineRouterImage`)
- **Read (no edit):** `tools/graphics/google_imagen.py`, `tools/base_tool.py`, `tools/tool_registry.py`
- **No edit** to `.env` (already populated), no edit to `image_selector` (auto-discovers).

## Implementation Steps
1. Read `google_imagen.py` fully for the field set, PNG-write, and `ToolResult` conventions; mirror them.
2. Scaffold `NineRouterImage` identity + `input_schema` + `get_status()` (env-based).
3. Implement the SSE POST with `requests(..., stream=True)`; iterate lines.
4. **Live-verify step:** make one minimal real call (small prompt) and log the raw SSE events to determine the terminal event + image field. Adjust parser to the observed schema. Record findings in the decision log (`category: "capability_extension"`).
5. Decode base64 → PNG (or download URL); write to `output_dir`; return artifacts.
6. Error paths: missing env, HTTP error, error event, empty stream, timeout+retry.
7. Register check: `python -c "from tools.tool_registry import registry; registry.discover(); print(registry.get('nine_router_image').get_info()['status'])"` and confirm it appears in `registry.get_by_capability('image_generation')`.

## Todo List
- [x] Read google_imagen.py conventions
- [x] Scaffold NineRouterImage (identity, schema, env get_status)
- [x] SSE streaming POST + line parser
- [x] Live-verify terminal event / image field (small call) — verified 2026-07-09, see raw shape below
- [x] Decode/download → write PNG (b64_json path implemented + unit-verified against real schema; url fallback implemented defensively, not observed live)
- [x] Error + retry paths (HTTP >=400, SSE error event, empty stream, timeout/connection-error retry once, unexpected exception)
- [x] Confirm registry + image_selector discovery

### Live verification notes (one real call, prompt "a single red circle on white background", quality=low, image_detail=low)
Raw observed SSE shape (HTTP 200, `Content-Type: text/event-stream`):
```
event: progress
data: {"stage":"response.created","bytesReceived":2418}

event: progress
data: {"stage":"response.output_item.added","bytesReceived":3246}

event: progress
data: {"stage":"response.image_generation_call.generating","bytesReceived":3458}

event: progress
data: {"stage":"response.image_generation_call.partial_image","bytesReceived":1161882}

event: partial_image
data: {"b64_json":"<partial preview PNG, ~1.16MB b64, discarded>"}

event: progress
data: {"stage":"response.output_item.done","bytesReceived":2321032}

event: progress
data: {"stage":"response.content_part.done","bytesReceived":2321528}

event: done
data: {"created":1783578181,"data":[{"b64_json":"<final PNG, ~2.3MB b64>"}]}
```
Terminal event is `done`; final image is `data.data[0].b64_json` (base64 PNG), not a URL. No `[DONE]` sentinel line follows — stream simply ends. `url` fallback path implemented defensively per spec but not exercised live.

Parser logic (`_parse_sse`) unit-tested by replaying this exact event/line sequence (synthetic small payload) through the shipped code — confirms progress/partial_image events are correctly ignored and the terminal `done` event's `b64_json` is extracted and decodes to valid bytes. A second live network call to exercise the full `execute()` path end-to-end (POST -> parse -> write PNG) was intentionally NOT made, to respect the single-test-call quota constraint; the implementation is a direct, unit-verified match to the schema observed in the one live call above. Registry confirms `status=available`, registered under `capability=image_generation` alongside the other 11 image providers (visible to `image_selector`).

## Success Criteria
- Registered; `get_info()["status"]` AVAILABLE with env set, UNAVAILABLE (clean) without.
- Appears in `registry.get_by_capability("image_generation")` → `image_selector` can route to it.
- One real call produces a valid PNG on disk (verified by ffprobe/PIL open).
- No API key value present anywhere in source; env-only.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SSE final-event schema differs from assumption | High | High | Live-verify (step 4) BEFORE finalizing parser; log raw events; don't assume base64 |
| Gateway slow / streams partial then stalls | Med | Med | Timeout + one retry; fail with clear error, no silent hang |
| Cost unknown → surprise spend | Med | Med | Announce before call; `estimate_cost` placeholder; sample-first in asset-director |
| Key accidentally logged | Low | High | Never log headers; read env only; scrub error messages |
| `size:"auto"` yields wrong aspect for 9:16 b-roll | Med | Low | Allow explicit `size` param; asset-director passes vertical size when needed |

## Security Considerations
- `NINE_ROUTER_API_KEY` read from `os.environ` exclusively; never written to artifacts, logs, or committed. Base URL is non-secret but treat key header carefully.
- Generated b-roll must use MOCK content (no real customer chat/PII) — enforced in asset-director prompt.

## Next Steps
Feeds Phase 04 asset-director b-roll image path; covered by Phase 05 registration/availability test (live call gated behind env flag).
