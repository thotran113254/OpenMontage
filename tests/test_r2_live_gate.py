"""CI safety guard for the opt-in live R2 suite (`tests/live/test_r2_live_smoke.py`).

Deliberately NOT marked `@pytest.mark.live` -- mirrors
`tests/test_cloud_render_live_gate.py`: this must run in the default suite
so it can catch a real R2 secret landing in the CI environment even though
the live test itself stays deselected by marker.
"""

from __future__ import annotations

import os


def test_r2_live_test_env_var_is_never_set_while_running_in_ci():
    if os.environ.get("CI"):
        assert not os.environ.get("R2_LIVE_TEST"), (
            "R2_LIVE_TEST is set while CI is set -- remove it from the workflow/secrets; "
            "the live R2 suite must never run in CI (see tests/live/README.md)")


def test_no_real_r2_secret_present_while_running_in_ci():
    if os.environ.get("CI"):
        assert not os.environ.get("CLOUDFLARE_R2_SECRET_ACCESS_KEY"), (
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY is set in CI -- no test in this repo "
            "should ever need a real R2 credential; the unit/integration suites "
            "run entirely against moto or stubs")
