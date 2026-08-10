"""Opt-in live Vast.ai smoke suite -- rents a REAL instance and spends real
money (~$0.01-0.05 total across the 3 tests below; see `tests/live/README.md`
for the breakdown and how to run this).

NEVER executed automatically. Three independent gates, all required:

1. `pytest.ini`'s `addopts = -m "not live"` deselects every `@pytest.mark.live`
   test by default -- `pytest tests/` / `make test` never even collect-run
   these bodies.
2. `tests/test_cloud_render_live_gate.py` (in the default suite, so it runs
   in CI) fails loudly if `VAST_LIVE_TEST` is ever set while `CI` is set.
3. Even given `-m live` explicitly, every test below is further gated on
   BOTH `VAST_LIVE_TEST=1` and `VAST_LIVE_MAX_USD` being set, and re-checks
   its own pre-flight cost estimate against that ceiling before renting
   anything.

Run deliberately, after reading the README:

    VAST_LIVE_TEST=1 VAST_LIVE_MAX_USD=0.05 make cloud-render-live-test

Uses the REAL `vast_client` / `remote` / `ledger` / `config` modules --
no monkeypatching, no fakes. That is the entire point of this suite: prove
the mocked default suite's assumptions still hold against the real Vast.ai
API and a real Linux Remotion render. `tests/test_cloud_render_fidelity.py`
already proves the *local* fake-remote version of the fidelity check; the
third test here proves the same claim against hardware the fake cannot
reach.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lib.cloud_render import config as cloud_config
from lib.cloud_render import ledger, remote, vast_client
from lib.talking_head_edit.job_store import Job
from lib.talking_head_edit.stages import render as render_stage
from tests.test_cloud_render_fidelity import _ffprobe_video_facts, _measure_psnr

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "plans" / "260806-1404-vastai-cloud-render" / "reports"

# The productized kit/remote path has no `--frames` override (see
# `lib/talking_head_edit/stages/render.py::build_remotion_command`) -- unlike
# the plan's manual ground-truth run, this smoke job's short render time
# comes from a short *synthetic* clip duration instead of a frame slice.
LIVE_CONFIG_OVERRIDE = {
    "enabled": True,            # global config ships disabled -- force on for this suite only
    "max_dph_usd": 0.15,
    "max_total_usd_per_rental": 0.50,
    "max_runtime_minutes": 15,  # well under config's 60min default; a 2s smoke needs minutes, not an hour
}

_missing_gate_reason = (
    "set VAST_LIVE_TEST=1 and VAST_LIVE_MAX_USD to run the live cloud-render suite "
    "-- see tests/live/README.md (this suite rents a real instance and spends real money)")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("VAST_LIVE_TEST") == "1" and os.environ.get("VAST_LIVE_MAX_USD")),
        reason=_missing_gate_reason),
]


def _max_usd_ceiling() -> float:
    raw = os.environ.get("VAST_LIVE_MAX_USD", "0")
    try:
        value = float(raw)
    except ValueError:
        pytest.skip(f"VAST_LIVE_MAX_USD={raw!r} is not a number")
    if value <= 0:
        pytest.skip("VAST_LIVE_MAX_USD must be > 0")
    return value


def _resolved_live_config() -> dict:
    return cloud_config.resolve(job=LIVE_CONFIG_OVERRIDE)


def _sweep_openmontage_instances() -> list[dict]:
    """Every `openmontage-*`-labelled instance still on the account,
    regardless of local ledger state -- the safety-layer-3 check this whole
    plan exists to prove holds even with zero local state."""
    return vast_client.list_labelled_instances()


def _record_live_measurement(test_name: str, **fields) -> None:
    """Append this run's real numbers to the phase's report dir, per the
    phase file's requirement that `render_seconds_per_video_second` (and
    boot/`npm ci` timings) get recalibrated from real data, not guesses."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = REPORTS_DIR / "live-smoke-measurements.jsonl"
    entry = {"test": test_name, "recorded_at": time.time(), **fields}
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


@pytest.fixture(autouse=True)
def _sweep_before_and_after_every_live_test():
    """Belt-and-suspenders per the phase's risk register ("live test itself
    leaks an instance when it fails early"): sweep before (in case a
    previous run leaked) and after (in `finally`, so a test failure still
    triggers the sweep) every test in this module."""
    ledger.reap()
    try:
        yield
    finally:
        ledger.reap()
        leaked = _sweep_openmontage_instances()
        assert not leaked, (
            f"openmontage-*-labelled instance(s) still on the account after this test: "
            f"{[i.get('id') for i in leaked]} -- destroy manually via "
            f"`python -m lib.cloud_render.reap`; this is exactly the failure mode this "
            f"whole plan exists to prevent")


def _build_synthetic_smoke_job(tmp_path: Path, job_id: str) -> Job:
    """A tiny, fully synthetic talking-head job: 2s of a solid-color clip
    generated locally with ffmpeg -- never real footage from `projects/`
    (see `tests/live/README.md`). Produces exactly the files
    `remote.render_now`/`kit.build_job_kit` read, the same way a real
    `lib.talking_head_edit` job would have them on disk by the time its
    local `render` stage runs."""
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    job = Job(job_id, job_dir)

    duration_seconds = 2.0
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=1080x1920:d={duration_seconds}",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", str(duration_seconds), "-shortest", "-c:v", "libx264", "-c:a", "aac",
         str(job.src_path)],
        check=True, capture_output=True, timeout=60)

    job.save({
        "job_id": job_id, "current_version": 1, "stages": {},
        "options": {"render_crf": 23, "render_jpeg_quality": 80},
    })
    props = {
        "videoSrc": job.src_path.name, "events": [], "keywords": [],
        "brandPill": "LIVE SMOKE TEST", "durationSeconds": duration_seconds,
    }
    job.props_path(1).write_text(json.dumps(props), encoding="utf-8")
    render_stage.stage_assets(job, props)
    return job


class TestLiveSmokeHappyPath:
    """Test 1: search -> rent cheapest under ceiling -> render -> pull ->
    destroy. `finally` (via the autouse fixture above) asserts zero
    `openmontage-*` instances remain."""

    def test_render_now_produces_a_playable_file_and_leaves_zero_instances(self, tmp_path):
        from tools.video.vast_cloud_render import VastCloudRender

        ceiling_usd = _max_usd_ceiling()
        config_resolved = _resolved_live_config()
        estimate_usd = VastCloudRender().estimate_cost({"mode": "render_now"})
        assert estimate_usd < ceiling_usd, (
            f"pre-flight estimate ${estimate_usd} already >= VAST_LIVE_MAX_USD=${ceiling_usd} "
            f"-- refusing to rent")

        job = _build_synthetic_smoke_job(tmp_path, "live-smoke-happy")
        start = time.monotonic()
        result = remote.render_now(job, 1, config=config_resolved)
        wall_seconds = round(time.monotonic() - start, 1)

        assert job.final_path.exists()
        assert job.final_path.stat().st_size > 0
        facts = _ffprobe_video_facts(job.final_path)
        assert facts, "downloaded output has no readable video stream"
        assert result.actual_usd <= ceiling_usd

        _record_live_measurement(
            "happy_path", wall_seconds=wall_seconds, actual_usd=result.actual_usd,
            instance_id=result.instance_id, offer_id=result.offer_id,
            output_size_bytes=job.final_path.stat().st_size)


class TestLiveSmokeCrashPath:
    """Test 2: rent, start the render, hard-kill the process running it
    mid-flight, then in a FRESH process (`python -m lib.cloud_render.reap`,
    sharing zero in-memory state with the killed one) prove the instance
    still gets destroyed. This is the only way to prove safety layers 2-3
    end to end -- layer 1 (the in-process `try/finally`) is already proven
    by the happy-path test above."""

    def test_hard_kill_mid_render_still_gets_reaped_by_a_fresh_process(self, tmp_path):
        ceiling_usd = _max_usd_ceiling()
        job = _build_synthetic_smoke_job(tmp_path, "live-smoke-crash")
        config_resolved = _resolved_live_config()

        handoff_path = tmp_path / "handoff.json"
        handoff_path.write_text(json.dumps({
            "job_id": job.job_id, "job_dir": str(job.dir), "config": config_resolved,
        }), encoding="utf-8")

        runner_code = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from lib.cloud_render import remote\n"
            "from lib.talking_head_edit.job_store import Job\n"
            "handoff = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
            "job = Job(handoff['job_id'], Path(handoff['job_dir']))\n"
            "remote.render_now(job, 1, config=handoff['config'])\n"
        )
        start = time.monotonic()
        child = subprocess.Popen(
            [sys.executable, "-c", runner_code, str(handoff_path)], cwd=str(REPO_ROOT))

        # Long enough that create_instance + boot + upload have definitely
        # happened (rental exists, money is already being spent), short
        # enough the render itself has not finished. Tune against the
        # measured numbers in live-smoke-measurements.jsonl once this suite
        # has actually been run -- 45s is a first estimate, not a
        # calibrated value.
        time.sleep(45.0)
        killed_at = round(time.monotonic() - start, 1)
        child.kill()
        child.wait(timeout=30.0)

        reap_result = subprocess.run(
            [sys.executable, "-m", "lib.cloud_render.reap"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180)

        assert reap_result.returncode == 1, (
            f"expected the reaper to find and destroy the killed rental (exit 1); "
            f"got {reap_result.returncode}. stdout:\n{reap_result.stdout}")
        assert not _sweep_openmontage_instances()

        _record_live_measurement(
            "crash_path", killed_at_seconds=killed_at, reap_stdout=reap_result.stdout.strip())


class TestLiveSmokeFidelity:
    """Test 3: render the same props locally and on the rented instance,
    compare with the PSNR gate. Slowest and most expensive of the three --
    per the phase file, may be run manually rather than as a routine part of
    `-m live`."""

    def test_cloud_render_matches_local_render_of_the_same_props(self, tmp_path):
        ceiling_usd = _max_usd_ceiling()
        job = _build_synthetic_smoke_job(tmp_path, "live-smoke-fidelity")
        config_resolved = _resolved_live_config()

        cloud_result = remote.render_now(job, 1, config=config_resolved)
        cloud_output = job.final_path

        job_options = job.load().get("options") or {}
        local_output = tmp_path / "local_reference.mp4"
        local_command = render_stage.build_remotion_command(
            entry="src/index.tsx", composition_id=render_stage.COMPOSITION_ID,
            out_path=local_output, props_path=job.props_path(1),
            public_dir=job.render_public_dir, workers=4,
            crf=int(job_options.get("render_crf", 17)),
            jpeg_quality=int(job_options.get("render_jpeg_quality", 100)), scale=1.0)
        local_proc = subprocess.run(
            local_command, cwd=str(render_stage.COMPOSER_DIR), capture_output=True,
            text=True, shell=(sys.platform == "win32"), timeout=180)
        assert local_proc.returncode == 0, local_proc.stdout[-2000:]
        assert local_output.exists()

        cloud_facts = _ffprobe_video_facts(cloud_output)
        local_facts = _ffprobe_video_facts(local_output)
        assert cloud_facts == local_facts, (
            f"resolution/fps/codec drifted: cloud={cloud_facts} local={local_facts}")

        psnr_db = _measure_psnr(cloud_output, local_output)
        assert psnr_db >= 40.0, f"PSNR {psnr_db} dB below the 40 dB fidelity gate"
        assert cloud_result.actual_usd <= ceiling_usd

        _record_live_measurement(
            "fidelity", psnr_db=psnr_db, actual_usd=cloud_result.actual_usd,
            cloud_facts=cloud_facts)
