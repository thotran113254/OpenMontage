"""Fake-remote fidelity test -- the highest-value test in the cloud-render
suite per the phase's key insight 4: point the render step at a *local*
executor instead of a rented instance and diff the output against a direct
local render of the same props. This is the one test that catches flag
drift between the cloud and local render paths without renting anything.

Real render, real ffmpeg/ffprobe, zero network and zero Vast.ai spend: this
test drives the actual `lib.cloud_render.remote.render_batch()` code path
(the same function `render_now`/`flush` call), with `transfer.push_kit` /
`transfer.fetch_to_remote` / `transfer.push_from_remote` / the R2 presign
calls / `remote._stream_command` monkeypatched to a *local* executor that
runs the exact command `render_batch` built -- against a real, local
`remotion-composer/` checkout, not a rented one. If flag drift is ever
introduced between the cloud and local render paths, this test's PSNR/
duration/resolution/fps/codec diff catches it; the argv-identity guard in
`tests/test_talking_head_render.py::TestBuildRemotionCommandParity` (landed
with the phase 02 refactor) proves the underlying builder is deterministic,
but only THIS test proves the *cloud plumbing around it* -- ssh command
stringification, remote path substitution -- does not silently mangle it.

Uses the `EndTag` composition (no video/audio props needed) so the test
does not depend on any project footage, and needs no fake video fixture.

Skips outright if `remotion-composer/node_modules` or `ffmpeg` are not
present -- this is the one test in the default suite that is not disabled
by choice but simply cannot prove anything real without them. CI's Python
job never installs the Node toolchain (`.github/workflows/ci.yml`'s
`typecheck-ui` job is separate and Python-less), so this test always skips
in CI; a developer with `remotion-composer/` set up gets the real check
locally.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.cloud_render import kit, remote, transfer
from lib.r2_storage import config as r2_config
from lib.talking_head_edit.resolve_media import probe_duration
from lib.talking_head_edit.stages import render

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
NODE_MODULES_PRESENT = (render.COMPOSER_DIR / "node_modules").exists()

pytestmark = pytest.mark.skipif(
    not (NODE_MODULES_PRESENT and FFMPEG and FFPROBE),
    reason=(
        "remotion-composer/node_modules and/or ffmpeg/ffprobe not available -- "
        "run `cd remotion-composer && npm install` and install ffmpeg to get real "
        "fidelity coverage locally; this always skips in CI (no Node toolchain there)"))

_SHELL_COMMAND_RE = re.compile(
    rf"^cd {re.escape(remote.REMOTE_KIT_DIR)} && timeout \d+s (?P<cmd>.+)$")


def _ffprobe_video_facts(path: Path) -> dict[str, str]:
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,codec_name",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    facts: dict[str, str] = {}
    for line in proc.stdout.strip().splitlines():
        key, _, value = line.partition("=")
        facts[key] = value
    return facts


def _measure_psnr(path_a: Path, path_b: Path) -> float:
    proc = subprocess.run(
        [FFMPEG, "-y", "-i", str(path_a), "-i", str(path_b), "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60)
    match = re.search(r"average:(\S+)", proc.stderr)
    assert match, f"no PSNR average found in ffmpeg output:\n{proc.stderr[-2000:]}"
    return float(match.group(1))  # "inf" parses fine as float("inf")


@pytest.fixture()
def fidelity_job_root(tmp_path):
    """A job dir that plays double duty as both the local kit source AND the
    fake "remote" job dir -- since the fake executor runs everything on this
    same machine, there is nothing to actually transfer, only paths to
    translate. `out/` pre-exists because the real remote box's onstart
    script would have created `/root/kit` already; nothing here relies on
    Remotion auto-creating parent dirs."""
    job_root = tmp_path / f"fidelity-{uuid.uuid4().hex[:8]}"
    (job_root / "public").mkdir(parents=True)
    (job_root / "out").mkdir()
    (job_root / "props.json").write_text("{}", encoding="utf-8")
    return job_root


def _install_local_remote_fake(monkeypatch, *, job_id: str, job_root: Path) -> None:
    """Monkeypatch the transport seam `render_batch`/`_render_one_item` use
    so the exact ssh-embedded command `remote.py` builds actually runs, on
    this machine, in `remotion-composer/`, instead of over ssh to a rented
    box. `transfer.push_kit`/`fetch_to_remote`/`push_from_remote` and the R2
    presign calls become no-ops/local-copies because the fake "remote" job
    dir IS `job_root` -- there is nothing to actually move through R2."""
    remote_job_prefix = f"{remote.REMOTE_JOBS_DIR}/{job_id}"

    fake_r2_settings = SimpleNamespace(bucket="test-bucket", prefix="projects",
                                       endpoint_url="https://fake.example")
    monkeypatch.setattr(r2_config, "is_configured", lambda: (True, []))
    monkeypatch.setattr(r2_config, "resolve", lambda *a, **k: fake_r2_settings)

    monkeypatch.setattr(transfer, "ssh", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(transfer, "push_kit", lambda *a, **k: ("key", 10, False))
    monkeypatch.setattr(transfer, "fetch_to_remote", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="", stderr=""))

    def fake_stream_command(args: list[str], *, timeout_s: float, on_line) -> int:
        shell_command = args[-1]
        match = _SHELL_COMMAND_RE.match(shell_command)
        assert match, f"unexpected shell command shape: {shell_command!r}"
        tokens = match.group("cmd").split(" ")
        translated = [tok.replace(remote_job_prefix, str(job_root)) for tok in tokens]
        proc = subprocess.run(
            translated, cwd=str(render.COMPOSER_DIR), capture_output=True,
            text=True, shell=(sys.platform == "win32"), timeout=timeout_s)
        for line in (proc.stdout or "").splitlines():
            on_line(line + "\n")
        return proc.returncode

    monkeypatch.setattr(remote, "_stream_command", fake_stream_command)
    monkeypatch.setattr(transfer, "push_from_remote", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="", stderr=""))

    def fake_download(key, local_path, *, settings=None):
        source = job_root / "out" / "final.mp4"
        shutil.copyfile(source, local_path)

    monkeypatch.setattr(remote.r2_presign, "download", fake_download)
    monkeypatch.setattr(remote.r2_presign, "copy_object", lambda *a, **k: None)
    monkeypatch.setattr(remote.r2_presign, "delete_object", lambda *a, **k: None)


class TestFakeRemoteFidelity:
    def test_cloud_path_output_matches_direct_local_render_of_the_same_props(
            self, fidelity_job_root, tmp_path, monkeypatch):
        job_id = fidelity_job_root.name
        flags = {"crf": 30, "jpeg_quality": 70, "scale": 1.0}

        job_kit_manifest = kit.JobKitManifest(
            job_id=job_id, kit_dir=fidelity_job_root, kit_hash="fidelity-test",
            size_bytes=2, file_count=1, composition_id="EndTag", props_path="props.json",
            public_dir="public", expected_output_name="out/final.mp4",
            estimated_render_seconds=6.0)
        item = remote.BatchItem(job_id=job_id, kit=job_kit_manifest, flags=flags, timeout_s=90.0)

        _install_local_remote_fake(monkeypatch, job_id=job_id, job_root=fidelity_job_root)

        rental = SimpleNamespace(id=1, ssh_host="127.0.0.1", ssh_port=22)
        composer_manifest = SimpleNamespace(kit_dir=render.COMPOSER_DIR, kit_hash="fidelity-composer")

        results = remote.render_batch(
            rental, composer_manifest, [item], key_path="/fake/key", max_concurrency=1,
            ready_timeout_s=1.0)

        assert results[0].status == "done", results[0].error
        cloud_output = results[0].result.local_output_path
        assert cloud_output.exists()

        # -- Direct local render of the exact same props, via the exact same
        # build_remotion_command() the cloud path used -- only the output
        # path differs.
        local_output = tmp_path / "direct_local.mp4"
        local_command = render.build_remotion_command(
            entry="src/index.tsx", composition_id="EndTag", out_path=local_output,
            props_path=fidelity_job_root / "props.json",
            public_dir=fidelity_job_root / "public", workers=1,
            crf=flags["crf"], jpeg_quality=flags["jpeg_quality"], scale=flags["scale"])
        local_proc = subprocess.run(
            local_command, cwd=str(render.COMPOSER_DIR), capture_output=True,
            text=True, shell=(sys.platform == "win32"), timeout=90.0)
        assert local_proc.returncode == 0, local_proc.stdout[-2000:]
        assert local_output.exists()

        # -- Compare: same duration/resolution/fps/codec, PSNR gate.
        cloud_facts = _ffprobe_video_facts(cloud_output)
        local_facts = _ffprobe_video_facts(local_output)
        assert cloud_facts == local_facts, (
            f"resolution/fps/codec drifted between cloud and local render paths: "
            f"cloud={cloud_facts} local={local_facts}")

        cloud_duration = probe_duration(cloud_output)
        local_duration = probe_duration(local_output)
        assert abs(cloud_duration - local_duration) < 0.05, (
            f"duration drifted: cloud={cloud_duration}s local={local_duration}s")

        psnr_db = _measure_psnr(cloud_output, local_output)
        # Identical argv run twice on the same machine on the same input
        # should decode bit-identical (ffmpeg reports "inf") -- the phase's
        # own >= 40 dB gate is the floor for "no perceptible difference",
        # kept as a real assertion (not just a >= 0 sanity check) so a
        # genuine future divergence still fails loudly.
        assert psnr_db >= 40.0, f"PSNR {psnr_db} dB below the 40 dB fidelity gate"
