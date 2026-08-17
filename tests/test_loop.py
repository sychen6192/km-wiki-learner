"""End-to-end tests for loop/daily.sh, the part of this project that breaks.

Every bug this project has shipped lived in the loop script — a Linux-only
flock, a missing --auto, an interrupt that committed anyway, a failed run whose
commit read like a success. None are reachable from the unit tests, and reaching
them by hand meant paying a model, so they went untested.

Each test builds a minimal repo in a temp directory: the working tree's scripts
plus a small fixture vault, so runs are fast, hermetic, and unaffected by what
the real vault happens to hold today. The agent is tests/stub_agent.py — no
model is called, nothing is pushed, and the real repo is never touched.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# What the loop itself needs; the vault is a fixture, not a copy.
NEEDED = ("loop/daily.sh", "scripts/python.sh",
          "tools/vault.py", "tools/extract.py", "tools/render.py", "tools/agent.py",
          "prompts/daily.md", "prompts/learn.md", "prompts/garden.md",
          "tests/stub_agent.py", "AGENTS.md")
HAVE_TOOLS = all(shutil.which(t) for t in ("git", "bash"))

NOTE = """---
status: evergreen
created: 2026-01-01
updated: 2026-01-01
tags: [test]
---

# {title}

**{title}** 是測試用的筆記，連向 {links}。{filler}
"""


@unittest.skipUnless(HAVE_TOOLS, "needs git and bash")
class LoopHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name) / "repo"

        for rel in NEEDED:
            dst = self.work / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dst)
        (self.work / "loop" / "daily.sh").chmod(0o755)

        vault = self.work / "vault"
        for sub in ("Notes", "Maps", "Sources", "Daily", "Review", "Raw", "_meta/Templates"):
            (vault / sub).mkdir(parents=True, exist_ok=True)
        filler = "字 " * 60
        (vault / "Notes" / "Loop Engineering.md").write_text(
            NOTE.format(title="Loop Engineering", links="[[LLM-Native Wiki]]", filler=filler),
            encoding="utf-8")
        (vault / "Notes" / "LLM-Native Wiki.md").write_text(
            NOTE.format(title="LLM-Native Wiki", links="[[Loop Engineering]] 與 [[Frontier Idea]]",
                        filler=filler),
            encoding="utf-8")
        (vault / "Home.md").write_text(
            "# Home\n\n<!-- km:stats:start -->\n<!-- km:stats:end -->\n", encoding="utf-8")
        (vault / "Inbox.md").write_text("- [ ] 一個待辦\n", encoding="utf-8")
        (vault / "_meta" / "Style Guide.md").write_text("# Style Guide\n\n規範。\n", encoding="utf-8")
        for name in ("Note", "Source", "Map", "Daily Report"):
            (vault / "_meta" / "Templates" / f"{name}.md").write_text("模板\n", encoding="utf-8")

        # Settle the generated dashboard before the baseline, so a run that does
        # nothing really leaves nothing to commit.
        subprocess.run([sys.executable, "tools/vault.py", "stats"], cwd=str(self.work),
                       check=True, capture_output=True)
        self.git("init", "--quiet", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "fixture")
        self.baseline = self.git("rev-parse", "HEAD").stdout.strip()

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=str(self.work), check=check,
                              capture_output=True, text=True, encoding="utf-8")

    def run_loop(self, mode="ok", **env):
        environ = {k: v for k, v in os.environ.items() if not k.startswith("KM_")}
        environ.update(
            KM_STUB_MODE=mode,
            KM_AGENT_CMD=f"{sys.executable} tests/stub_agent.py",
            KM_NO_PULL="1", KM_NO_PUSH="1", KM_MAX_ITEMS="1", KM_TIMEOUT="60",
            **env,
        )
        done = subprocess.run(["bash", "./loop/daily.sh"], cwd=str(self.work), env=environ,
                              capture_output=True, text=True, encoding="utf-8", timeout=180)
        return done.stdout + done.stderr

    def head_message(self) -> str:
        return self.git("log", "-1", "--pretty=%s").stdout.strip()

    def new_commits(self) -> int:
        return int(self.git("rev-list", f"{self.baseline}..HEAD", "--count").stdout.strip() or 0)


class TestLoopRuns(LoopHarness):
    def test_a_good_run_commits_the_agents_work(self):
        out = self.run_loop("ok")
        self.assertIn("stub: prompt ok", out, out)
        self.assertIn("km-wiki loop finished", out, out)
        self.assertEqual(self.new_commits(), 1, out)
        # The daily report's summary line reaches the commit subject, unlabelled.
        self.assertIn("整合測試寫了一篇存根筆記", self.head_message())
        self.assertNotIn("agent 未完成", self.head_message())
        self.assertTrue((self.work / "vault" / "Notes" / "Stub Test Note.md").is_file())
        self.assertIn("0 error", out)

    def test_a_run_that_wrote_nothing_is_labelled_not_praised(self):
        out = self.run_loop("silent")
        self.assertIn("no daily report written", out, out)
        # Nothing changed, so there is nothing to commit — the loop must not
        # manufacture an empty commit to look busy.
        self.assertEqual(self.new_commits(), 0, out)

    def test_a_crashed_agent_still_commits_but_says_so(self):
        (self.work / "vault" / "Raw" / "dropped-by-human.txt").write_text("材料", encoding="utf-8")
        out = self.run_loop("crash")
        self.assertIn("agent run exited non-zero", out, out)
        self.assertEqual(self.new_commits(), 1, out)
        self.assertIn("agent 未完成", self.head_message())
        # Whatever the human left behind survives the agent dying.
        self.assertTrue((self.work / "vault" / "Raw" / "dropped-by-human.txt").is_file())

    def test_bad_output_triggers_the_repair_pass_and_commits_anyway(self):
        out = self.run_loop("bad-note")
        self.assertIn("ERROR", out, out)
        self.assertIn("attempting one repair pass", out, out)
        # The stub cannot repair anything, so lint still fails — and the loop
        # commits regardless rather than discard the run.
        self.assertEqual(self.new_commits(), 1, out)
        self.assertIn("agent 未完成", self.head_message())

    def test_dry_run_never_reaches_the_agent(self):
        out = self.run_loop("crash", KM_SKIP_AGENT="1")
        self.assertIn("skipping agent run", out, out)
        self.assertNotIn("stub:", out, out)
        self.assertEqual(self.new_commits(), 0, out)


class TestLoopLocking(LoopHarness):
    """Liveness is a heartbeat, not a pid — see the comment above LOCK in daily.sh."""

    def plant_lock(self, idle_seconds: float = 0) -> Path:
        lock = self.work / "loop" / "state" / "lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text("msys=1 win=? since=00:00:00", encoding="utf-8")
        if idle_seconds:
            stamp = time.time() - idle_seconds
            os.utime(lock, (stamp, stamp))
        return lock

    def test_a_beating_lock_keeps_a_second_run_out(self):
        self.plant_lock()
        out = self.run_loop("ok")
        self.assertIn("already running", out, out)
        self.assertEqual(self.new_commits(), 0, out)

    def test_a_lock_whose_heartbeat_stopped_is_reclaimed(self):
        self.plant_lock(idle_seconds=600)  # default staleness threshold is 120s
        out = self.run_loop("ok")
        self.assertIn("回收停擺的鎖", out, out)
        self.assertEqual(self.new_commits(), 1, out)

    def test_the_lock_is_released_after_a_normal_run(self):
        self.run_loop("ok")
        self.assertFalse((self.work / "loop" / "state" / "lock").exists())

    def test_it_returns_promptly_when_its_output_is_captured(self):
        # Killing the heartbeat subshell does not reap the sleep it is inside,
        # so if that sleep inherited our stdout, a caller reading the loop's
        # output stays blocked for a full heartbeat interval after the run has
        # finished — invisible from a terminal, obvious from CI.
        started = time.monotonic()
        self.run_loop("ok", KM_HEARTBEAT_SEC="30")
        self.assertLess(time.monotonic() - started, 15,
                        "the loop held the caller's pipe after finishing")


if __name__ == "__main__":
    unittest.main()
