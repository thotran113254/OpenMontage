"""Tests for lib/cloud_render/onstart.py -- pure string builder, no I/O."""

from __future__ import annotations

from lib.cloud_render.onstart import READY_MARKER, build

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIfakefakefake user@host"
PACKAGES = ["ffmpeg", "ca-certificates"]


def test_no_auto_tmux_comes_before_apt_get():
    """The default images hijack ssh into tmux and drop a non-interactive
    command unless .no_auto_tmux exists before the first real command."""
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    tmux_index = script.index("no_auto_tmux")
    apt_index = script.index("apt-get")
    assert tmux_index < apt_index


def test_pubkey_is_single_quoted():
    """A key with a comment containing spaces must not split the shell word."""
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    assert f"'{PUBKEY}'" in script


def test_apt_install_is_not_guarded_with_or_true():
    """A failed apt install must be visible -- every other step is || true,
    this one is not."""
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    apt_line = next(line for line in script.splitlines() if "apt-get install" in line)
    assert "|| true" not in apt_line


def test_all_other_steps_are_guarded_with_or_true():
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    for line in script.splitlines():
        if "apt-get install" in line or line.startswith("#") or "nohup" in line:
            continue
        if line.strip():
            assert "|| true" in line, f"line missing || true guard: {line!r}"


def test_ready_marker_written_only_after_apt_success():
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    apt_line = next(line for line in script.splitlines() if "apt-get install" in line)
    assert f"&& touch {READY_MARKER}" in apt_line


def test_packages_are_joined_into_apt_command():
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    assert "apt-get install -y ffmpeg ca-certificates" in script


def test_sleep_seconds_computed_from_deadline_minus_now():
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_800_003_600, now_epoch=1_800_000_000)
    assert "sleep 3600;" in script


def test_sleep_clamped_to_zero_when_deadline_already_passed():
    """A deadline in the past (e.g. clock drift, slow onstart start) must not
    produce a negative sleep -- the watchdog fires immediately instead."""
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_000, now_epoch=2_000)
    assert "sleep 0;" in script


def test_shutdown_command_present():
    script = build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    assert "shutdown -h now" in script


def test_build_is_pure_no_io_side_effects(tmp_path, monkeypatch):
    """No filesystem or network access -- calling build() must not create
    any file. Guard against a future regression that adds I/O."""
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    build(PUBKEY, PACKAGES, deadline_epoch=1_900_000_000, now_epoch=1_800_000_000)
    after = set(tmp_path.iterdir())
    assert before == after
