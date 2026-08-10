"""Cost-governance coverage the phase 04 registration suite left open.

`tests/test_cloud_render_tool_registration.py::TestCostGovernance` already
proves the approval gate (`reserve()` raises `ApprovalRequiredError` on first
paid use, using real `config.yaml` defaults) -- that is NOT duplicated here.

This file covers the two remaining bullets from the phase's coverage
matrix row "cost (P4)":

- `reconcile` gets actual, not estimate.
- `refund` on pre-rental failure.

`cost_tracker.py` itself is unmodified by this plan (it is a generic,
tool-agnostic budget ledger the orchestrator/agent layer calls around any
paid tool -- confirmed by grep: no tool's `execute()` calls
`CostTracker.reserve/reconcile/refund` anywhere in this repo today). So
these tests exercise `CostTracker` directly against `vast_cloud_render`'s
own numbers (`estimate_cost()` for the pre-flight estimate, a rental's
`actual_usd` for the post-hoc reconcile), which is the calling convention
`skills/core/cloud-render.md` documents an agent must follow. `refund()`
itself has zero test coverage anywhere in the repo before this file.
"""

from __future__ import annotations

import pytest

from lib.config_model import BudgetMode
from tools.cost_tracker import ApprovalRequiredError, CostTracker, EntryStatus
from tools.video.vast_cloud_render import VastCloudRender


@pytest.fixture
def render_tool() -> VastCloudRender:
    return VastCloudRender()


@pytest.fixture
def approved_tracker() -> CostTracker:
    """Budget-mode OBSERVE + tool pre-approved -- isolates these tests from
    the approval-gate behavior (already covered elsewhere) so they can focus
    on reconcile/refund arithmetic."""
    tracker = CostTracker(budget_total_usd=10.0, mode=BudgetMode.OBSERVE)
    tracker.approve_tool("vast_cloud_render")
    return tracker


# ---------------------------------------------------------------------------
# reconcile() gets actual, not estimate
# ---------------------------------------------------------------------------

class TestReconcileUsesActualNotEstimate:
    def test_reconcile_after_a_render_records_actual_usd_not_the_preflight_estimate(
            self, approved_tracker, render_tool):
        # The tool's own pre-flight estimate, from the real estimate_cost()
        # contract (uses config ceilings as the worst-case dph since no
        # search has happened yet -- see VastCloudRender._cost_inputs).
        estimated_usd = render_tool.estimate_cost(
            {"mode": "render_now", "job_id": "job-1"})
        entry_id = approved_tracker.estimate("vast_cloud_render", "render_now", estimated_usd)
        approved_tracker.reserve(entry_id)
        assert approved_tracker.budget_reserved_usd == pytest.approx(estimated_usd)

        # The rental actually came in cheaper than the ceiling-based estimate
        # (the normal case -- estimate_cost() uses max_dph_usd as a
        # deliberately pessimistic stand-in before any real offer search).
        actual_usd = round(estimated_usd * 0.4, 4)
        assert actual_usd != estimated_usd, "fixture must exercise actual != estimate"

        approved_tracker.reconcile(entry_id, actual_usd, success=True)

        assert approved_tracker.budget_spent_usd == pytest.approx(actual_usd)
        assert approved_tracker.budget_reserved_usd == 0.0
        entry = approved_tracker.entries[-1]
        assert entry["status"] == EntryStatus.COMPLETED.value
        assert entry["actual_usd"] == pytest.approx(actual_usd)
        assert entry["actual_usd"] != entry["estimated_usd"]

    def test_reconcile_on_failure_still_records_actual_partial_spend(self, approved_tracker):
        """A rental that fails mid-render still burned real rented-minutes --
        reconcile(success=False) must keep that actual spend, not zero it out
        as if nothing happened."""
        entry_id = approved_tracker.estimate("vast_cloud_render", "render_now", 0.05)
        approved_tracker.reserve(entry_id)

        partial_actual_usd = 0.02  # instance ran a few minutes before npm ci failed
        approved_tracker.reconcile(entry_id, partial_actual_usd, success=False)

        assert approved_tracker.budget_spent_usd == pytest.approx(partial_actual_usd)
        entry = approved_tracker.entries[-1]
        assert entry["status"] == EntryStatus.FAILED.value
        assert entry["actual_usd"] == pytest.approx(partial_actual_usd)


# ---------------------------------------------------------------------------
# refund() on pre-rental failure
# ---------------------------------------------------------------------------

class TestRefundOnPreRentalFailure:
    def test_refund_zeroes_the_reservation_without_touching_spend(self, approved_tracker):
        entry_id = approved_tracker.estimate("vast_cloud_render", "render_now", 0.05)
        approved_tracker.reserve(entry_id)
        assert approved_tracker.budget_reserved_usd == pytest.approx(0.05)

        approved_tracker.refund(entry_id)

        assert approved_tracker.budget_reserved_usd == 0.0
        assert approved_tracker.budget_spent_usd == 0.0  # never actually spent
        entry = approved_tracker.entries[-1]
        assert entry["status"] == EntryStatus.REFUNDED.value

    def test_refund_after_no_eligible_offer_never_shows_up_as_spend(
            self, approved_tracker, render_tool):
        """Mirrors `skills/core/cloud-render.md`'s documented step 5: "on any
        failure before a rental actually starts: tracker.refund(entry)".
        The failure mode itself -- `remote.render_now()` raising
        `CloudRenderError` because `vast_client.search()` found no offer
        under the ceiling, before any `create_instance`/spend -- is already
        proven end to end in
        `tests/test_cloud_render_remote.py::TestRenderNowFailureModesAlwaysDestroyOnce::test_no_eligible_offer_never_rents_or_destroys`.
        This test covers what that one does not: the budget-side bookkeeping
        a caller must do when it catches that exact exception."""
        from lib.cloud_render.remote import CloudRenderError

        estimated_usd = render_tool.estimate_cost({"mode": "render_now", "job_id": "job-1"})
        entry_id = approved_tracker.estimate("vast_cloud_render", "render_now", estimated_usd)
        approved_tracker.reserve(entry_id)
        assert approved_tracker.budget_reserved_usd == pytest.approx(estimated_usd)

        try:
            raise CloudRenderError(
                "Không có offer nào <= $0.15/h khớp query "
                "'reliability>0.95 cpu_cores_effective>=32'")
        except CloudRenderError:
            # No create_instance ever happened -- refund, not reconcile.
            approved_tracker.refund(entry_id)

        assert approved_tracker.budget_spent_usd == 0.0
        assert approved_tracker.budget_reserved_usd == 0.0
        assert approved_tracker.entries[-1]["status"] == EntryStatus.REFUNDED.value

    def test_refund_raises_for_unknown_entry(self, approved_tracker):
        with pytest.raises(KeyError):
            approved_tracker.refund("does-not-exist")


# ---------------------------------------------------------------------------
# Sanity: the approval gate this whole module's fixture bypasses is still
# real and still enforced (regression guard against the fixture silently
# neutering it -- the actual gate test lives in
# tests/test_cloud_render_tool_registration.py::TestCostGovernance).
# ---------------------------------------------------------------------------

def test_reserve_without_approval_still_raises_for_a_fresh_tracker():
    tracker = CostTracker(budget_total_usd=10.0, mode=BudgetMode.WARN)
    entry_id = tracker.estimate("vast_cloud_render", "render_now", 0.05)
    with pytest.raises(ApprovalRequiredError):
        tracker.reserve(entry_id)
