"""Self-tests for the shared fake `vastai` double (`tests/fixtures/fake_vastai.py`)
and its `fake_vastai_sdk` fixture (`tests/conftest.py`).

Phases 01-04 each already have their own inline fakes and 86 passing tests
built on them -- this file does not touch those. It proves the *new* shared
fixture behaves correctly so future cloud-render tests can rely on it
without re-deriving the same guarantees: call recording, failure injection,
status progression, and the `sys.modules` teardown guard.
"""

from __future__ import annotations

import sys

import pytest

from tests.fixtures import fake_vastai


def test_installed_module_is_sys_modules_vastai(fake_vastai_sdk):
    assert sys.modules["vastai"].__name__ == "vastai"
    assert isinstance(sys.modules["vastai"].VastAI(), object)


def test_calls_are_recorded_in_order():
    account = fake_vastai.FakeVastAI()
    account.seed_offers([{"id": 1}])
    account.search_offers(query="q", type="on-demand")
    account.create_instance(id=1, image="node:22-bookworm", disk=12, label="x")
    assert [c[0] for c in account.calls] == ["search_offers", "create_instance"]


def test_fail_on_raises_on_the_nth_call_only():
    account = fake_vastai.FakeVastAI(fail_on=("destroy_instance", 2))
    first = account.create_instance(id=1, label="a")["new_contract"]
    second = account.create_instance(id=2, label="b")["new_contract"]

    account.destroy_instance(id=first)  # 1st destroy_instance call -- no raise
    with pytest.raises(fake_vastai.FakeVastAIError):
        account.destroy_instance(id=second)  # 2nd call -- injected failure


def test_destroy_instance_still_recorded_even_when_it_raises():
    """The safety guarantee this whole suite tests is "was destroy called" --
    that must be provable even when the call itself fails."""
    account = fake_vastai.FakeVastAI(fail_on=("destroy_instance", 1))
    instance_id = account.create_instance(id=1, label="a")["new_contract"]
    with pytest.raises(fake_vastai.FakeVastAIError):
        account.destroy_instance(id=instance_id)
    assert account.calls[-1] == ("destroy_instance", {"id": instance_id})


def test_destroy_unknown_instance_raises_404_shaped_error():
    account = fake_vastai.FakeVastAI()
    with pytest.raises(fake_vastai.FakeVastAIError) as exc_info:
        account.destroy_instance(id=999999)
    assert exc_info.value.response.status_code == 404


def test_status_progression_defaults_to_running_on_first_poll():
    account = fake_vastai.FakeVastAI()
    response = account.create_instance(id=1, label="a")
    instance_id = response["new_contract"]
    assert account.show_instance(id=instance_id)["actual_status"] == "running"


def test_status_progression_can_be_scripted_to_stay_loading_for_n_polls():
    account = fake_vastai.FakeVastAI()
    response = account.create_instance(id=1, label="a", poll_until_running=3)
    instance_id = response["new_contract"]

    statuses = [account.show_instance(id=instance_id)["actual_status"] for _ in range(3)]

    assert statuses == ["loading", "loading", "running"]


def test_seed_instance_appears_in_show_instances_without_create_instance():
    account = fake_vastai.FakeVastAI()
    account.seed_instance(555, label="openmontage-orphan-until-1")
    assert any(i["id"] == 555 for i in account.show_instances())
    assert not any(c[0] == "create_instance" for c in account.calls)


def test_teardown_guard_passes_when_fixture_left_untouched(fake_vastai_sdk):
    """Sanity: using the fixture normally (no test tampering with
    sys.modules['vastai']) must not itself trip the teardown assertion --
    this test passing at all is the proof, since the assertion runs after
    this test function returns."""
    assert sys.modules["vastai"] is not None
