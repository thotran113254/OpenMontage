"""How much of this machine the pipeline may use.

The VPS is shared: other projects' test runs and servers live on the same
cores. Left alone, one resolve ran 4 x264 encoders at ~1.5 threads per core
each, and a render added Remotion's Chrome workers on top — enough to pin every
core and starve everything else. Three levers, all read from here:

* `AUTOEDIT_CPU_BUDGET` — cores the pipeline plans for (default 60% of the
  cores this process may run on, at least 2). ffmpeg threads and Remotion
  concurrency are sized from it.
* `low_priority_prefix()` — `nice`/`ionice` for the pipeline subprocess, so
  whatever it cannot avoid using, it yields to interactive work first.
* `AUTOEDIT_NICE` — the nice level (default 10; 0 turns the prefix off).
"""

from __future__ import annotations

import os
import shutil

DEFAULT_SHARE = 0.6
DEFAULT_NICE = 10


def available_cores() -> int:
    """Cores this process may actually run on (affinity/cgroup-pinned), not the host's."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def cpu_budget() -> int:
    cores = available_cores()
    configured = _env_int("AUTOEDIT_CPU_BUDGET")
    if configured is None:
        configured = round(cores * DEFAULT_SHARE)
    return max(1, min(cores, max(2, configured)))


def thread_count(parallel_jobs: int = 1) -> int:
    """Threads for one of `parallel_jobs` ffmpeg processes running side by side."""
    return max(1, cpu_budget() // max(1, parallel_jobs))


def _side_threads(parallel_jobs: int) -> str:
    """Decoder and filter-graph threads: twice the encoder's, within the budget."""
    return str(min(cpu_budget(), 2 * thread_count(parallel_jobs)))


def ffmpeg_threads(parallel_jobs: int = 1) -> list[str]:
    """Output-side caps: the x264 encoder and the filter graph.

    Measured on this VPS, 4 spans of 1080p HEVC → x264 medium in parallel:
    encoder 1 / decoder 1 / filter 1 took 170s at 2.2 cores (the HEVC decoder
    starves); encoder 1 / decoder 2 / filter 2 took 77s at 4.2 cores and the
    least CPU-seconds of any setting; uncapped, one encode alone used ~7.7 cores.
    """
    return ["-threads", str(thread_count(parallel_jobs)),
            "-filter_threads", _side_threads(parallel_jobs)]


def ffmpeg_decode_threads(parallel_jobs: int = 1) -> list[str]:
    """Input-side cap; must come before the `-i` it applies to."""
    return ["-threads", _side_threads(parallel_jobs)]


def low_priority_prefix() -> list[str]:
    """Command prefix that runs a subprocess below normal CPU and I/O priority."""
    if os.name == "nt":
        return []
    level = _env_int("AUTOEDIT_NICE")
    level = DEFAULT_NICE if level is None else level
    if level <= 0 or not shutil.which("nice"):
        return []
    # `--` ends option parsing: uutils `nice` (this VPS) otherwise reads the
    # command's own flags, e.g. `python -m`, as its own and refuses to run.
    prefix = ["nice", "-n", str(min(19, level)), "--"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c2", "-n7", "--"]
    return prefix
