"""Disfluency-detection prompt fragment + output schema snippet, injected
into ``footage_edit_prompt.build_prompt()`` (tools/analysis/footage_edit_prompt.py).

Split into its own module (rather than inlined in footage_edit_prompt.py,
already at the project's ~200-line convention) so this stays independently
readable/testable.

Design note (why this prompt is careful): an earlier generic "find fillers"
prompt this session produced false positives — it flagged real content
("3 sai") as filler and flagged "thì" as filler despite being told the word
has a legitimate grammatical role. Disfluency detection is even riskier
(it requires semantic judgment, not lexicon matching), so this prompt:
  - requires BOTH the stumbled fragment AND the clean continuation to be
    quoted verbatim, so a human reviewer can verify the claim from the text
    alone without re-watching the video;
  - requires high confidence (>=0.8) and explicitly instructs an empty list
    as the correct/expected output for a clean take, rather than forcing a
    guess;
  - explicitly excludes grammatical connectors/fillers ("thì", "là", "á",
    "nè") from counting as disfluencies, even though they can sound
    hesitation-like out of context.
"""

from __future__ import annotations

MIN_DISFLUENCY_CONFIDENCE_DEFAULT = 0.75

DISFLUENCY_INSTRUCTIONS = """5. Additionally, scan the audio across the ENTIRE video for genuine SPEECH DISFLUENCIES: moments where the speaker visibly stumbles mid-sentence, then either repeats a word/phrase or abandons what they were saying and restarts with different wording. This is NOT the same as a filler word (à, ừm, ờ) and NOT the same as a grammatical connector (thì, là, á, nè) used correctly — do not report those. Only report a disfluency when BOTH conditions hold:
   - The speaker said a word, phrase, or partial sentence, then immediately repeated it verbatim OR abandoned it mid-word/mid-idea and continued with a corrected/different version (examples: saying the same word twice in a row unintentionally, starting a sentence then restarting it with different wording, a false start followed by self-correction).
   - You are HIGHLY CONFIDENT (>= 0.8) this is an unintentional stumble — not stylistic repetition used for emphasis, not a normal filler word, not a correctly-used connector.
   For each genuine disfluency, report start_time/end_time (MM:SS, spanning from the start of the stumbled fragment to the end of the corrected continuation), the EXACT stumbled/repeated/abandoned words as spoken (stumbled_fragment), the EXACT words spoken right after that represent the corrected/continued version (clean_continuation), and a confidence score. If you are not highly confident about ANY moment in the video, return an empty disfluency_spans list — that is the correct and expected output for a clean take, not a failure."""

DISFLUENCY_SCHEMA_SNIPPET = """,
  "disfluency_spans": [
    {"start_time": "MM:SS", "end_time": "MM:SS", "stumbled_fragment": string, "clean_continuation": string, "confidence": number}
  ]"""
