"""Talking-head auto-edit: word-anchored 6-stage job pipeline.

The accuracy of this pipeline rests on one invariant: there is exactly ONE
clock (the Whisper word spine) and the AI director never emits a second —
only word indices. `stages/resolve.py` is the single place that turns word
indices into real time, and the single place that cuts media.

Nothing here is imported by `tools/` at module scope: `registry.discover()`
walks the whole `tools` package, so heavy job/server code lives in `lib/`.
"""

from lib.talking_head_edit.job_store import Job, JobStore  # noqa: F401

__all__ = ["Job", "JobStore"]
