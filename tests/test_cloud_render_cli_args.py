"""Phase 07 -- CLI arg-validation tests for `lib/talking_head_edit/cli.py`'s
cloud-render surface (`--render-location`, `--cloud-offer`, `--cloud-yes`,
`--cloud-offers`, `--cloud-max-usd`, `--cloud-queue*`, `--cloud-flush`).

Deliberately its own file (not shared with any `tests/test_talking_head_*`
or `tests/test_cloud_render_*` file another phase owns) so a parallel test
suite phase touching those files never collides with this one.

Every test here is network-free. `--cloud-offers` is exercised end-to-end
with `vast_client.search`/`list_labelled_instances` faked and `vast_client.rent`
replaced by a call-recording bomb -- proving a real rental is unreachable from
this flag, not merely unlikely (same standard as
`tests/test_cloud_render_tool_registration.py`'s `no_sdk_calls` fixture).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from lib.cloud_render import dry_run_store, ledger, queue, vast_client
from lib.talking_head_edit import cli

# ---------------------------------------------------------------------------
# Pure validation matrix -- _validate_cloud_args / _clamp_cloud_max_usd /
# _split_at_render need no fixtures at all: zero I/O, zero network.
# ---------------------------------------------------------------------------

PARSER = cli.build_parser()


def _args(*argv: str):
    return PARSER.parse_args(list(argv))


class TestValidateCloudArgs:
    def test_render_location_cloud_without_offer_or_yes_is_refused(self):
        error = cli._validate_cloud_args(_args("--render-location", "cloud"))
        assert error is not None
        assert "--cloud-offer" in error and "--cloud-yes" in error

    def test_render_location_cloud_with_offer_is_allowed(self):
        assert cli._validate_cloud_args(
            _args("--render-location", "cloud", "--cloud-offer", "5")) is None

    def test_render_location_cloud_with_yes_is_allowed(self):
        assert cli._validate_cloud_args(
            _args("--render-location", "cloud", "--cloud-yes")) is None

    def test_local_render_location_never_needs_cloud_flags(self):
        assert cli._validate_cloud_args(_args()) is None

    def test_autopilot_cloud_without_yes_is_refused(self):
        error = cli._validate_cloud_args(
            _args("--autopilot", "--render-location", "cloud"))
        assert error is not None
        assert "--cloud-yes" in error

    def test_autopilot_cloud_without_yes_is_refused_even_with_cloud_offer(self):
        """`--cloud-offer` alone is not "a human present" for an unattended
        autopilot run -- only `--cloud-yes` satisfies the autopilot gate."""
        error = cli._validate_cloud_args(
            _args("--autopilot", "--render-location", "cloud", "--cloud-offer", "5"))
        assert error is not None
        assert "--cloud-yes" in error

    def test_autopilot_cloud_with_yes_is_allowed(self):
        assert cli._validate_cloud_args(
            _args("--autopilot", "--render-location", "cloud", "--cloud-yes")) is None

    def test_autopilot_local_never_needs_cloud_yes(self):
        assert cli._validate_cloud_args(_args("--autopilot")) is None

    def test_cloud_flush_without_yes_is_refused(self):
        error = cli._validate_cloud_args(_args("--cloud-flush"))
        assert error is not None
        assert "--cloud-yes" in error

    def test_cloud_flush_with_yes_is_allowed(self):
        assert cli._validate_cloud_args(_args("--cloud-flush", "--cloud-yes")) is None

    def test_cloud_offers_never_needs_consent_flags(self):
        """--cloud-offers only searches (read-only) and exits -- the
        render-location consent rule must not block it."""
        assert cli._validate_cloud_args(_args("--cloud-offers")) is None
        assert cli._validate_cloud_args(
            _args("--cloud-offers", "--render-location", "cloud")) is None

    def test_cloud_queue_status_never_needs_consent_flags(self):
        assert cli._validate_cloud_args(_args("--cloud-queue-status")) is None

    def test_cloud_queue_never_needs_consent_flags(self):
        assert cli._validate_cloud_args(_args("--cloud-queue")) is None
        assert cli._validate_cloud_args(
            _args("--cloud-queue", "--render-location", "cloud")) is None


class TestClampCloudMaxUsd:
    def test_none_keeps_the_configured_ceiling_no_warning(self):
        value, warning = cli._clamp_cloud_max_usd(None, 0.50)
        assert value == 0.50
        assert warning is None

    def test_within_ceiling_passes_through_no_warning(self):
        value, warning = cli._clamp_cloud_max_usd(0.20, 0.50)
        assert value == 0.20
        assert warning is None

    def test_above_ceiling_is_clamped_down_with_a_warning(self):
        """The exact success-criterion case: --cloud-max-usd 999 must clamp
        to the config ceiling, never raise it, and must warn."""
        value, warning = cli._clamp_cloud_max_usd(999, 0.50)
        assert value == 0.50
        assert warning is not None
        assert "0.5" in warning

    def test_non_positive_value_is_rejected_down_to_the_ceiling(self):
        value, warning = cli._clamp_cloud_max_usd(-1.0, 0.50)
        assert value == 0.50
        assert warning is not None

    def test_exactly_at_ceiling_is_not_a_raise_and_is_not_warned(self):
        value, warning = cli._clamp_cloud_max_usd(0.50, 0.50)
        assert value == 0.50
        assert warning is None


class TestCloudRenderPlan:
    """`_cloud_render_plan` decides whether *this* invocation should route
    'render' through cloud at all -- the fix for a real bug caught during
    review: a custom `--stages` list that never includes 'render' (e.g.
    `--stages audit,resolve --render-location cloud`) must not attempt a
    cloud render just because `--render-location cloud` was passed."""

    def test_local_render_location_never_activates_cloud(self):
        assert cli._cloud_render_plan(_args(), None) is None

    def test_cloud_with_default_full_stage_plan_splits_at_render(self):
        from lib.talking_head_edit.job_store import STAGES

        plan = cli._cloud_render_plan(_args("--render-location", "cloud", "--cloud-yes"), None)
        assert plan is not None
        pre, post = plan
        assert "render" not in pre and "render" not in post
        assert pre == [s for s in STAGES if s != "calibrate" and s != "render"
                       and STAGES.index(s) < STAGES.index("render")]
        assert post == ["verify"]

    def test_cloud_with_custom_stages_excluding_render_does_not_activate(self):
        """The bug this test guards against: --stages without 'render' must
        never trigger a cloud rental."""
        args = _args("--render-location", "cloud", "--cloud-yes",
                     "--stages", "audit,resolve")
        assert cli._cloud_render_plan(args, ["audit", "resolve"]) is None

    def test_cloud_with_custom_stages_including_render_splits_correctly(self):
        args = _args("--render-location", "cloud", "--cloud-yes")
        plan = cli._cloud_render_plan(args, ["resolve", "render", "verify"])
        assert plan == (["resolve"], ["verify"])

    def test_skip_render_disables_cloud_even_if_requested(self):
        args = _args("--render-location", "cloud", "--cloud-yes", "--skip-render")
        assert cli._cloud_render_plan(args, None) is None

    def test_autopilot_disables_the_direct_cloud_plan(self):
        """Autopilot routes cloud through `_cloud_aware_runner` instead --
        `_cloud_render_plan` must stay inert for it (checked separately by
        `main()`'s own `autopilot_cloud` flag)."""
        args = _args("--autopilot", "--render-location", "cloud", "--cloud-yes")
        assert cli._cloud_render_plan(args, None) is None

    def test_render_only_plan_yields_empty_pre(self):
        args = _args("--render-location", "cloud", "--cloud-yes", "--only", "render")
        assert cli._cloud_render_plan(args, ["render"]) == ([], [])


class TestSplitAtRender:
    def test_splits_around_render(self):
        pre, post = cli._split_at_render(["probe", "resolve", "render", "verify"])
        assert pre == ["probe", "resolve"]
        assert post == ["verify"]

    def test_render_absent_is_all_pre_empty_post(self):
        pre, post = cli._split_at_render(["probe", "resolve"])
        assert pre == ["probe", "resolve"]
        assert post == []

    def test_render_first_yields_empty_pre(self):
        pre, post = cli._split_at_render(["render", "verify"])
        assert pre == []
        assert post == ["verify"]


# ---------------------------------------------------------------------------
# main()-level refusals -- no job/--input needed at all: _validate_cloud_args
# fires before job resolution, so these never touch the filesystem.
# ---------------------------------------------------------------------------

class TestMainLevelRefusals:
    def test_render_location_cloud_without_consent_exits_nonzero(self, capsys):
        code = cli.main(["--render-location", "cloud"])
        assert code != 0
        captured = capsys.readouterr()
        assert "--cloud-offer" in captured.err or "--cloud-offer" in captured.out
        assert "--cloud-yes" in captured.err or "--cloud-yes" in captured.out

    def test_render_location_cloud_never_prompts(self, capsys, monkeypatch):
        """No interactive prompt exists on this path at all -- proven by
        making `input()` a hard failure and confirming it is never called."""
        monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("CLI must never call input() -- no tty when run as a subprocess")))
        code = cli.main(["--render-location", "cloud"])
        assert code != 0

    def test_autopilot_cloud_without_cloud_yes_is_refused(self):
        assert cli.main(["--autopilot", "--render-location", "cloud"]) != 0

    def test_cloud_flush_without_cloud_yes_is_refused(self):
        assert cli.main(["--cloud-flush"]) != 0


# ---------------------------------------------------------------------------
# --cloud-offers end-to-end: fake vast_client, isolated state dirs, and a
# call-recording bomb standing in for any create/rent call.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FakeOffer:
    id: int = 40179084
    dph: float = 0.069
    cpu_cores_effective: float | None = 32.0
    ram_gb: float | None = 64.0
    disk_avail_gb: float | None = 100.0
    reliability: float | None = 0.98
    geolocation: str | None = "US"
    gpu_name: str | None = None


@pytest.fixture()
def isolated_cloud_state(tmp_path, monkeypatch):
    """Redirect every on-disk cloud-render state file into `tmp_path` and
    fake the two read-only account calls `dry_run()`/`ledger.reap()` make.
    `vast_client.rent`/`create_instance`-equivalent is replaced by a
    call-recording bomb: any attempt to reach it fails the test loudly."""
    state_dir = tmp_path / "cloud-render"
    monkeypatch.setattr(dry_run_store, "STATE_DIR", state_dir)
    monkeypatch.setattr(dry_run_store, "REFS_PATH", state_dir / "dry_runs.json")
    monkeypatch.setattr(ledger, "STATE_DIR", state_dir)
    monkeypatch.setattr(ledger, "ACTIVE_PATH", state_dir / "active.json")
    monkeypatch.setattr(ledger, "RENTALS_LOG_PATH", state_dir / "rentals.jsonl")
    monkeypatch.setattr(queue, "STATE_DIR", state_dir)
    monkeypatch.setattr(queue, "QUEUE_PATH", state_dir / "batch-queue.json")
    monkeypatch.setattr(queue, "LOCK_PATH", state_dir / "batch-queue.lock")

    calls: list[tuple] = []

    def fake_search(query, *, mode="on-demand", order="score-", limit=None, api_key=None):
        calls.append(("search", mode))
        return [FakeOffer()]

    def fake_list_labelled(*, api_key=None):
        calls.append(("list_labelled_instances",))
        return []

    def bomb(*args, **kwargs):
        calls.append(("RENT_OR_CREATE_INSTANCE_CALLED", args, kwargs))
        raise AssertionError(
            "vast_client.rent (create_instance path) was reached -- "
            "--cloud-offers must never be able to rent anything")

    monkeypatch.setattr(vast_client, "search", fake_search)
    monkeypatch.setattr(vast_client, "list_labelled_instances", fake_list_labelled)
    monkeypatch.setattr(vast_client, "rent", bomb)
    return calls


class TestCloudOffers:
    def test_prints_shortlist_and_creates_zero_instances(self, isolated_cloud_state, capsys):
        code = cli.main(["--cloud-offers"])
        captured = capsys.readouterr()

        assert "RENDER LOCATION: cloud (Vast.ai)" in captured.out
        assert "#40179084" in captured.out
        assert "Approve?" in captured.out
        assert code == 0

        assert not any(c[0] == "RENT_OR_CREATE_INSTANCE_CALLED" for c in isolated_cloud_state), (
            f"a rent/create call happened: {isolated_cloud_state}")
        # search() ran (once for the announced mode, once for the alternative
        # pricing mode) -- proves the shortlist is real, not a stub.
        assert any(c[0] == "search" for c in isolated_cloud_state)

    def test_works_without_a_job_id(self, isolated_cloud_state, capsys):
        """No --job/--input required at all -- --cloud-offers is standalone."""
        code = cli.main(["--cloud-offers"])
        assert code == 0
        assert not any(c[0] == "RENT_OR_CREATE_INSTANCE_CALLED" for c in isolated_cloud_state)

    def test_never_calls_input(self, isolated_cloud_state, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("--cloud-offers must never prompt")))
        assert cli.main(["--cloud-offers"]) == 0


class TestCloudQueueStatus:
    def test_prints_empty_queue_without_error(self, isolated_cloud_state, capsys):
        code = cli.main(["--cloud-queue-status"])
        captured = capsys.readouterr()
        assert code == 0
        assert "rỗng" in captured.out or "0" in captured.out
        assert not any(c[0] == "RENT_OR_CREATE_INSTANCE_CALLED" for c in isolated_cloud_state)


class TestCloudFlushRefusesWithoutQueueOrConsent:
    def test_cloud_flush_with_yes_but_empty_queue_refuses_cleanly(
        self, isolated_cloud_state, capsys,
    ):
        """`--cloud-yes` satisfies `_validate_cloud_args`, but an empty queue
        must still refuse before ever reaching `dry_run()`/`execute()` --
        there is nothing to search offers for."""
        code = cli.main(["--cloud-flush", "--cloud-yes"])
        captured = capsys.readouterr()
        assert code != 0
        assert "rỗng" in captured.err or "rỗng" in captured.out
        assert not any(c[0] == "RENT_OR_CREATE_INSTANCE_CALLED" for c in isolated_cloud_state)
        assert not any(c[0] == "search" for c in isolated_cloud_state)
