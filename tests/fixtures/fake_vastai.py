"""Shared fake `vastai` SDK module for the cloud-render test suite.

Phases 01-04 each rolled their own narrower inline fake (`FakeVastAccount` /
`fake_account` in `tests/test_cloud_render_ledger_reaper.py`, `FakeOffer` /
`FakeRental` in `tests/test_cloud_render_remote.py`, etc.) before this file
existed. Those working, passing test files are left alone -- rewriting a
green test file just to share a fixture is not worth the regression risk.
New cloud-render tests should use this one instead of adding a fifth inline
fake.

Why a fake *module* injected into `sys.modules["vastai"]`, not a patched
method: patching `VastAI.create_instance` still leaves a real `VastAI()`
instance around that reads the real API key and could reach the network
from any call site nobody thought to patch. Injecting a fake module makes
that impossible by construction -- there is no real SDK object anywhere in
the process for the duration of the test.

`FakeVastAI` is a *recording double*: every call appends to `.calls` before
doing anything else (including before raising an injected failure), because
every safety assertion in this plan is shaped "how many times was X called"
or "was destroy called exactly once" -- a stub that silently no-ops would
make those assertions untestable.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

FAKE_INSTANCE_ID_START = 40_000_000


class FakeVastAIError(Exception):
    """Stands in for whatever the real SDK/`requests` layer would raise
    (a bare `Exception` with an optional `.response.status_code`, matching
    the shape `lib.cloud_render.vast_client.destroy()` inspects)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.response = types.SimpleNamespace(status_code=status_code) if status_code else None


@dataclass
class FakeVastAI:
    """Recording double for `vastai.VastAI`.

    `fail_on=(method_name, n)` makes the n-th call (1-indexed, counted per
    method) to that method raise `FakeVastAIError` instead of running
    normally -- the mechanism every "destroy still happens after X fails"
    test in this suite needs.

    Status progression: a freshly created instance starts at
    `"loading"` and flips to `"running"` once `show_instance()` has been
    called `poll_until_running` times for it (default `1`, i.e. running on
    the very first poll -- most tests do not care about the boot delay and
    should not have to script it). Pass `poll_until_running=` to
    `create_instance()` (accepted via `**kwargs`, never forwarded to the
    real SDK by `lib.cloud_render.vast_client`) to script a longer boot.
    """

    api_key: str | None = None
    fail_on: tuple[str, int] | None = None
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._instances: dict[int, dict[str, Any]] = {}
        self._poll_until_running: dict[int, int] = {}
        self._poll_counts: dict[int, int] = {}
        self._next_id = FAKE_INSTANCE_ID_START
        self._method_call_counts: dict[str, int] = {}
        self._offers: list[dict[str, Any]] = []

    # ---- recording + failure injection -------------------------------------

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        self._method_call_counts[method] = self._method_call_counts.get(method, 0) + 1
        if self.fail_on and self.fail_on[0] == method \
                and self._method_call_counts[method] == self.fail_on[1]:
            raise FakeVastAIError(
                f"injected failure: {method} call #{self.fail_on[1]}")

    # ---- test setup helpers (not part of the real SDK) ---------------------

    def seed_offers(self, offers: list[dict[str, Any]]) -> None:
        """Offers `search_offers()` returns verbatim (already-normalized
        dicts, same shape `lib.cloud_render.vast_client._offer_from_dict`
        reads)."""
        self._offers = offers

    def seed_instance(self, instance_id: int, **fields: Any) -> None:
        """Simulate an instance that exists on the account without going
        through `create_instance` -- an orphan the reaper must find, or a
        retry's already-rented instance."""
        self._instances[instance_id] = {
            "id": instance_id, "label": None, "actual_status": "running",
            "ssh_host": "5.6.7.8", "ssh_port": 22, "dph_total": 0.08,
            **fields,
        }

    # ---- the fake SDK surface ----------------------------------------------

    def search_offers(self, query: str, type: str = "on-demand",  # noqa: A002 - matches SDK param name
                      order: str = "score-", limit: int | None = None) -> list[dict[str, Any]]:
        self._record("search_offers", query=query, type=type, order=order, limit=limit)
        return list(self._offers)

    def create_instance(self, id: int, image: str | None = None, disk: float = 10,  # noqa: A002
                        poll_until_running: int = 1, **kwargs: Any) -> dict[str, Any]:
        self._record("create_instance", id=id, image=image, disk=disk, **kwargs)
        new_id = self._next_id
        self._next_id += 1
        self._instances[new_id] = {
            "id": new_id, "label": kwargs.get("label"), "actual_status": "loading",
            "ssh_host": "1.2.3.4", "ssh_port": 22, "dph_total": kwargs.get("price") or 0.08,
        }
        self._poll_until_running[new_id] = max(1, poll_until_running)
        self._poll_counts[new_id] = 0
        return {"success": True, "new_contract": new_id}

    def show_instance(self, id: int) -> dict[str, Any]:  # noqa: A002
        self._record("show_instance", id=id)
        instance = self._instances[id]
        self._poll_counts[id] = self._poll_counts.get(id, 0) + 1
        threshold = self._poll_until_running.get(id, 1)
        instance["actual_status"] = (
            "running" if self._poll_counts[id] >= threshold else "loading")
        return dict(instance)

    def show_instances(self) -> list[dict[str, Any]]:
        self._record("show_instances")
        return [dict(v) for v in self._instances.values()]

    def destroy_instance(self, id: int) -> dict[str, Any]:  # noqa: A002
        self._record("destroy_instance", id=id)
        if id not in self._instances:
            raise FakeVastAIError(f"instance {id} not found", status_code=404)
        del self._instances[id]
        self._poll_until_running.pop(id, None)
        self._poll_counts.pop(id, None)
        return {"success": True}

    def label_instance(self, id: int, label: str) -> dict[str, Any]:  # noqa: A002
        self._record("label_instance", id=id, label=label)
        if id in self._instances:
            self._instances[id]["label"] = label
        return {"success": True}


def install(monkeypatch: Any, *, fail_on: tuple[str, int] | None = None) -> FakeVastAI:
    """Install a fresh `FakeVastAI` as `sys.modules["vastai"]` for the
    duration of one test via `monkeypatch` (auto-reverted at teardown).

    Prefer the `fake_vastai` fixture in `tests/conftest.py` for the normal
    case -- call this directly only when a test needs a second,
    independently-configured fake account within the same test."""
    account = FakeVastAI(fail_on=fail_on)
    module = types.ModuleType("vastai")

    class _VastAIProxy:
        def __init__(self, api_key: str | None = None, **_kwargs: Any) -> None:
            account.api_key = api_key

        def __getattr__(self, name: str) -> Any:
            return getattr(account, name)

    module.VastAI = _VastAIProxy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vastai", module)
    return account
