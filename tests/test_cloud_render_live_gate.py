"""CI safety guard for the opt-in live Vast.ai suite (`tests/live/`).

Deliberately NOT marked `@pytest.mark.live` -- this test must run in the
default suite (the one CI actually executes) so it can catch the one thing
`-m "not live"` cannot: someone adding `VAST_LIVE_TEST` as a repository/
workflow secret so it is present in the CI environment even though the live
tests themselves stay deselected by marker. Cheap insurance per the phase's
risk register: "Someone adds a Vast.ai secret to CI 'to test the real
thing'".
"""

from __future__ import annotations

import os


def test_vast_live_test_env_var_is_never_set_while_running_in_ci():
    """`CI=true` is set by GitHub Actions (and most other CI providers) for
    every job. If `VAST_LIVE_TEST` is also set in that same environment, a
    future change to `.github/workflows/ci.yml` that drops `-m "not live"`
    would start renting real Vast.ai instances on every push/PR."""
    if os.environ.get("CI"):
        assert not os.environ.get("VAST_LIVE_TEST"), (
            "VAST_LIVE_TEST is set while CI is set -- remove it from the workflow/secrets; "
            "the live cloud-render suite must never run in CI (see tests/live/README.md)")
