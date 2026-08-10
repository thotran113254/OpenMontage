"""Tests for lib/cloud_render/ledger.py + vast_client.py's rent()/reap()/destroy().

Zero network, zero spend: `vastai` is a fully fake module injected into
`sys.modules`, never the real installed SDK. `FakeVastAccount` stands in for
the whole account: rentals live in a dict, `create_instance` allocates a new
id, `destroy_instance` removes one (or raises a fake 404 if already gone).
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

FAKE_INSTANCE_ID_START = 40_000_000


class FakeHTTPError(Exception):
    """Stands in for `requests.HTTPError` -- has a `.response.status_code`."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = types.SimpleNamespace(status_code=status_code)


class FakeVastAccount:
    """The account's instance table + call log, shared by the fake `VastAI`."""

    def __init__(self):
        self.instances: dict[int, dict] = {}
        self.create_calls: list[dict] = []
        self.destroy_calls: list[int] = []
        self._next_id = FAKE_INSTANCE_ID_START

    def show_instances(self) -> list[dict]:
        return [dict(v) for v in self.instances.values()]

    def show_instance(self, id: int) -> dict:  # noqa: A002 -- matches SDK param name
        return dict(self.instances[id])

    def create_instance(self, id: int, image=None, disk=10, **kwargs) -> dict:  # noqa: A002
        self.create_calls.append({"offer_id": id, "image": image, "disk": disk, **kwargs})
        new_id = self._next_id
        self._next_id += 1
        self.instances[new_id] = {
            "id": new_id,
            "label": kwargs.get("label"),
            "actual_status": "running",
            "ssh_host": "1.2.3.4",
            "ssh_port": 22,
            "dph_total": kwargs.get("price") or 0.08,
        }
        return {"success": True, "new_contract": new_id}

    def destroy_instance(self, id: int) -> dict:  # noqa: A002
        self.destroy_calls.append(id)
        if id not in self.instances:
            raise FakeHTTPError(404)
        del self.instances[id]
        return {"success": True}

    def add_remote_instance(self, instance_id: int, label: str, **extra) -> None:
        """Test helper: seed an instance that exists on the account without
        going through create_instance (simulating a prior/orphaned rental)."""
        self.instances[instance_id] = {
            "id": instance_id, "label": label, "actual_status": "running",
            "ssh_host": "5.6.7.8", "ssh_port": 22, "dph_total": 0.08,
            **extra,
        }


@pytest.fixture()
def fake_account(monkeypatch):
    """Inject a fully fake `vastai` module into sys.modules for the duration
    of one test. Never touches the real installed SDK."""
    account = FakeVastAccount()

    class FakeVastAI:
        def __init__(self, api_key=None, **kwargs):
            self._account = account

        def __getattr__(self, name):
            return getattr(self._account, name)

    fake_module = types.ModuleType("vastai")
    fake_module.VastAI = FakeVastAI
    monkeypatch.setitem(sys.modules, "vastai", fake_module)
    return account


@pytest.fixture()
def cloud_render_modules(monkeypatch, tmp_path):
    """Reload ledger + vast_client with active.json/rentals.jsonl redirected
    into a tmp dir, so tests never touch the real projects/cloud-render/."""
    from lib.cloud_render import ledger, vast_client

    importlib.reload(ledger)
    importlib.reload(vast_client)

    active_path = tmp_path / "active.json"
    rentals_path = tmp_path / "rentals.jsonl"
    monkeypatch.setattr(ledger, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ledger, "ACTIVE_PATH", active_path)
    monkeypatch.setattr(ledger, "RENTALS_LOG_PATH", rentals_path)

    return ledger, vast_client


# ---------------------------------------------------------------------------
# rent() -- adopt-on-retry
# ---------------------------------------------------------------------------

def test_rent_twice_same_intent_id_creates_once_and_returns_same_instance(
        fake_account, cloud_render_modules):
    ledger, vast_client = cloud_render_modules

    kwargs = dict(intent_id="intent-a", deadline_epoch=9_999_999_999, ceiling_dph=0.20,
                 image="node:22-bookworm", disk_gb=12, onstart="echo hi")
    first = vast_client.rent(1, 0.08, **kwargs)
    second = vast_client.rent(1, 0.08, **kwargs)

    assert len(fake_account.create_calls) == 1
    assert first.id == second.id
    assert ledger.active()[0]["status"] == "active"
    assert ledger.active()[0]["instance_id"] == first.id


# ---------------------------------------------------------------------------
# rent() -- ceiling refusal
# ---------------------------------------------------------------------------

def test_rent_refuses_when_offer_dph_exceeds_ceiling(fake_account, cloud_render_modules):
    ledger, vast_client = cloud_render_modules

    with pytest.raises(vast_client.CeilingExceeded):
        vast_client.rent(1, 0.20, intent_id="intent-b", deadline_epoch=9_999_999_999,
                         ceiling_dph=0.05, image="node:22-bookworm", disk_gb=12, onstart="echo hi")

    assert fake_account.create_calls == []
    assert ledger.active() == []


# ---------------------------------------------------------------------------
# destroy() -- idempotent
# ---------------------------------------------------------------------------

def test_destroy_already_gone_instance_is_not_an_error(fake_account, cloud_render_modules):
    _, vast_client = cloud_render_modules
    vast_client.destroy(999999)  # never existed
    assert fake_account.destroy_calls == [999999]


def test_destroy_existing_instance_removes_it(fake_account, cloud_render_modules):
    _, vast_client = cloud_render_modules
    fake_account.add_remote_instance(555, label="openmontage-x-until-1")
    vast_client.destroy(555)
    assert 555 not in fake_account.instances


# ---------------------------------------------------------------------------
# reap() -- decision table
# ---------------------------------------------------------------------------

def test_reap_destroys_orphan_with_no_local_record_past_deadline(fake_account, cloud_render_modules):
    """no local / yes remote / past deadline -> destroy, ledger reaped."""
    ledger, _ = cloud_render_modules
    fake_account.add_remote_instance(111, label="openmontage-orphan1-until-1")  # epoch 1 = 1970

    report = ledger.reap(now_epoch=2_000_000_000)

    assert 111 in fake_account.destroy_calls
    assert any(d["instance_id"] == 111 for d in report.destroyed)
    assert report.warnings


def test_reap_destroys_unknown_label_orphan_by_default(fake_account, cloud_render_modules):
    """no local / yes remote / not past deadline -> destroy + warn by default
    (an openmontage-labelled instance with no local record is an orphan)."""
    ledger, _ = cloud_render_modules
    far_future = 9_999_999_999
    fake_account.add_remote_instance(222, label=f"openmontage-orphan2-until-{far_future}")

    report = ledger.reap(now_epoch=2_000_000_000)

    assert 222 in fake_account.destroy_calls
    assert any(d["instance_id"] == 222 for d in report.destroyed)


def test_reap_leaves_unknown_label_orphan_alone_when_adopt_unknown_set(
        fake_account, cloud_render_modules):
    ledger, _ = cloud_render_modules
    far_future = 9_999_999_999
    fake_account.add_remote_instance(333, label=f"openmontage-orphan3-until-{far_future}")

    report = ledger.reap(now_epoch=2_000_000_000, adopt_unknown=True)

    assert 333 not in fake_account.destroy_calls
    assert any(d["instance_id"] == 333 for d in report.left_alone)


def test_reap_closes_local_record_whose_remote_instance_is_already_gone(
        fake_account, cloud_render_modules):
    """yes local / no remote -> ledger closed (already gone), nothing to destroy."""
    ledger, _ = cloud_render_modules
    ledger.open_pending("gone1", label="openmontage-gone1-until-9999999999", dph_usd=0.08,
                        mode="bid", deadline_epoch=9_999_999_999)
    ledger.promote("gone1", instance_id=999)
    # no matching instance seeded on the fake account -> "gone"

    report = ledger.reap(now_epoch=2_000_000_000)

    assert fake_account.destroy_calls == []
    assert ledger.active() == []
    assert any(r["intent_id"] == "gone1" for r in report.closed)


def test_reap_destroys_local_record_past_deadline_with_matching_remote(
        fake_account, cloud_render_modules):
    """yes local / yes remote / past deadline -> destroy, ledger reaped, warn."""
    ledger, _ = cloud_render_modules
    ledger.open_pending("live1", label="openmontage-live1-until-1", dph_usd=0.08,
                        mode="bid", deadline_epoch=1)
    ledger.promote("live1", instance_id=888)
    fake_account.add_remote_instance(888, label="openmontage-live1-until-1")

    report = ledger.reap(now_epoch=2_000_000_000)

    assert 888 in fake_account.destroy_calls
    assert ledger.active() == []
    assert any(d["intent_id"] == "live1" for d in report.destroyed)
    assert report.warnings


def test_reap_leaves_local_record_alone_when_still_in_deadline(fake_account, cloud_render_modules):
    """yes local / yes remote / not past deadline -> leave alone."""
    ledger, _ = cloud_render_modules
    far_future = 9_999_999_999
    ledger.open_pending("live2", label=f"openmontage-live2-until-{far_future}", dph_usd=0.08,
                        mode="bid", deadline_epoch=far_future)
    ledger.promote("live2", instance_id=666)
    fake_account.add_remote_instance(666, label=f"openmontage-live2-until-{far_future}")

    report = ledger.reap(now_epoch=2_000_000_000)

    assert fake_account.destroy_calls == []
    assert len(ledger.active()) == 1
    assert ledger.active()[0]["instance_id"] == 666
    assert any(r["intent_id"] == "live2" for r in report.left_alone)


def test_reap_dry_run_destroys_nothing(fake_account, cloud_render_modules):
    ledger, _ = cloud_render_modules
    fake_account.add_remote_instance(444, label="openmontage-dryrun-until-1")

    report = ledger.reap(dry_run=True, now_epoch=2_000_000_000)

    assert fake_account.destroy_calls == []
    assert any(d["instance_id"] == 444 for d in report.destroyed)  # reported as would-destroy


def test_reap_on_clean_account_is_a_true_no_op(fake_account, cloud_render_modules):
    ledger, _ = cloud_render_modules
    report = ledger.reap(now_epoch=2_000_000_000)
    assert report.total == 0
    assert report.destroyed == []


def test_reap_ignores_non_openmontage_labelled_instances(fake_account, cloud_render_modules):
    """A human's manually-rented instance on the same account must never be
    touched -- the reaper only recognizes the openmontage- prefix."""
    ledger, _ = cloud_render_modules
    fake_account.add_remote_instance(777, label="someones-manual-box")

    report = ledger.reap(now_epoch=2_000_000_000)

    assert 777 not in fake_account.destroy_calls
    assert report.total == 0


def test_reap_leaves_fresh_pending_record_alone_with_no_remote_match(
        fake_account, cloud_render_modules):
    """A `pending` record with no matching remote instance, still younger
    than PENDING_STALE_SECONDS, is left alone -- rent() may genuinely still
    be in flight."""
    ledger, _ = cloud_render_modules
    ledger.open_pending("fresh-pending", label="openmontage-fresh-pending-until-1",
                        dph_usd=0.08, mode="bid", deadline_epoch=1)
    # created_at defaults to now; sweep at the same instant -> not stale.
    now = ledger._parse_iso_epoch(ledger.active()[0]["created_at"])

    report = ledger.reap(now_epoch=now)

    assert fake_account.destroy_calls == []
    assert len(ledger.active()) == 1
    assert ledger.active()[0]["status"] == "pending"
    assert any(r["intent_id"] == "fresh-pending" for r in report.left_alone)


def test_reap_closes_stale_pending_record_with_no_remote_match(fake_account, cloud_render_modules):
    """A `pending` record that never got promote()'d and has no matching
    remote instance, older than PENDING_STALE_SECONDS, must not sit in
    active.json forever -- `rent()` failed before create_instance ever
    returned (or its response was genuinely lost with no instance created)."""
    ledger, _ = cloud_render_modules
    ledger.open_pending("stale-pending", label="openmontage-stale-pending-until-1",
                        dph_usd=0.08, mode="bid", deadline_epoch=1)
    created_epoch = ledger._parse_iso_epoch(ledger.active()[0]["created_at"])

    report = ledger.reap(now_epoch=created_epoch + ledger.PENDING_STALE_SECONDS + 1)

    assert fake_account.destroy_calls == []  # nothing to destroy -- no remote instance exists
    assert ledger.active() == []             # but the local zombie row must be gone
    assert any(r["intent_id"] == "stale-pending" for r in report.closed)
    assert report.warnings


def test_reap_treats_pending_record_with_live_remote_as_active(fake_account, cloud_render_modules):
    """The exact "create succeeded, response lost" case `open_pending`'s
    docstring exists for: promote() never ran, but the account really did
    create the instance. The sweep must not ignore it just because the
    local status is still `pending`."""
    ledger, _ = cloud_render_modules
    far_future = 9_999_999_999
    ledger.open_pending("lost-promote", label=f"openmontage-lost-promote-until-{far_future}",
                        dph_usd=0.08, mode="bid", deadline_epoch=far_future)
    fake_account.add_remote_instance(
        321, label=f"openmontage-lost-promote-until-{far_future}")

    report = ledger.reap(now_epoch=2_000_000_000)

    assert fake_account.destroy_calls == []  # not past deadline -> left alone, not destroyed
    assert len(ledger.active()) == 1
    assert ledger.active()[0]["status"] == "pending"
    assert any(r["intent_id"] == "lost-promote" for r in report.left_alone)
    assert report.warnings  # flags the promote()-likely-failed condition


def test_reap_destroys_pending_record_with_live_remote_past_deadline(
        fake_account, cloud_render_modules):
    """Same lost-promote case, but past its deadline -> must still be
    destroyed even though the local status never reached `active`."""
    ledger, _ = cloud_render_modules
    ledger.open_pending("lost-promote-late", label="openmontage-lost-promote-late-until-1",
                        dph_usd=0.08, mode="bid", deadline_epoch=1)
    fake_account.add_remote_instance(654, label="openmontage-lost-promote-late-until-1")

    report = ledger.reap(now_epoch=2_000_000_000)

    assert 654 in fake_account.destroy_calls
    assert ledger.active() == []
    assert any(d["intent_id"] == "lost-promote-late" for d in report.destroyed)


# ---------------------------------------------------------------------------
# Atomic write survival
# ---------------------------------------------------------------------------

def test_active_json_survives_a_crash_before_os_replace(fake_account, cloud_render_modules,
                                                         monkeypatch):
    """A crash between the temp-file write and os.replace must not lose the
    instance id of a live rental already committed to active.json."""
    ledger, _ = cloud_render_modules

    ledger.open_pending("safe", label="openmontage-safe-until-9999999999", dph_usd=0.08,
                        mode="bid", deadline_epoch=9_999_999_999)
    ledger.promote("safe", instance_id=123456)
    committed = ledger.ACTIVE_PATH.read_text(encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(ledger.os, "replace", _boom)
    with pytest.raises(OSError):
        ledger.open_pending("unsafe", label="openmontage-unsafe-until-1", dph_usd=0.08,
                            mode="bid", deadline_epoch=1)

    assert ledger.ACTIVE_PATH.read_text(encoding="utf-8") == committed
    assert ledger.active()[0]["instance_id"] == 123456
