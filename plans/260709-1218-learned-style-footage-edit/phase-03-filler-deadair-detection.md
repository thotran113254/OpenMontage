# Phase 03 — Filler-Word / Dead-Air Detection

## Context Links
- `tools/analysis/transcriber.py` (word-level timestamps)
- `tools/analysis/audio_energy.py` (loudness profile, corroboration only)
- `tools/base_tool.py` (tool contract)
- Consumed by: Phase 02 (`removal_spans`), Phase 04 (edit-director drops ranges)

## Overview
- **Priority:** P1 (parallel with P02; both feed P04)
- **Status:** completed
- **Description:** Produce time-coded `removal_spans[]` marking filler words ("à", "ừm", "ờ", "kiểu") and dead-air pauses in raw footage, so the edit stage can tighten the single take. This is a NEW requirement designed from editing best-practice — the clean reference has no filler to observe.

## Key Insights
- `transcriber` already emits word-level `start/end/probability` (`transcriber.py:170-179`) and `vad_filter=True` (`transcriber.py:154`). This is the primary signal: **dead-air = inter-word gap > threshold**; **filler = word text ∈ lexicon**.
- `audio_energy` is 1-second granular and tuned for music-offset selection (`audio_energy.py:184-200`) — too coarse for frame-accurate cut points. Use ONLY to corroborate a suspected pause (low LUFS over the gap), not as the boundary source.
- No existing tool flags filler/dead-air — genuine gap. Keep it a **separate module** from P02's analyzer to preserve file-ownership isolation and single-responsibility (SRP/KISS).
- Vietnamese fillers are short and whisper may attach them to adjacent words — lexicon match must be substring/normalized (lowercase, strip punctuation/diacritic-tolerant).

## Requirements
**Functional**
- New tool `SpeechGapDetector` (`name="speech_gap_detector"`, `capability="analysis"`, `runtime=LOCAL`).
- Inputs: `input_path` (audio/video), `filler_lexicon[]` (default VI list, overridable), `min_dead_air_seconds` (default 0.6), `min_filler_confidence` (default 0.5), `output_dir`.
- Runs `transcriber` internally (word_timestamps) OR accepts a precomputed transcript path (avoid double transcription when P02 already has one — accept `transcript_json` optional param).
- Emits `removal_spans[]`: `{start_seconds, end_seconds, kind: "filler"|"dead_air", text?, confidence}`.
- Dead-air: gap between consecutive word `end`→next `start` ≥ `min_dead_air_seconds`; trim to leave a small natural pad (e.g. keep 0.15s) — output the removable middle.
- Filler: word whose normalized text ∈ lexicon and `probability` ≥ `min_filler_confidence`.
- Never remove overlapping spans twice; merge adjacent removals.

**Non-functional**
- < 200 lines; pure-python span math; no new heavy deps (reuse transcriber's whisper).

## Architecture
```
SpeechGapDetector.execute
  ├─ transcript = provided or Transcriber(input_path).word_timestamps
  ├─ dead_air_spans = gaps >= min_dead_air (word[i].end -> word[i+1].start)
  ├─ filler_spans   = words where normalize(text) in lexicon & prob>=thr
  ├─ (optional) corroborate dead_air via audio_energy low-LUFS over gap
  └─ merge + sort -> removal_spans[]
```
Boundary edge cases explicitly handled: leading/trailing silence, gap at segment boundary, filler immediately adjacent to a real word (cut only the filler token span), back-to-back fillers.

## Related Code Files
- **Create:** `tools/analysis/speech_gap_detector.py` (class `SpeechGapDetector`)
- **Read (no edit):** `tools/analysis/transcriber.py`, `tools/analysis/audio_energy.py`, `tools/base_tool.py`
- **No edit** to transcriber (call it, don't modify).

## Implementation Steps
1. Define default VI filler lexicon constant (normalized): `à, ừm, ờ, ừ, kiểu, kiểu như, thì, ừ thì` (curate; keep conservative to avoid cutting meaningful words like "thì").
2. Scaffold `SpeechGapDetector(BaseTool)` with input_schema + fields.
3. If `transcript_json` provided, load it; else run `Transcriber` (base model) and read `word_timestamps`.
4. Compute dead-air spans from word gaps; apply pad; enforce `min_dead_air_seconds`.
5. Compute filler spans via normalized lexicon match + probability threshold.
6. Optional: for each dead-air candidate, sample `audio_energy` profile; drop candidate if loudness suggests speech present (guards VAD misses).
7. Merge/sort/dedupe spans; write `removal_spans.json`; return `ToolResult`.
8. Register check via registry discover.

## Todo List
- [x] Curate conservative VI filler lexicon
- [x] Implement SpeechGapDetector (transcript-or-run)
- [x] Dead-air gap math + pad + edge cases
- [x] Filler lexicon match + confidence gate
- [x] Optional audio_energy corroboration
- [x] Merge/dedupe + write artifact
- [x] Registry discovery check

## Success Criteria
- On a talking-head clip with induced pauses + fillers, detects them within ±0.2s; no false removal of meaningful words in a 10-utterance smoke test.
- Output merges cleanly into P02 `footage_edit_plan.removal_spans[]` (same shape).
- Runs offline (LOCAL), no API key.

## Risk Assessment
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Aggressive lexicon cuts meaningful words ("thì" is a real conjunction) | Med | High | Keep lexicon conservative; require confidence gate; make lexicon overridable; corroborate |
| Whisper VAD already swallowed the pause → gap invisible | Med | Med | audio_energy corroboration + allow `diarize=False` base run; document limitation |
| Double transcription cost/time if P02 also transcribes | Med | Low | Accept precomputed `transcript_json` param; P02 passes its transcript |
| transcriber UNAVAILABLE (faster-whisper not installed) | Med | Med | Detect + return structured error; degrade to audio_energy-only dead-air (no filler) |

## Security Considerations
None — fully local, no secrets, no network.

## Next Steps
Feeds Phase 02 merge and Phase 04 edit-director removal logic.
