"""Autopilot limits, remedy lookup, and chat revise with history.

Everything here is about what autopilot REFUSES to do. The failure mode being
prevented is a retry loop that burns renders on guesses, so the tests are mostly
"it stopped, and it said why".
"""

from __future__ import annotations

import json

import pytest

from lib.talking_head_edit import autopilot as autopilot_mod
from lib.talking_head_edit import chat_revise, remedies
from lib.talking_head_edit.job_store import JobStore
from lib.talking_head_edit.stages import verify as verify_stage


def issue(code, message="lỗi"):
    return {"code": code, "message": message}


@pytest.fixture
def job(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"0")
    job = JobStore(tmp_path / "jobs").create(clip, {})
    job.update(current_version=1)
    return job


def write_verify(job, issues, version=1):
    (job.dir / f"verify_report_v{version}.json").write_text(
        json.dumps({"issues": issues, "passed": not issues}, ensure_ascii=False),
        encoding="utf-8")


class TestRemedyTable:
    def test_known_codes_have_a_note_and_stages(self):
        for code, entry in remedies.REMEDY.items():
            assert entry["note"], f"{code} thiếu note giải thích vì sao retry"
            assert entry["stages"], f"{code} thiếu danh sách stage"

    def test_stutter_forces_half_concurrency(self):
        assert remedies.remedy_for("stutter")["options"] == {
            "render_concurrency": "half"}

    def test_an_unknown_code_has_no_remedy(self):
        assert remedies.remedy_for("khong_co_that") is None

    def test_plan_unions_the_stages_in_pipeline_order(self):
        decision = remedies.plan([issue("stutter"), issue("bgm_missing")])
        assert decision["stages"] == ["resolve", "render", "verify"]
        assert decision["options"] == {"render_concurrency": "half"}
        assert decision["actionable"] is True

    def test_plan_refuses_to_act_when_anything_is_unhandled(self):
        """Fixing two of three problems and declaring success is worse than
        reporting all three."""
        decision = remedies.plan([issue("stutter"), issue("mau_bi_chay")])
        assert decision["unhandled"] == ["mau_bi_chay"]
        assert decision["actionable"] is False

    def test_unmeasurable_findings_are_not_failures(self):
        decision = remedies.plan([issue("unmeasured"), issue("seam_suspect")])
        assert decision["stages"] == []
        assert decision["unhandled"] == []
        assert set(decision["ignored"]) == {"unmeasured", "seam_suspect"}

    def test_every_verify_code_is_either_remedied_or_ignored_on_purpose(self):
        """A new code with neither a remedy nor an explicit ignore would silently
        stop every autopilot run — so the choice has to be deliberate."""
        codes = {value for name, value in vars(verify_stage).items()
                 if name.startswith("CODE_")}
        undecided = (codes - set(remedies.REMEDY) - remedies.IGNORED_CODES
                     - set(remedies.NEEDS_HUMAN))
        assert undecided == set(), f"code chưa quyết: {undecided}"

    def test_a_needs_human_code_stops_and_explains_why(self):
        decision = remedies.plan([issue("sfx_missing")])
        assert decision["unhandled"] == ["sfx_missing"]
        assert decision["actionable"] is False
        assert any("thêm file" in note for note in decision["notes"]), \
            "phải nói người dùng cần làm gì, không chỉ nói 'không sửa được'"

    def test_plan_of_nothing_is_not_actionable(self):
        assert remedies.plan([])["actionable"] is False


class TestAutopilotLimits:
    def _runner(self, job, issues_per_pass):
        """A stubbed run_job that writes a scripted verify report each pass."""
        calls: list[dict] = []
        sequence = iter(issues_per_pass)

        def run(target, options=None, stages=None, use_cache=True):
            calls.append({"options": options, "stages": stages,
                          "use_cache": use_cache})
            write_verify(target, next(sequence, []))
            target.emit("stage_end", "render", "render xong")
            return {}

        return run, calls

    def test_a_clean_first_pass_never_retries(self, job):
        run, calls = self._runner(job, [[]])
        report = autopilot_mod.run(job, runner=run)
        assert report["retries"] == 0
        assert report["resolved"] is True
        assert len(calls) == 1

    def test_a_known_failure_is_retried_once_and_only_once(self, job):
        run, calls = self._runner(job, [[issue("stutter")], [issue("stutter")]])
        report = autopilot_mod.run(job, runner=run)
        assert report["retries"] == 1, "trần cứng 1 lần, dù lần 2 vẫn fail"
        assert len(calls) == 2
        assert report["resolved"] is False
        assert report["remaining_issues"][0]["code"] == "stutter"

    def test_the_retry_only_reruns_the_affected_stages(self, job):
        run, calls = self._runner(job, [[issue("stutter")], []])
        autopilot_mod.run(job, runner=run)
        assert calls[1]["stages"] == ["render", "verify"], \
            "không được chạy lại từ đầu — sẽ phá thứ đang đúng"

    def test_the_retry_turns_the_cache_off(self, job):
        """The cached result of the retried stage is exactly the bad output."""
        run, calls = self._runner(job, [[issue("stutter")], []])
        autopilot_mod.run(job, runner=run)
        assert calls[1]["use_cache"] is False

    def test_the_remedy_options_reach_the_rerun(self, job):
        run, calls = self._runner(job, [[issue("stutter")], []])
        autopilot_mod.run(job, runner=run)
        assert calls[1]["options"] == {"render_concurrency": "half"}

    def test_an_unknown_failure_stops_without_retrying(self, job):
        run, calls = self._runner(job, [[issue("mau_bi_chay", "màu bị cháy")]])
        report = autopilot_mod.run(job, runner=run)
        assert report["retries"] == 0
        assert len(calls) == 1, "không có remedy thì không được thử ngẫu nhiên"
        assert report["remaining_issues"][0]["code"] == "mau_bi_chay"

    def test_never_more_than_two_renders(self, job):
        run, _ = self._runner(job, [[issue("stutter")], [issue("stutter")]])
        report = autopilot_mod.run(job, runner=run)
        assert report["renders"] <= 2

    def test_the_time_budget_stops_a_retry(self, job, monkeypatch):
        run, calls = self._runner(job, [[issue("stutter")], []])
        clock = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])
        monkeypatch.setattr(autopilot_mod.time, "time",
                            lambda: next(clock, 10_000.0))
        report = autopilot_mod.run(job, runner=run, time_budget=60.0)
        assert report["retries"] == 0
        assert len(calls) == 1

    def test_unmeasured_findings_do_not_trigger_a_render(self, job):
        run, calls = self._runner(job, [[issue("unmeasured", "không đo được")]])
        report = autopilot_mod.run(job, runner=run)
        assert len(calls) == 1
        assert report["retries"] == 0

    def test_the_report_is_written_even_when_it_failed(self, job):
        """"Tried this, here is what is still wrong" is the useful outcome."""
        run, _ = self._runner(job, [[issue("stutter")], [issue("av_drift")]])
        autopilot_mod.run(job, runner=run)
        report = json.loads(
            (job.dir / "autopilot_report.json").read_text(encoding="utf-8"))
        assert report["resolved"] is False
        assert [a["pass"] for a in report["attempts"]] == [1, 2]
        assert job.load()["autopilot"]["retries"] == 1

    def test_a_legacy_report_without_codes_still_parses(self, job):
        """An old job's verify report holds plain strings."""
        def run(target, options=None, stages=None, use_cache=True):
            (target.dir / "verify_report_v1.json").write_text(
                json.dumps({"issues": ["Lệch video/audio 0.9s"]}), encoding="utf-8")
            return {}

        report = autopilot_mod.run(job, runner=run)
        assert report["remaining_issues"][0]["code"] == ""
        assert report["retries"] == 0, "không có code thì không tra được remedy"


class TestRenderConcurrencyOption:
    def test_half_halves_the_default(self):
        from lib.talking_head_edit.stages.render import _concurrency

        assert _concurrency({"render_concurrency": "half"}) == max(1, _concurrency() // 2)

    def test_an_explicit_number_is_honoured_and_clamped(self):
        from lib.talking_head_edit.stages.render import MAX_CONCURRENCY, _concurrency

        assert _concurrency({"render_concurrency": 2}) == 2
        assert _concurrency({"render_concurrency": 999}) == MAX_CONCURRENCY

    def test_nonsense_falls_back_to_the_default(self):
        from lib.talking_head_edit.stages.render import _concurrency

        assert _concurrency({"render_concurrency": "nhanh lên"}) == _concurrency()

    def test_no_option_is_the_default(self):
        from lib.talking_head_edit.stages.render import _concurrency

        assert _concurrency(None) == _concurrency({})


class TestChatRevise:
    def _stub(self, job, monkeypatch, report=None):
        """A revise stage that records the prompt options it was handed."""
        seen: list[dict] = []

        def fake_revise(target, options):
            seen.append(dict(options))
            version = int(target.load().get("current_version", 0)) + 1
            target.spec_path(version).write_text('{"events": []}', encoding="utf-8")
            (target.dir / f"revise_diff_v{version}.json").write_text(
                json.dumps({"diff": {"removed": ["card w24"]}}), encoding="utf-8")
            state = target.load()
            state["current_version"] = version
            state.setdefault("versions", []).append({"version": version,
                                                     "kind": "revise"})
            target.save(state)
            return {"version": version,
                    "report": report or {"added": 0, "removed": 1, "modified": 0,
                                         "top_level_changed": "", "errors": []},
                    "usage": {"total_tokens": 8000}, "cost_usd": 0.0}

        return fake_revise, seen

    def test_a_turn_is_recorded_with_its_outcome(self, job, monkeypatch):
        stub, _ = self._stub(job, monkeypatch)
        result = chat_revise.chat(job, "bỏ card 2", revise_runner=stub)
        assert result["applied"] is True
        turns = chat_revise.read_history(job)
        assert turns[0]["message"] == "bỏ card 2"
        assert "bỏ 1" in turns[0]["result"]

    def test_the_third_turn_sees_the_first_two(self, job, monkeypatch):
        """"bỏ card 2" then "thêm lại card đó" — the second turn needs the first."""
        stub, seen = self._stub(job, monkeypatch)
        chat_revise.chat(job, "bỏ card 2", revise_runner=stub)
        chat_revise.chat(job, "đổi nhạc sang cái nhẹ hơn", revise_runner=stub)
        chat_revise.chat(job, "thêm lại card đó", revise_runner=stub)

        history = seen[-1]["revise_history"]
        assert "bỏ card 2" in history
        assert "đổi nhạc" in history

    def test_history_is_capped_at_three_turns(self, job, monkeypatch):
        """The point of revise is being cheap; unbounded history spends that."""
        stub, seen = self._stub(job, monkeypatch)
        for index in range(6):
            chat_revise.chat(job, f"yêu cầu {index}", revise_runner=stub)
        # The history handed to turn 5 is turns 2-4: the current request is not
        # part of its own context.
        history = seen[-1]["revise_history"]
        assert "yêu cầu 4" in history
        assert "yêu cầu 1" not in history
        assert history.count("→") == 3

    def test_the_first_turn_has_no_history_block(self, job, monkeypatch):
        stub, seen = self._stub(job, monkeypatch)
        chat_revise.chat(job, "bỏ card 2", revise_runner=stub)
        assert seen[0]["revise_history"] == ""

    def test_dry_run_creates_no_version(self, job, monkeypatch):
        stub, _ = self._stub(job, monkeypatch)
        before = job.load()["current_version"]
        result = chat_revise.chat(job, "thử bỏ card 2", dry_run=True,
                                 revise_runner=stub)
        assert result["applied"] is False
        assert result["version"] is None
        assert job.load()["current_version"] == before
        assert job.load()["versions"] == []

    def test_dry_run_still_returns_the_diff(self, job, monkeypatch):
        stub, _ = self._stub(job, monkeypatch)
        result = chat_revise.chat(job, "thử", dry_run=True, revise_runner=stub)
        assert result["diff"]["diff"]["removed"] == ["card w24"]

    def test_dry_run_keeps_its_proposal_readable(self, job, monkeypatch):
        stub, _ = self._stub(job, monkeypatch)
        chat_revise.chat(job, "thử", dry_run=True, revise_runner=stub)
        assert (job.dir / "spec_dryrun_v2.json").exists()

    def test_a_dry_run_is_recorded_but_not_used_as_context(self, job, monkeypatch):
        """"I asked and looked but did not apply" should be visible, and should
        not pretend to be a change the next turn can refer back to."""
        stub, seen = self._stub(job, monkeypatch)
        chat_revise.chat(job, "chỉ xem thử", dry_run=True, revise_runner=stub)
        chat_revise.chat(job, "làm thật đi", revise_runner=stub)
        assert len(chat_revise.read_history(job)) == 2
        assert "chỉ xem thử" not in seen[-1]["revise_history"]

    def test_an_empty_message_is_refused(self, job):
        with pytest.raises(chat_revise.ChatReviseError):
            chat_revise.chat(job, "   ")

    def test_an_overlong_message_is_refused(self, job):
        with pytest.raises(chat_revise.ChatReviseError, match="quá dài"):
            chat_revise.chat(job, "x" * 3000)

    def test_a_failed_turn_is_recorded_with_its_error(self, job):
        def explode(target, options):
            raise RuntimeError("model từ chối yêu cầu này")

        with pytest.raises(RuntimeError):
            chat_revise.chat(job, "làm điều không thể", revise_runner=explode)
        turn = chat_revise.read_history(job)[0]
        assert turn["applied"] is False
        assert "từ chối" in turn["error"]

    def test_an_expensive_turn_warns(self, job, monkeypatch):
        def pricey(target, options):
            version = int(target.load().get("current_version", 0)) + 1
            state = target.load()
            state["current_version"] = version
            target.save(state)
            return {"version": version, "report": {}, "usage": {"total_tokens": 40_000}}

        chat_revise.chat(job, "sửa nhiều thứ", revise_runner=pricey)
        warnings = [e for e in job.read_events()
                    if e.get("type") == "warning" and e.get("stage") == "chat"]
        assert warnings and "token" in warnings[-1]["message"]


class TestRevisePromptHistory:
    def test_v2_without_history_matches_v1(self):
        """A one-off revise must render exactly the prompt it always did."""
        from lib.talking_head_edit.stages.revise import build_revise_prompt

        spec = {"events": [{"type": "caption", "w0": 0, "w1": 5, "text": "a"}]}
        words = [{"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4}
                 for i in range(10)]
        assert build_revise_prompt(spec, words, "bỏ card", version="v2") == \
            build_revise_prompt(spec, words, "bỏ card", version="v1")

    def test_v2_includes_the_history_block(self):
        from lib.talking_head_edit.stages.revise import build_revise_prompt

        spec = {"events": []}
        words = [{"word": "a", "start": 0.0, "end": 0.4}]
        prompt = build_revise_prompt(spec, words, "thêm lại", version="v2",
                                    history="\n\nTRƯỚC ĐÓ: bỏ card 2")
        assert "TRƯỚC ĐÓ: bỏ card 2" in prompt


class TestCancelKillsTheWholeTree:
    """`/cancel` must stop ffmpeg and node too, not just the CLI wrapper.

    Killing only the parent leaves a render chewing every core with nothing
    watching it — worse than offering no cancel, because the UI says "cancelled".
    """

    def test_a_child_process_really_dies(self, tmp_path):
        import os
        import subprocess
        import sys
        import time

        from server.queue_worker import kill_tree

        marker = tmp_path / "child.pid"
        # Parent spawns a child that outlives it unless the tree is killed.
        script = (
            "import subprocess, sys, pathlib, time\n"
            "child = subprocess.Popen([sys.executable, '-c',"
            " 'import time; time.sleep(120)'])\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
            "time.sleep(120)\n"
        )
        isolation = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                     if os.name == "nt" else {"start_new_session": True})
        parent = subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, **isolation)
        try:
            deadline = time.time() + 15
            while not marker.exists() and time.time() < deadline:
                time.sleep(0.1)
            assert marker.exists(), "tiến trình con chưa kịp khởi động"
            child_pid = int(marker.read_text())

            note = kill_tree(parent)
            assert "process con" in note

            # Give the OS a moment, then confirm the child is gone.
            gone = False
            deadline = time.time() + 10
            while time.time() < deadline:
                if not _pid_alive(child_pid):
                    gone = True
                    break
                time.sleep(0.2)
            assert gone, f"tiến trình con {child_pid} vẫn còn sống sau khi cancel"
        finally:
            parent.kill()

    def test_an_already_finished_process_is_reported_not_killed(self):
        import subprocess
        import sys

        from server.queue_worker import kill_tree

        done = subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait()
        assert "tự kết thúc" in kill_tree(done)


def _pid_alive(pid: int) -> bool:
    import os
    import subprocess

    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
