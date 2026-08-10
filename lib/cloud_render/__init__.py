"""Cloud render: rent a Vast.ai box, render, tear it down, never leak a rental.

Re-exports the small public surface the tool wrappers
(`tools/video/vast_cloud_render.py`, `tools/video/cloud_render_queue.py`)
need, so they import one surface (`lib.cloud_render`) instead of reaching
into every submodule directly.

Safe to import eagerly: none of `remote`, `queue`, `ledger`, `config` import
the `vastai` SDK at module scope -- only `vast_client._client()` does,
lazily, inside the function body that actually needs the account (see
`vast_client.py`'s module docstring). `import lib.cloud_render` therefore
never requires `vastai` to be installed, and never loads it as a side
effect -- a missing/optional dependency must degrade a caller's feature
detection, not crash it. Reach into a submodule directly
(`from lib.cloud_render import vast_client`) for anything not re-exported
here.
"""

from __future__ import annotations

from lib.cloud_render import config, ledger, queue
from lib.cloud_render.queue import flush
from lib.cloud_render.remote import render_now

__all__ = ["render_now", "flush", "queue", "ledger", "config"]
